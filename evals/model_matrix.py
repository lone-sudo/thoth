"""Model matrix eval — one row per local model, four protocol scores (ADR-004).

Scores each local GGUF on *protocol adherence*, not task quality — the four
questions Team-B will ask about every candidate brain:
  json_validity      planner answers that parsed into a plan (pooled over the
                     model's episodes: parsed answers / attempted answers)
  tool_turn_rate     episodes with >=1 verified tool turn
  park_cleanliness   episodes ending in an expected terminal state (self-done,
                     or a diagnostic park — never a crash)
  self_finish        episodes the model ended itself (done=true, in-loop or
                     via the tool-free finish-confirmation probe)

Scored per GOAL FAMILY (ADR-006 amended 2026-09-30): the same protocol runs
over each family — "read" (file.read over the workspace) and "memory" (FTS
retrieval via memory.search) — and the ADR-006 gate passes a model only when
json% = tool% = 100% within EVERY family. Generalization is measured, not
assumed.

Stabilized protocol (journal 2026-W39, "publication-grade rates"): 5 episodes
per model at a PINNED temperature (0.2 — the production planner's setting, not
the smoke demo's 0.4) and Wilson 95% score intervals on every rate. n=2 rows
were directional only; a 0% or 100% rate at n=5 still carries a real interval,
which is exactly what a decision log needs to see.

Every byte still crosses guard.check_egress: the matrix reuses the smoke
driver's shim and plain harness unchanged (hint-free goal, no few-shot).

Before any row is scored, a GGUF drift preflight (evals/model_drift.py,
ADR-006 Open Question 3 resolved 2026-09-30) compares the catalog GGUFs
against the committed manifest (evals/model_manifest.json); a mismatch warns
loudly that old rows stopped being evidence. The run proceeds — refusing is
the operator's call (--check) — but a matrix run on drifted bytes can never
happen silently again.

Prereqs: the smoke session's environment (journal 2026-W39) — llama-server
(llama.cpp b11223) at %TEMP%/thoth-smoke/llama/llama-server.exe (override with
LLAMA_SERVER) and the four sha-verified GGUFs in %TEMP%/thoth-smoke/.

Usage:
  python -m evals.model_matrix                    # all four models, 5 episodes
                                                  # per model PER FAMILY
  python -m evals.model_matrix --models qwen2.5-3b
  python -m evals.model_matrix --family memory    # one goal family only
                                                  # (a verdict needs all)
  python -m evals.model_matrix --json             # machine rows + episodes
  python -m evals.model_matrix --add cand /path/to.gguf 1.5B
                                                  # ADR-006 audition: a new
                                                  # candidate through the gate
                                                  # (every family), no code edits
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib import request as _urlrequest
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evals import model_drift as drift  # noqa: E402  (GGUF preflight)
from evals import smoke_local_planner as smoke  # noqa: E402
from thoth import db, notes  # noqa: E402

SMOKE_DIR = Path(tempfile.gettempdir()) / "thoth-smoke"
DEFAULT_SERVER = SMOKE_DIR / "llama" / "llama-server.exe"
PORT = 11434

# One row per model: the same four GGUFs the smoke session downloaded and
# sha-verified (journal 2026-W39). Floor-first, capability ascending.
# gguf is resolved at import (see _resolve_gguf): SMOKE_DIR-relative files,
# absolute paths for candidates added via --add.
MODELS: list[dict[str, str]] = [
    {"key": "smollm2-135m", "file": "model.gguf", "params": "135M"},
    {"key": "smollm2-360m", "file": "model360.gguf", "params": "360M"},
    {"key": "qwen2.5-0.5b", "file": "qwen05b.gguf", "params": "0.5B"},
    {"key": "qwen2.5-3b", "file": "qwen3b.gguf", "params": "3B"},
]


EPISODES_PER_MODEL = 5
# Pinned sampling temperature for every matrix episode (journal 2026-W39):
# the production planner runs at 0.2; the smoke demo's 0.4 is a demo aid.
# Rates measured at a different temperature are not comparable across runs.
DEFAULT_TEMPERATURE = 0.2

# Goal families (ADR-006 amended 2026-09-30): the gate measures whether a
# model generalizes the plan->act->verify->finish protocol across different
# level-0 tool surfaces, not just the one README-reading goal it was first
# gated on. Every goal is hint-free (no few-shot, no suggested plan).
#   read   — the smoke driver's exported PLAIN_GOAL, verbatim: the exact
#            --plain protocol test, so read-family rows stay comparable with
#            the 2/2 smoke benchmark (single source of truth: smoke.PLAIN_GOAL).
#   memory — retrieval, not file reading: the answer lives in stored notes,
#            reachable only through memory.search (FTS). run_episode seeds the
#            fresh per-episode DB with the notes, exactly like production
#            memory. A model can pass "read" and fail "memory"; the gate
#            counts that as a fail.
GOAL_FAMILIES: dict[str, dict[str, str]] = {
    "read": {
        "goal": smoke.PLAIN_GOAL,
        "describe": "file.read",
    },
    "memory": {
        "goal": ("Search stored memory for the deployment port of the thoth "
                 "web app, then state the port in one sentence."),
        "describe": "memory.search",
    },
}
# Table/gate order: floor-first, insertion order of GOAL_FAMILIES.
FAMILY_ORDER = tuple(GOAL_FAMILIES)

# Seed notes for the memory family (written into the episode's fresh DB
# before the run; the answer note is FTS-reachable via the goal's own words).
MEMORY_SEED_NOTES = (
    "The thoth web app deployment listens on port 8031.",
    "Deployment dry-run logs go to the ops channel, not to a file.",
)

# Park reasons that are the runner working as designed (ADR-003 bounds +
# journal 2026-W39 diagnostics + the finish floor). Anything else on a parked
# run is a crash.
CLEAN_PARK_REASONS = (
    "repeat-breaker", "max turns", "no provider", "tool-call budget",
    "unknown tool", "invalid tool args", "verify failed",
    "finish floor",
)


# ---------------------------------------------------------------------------
# variance-stable reporting (journal 2026-W39)
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval on a binomial proportion (95% by default).

    Chosen over the naive normal approximation because it stays honest at the
    edges — 0/5 and 5/5 get real intervals instead of [0, 0] / [1, 1] — and
    over Laplace smoothing because it never invents successes. Returns
    (0.0, 0.0) for n == 0."""
    if n <= 0:
        return 0.0, 0.0
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    spread = (z / denom) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, center - spread), min(1.0, center + spread)


