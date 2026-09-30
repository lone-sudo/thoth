"""GGUF drift check — offline, zero server, zero episodes.

The drift tool is pure data-in/data-out over the matrix catalog: streaming
sha256, head fingerprints, manifest compare, and a thin CLI. These tests pin
the core (every drift kind), the CLI flows (exit codes 0/1/2/3/4), and the
matrix preflight wiring (ADR-006 Open Question 3, resolved 2026-09-30).

The one disk-touching integration test hashes only sizes + first-1MiB
fingerprints (~4 MiB total) — full-file sha256 is deliberately NOT exercised
against the real 2 GB qwen3b.gguf in CI; the byte-swap test below proves the
hasher itself on a tmp file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evals import model_drift as md
from evals import model_matrix as mm


# --------------------------------------------------------------- pure core

def test_sha256_and_fingerprint_track_bytes(tmp_path):
    """The hasher sees content, not names: rewriting a file with different
    bytes changes both hashes; a byte-identical copy matches."""
    a = tmp_path / "a.gguf"
    a.write_bytes(b"thoth-gguf-payload" * 1000)
    h1 = md.sha256_file(a)
    assert md.sha256_file(a) == h1                    # stable across re-reads
    copy = tmp_path / "copy.gguf"
    copy.write_bytes(a.read_bytes())
    assert md.sha256_file(copy) == h1                 # identity is bytes-only
    a.write_bytes(b"thoth-gguf-payl0ad" * 1000)       # one byte differs
    assert md.sha256_file(a) != h1
    assert md.fingerprint_file(a) != md.fingerprint_file(copy)
    # head fingerprint ignores everything past its window (explicit 64 B head:
    # the default 1 MiB window would swallow these 128-byte test files whole)
    tail_changed = tmp_path / "tail.gguf"
    tail_changed.write_bytes(a.read_bytes()[:64] + b"x" * 64)
    original = tmp_path / "orig.gguf"
    original.write_bytes(a.read_bytes()[:64] + b"y" * 64)
    assert (md.fingerprint_file(tail_changed, head_bytes=64)
            == md.fingerprint_file(original, head_bytes=64))


def test_compare_reports_every_drift_kind(tmp_path):
    """missing / size / fingerprint / sha256 all surface, with the model key
    attached. Clean bytes produce no records."""
    catalog = [
        {"key": "m-ok", "file": "ok.gguf", "params": "?",
         "gguf": str(tmp_path / "ok.gguf")},
        {"key": "m-size", "file": "size.gguf", "params": "?",
         "gguf": str(tmp_path / "size.gguf")},
        {"key": "m-bytes", "file": "bytes.gguf", "params": "?",
         "gguf": str(tmp_path / "bytes.gguf")},
        {"key": "m-gone", "file": "gone.gguf", "params": "?",
         "gguf": str(tmp_path / "gone.gguf")},
    ]
    for m in catalog[:3]:
        Path(m["gguf"]).write_bytes(b"payload-" + m["key"].encode())
    ok_path = Path(catalog[0]["gguf"])
    manifest = {"schema_version": 1, "models": [
        {"file": "ok.gguf", "params": "?",
         "size_bytes": ok_path.stat().st_size,
         "sha256": md.sha256_file(ok_path),
         "head_sha256": md.fingerprint_file(ok_path)},
        {"file": "size.gguf", "params": "?", "size_bytes": 999,
         "sha256": "x", "head_sha256": "x"},
        {"file": "bytes.gguf", "params": "?",
         "size_bytes": Path(catalog[2]["gguf"]).stat().st_size,
         "sha256": "0" * 64, "head_sha256": "1" * 64},
    ]}
    # m-ok matches everywhere -> no record; the others fail at their stage
    kinds = {d["model"]: d["kind"] for d in md.compare(manifest, catalog)}
    assert kinds == {"m-size": "size", "m-bytes": "fingerprint",
                     "m-gone": "missing"}
    # a matching size AND matching head fingerprint but wrong full sha256
    # surfaces as sha256 drift (the last line of defense)
    m2 = dict(manifest["models"][1],
              size_bytes=Path(catalog[1]["gguf"]).stat().st_size,
              head_sha256=md.fingerprint_file(Path(catalog[1]["gguf"])),
              sha256="2" * 64)
    manifest2 = {"schema_version": 1, "models": [manifest["models"][0], m2]}
    kinds2 = {d["model"]: d["kind"] for d in md.compare(manifest2, catalog)}
    assert kinds2["m-size"] == "sha256"


def test_fast_compare_matches_full_on_head_drift(tmp_path):
    """Fast mode keeps every stage except the full-sha one: same-size byte
    changes are still caught (that is the fingerprint's whole job)."""
    catalog = _mini_catalog(tmp_path)
    manifest = md.build_manifest(catalog)
    assert md.compare(manifest, catalog, fast=True) == []
    Path(catalog[1]["gguf"]).write_bytes(b"mini-qwen3b.ggug")  # 16 B, same size
    kinds_fast = {d["model"]: d["kind"]
                  for d in md.compare(manifest, catalog, fast=True)}
    kinds_full = {d["model"]: d["kind"] for d in md.compare(manifest, catalog)}
    assert kinds_fast == {"qwen3b": "fingerprint"}
    assert kinds_fast == kinds_full


def test_fast_blind_spot_is_tail_only_full_check_catches_it(tmp_path):
    """The honest tradeoff, pinned: bytes past the head window are invisible
    to fast mode; the full sha256 remains the last word."""
    p = tmp_path / "big.gguf"
    head = b"A" * md.CHECK_BYTES
    p.write_bytes(head + b"B" * md.CHECK_BYTES)
    catalog = [{"key": "big", "file": "big.gguf", "params": "?",
                "gguf": str(p)}]
    manifest = {"schema_version": 1, "models": [
        {"file": "big.gguf", "params": "?",
         "size_bytes": p.stat().st_size, "sha256": md.sha256_file(p),
         "head_sha256": md.fingerprint_file(p)}]}
    assert md.compare(manifest, catalog, fast=True) == []
    p.write_bytes(head + b"C" + b"B" * (md.CHECK_BYTES - 1))  # tail corruption
    assert md.compare(manifest, catalog, fast=True) == []     # blind by design
    kinds = {d["model"]: d["kind"] for d in md.compare(manifest, catalog)}
    assert kinds == {"big": "sha256"}


def test_compare_refuses_duplicate_manifest_entries(tmp_path):
    p = tmp_path / "dup.gguf"
    p.write_bytes(b"x")
    catalog = [{"key": "m", "file": "dup.gguf", "params": "?",
                "gguf": str(p)}]
    manifest = {"schema_version": 1, "models": [
        {"file": "dup.gguf", "size_bytes": 1, "sha256": "a", "head_sha256": "b"},
        {"file": "dup.gguf", "size_bytes": 1, "sha256": "a", "head_sha256": "b"},
    ]}
    with pytest.raises(ValueError, match="twice"):
        md.compare(manifest, catalog)


def test_build_manifest_never_records_absolute_paths(tmp_path):
    """The manifest must survive storage moves: file names, not paths."""
    gguf = tmp_path / "nested" / "m.gguf"
    gguf.parent.mkdir()
    gguf.write_bytes(b"abc")
    manifest = md.build_manifest(
        [{"key": "m", "file": "m.gguf", "params": "tiny",
          "gguf": str(gguf)}])
    dumped = json.dumps(manifest)
    assert str(tmp_path) not in dumped and "nested" not in dumped
    entry = manifest["models"][0]
    assert entry["file"] == "m.gguf" and entry["params"] == "tiny"
    assert entry["sha256"] == md.sha256_file(gguf)
    assert manifest["schema_version"] == md.SCHEMA_VERSION


# --------------------------------------------------------------- CLI flows

def _write_manifest(path: Path, catalog: list[dict], tmp_path: Path) -> None:
    path.write_text(json.dumps(md.build_manifest(catalog)), encoding="utf-8")


def _mini_catalog(tmp_path: Path) -> list[dict]:
    out = []
    for name in ("model.gguf", "qwen3b.gguf"):
        p = tmp_path / name
        p.write_bytes(b"mini-" + name.encode())
        out.append({"key": name.removesuffix(".gguf"), "file": name,
                    "params": "?", "gguf": str(p)})
    return out


def test_cli_check_clean_exit_zero(tmp_path, capsys, monkeypatch):
    catalog = _mini_catalog(tmp_path)
    manifest_path = tmp_path / "m.json"
    _write_manifest(manifest_path, catalog, tmp_path)
    monkeypatch.setattr(md.mm, "MODELS", catalog)   # check runs over the catalog
    monkeypatch.setattr(sys, "argv", ["model_drift", "--manifest",
                                      str(manifest_path)])
    assert md.main() == 0
    assert "clean: all 2" in capsys.readouterr().out


def test_cli_fast_flag_checks_fingerprint_stage_only(tmp_path, capsys,
                                                     monkeypatch):
    catalog = _mini_catalog(tmp_path)
    manifest_path = tmp_path / "m.json"
    _write_manifest(manifest_path, catalog, tmp_path)
    monkeypatch.setattr(md.mm, "MODELS", catalog)
    monkeypatch.setattr(sys, "argv", ["model_drift", "--fast", "--manifest",
                                      str(manifest_path)])
    assert md.main() == 0
    out = capsys.readouterr().out
    assert "clean: all 2" in out and "fast" in out


def test_cli_detects_drift_exit_three(tmp_path, capsys, monkeypatch):
    catalog = _mini_catalog(tmp_path)
    manifest_path = tmp_path / "m.json"
    _write_manifest(manifest_path, catalog, tmp_path)
    monkeypatch.setattr(md.mm, "MODELS", catalog)   # check runs over the catalog
    # same size, different bytes: the sneakier re-quantization — size passes,
    # the head fingerprint is what catches it
    Path(catalog[1]["gguf"]).write_bytes(b"REQUANTIZED-BYTE")
    monkeypatch.setattr(sys, "argv", ["model_drift", "--manifest",
                                      str(manifest_path)])
    assert md.main() == 3
    out = capsys.readouterr().out
    assert "DRIFT" in out and "fingerprint" in out
    assert "no longer evidence" in out        # next step is baked in


def test_cli_missing_manifest_exit_four(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["model_drift", "--manifest",
                                      str(tmp_path / "nope.json")])
    assert md.main() == 4
    assert "--build" in capsys.readouterr().out


def test_cli_build_missing_gguf_exit_one(tmp_path, capsys, monkeypatch):
    catalog = _mini_catalog(tmp_path)
    Path(catalog[0]["gguf"]).unlink()
    monkeypatch.setattr(md, "DEFAULT_MANIFEST", tmp_path / "m.json")
    monkeypatch.setattr(md.mm, "MODELS", catalog)
    monkeypatch.setattr(sys, "argv", ["model_drift", "--build"])
    assert md.main() == 1
    assert "cannot build" in capsys.readouterr().out


def test_cli_build_then_check_roundtrip(tmp_path, capsys, monkeypatch):
    catalog = _mini_catalog(tmp_path)
    manifest_path = tmp_path / "m.json"
    monkeypatch.setattr(md, "DEFAULT_MANIFEST", manifest_path)
    monkeypatch.setattr(md.mm, "MODELS", catalog)
    monkeypatch.setattr(sys, "argv", ["model_drift", "--build", "--manifest",
                                      str(manifest_path)])
    assert md.main() == 0
    monkeypatch.setattr(sys, "argv", ["model_drift", "--check", "--manifest",
                                      str(manifest_path)])
    assert md.main() == 0
    assert "clean: all 2" in capsys.readouterr().out


def test_cli_conflicting_flags_exit_two(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["model_drift", "--build", "--check",
                                      "--manifest", str(tmp_path / "m.json")])
    assert md.main() == 2
    assert "--build runs alone" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["model_drift", "--build", "--fast",
                                      "--manifest", str(tmp_path / "m.json")])
    assert md.main() == 2                                    # --fast too


