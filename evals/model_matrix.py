"""Model matrix eval — one row per local model, four protocol scores (ADR-004).

Scores each local GGUF on *protocol adherence*, not task quality — the four
questions Team-B will ask about every candidate brain:
  json_validity      fraction of planner answers that parsed into a plan
  tool_turn_rate     fraction of episodes with >=1 verified tool turn
  park_cleanliness   fraction of episodes ending in an expected terminal state
                     (self-done, or a diagnostic park — never a crash)
  self_finish        fraction of episodes the model ended itself (done=true,
                     in-loop or via the tool-free finish-confirmation probe)

Every byte still crosses guard.check_egress: the matrix reuses the smoke
driver's shim and plain harness unchanged (hint-free goal, no few-shot).

Prereqs: the smoke session's environment (journal 2026-W39) — llama-server
(llama.cpp b11223) at %TEMP%/thoth-smoke/llama/llama-server.exe (override with
LLAMA_SERVER) and the four sha-verified GGUFs in %TEMP%/thoth-smoke/.

Usage:
  python -m evals.model_matrix                    # all four models
  python -m evals.model_matrix --models qwen2.5-3b
  python -m evals.model_matrix --json             # machine rows + episodes
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib import request as _urlrequest
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evals import smoke_local_planner as smoke  # noqa: E402
from thoth import db  # noqa: E402

SMOKE_DIR = Path(tempfile.gettempdir()) / "thoth-smoke"
DEFAULT_SERVER = SMOKE_DIR / "llama" / "llama-server.exe"
PORT = 11434

# One row per model: the same four GGUFs the smoke session downloaded and
# sha-verified (journal 2026-W39). Floor-first, capability ascending.
MODELS: list[dict[str, str]] = [
    {"key": "smollm2-135m", "file": "model.gguf", "params": "135M"},
    {"key": "smollm2-360m", "file": "model360.gguf", "params": "360M"},
    {"key": "qwen2.5-0.5b", "file": "qwen05b.gguf", "params": "0.5B"},
    {"key": "qwen2.5-3b", "file": "qwen3b.gguf", "params": "3B"},
]

EPISODES_PER_MODEL = 2

# Park reasons that are the runner working as designed (ADR-003 bounds +
# journal 2026-W39 diagnostics). Anything else on a parked run is a crash.
CLEAN_PARK_REASONS = (
    "repeat-breaker", "max turns", "no provider", "tool-call budget",
    "unknown tool", "invalid tool args", "verify failed",
)


# ---------------------------------------------------------------------------
# server swap
# ---------------------------------------------------------------------------

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

def run_episode(model_key: str) -> dict:
    """One full runner episode against the live local model; returns its
    terminal state + the raw counters the matrix scores."""
    tmp = Path(tempfile.gettempdir()) / "thoth-matrix-run.db"
    if tmp.exists():
        tmp.unlink()
    conn = db.connect(tmp)
    try:
        ok, why = smoke._probe_llama(conn)
        if not ok:
            raise RuntimeError(f"llama-server probe failed: {why}")
        smoke._FLAGS["fewshot"] = False  # matrix rows are always hint-free

        from thoth import providers, runner, tools
        smoke._CLIENTS["ollama-local"] = smoke._shim_attempt
        avail = providers.AvailabilityCache()
        avail.mark("ollama-local", True, why)

        # The exact hint-free protocol test the smoke driver runs (--plain),
        # so matrix rows are comparable with the 2/2 smoke benchmark.
        goal = smoke.PLAIN_GOAL
        sid = runner.start_run(conn, "thoth", goal, max_turns=6,
                               tool_calls_budget=6)
        registry = tools.default_registry()
        planner = smoke.ModelPlanner(conn, availability=avail,
                                     task_class="plan", tool_registry=registry)
        result = runner.execute_run(conn, sid, planner, registry, max_turns=6,
                                    tool_calls_budget=6)

        # Planner-answer counters (finish-check probes excluded: they are a
        # different question with a different system prompt).
        attempts = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE kind='provider.attempt' "
            "AND json_extract(payload_json, '$.finish_check') IS NULL"
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
    """The four binary scores for ONE episode dict (as returned by run_episode)."""
    clean = ep["status"] == "done" or (
        ep["status"] == "parked"
        and any(str(ep["reason"]).startswith(r) for r in CLEAN_PARK_REASONS))
    attempts = max(1, int(ep.get("planner_attempts", 0)))
    return {
        "json_valid": float(ep.get("planner_parsed", 0)) / attempts,
        "tool_turn": 1.0 if int(ep.get("verified_turns", 0)) >= 1 else 0.0,
        "clean_park": 1.0 if clean else 0.0,
        "self_finish": 1.0 if ep["status"] == "done" else 0.0,
    }


def score_model(episodes: list[dict]) -> dict:
    """Aggregate one model's episodes into its matrix row (rates, plus n and
    the last episode's terminal reason for the human-readable table)."""
    n = len(episodes)
    if n == 0:
        return {"model": episodes[0]["model"] if episodes else "?", "n": 0,
                "json_validity": 0.0, "tool_turn_rate": 0.0,
                "park_cleanliness": 0.0, "self_finish": 0.0, "reason": "no episodes"}
    per = [episode_scores(ep) for ep in episodes]
    return {
        "model": episodes[0]["model"],
        "n": n,
        "json_validity": sum(p["json_valid"] for p in per) / n,
        "tool_turn_rate": sum(p["tool_turn"] for p in per) / n,
        "park_cleanliness": sum(p["clean_park"] for p in per) / n,
        "self_finish": sum(p["self_finish"] for p in per) / n,
        "reason": str(episodes[-1].get("reason", ""))[:80],
    }