# ---------------------------------------------------------------------------
# server swap
# ---------------------------------------------------------------------------

def _resolve_gguf(file_name: str) -> Path:
    """Catalog GGUFs live in the smoke dir; absolute paths pass through."""
    p = Path(file_name)
    return p if p.is_absolute() else SMOKE_DIR / p


MODELS = [dict(row, gguf=str(_resolve_gguf(row["file"]))) for row in MODELS]


# ---------------------------------------------------------------------------
# ADR-006 gate: self-serve candidate auditions (--add)
# ---------------------------------------------------------------------------

def _candidate_entry(key: str, gguf_arg: str, params: str) -> dict[str, str]:
    """Resolve a --add candidate into a matrix entry (auditioned across every
    goal family by main) without touching the
    committed catalog: the GGUF may live anywhere (absolute path), collisions
    with existing keys and with the canonical qwen2.5-3b tag are refused."""
    if any(m["key"] == key for m in MODELS):
        print(f"refusing duplicate key '{key}': already in the matrix catalog")
        raise SystemExit(2)
    if key == "qwen2.5-3b":
        # The recorded decision's tag; re-auditioning under the same name would
        # blur the evidence trail. Use a distinct key (e.g. qwen3b-recheck).
        print("refusing key 'qwen2.5-3b': reserved by the V3 decision record")
        raise SystemExit(2)
    path = Path(gguf_arg)
    if not path.is_file():
        print(f"GGUF not found: {path}")
        raise SystemExit(2)
    return {"key": key, "file": path.name, "params": params,
            "gguf": str(path.resolve())}