# -------------------------------------------------- committed-manifest pins

def test_committed_manifest_pins_the_catalog():
    """The manifest on disk must cover exactly the matrix catalog, with the
    recorded params — a catalog edit without a manifest rebuild fails CI."""
    manifest = md.load_manifest(md.DEFAULT_MANIFEST)
    listed = {e["file"]: e for e in manifest["models"]}
    assert set(listed) == {Path(m["gguf"]).name for m in mm.MODELS}
    for m in mm.MODELS:
        assert listed[Path(m["gguf"]).name]["params"] == m["params"]


def test_committed_manifest_records_are_shape_complete():
    manifest = md.load_manifest(md.DEFAULT_MANIFEST)
    for e in manifest["models"]:
        assert set(e) == {"file", "params", "size_bytes", "sha256",
                          "head_sha256"}
        assert len(e["sha256"]) == 64 and len(e["head_sha256"]) == 64
        assert int(e["size_bytes"]) > 0


def test_committed_manifest_fast_check_passes():
    """The CI drift gate (ADR-006 Q3): sizes + first-1MiB fingerprints over
    the real catalog GGUFs — ~4 MiB read, seconds, no server. Skipped where
    the smoke-session GGUFs are absent; the full --check remains the
    operator's last word before bytes enter a decision record."""
    if not all(Path(m["gguf"]).is_file() for m in mm.MODELS):
        pytest.skip("catalog GGUFs not on disk (no smoke-session environment)")
    manifest = md.load_manifest(md.DEFAULT_MANIFEST)
    assert md.compare(manifest, mm.MODELS, fast=True) == []


