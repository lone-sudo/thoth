"""GGUF drift check — does the catalog still hold the weights the gate scored?

ADR-006 Open Question 3 (resolved 2026-09-30): GGUF re-quantizations and
re-downloads silently change model behavior, and a matrix row is only
evidence for the bytes it actually scored. This module makes the bytes part
of the record: a sha256 manifest over the matrix catalog's GGUFs, committed
next to the decision record, checked before the gate runs.

- Pure core, zero I/O surprises: hashing is streaming (chunked), so the 2 GB
  qwen3b.gguf never sits whole in memory; stdlib only ($0 policy).
- No server, no DB, no episodes: the check is cheap enough to run as a
  preflight before every matrix invocation.
- Drift is not refusal BY DEFAULT. The gate's decision record says which
  artifacts were scored; a drifted GGUF invalidates old rows as *evidence*,
  and that call belongs to the operator: rebuild the manifest, re-run the
  matrix, amend the record (the re-decision procedure in ADR-006 section 5).
  The matrix main() surfaces the warning loudly; `--check` exits nonzero.

Usage:
  python -m evals.model_drift            # full check: catalog vs the manifest
  python -m evals.model_drift --fast     # sizes + head fingerprints only (seconds)
  python -m evals.model_drift --build    # (re)build the manifest from disk
  python -m evals.model_drift --check    # explicit check; exit 3 on drift

Exit codes: 0 clean | 1 missing artifact | 3 drift (size/hash/fingerprint)
| 4 no manifest (run --build) | 2 bad arguments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evals import model_matrix as mm  # noqa: E402  (the catalog it pins)

SCHEMA_VERSION = 1
HASH_CHUNK = 1 << 20  # 1 MiB streaming chunks: a 2 GB GGUF must not be read whole
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "model_manifest.json"

# Canonical fingerprint: parameters + byte length + sha256 of the first
# CHECK_BYTES bytes. Stable across storage moves (absolute paths never enter
# the manifest), catches the common failure fast (first-megabyte fingerprint
# differs immediately after a re-quantization), and stays verifiable for any
# catalog GGUF regardless of size.
CHECK_BYTES = 1 << 20  # 1 MiB


# ---------------------------------------------------------------------------
# pure core (unit-tested with zero I/O beyond tmp files)
# ---------------------------------------------------------------------------

def sha256_file(path: Path, chunk_size: int = HASH_CHUNK) -> str:
    """Streaming sha256 of one file; never loads the whole GGUF into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def fingerprint_file(path: Path, head_bytes: int = CHECK_BYTES) -> str:
    """sha256 over the first `head_bytes` bytes — the cheap drift canary."""
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        while read < head_bytes:
            chunk = fh.read(min(HASH_CHUNK, head_bytes - read))
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
    return h.hexdigest()


def entry_for(path: Path, params: str) -> dict:
    """One manifest record: identity + size + full sha256 + head fingerprint.

    Absolute paths never enter the manifest — the file's name is its stable
    handle (the matrix catalog already resolves catalog names onto whatever
    SMOKE_DIR the box has)."""
    return {
        "file": path.name,
        "params": params,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "head_sha256": fingerprint_file(path),
    }


def build_manifest(models: list[dict[str, str]]) -> dict:
    """Manifest dict for the given matrix-catalog entries (resolved gguf
    paths). Pure data in / data out; the CLI writes it."""
    entries = [entry_for(Path(m["gguf"]), m["params"]) for m in models]
    return {"schema_version": SCHEMA_VERSION, "models": entries}