def gate_row(rows: list[dict]) -> dict:
    """Fold per-family matrix rows into one flat gate record: metric values
    keyed by family (`json_validity:read`), the family list carried on
    `families` (in FAMILY_ORDER), and n = the smallest per-family n (the
    gate needs every family, so its n is the weakest link). Raises
    ValueError on duplicate family rows — one row per family, ever."""
    families: dict[str, dict] = {}
    for row in rows:
        fam = str(row.get("family", "read"))
        if fam in families:
            raise ValueError(f"duplicate family row for the gate: {fam}")
        families[fam] = row
    flat: dict = {
        "model": rows[0]["model"] if rows else "?",
        "n": min((int(r.get("n", 0)) for r in rows), default=0),
        "families": [f for f in FAMILY_ORDER if f in families],
    }
    for fam, row in families.items():
        for metric in ("json_validity", "tool_turn_rate"):
            flat[f"{metric}:{fam}"] = float(row[metric])
    return flat


def gate_verdict(row: dict) -> str:
    """The ADR-006 section 4 admissibility rule, as code (amended 2026-09-30
    for goal families): a planner model is admissible only when
    json% = tool% = 100% WITHIN EVERY goal family — a model that reads but
    cannot retrieve is not routable. `row` is a gate_row() record; a verdict
    needs every family present and n >= 1 (else 'incomplete'). Raises
    KeyError on a malformed record — the gate fails loudly, not softly."""
    if int(row.get("n", 0)) < 1 or set(row.get("families") or ()) != set(FAMILY_ORDER):
        return "incomplete"
    for fam in FAMILY_ORDER:
        if row[f"json_validity:{fam}"] != 1.0 or row[f"tool_turn_rate:{fam}"] != 1.0:
            return "FAIL"
    return "PASS"


