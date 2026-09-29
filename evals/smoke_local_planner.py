"""End-to-end smoke: one run planned by a REAL local model, zero cloud, zero $.

Prereqs (journal 2026-W39): llama-server (llama.cpp b11223) serving a SmolLM2
GGUF on 127.0.0.1:11434. llama-server speaks OpenAI wire format, not Ollama's —
so the driver registers a shim client (same signature, same guard call, /v1/
endpoints). Production Ollama needs NO shim; the shim exists so the full ladder
is exercisable on any box. Every byte still crosses guard.check_egress.

  llama-server -m model.gguf --port 11434

Usage:
  python -m evals.smoke_local_planner          # mechanics demo (hint-fed goal)
  python -m evals.smoke_local_planner --plain  # hint-free protocol-adherence test:
                                               # no few-shot, no suggested plan;
                                               # pass = status done AND >=1 verified tool turn
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib import request as _urlrequest
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from thoth import db, events, ollama, providers, runner, tools  # noqa: E402
from thoth.planner_model import ModelPlanner, _CLIENTS  # private seam, journaled

WS = Path(__file__).resolve().parents[2]  # structural-rcc-suite-v4/
TARGET = "local:llama-server@http://127.0.0.1:11434"
PLAIN_GOAL = ("Read the file README.md in the workspace, then state in one "
              "sentence what the project is.")
_MODEL_NAME = "smollm2-135m"  # replaced by whatever /v1/models reports
_FLAGS = {"fewshot": True}    # --plain turns the demo aids off


def _guard_or_raise(conn) -> None:
    g = ollama.Guard(conn, actor="ollama-client")
    decision = g.check_egress(ollama.KIND_PROVIDER, TARGET, data_class=ollama.PUBLIC)
    if not decision.allowed:
        raise ollama.OllamaUnavailable(f"guard denied egress: {decision.reason}")


FEWSHOT = (
    "EXAMPLE:\n"
    "CONTEXT:\nGoal: show the notes file.\n"
    "TOOL LIST:\n- file.read: Read a text file inside the workspace.\n"
    '- shell.read: Run an allowlisted read-only shell command.\n\n'
    'Correct plan: {"tool": "file.read", "args": {"path": "NOTES.md"}, '
    '"summary": "read NOTES.md", "next_intent": "show it", "done": false}\n\n'
    "NOW THE REAL TASK.\n\n"
)


def _shim_attempt(conn, prompt: str, system: str | None = None) -> str:
    """Same contract as ollama.attempt (plus the optional system override the
    finish-confirmation probe uses): guard first, then one local completion
    over llama-server's OpenAI-compatible /v1/chat/completions."""
    _guard_or_raise(conn)
    if system is None:
        system = ollama.PLAN_SYSTEM
    if _FLAGS["fewshot"] and system is ollama.PLAN_SYSTEM:
        prompt = FEWSHOT + prompt
    payload = {
        "model": _MODEL_NAME,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 150,
    }
    req = _urlrequest.Request(
        "http://127.0.0.1:11434/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _urlrequest.urlopen(req, timeout=180) as resp:  # noqa: S310 (loopback only)
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError) as exc:
        raise ollama.OllamaUnavailable(
            f"local server failed: {type(exc).__name__}: {exc}") from exc
    choices = data.get("choices") or []
    content = (choices[0].get("message") or {}).get("content", "") if choices else ""
    if not str(content).strip():
        raise ollama.OllamaUnavailable("local server returned an empty answer")
    return str(content)