# ---------------------------------------------------- matrix preflight wire

def test_matrix_preflight_warns_on_drift(capsys, monkeypatch, tmp_path):
    """The matrix surfaces drift loudly but proceeds — refusing is the
    operator's explicit --check call."""
    drift_rec = [{"model": "qwen2.5-3b", "file": "qwen3b.gguf",
                  "kind": "fingerprint", "detail": "bytes changed"}]
    monkeypatch.setattr(md, "load_manifest",
                        lambda p: {"schema_version": 1, "models": []})
    monkeypatch.setattr(md, "compare",
                        lambda man, models, fast=False: drift_rec)
    monkeypatch.setattr(mm.drift, "load_manifest",
                        lambda p: {"schema_version": 1, "models": []})
    monkeypatch.setattr(mm.drift, "compare",
                        lambda man, models, fast=False: drift_rec)
    mm._preflight_drift(tmp_path / "unused")
    out = capsys.readouterr().out
    assert "DRIFT WARNING" in out and "no longer evidence" in out


def test_matrix_preflight_silent_when_clean(capsys, monkeypatch):
    monkeypatch.setattr(md, "load_manifest", lambda p: {"models": []})
    monkeypatch.setattr(md, "compare", lambda man, models, fast=False: [])
    monkeypatch.setattr(mm.drift, "load_manifest", lambda p: {"models": []})
    monkeypatch.setattr(mm.drift, "compare",
                        lambda man, models, fast=False: [])
    mm._preflight_drift("unused")
    assert "DRIFT" not in capsys.readouterr().out