def _swap_model(server: Path, gguf: Path, port: int = PORT) -> None:
    """Restart llama-server pinned to exactly one GGUF; wait until /v1/models
    reports a loaded model. Raises on missing artifacts or a failed swap."""
    if not server.is_file():
        raise FileNotFoundError(f"llama-server not found: {server}")
    if not gguf.is_file():
        raise FileNotFoundError(f"model GGUF not found: {gguf}")
    try:
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                       capture_output=True, timeout=15)
    except OSError as exc:
        print(f"   swap: taskkill failed ({exc}); continuing")
    time.sleep(1.0)
    subprocess.Popen(
        [str(server), "-m", str(gguf), "--port", str(port), "--host", "127.0.0.1",
         "-c", "2048"],
        cwd=str(server.parent), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x00000008)  # DETACHED_PROCESS (Windows; session pattern)
    deadline = time.time() + 90.0
    while time.time() < deadline:
        time.sleep(1.0)
        try:
            with _urlrequest.urlopen(  # noqa: S310 (loopback only)
                    f"http://127.0.0.1:{port}/v1/models", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("data"):
                return
        except (URLError, OSError, ValueError):
            pass  # server not accepting yet
    raise RuntimeError(f"llama-server did not serve {gguf.name} within 90s")


# ---------------------------------------------------------------------------
# one episode: the plain protocol harness, scored
# ---------------------------------------------------------------------------

def run_episode(model_key: str, family: str = "read") -> dict:
    """One full runner episode against the live local model, in one goal
    family (GOAL_FAMILIES); returns its terminal state + the raw counters
    the matrix scores. Raises ValueError on an unknown family."""
    if family not in GOAL_FAMILIES:
        raise ValueError(
            f"unknown goal family {family!r}; known: {', '.join(GOAL_FAMILIES)}")
    tmp = Path(tempfile.gettempdir()) / "thoth-matrix-run.db"
    if tmp.exists():
        tmp.unlink()
    conn = db.connect(tmp)
    try:
        ok, why = smoke._probe_llama(conn)
        if not ok:
            raise RuntimeError(f"llama-server probe failed: {why}")
        smoke._FLAGS["fewshot"] = False  # matrix rows are always hint-free
        smoke._FLAGS["temperature"] = DEFAULT_TEMPERATURE  # pinned (see header)

        from thoth import providers, runner, tools
        smoke._CLIENTS["ollama-local"] = smoke._shim_attempt
        avail = providers.AvailabilityCache()
        avail.mark("ollama-local", True, why)

        # The family's hint-free goal. Family "read" IS the smoke driver's
        # exported PLAIN_GOAL (the --plain protocol test), so read-family
        # rows stay comparable with the 2/2 smoke benchmark; "memory" composes
        # on the same harness with a different level-0 tool.
        goal = GOAL_FAMILIES[family]["goal"]
        if family == "memory":
            # Production memory: notes exist before the run; the answer is
            # reachable only through memory.search (FTS over these rows).
            for body in MEMORY_SEED_NOTES:
                notes.add(conn, body, kind="fact", project="thoth")
        sid = runner.start_run(conn, "thoth", goal, max_turns=6,
                               tool_calls_budget=6)
        registry = tools.default_registry()
        planner = smoke.ModelPlanner(conn, availability=avail,
                                     task_class="plan", tool_registry=registry)
        result = runner.execute_run(conn, sid, planner, registry, max_turns=6,
                                    tool_calls_budget=6)

        # Planner-answer counters (finish-check probes excluded: they are a
        # different question with a different system prompt). attempts counts
        # ANSWERS (provider.outcome ok/error), not provider.attempt rounds:
        # a bounded retry consumes two answers in one round (journal 2026-W39).
        attempts = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind='provider.outcome' "
            "AND json_extract(payload_json, '$.outcome') IN ('ok','error') "
            "AND json_extract(payload_json, '$.reason') NOT LIKE 'finish check%'"
        ).fetchone()["n"]
        parsed = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind='provider.outcome' "
            "AND json_extract(payload_json, '$.outcome') = 'ok' "
            "AND json_extract(payload_json, '$.reason') != 'finish check'"
        ).fetchone()["n"]
        verified_turns = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind='run.turn.completed' "
            "AND json_extract(payload_json, '$.verify.ok') = 1"
        ).fetchone()["n"]
        probes = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind='provider.attempt' "
            "AND json_extract(payload_json, '$.finish_check') = 1"
        ).fetchone()["n"]
        return {
            "model": model_key,
            "family": family,
            "status": result.status,
            "reason": result.reason,
            "planner_attempts": attempts,
            "planner_parsed": parsed,
            "verified_turns": verified_turns,
            "finish_probes": probes,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# offline scoring core (unit-tested with zero server)
# ---------------------------------------------------------------------------

def episode_scores(ep: dict) -> dict[str, float]:
    """The four scores for ONE episode dict (as returned by run_episode).

    json_valid is the episode's raw parsed/attempts ratio — for the per-model
    rate, score_model pools attempts across episodes first (journal 2026-W39):
    an episode with 1 attempt is not worth 1/2 of an episode with 12.
    """
    clean = ep["status"] == "done" or (
        ep["status"] == "parked"
        and any(str(ep["reason"]).startswith(r) for r in CLEAN_PARK_REASONS))
    attempts = int(ep.get("planner_attempts", 0))
    parsed = int(ep.get("planner_parsed", 0))
    return {
        "json_valid": (parsed / attempts) if attempts > 0 else 0.0,
        "tool_turn": 1.0 if int(ep.get("verified_turns", 0)) >= 1 else 0.0,
        "clean_park": 1.0 if clean else 0.0,
        "self_finish": 1.0 if ep["status"] == "done" else 0.0,
    }


def score_model(episodes: list[dict]) -> dict:
    """Aggregate one model's episodes into its matrix row.

    Rates are point estimates; each row also carries a Wilson 95% interval per
    metric (`*_lo` / `*_hi`) so n=5 rows state their own uncertainty. Every
    interval is over the SAME trials as its point estimate: json_validity
    pools raw answers (attempts are the trials), the other three count
    episodes — brackets always bracket their own point.
    """
    if not episodes:
        return {"model": "?", "family": "read", "n": 0, "json_validity": 0.0,
                "json_validity_lo": 0.0, "json_validity_hi": 0.0,
                "tool_turn_rate": 0.0, "tool_turn_rate_lo": 0.0,
                "tool_turn_rate_hi": 0.0, "park_cleanliness": 0.0,
                "park_cleanliness_lo": 0.0, "park_cleanliness_hi": 0.0,
                "self_finish": 0.0, "self_finish_lo": 0.0,
                "self_finish_hi": 0.0, "reason": "no episodes"}
    fams_seen = {str(ep.get("family", "read")) for ep in episodes}
    if len(fams_seen) > 1:
        raise ValueError(
            "score_model mixes goal families: " + ", ".join(sorted(fams_seen))
            + " - score each family separately (the gate is per-family)")
    family = fams_seen.pop()
    per = [episode_scores(ep) for ep in episodes]
    n = len(episodes)
    attempts = sum(int(ep.get("planner_attempts", 0)) for ep in episodes)
    parsed = sum(int(ep.get("planner_parsed", 0)) for ep in episodes)
    json_rate = (parsed / attempts) if attempts > 0 else 0.0

    def _interval(rate_key: str, successes: int) -> tuple[float, float]:
        return wilson(successes, n)

    tool_hits = sum(1 for p in per if p["tool_turn"] == 1.0)
    clean_hits = sum(1 for p in per if p["clean_park"] == 1.0)
    finish_hits = sum(1 for p in per if p["self_finish"] == 1.0)
    row: dict = {
        "model": episodes[0]["model"],
        "family": family,
        "n": n,
        "json_validity": json_rate,
        "tool_turn_rate": tool_hits / n,
        "park_cleanliness": clean_hits / n,
        "self_finish": finish_hits / n,
        "reason": str(episodes[-1].get("reason", ""))[:80],
    }
    # Each interval over the same trials as its point estimate (journal
    # 2026-W39): pooled answers for json_validity, episodes for the rest.
    intervals = {
        "json_validity": wilson(parsed, attempts),
        "tool_turn_rate": wilson(tool_hits, n),
        "park_cleanliness": wilson(clean_hits, n),
        "self_finish": wilson(finish_hits, n),
    }
    for key, (lo, hi) in intervals.items():
        row[f"{key}_lo"] = lo
        row[f"{key}_hi"] = hi
    return row


def render_table(rows: list[dict]) -> str:
    """Fixed-width table; one line per model, floor-first, with Wilson 95%
    intervals on every rate (journal 2026-W39: n=5 rows state their error)."""
    header = (f"| {'model':<14.14} | {'family':<6.6} | {'n':>2} | {'json%':>14} | "
              f"{'tool%':>14} | {'clean%':>14} | {'finish%':>14} |")
    lines = [header, "|" + "-" * (len(header) - 2) + "|"]

    def _cell(row: dict, key: str) -> str:
        pct = f"{100 * float(row[key]):.0f}%"
        lo, hi = float(row.get(f"{key}_lo", 0.0)), float(row.get(f"{key}_hi", 0.0))
        return f"{pct:>4} [{100 * lo:.0f},{100 * hi:.0f}]".rjust(14)

    for row in rows:
        lines.append(
            f"| {str(row['model']):<14.14} | {str(row.get('family', 'read')):<6.6} "
            f"| {int(row['n']):>2} "
            f"| {_cell(row, 'json_validity')} "
            f"| {_cell(row, 'tool_turn_rate')} "
            f"| {_cell(row, 'park_cleanliness')} "
            f"| {_cell(row, 'self_finish')} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def _preflight_drift(server: Path) -> None:
    """ADR-006 Q3 (resolved 2026-09-30): warn loudly when the catalog GGUFs no
    longer match the committed manifest — a re-quantized GGUF invalidates the
    decision record's rows as evidence. Warn-and-proceed; refusing is the
    operator's explicit call (python -m evals.model_drift --check, exit 3).
    Runs the FAST check (sizes + head fingerprints, seconds — no full-file
    hashing on the episode path); the full sha256 remains the last word for
    the operator's explicit --check before bytes enter a decision record."""
    try:
        manifest = drift.load_manifest(drift.DEFAULT_MANIFEST)
        records = drift.compare(manifest, MODELS, fast=True)
    except FileNotFoundError:
        print("drift preflight: no manifest yet "
              "(python -m evals.model_drift --build hashes the scored bytes)")
        return
    except (OSError, ValueError) as exc:
        print(f"drift preflight: unavailable ({exc})")
        return
    if records:
        print("DRIFT WARNING: catalog GGUFs no longer match "
              f"{drift.DEFAULT_MANIFEST.name}:")
        for d in records:
            print(f"   {d['model']:<14} {d['file']:<14} {d['kind']:<12} {d['detail']}")
        print("   rows scored on the old bytes are no longer evidence; re-run "
              "the matrix, rebuild the manifest (--build), amend the record.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Thoth local-model protocol matrix")
    ap.add_argument("--models", nargs="*", default=None,
                    help="subset of model keys (default: all four)")
    ap.add_argument("--episodes", type=int, default=EPISODES_PER_MODEL,
                    help=f"episodes per model PER FAMILY (default "
                         f"{EPISODES_PER_MODEL})")
    ap.add_argument("--family", nargs="*", default=None, metavar="FAMILY",
                    help="restrict to goal families (default: all of "
                         f"{', '.join(FAMILY_ORDER)}; a gate verdict needs all)")
    ap.add_argument("--json", action="store_true",
                    help="print JSON rows (incl. per-episode records)")
    ap.add_argument("--add", nargs=3, metavar=("KEY", "GGUF_PATH", "PARAMS"),
                    default=None,
                    help="audition a new candidate GGUF through the ADR-006 "
                         "gate without code edits (ephemeral row; persist via "
                         "the matrix catalog + decision record)")
    args = ap.parse_args()

    entries = list(MODELS)
    if args.add:
        key, gguf_arg, params = args.add
        entries = entries + [_candidate_entry(key, gguf_arg, params)]
        args.models = [key]  # an audition runs alone

    keys = [m["key"] for m in entries]
    wanted = args.models or keys
    unknown = [k for k in wanted if k not in keys]
    if unknown:
        print(f"unknown model key(s): {', '.join(unknown)}; known: {', '.join(keys)}")
        return 2
    if args.episodes < 1:
        print("--episodes must be >= 1")
        return 2
    fams = args.family or list(FAMILY_ORDER)
    unknown_fams = [f for f in fams if f not in GOAL_FAMILIES]
    if unknown_fams:
        print(f"unknown goal family(s): {', '.join(unknown_fams)}; "
              f"known: {', '.join(FAMILY_ORDER)}")
        return 2
    fams = [f for f in FAMILY_ORDER if f in fams]  # stable family order
    server = Path(os.environ.get("LLAMA_SERVER", str(DEFAULT_SERVER)))
    _preflight_drift(server)  # ADR-006 Q3: drift never silent

    rows: list[dict] = []
    all_episodes: list[dict] = []
    incomplete: list[str] = []
    for entry in entries:
        if entry["key"] not in wanted:
            continue
        print(f"== {entry['key']} ({entry['file']}, {entry['params']}) ==")
        try:
            _swap_model(server, Path(entry["gguf"]))
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"   SKIP: {exc}")
            incomplete.append(entry["key"])
            continue
        for fam in fams:  # one server load, every goal family
            print(f"   -- family: {fam} ({GOAL_FAMILIES[fam]['describe']}) --")
            episodes: list[dict] = []
            for i in range(args.episodes):
                try:
                    ep = run_episode(entry["key"], fam)
                except RuntimeError as exc:
                    ep = {"model": entry["key"], "family": fam,
                          "status": "failed", "reason": str(exc)[:120],
                          "planner_attempts": 0, "planner_parsed": 0,
                          "verified_turns": 0, "finish_probes": 0}
                episodes.append(ep)
                all_episodes.append(ep)
                print(f"   episode {i + 1}: {ep['status']} ({ep.get('reason', '')}) "
                      f"attempts={ep.get('planner_attempts', 0)} "
                      f"parsed={ep.get('planner_parsed', 0)} "
                      f"turns={ep.get('verified_turns', 0)}")
            if len(episodes) < args.episodes:  # defensive; loop always fills
                incomplete.append(entry["key"])
            rows.append(score_model(episodes))

    print()
    if args.json:
        print(json.dumps({
            "temperature": DEFAULT_TEMPERATURE,
            "episodes_per_model_per_family": args.episodes,
            "rows": rows,
            "episodes": all_episodes,
        }, indent=2))
    else:
        print(render_table(rows))
        print(f"\n(n={args.episodes} per model per family, temperature pinned "
              f"at {DEFAULT_TEMPERATURE}; brackets are Wilson 95% intervals)")
    if args.add and rows:
        verdict = gate_verdict(gate_row(rows))
        print(f"\nADR-006 gate verdict for {rows[-1]['model']}: {verdict}")
        if verdict == "incomplete":
            print("incomplete: a verdict needs every goal family "
                  f"({', '.join(FAMILY_ORDER)}); rerun without --family.")
        print("PASS: update the ROADMAP decision record, then the DEFAULT_MODEL "
              "pin (re-decision procedure in ADR-006 section 5)."
              if verdict == "PASS" else
              "FAIL: not routable; the matrix row is the evidence.")
    # A skipped swap means the row is missing — say so in the exit code instead
    # of silently printing a partial matrix.
    return 1 if incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