def _probe_llama(conn) -> tuple[bool, str]:
    """Guard-gated availability probe against llama-server's /v1/models."""
    try:
        _guard_or_raise(conn)
    except ollama.OllamaUnavailable as exc:
        return False, str(exc)
    try:
        with _urlrequest.urlopen("http://127.0.0.1:11434/v1/models", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = [m.get("id", "") or m.get("name", "") for m in data.get("data", [])]
        names = [n for n in names if n]
        if not names:
            return False, "server reachable but no model loaded"
        global _MODEL_NAME
        _MODEL_NAME = names[0]
        return True, f"models: {', '.join(names[:3])}"
    except (URLError, OSError, json.JSONDecodeError) as exc:
        return False, f"llama-server probe failed: {type(exc).__name__}: {exc}"


def _seed(conn) -> None:
    events.emit(conn, "session.started", {"id": "s-smoke", "project": "thoth"})
    events.emit(conn, "session.wrapup", {"id": "s-smoke", "summary": "smoke seed"})
    events.emit(conn, "session.ended", {"id": "s-smoke", "project": "thoth"})


def main() -> int:
    ap = argparse.ArgumentParser(description="Thoth end-to-end local-planner smoke")
    ap.add_argument("--plain", action="store_true",
                    help="hint-free protocol test: no few-shot, no suggested plan; "
                         "pass = done AND >=1 verified tool turn")
    plain = ap.parse_args().plain
    _FLAGS["fewshot"] = not plain
    tmp = Path.home() / "AppData/Local/Temp/thoth-smoke-run.db"
    if tmp.exists():
        tmp.unlink()
    conn = db.connect(tmp)

    print("== 1. guard-gated probe ==")
    ok, why = _probe_llama(conn)
    print(f"   probe: {ok} ({why})")
    if not ok:
        print("   STOP: start llama-server first (see module docstring).")
        conn.close()
        return 1
    avail = providers.AvailabilityCache()
    avail.mark("ollama-local", True, why)

    print("== 2. register shim client (OpenAI-wire shim, guard-gated) ==")
    _CLIENTS["ollama-local"] = _shim_attempt
    print("   _CLIENTS['ollama-local'] -> llama-server /v1 wire shim")

    print("== 3. seed + start run ==")
    _seed(conn)
    if plain:
        # Hint-free protocol test (journal 2026-W39): no few-shot, no suggested
        # plan. Pure action goal — the pass bar is self-driven goal->action->done;
        # answer synthesis (counting lines) needs a tool the V0 registry lacks.
        goal = PLAIN_GOAL
    else:
        # Mechanics demo (journaled): tiny models parrot rather than adapt, so
        # the goal carries an explicit suggested plan.
        goal = ('Read the file README.md. Suggested plan: {"tool": "file.read", '
                '"args": {"path": "README.md"}, "summary": "read README.md", '
                '"next_intent": "report and finish", "done": false}')
    sid = runner.start_run(conn, "thoth", goal, max_turns=6, tool_calls_budget=6)
    print(f"   run {sid} created (project 'thoth')")

    print("== 4. execute: full ladder, real model plans each turn ==")
    registry = tools.default_registry()
    planner = ModelPlanner(conn, availability=avail, task_class="plan",
                           tool_registry=registry)
    result = runner.execute_run(conn, sid, planner, registry, max_turns=6,
                                tool_calls_budget=6)
    print(f"   result: {result.status} ({result.reason})")

    # Pass criterion (journal 2026-W39): >=1 verified tool turn means the full
    # ladder worked on a real model. A clean self-driven finish needs a bigger
    # model (protocol adherence), so run.status alone is NOT the gate.
    turns = conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE kind='run.turn.completed' "
        "AND json_extract(payload_json, '$.verify.ok') = 1").fetchone()["n"]
    print(f"   verified tool turns: {turns}")
    if plain:
        print(f"   plain gate: done={result.status == 'done'} turns={turns}")

    print("== 5. event trail ==")
    for row in conn.execute(
            "SELECT kind, payload_json FROM events ORDER BY rowid"):
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        brief = ""
        if row["kind"] == "run.turn.completed":
            brief = (f"tool={payload.get('tool')} "
                     f"ok={payload.get('verify', {}).get('ok')}")
        elif row["kind"] == "provider.outcome":
            brief = f"{payload.get('provider')} -> {payload.get('outcome')}"
        elif row["kind"] == "guard.decision":
            brief = f"{payload.get('kind')} {payload.get('verdict')} ({payload.get('rule')})"
        print(f"   {row['kind']:<26} {brief}")

    conn.close()
    if plain:
        return 0 if (result.status == "done" and turns >= 1) else 1
    return 0 if turns >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