def render_table(rows: list[dict]) -> str:
    """Fixed-width table; one line per model, floor-first."""
    header = (f"| {'model':<13} | {'n':>2} | {'json%':>5} | {'tool%':>5} | "
              f"{'clean%':>6} | {'finish%':>7} |")
    lines = [header, "|" + "-" * (len(header) - 2) + "|"]
    for row in rows:
        lines.append(
            f"| {str(row['model']):<13} | {int(row['n']):>2} "
            f"| {100 * float(row['json_validity']):>4.0f}% "
            f"| {100 * float(row['tool_turn_rate']):>4.0f}% "
            f"| {100 * float(row['park_cleanliness']):>5.0f}% "
            f"| {100 * float(row['self_finish']):>6.0f}% |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Thoth local-model protocol matrix")
    ap.add_argument("--models", nargs="*", default=None,
                    help="subset of model keys (default: all four)")
    ap.add_argument("--episodes", type=int, default=EPISODES_PER_MODEL,
                    help=f"episodes per model (default {EPISODES_PER_MODEL})")
    ap.add_argument("--json", action="store_true",
                    help="print JSON rows (incl. per-episode records)")
    args = ap.parse_args()

    keys = [m["key"] for m in MODELS]
    wanted = args.models or keys
    unknown = [k for k in wanted if k not in keys]
    if unknown:
        print(f"unknown model key(s): {', '.join(unknown)}; known: {', '.join(keys)}")
        return 2
    server = Path(os.environ.get("LLAMA_SERVER", str(DEFAULT_SERVER)))

    rows: list[dict] = []
    all_episodes: list[dict] = []
    failures = 0
    for entry in MODELS:
        if entry["key"] not in wanted:
            continue
        print(f"== {entry['key']} ({entry['file']}, {entry['params']}) ==")
        try:
            _swap_model(server, SMOKE_DIR / entry["file"])
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"   SKIP: {exc}")
            rows.append({"model": entry["key"], "n": 0, "json_validity": 0.0,
                         "tool_turn_rate": 0.0, "park_cleanliness": 0.0,
                         "self_finish": 0.0, "reason": str(exc)[:80]})
            failures += 1
            continue
        episodes: list[dict] = []
        for i in range(args.episodes):
            try:
                ep = run_episode(entry["key"])
            except RuntimeError as exc:
                ep = {"model": entry["key"], "status": "failed",
                      "reason": str(exc)[:120], "planner_attempts": 0,
                      "planner_parsed": 0, "verified_turns": 0,
                      "finish_probes": 0}
            episodes.append(ep)
            all_episodes.append(ep)
            print(f"   episode {i + 1}: {ep['status']} ({ep.get('reason', '')}) "
                  f"attempts={ep.get('planner_attempts', 0)} "
                  f"parsed={ep.get('planner_parsed', 0)} "
                  f"turns={ep.get('verified_turns', 0)}")
        rows.append(score_model(episodes))

    print()
    if args.json:
        print(json.dumps({"rows": rows, "episodes": all_episodes}, indent=2))
    else:
        print(render_table(rows))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