def compare(manifest: dict, models: list[dict[str, str]],
            fast: bool = False) -> list[dict]:
    """Catalog vs manifest. Returns drift records (empty when clean).

    Each record names the model key, the catalog file, the kind of drift
    (missing | size | sha256 | fingerprint), and the two sides (as available).
    Catalog files absent from the manifest are drift too: the manifest is the
    evidence lock, and an unlocked catalog is not a pinned one.

    fast=True verifies sizes + first-1MiB fingerprints only, skipping the
    full-file sha256 stage (~4 MiB read for the four-model catalog instead of
    ~3 GB): cheap enough for a per-invocation preflight and a CI gate. A full
    --check remains the last word before bytes enter a decision record."""
    listed: dict[str, dict] = {}
    for entry in manifest.get("models", []):
        name = str(entry.get("file", ""))
        if name in listed:
            raise ValueError(f"manifest lists {name!r} twice")
        listed[name] = entry

    out: list[dict] = []
    for m in models:
        key, gguf_path = m["key"], Path(m["gguf"])
        entry = listed.get(gguf_path.name)
        if entry is None:
            out.append({"model": key, "file": gguf_path.name, "kind": "missing",
                        "detail": "not in the manifest"})
            continue
        if not gguf_path.is_file():
            out.append({"model": key, "file": gguf_path.name, "kind": "missing",
                        "detail": f"GGUF not on disk: {gguf_path}"})
            continue
        size = gguf_path.stat().st_size
        if size != int(entry.get("size_bytes", -1)):
            out.append({"model": key, "file": gguf_path.name, "kind": "size",
                        "detail": f"manifest {entry.get('size_bytes')} vs disk {size}"})
            continue
        head = fingerprint_file(gguf_path)
        if head != entry.get("head_sha256"):
            out.append({"model": key, "file": gguf_path.name, "kind": "fingerprint",
                        "detail": "first-1MiB hash differs (bytes changed)"})
            continue
        if fast:
            continue  # fast mode: sizes + head fingerprints are the contract
        if sha256_file(gguf_path) != entry.get("sha256"):
            out.append({"model": key, "file": gguf_path.name, "kind": "sha256",
                        "detail": "full-file hash differs (bytes changed)"})
    return out


# ---------------------------------------------------------------------------
# manifest persistence + thin CLI
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("schema_version", 0)) != SCHEMA_VERSION:
        raise ValueError(f"manifest schema {data.get('schema_version')!r} != "
                         f"{SCHEMA_VERSION}; rebuild with --build")
    if not isinstance(data.get("models"), list):
        raise ValueError("manifest has no models list; rebuild with --build")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(
        description="GGUF drift check over the model matrix catalog (ADR-006 Q3)")
    ap.add_argument("--build", action="store_true",
                    help="hash the catalog GGUFs and (re)write the manifest")
    ap.add_argument("--check", action="store_true",
                    help="explicit check (the default action); exit 3 on drift")
    ap.add_argument("--fast", action="store_true",
                    help="sizes + first-1MiB fingerprints only, no full-file "
                         "sha256 (seconds; what the matrix preflight and the "
                         "CI gate run)")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                    help=f"manifest path (default {DEFAULT_MANIFEST.name})")
    args = ap.parse_args()
    if args.build and (args.check or args.fast):
        print("--build runs alone (--check/--fast verify an existing manifest)")
        return 2
    manifest_path = Path(args.manifest)
    try:
        if args.build:
            missing = [m["file"] for m in mm.MODELS if not Path(m["gguf"]).is_file()]
            if missing:
                print(f"cannot build: catalog GGUF(s) not on disk: {', '.join(missing)}")
                print("the smoke session environment is a prerequisite "
                      "(journal 2026-W39)")
                return 1
            manifest = build_manifest(mm.MODELS)
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print(f"manifest written: {manifest_path}")
            for e in manifest["models"]:
                print(f"   {e['file']:<14} {e['params']:>5}  "
                      f"{e['size_bytes']:>12,} B  sha256 {e['sha256'][:12]}...")
            return 0

        if not manifest_path.is_file():
            print(f"no manifest at {manifest_path}; run "
                  f"`python -m evals.model_drift --build` on the scored bytes")
            return 4
        manifest = load_manifest(manifest_path)
        drift = compare(manifest, mm.MODELS, fast=args.fast)
        if not drift:
            mode = "fast (sizes + head fingerprints)" if args.fast else "full"
            print(f"clean: all {len(mm.MODELS)} catalog GGUFs match the "
                  f"manifest ({manifest_path.name}, {mode} check)")
            return 0
        print(f"DRIFT: {len(drift)} of {len(mm.MODELS)} catalog GGUFs do not "
              f"match the manifest ({manifest_path.name}):")
        for d in drift:
            print(f"   {d['model']:<14} {d['file']:<14} {d['kind']:<12} {d['detail']}")
        print("rows scored on the old bytes are no longer evidence: re-run the "
              "matrix on the new GGUFs, rebuild the manifest (--build), and "
              "amend the decision record (ADR-006 section 5).")
        return 3
    except (OSError, ValueError) as exc:
        print(f"drift check failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
