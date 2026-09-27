"""The runner loop (ADR-003): load → plan → act → verify → checkpoint, per turn.

V0.2 skeleton: the planner interface exists with a deterministic NoopPlanner so the
whole loop is testable end-to-end with zero AI calls. A model-backed planner plugs
into the same Protocol; the provider hook is the single function where the V1
spend-guard lands (ADR-001/ADR-003 §5).
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from . import tasks as tasks_mod
from .events import emit, now_iso
from .tools import ToolRegistry, ToolSpec, VerifyReport

# ---------------------------------------------------------------------------
# event kinds
# ---------------------------------------------------------------------------

K_RUN_STARTED = "run.started"
K_TURN_COMPLETED = "run.turn.completed"
K_RUN_PARKED = "run.parked"


# ---------------------------------------------------------------------------
# planner protocol (plugs a model in later; NoopPlanner keeps V0.2 AI-free)
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    tool: str | None                    # None + done=True → finish the run
    args: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    next_intent: str = ""
    done: bool = False


class Planner(Protocol):
    def decide(self, context: str, history: list[dict[str, Any]]) -> Plan: ...


class NoopPlanner:
    """Deterministic V0.2 planner: execute a scripted tool queue, then finish.

    Scripts a single tool by name, or finishes immediately when empty. Exists so
    the loop, bounds, checkpointing, and resume are testable without any model.
    """

    def __init__(self, script: list[tuple[str, dict[str, Any]]] | None = None) -> None:
        self._script = list(script or [])

    def decide(self, context: str, history: list[dict[str, Any]]) -> Plan:
        if self._script:
            name, args = self._script.pop(0)
            return Plan(tool=name, args=args,
                        summary=f"planned {name}", next_intent="continue script")
        return Plan(tool=None, done=True, summary="script exhausted")


# ---------------------------------------------------------------------------
# context package (ADR-003 §2)
# ---------------------------------------------------------------------------

SECTION_BUDGETS: dict[str, int] = {
    "identity": 200,
    "project": 800,
    "task_state": 400,
    "memories": 1200,
    "turn_window": 800,
}


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _fit(entries: list[str], budget: int) -> tuple[list[str], int]:
    """Keep whole lines up to the token budget; returns (kept, tokens_used)."""
    kept: list[str] = []
    used = 0
    for entry in entries:
        cost = _approx_tokens(entry)
        if used + cost > budget:
            continue
        kept.append(entry)
        used += cost
    return kept, used


def build_context(
    conn: sqlite3.Connection,
    project: str | None,
    goal: str,
    run_id: str | None,
    history: list[dict[str, Any]],
) -> tuple[str, dict[str, int]]:
    """Fixed-order context package with per-section budgets. Code-built, never model-built."""
    sections: dict[str, list[str]] = {"identity": [], "project": [], "task_state": [],
                                      "memories": [], "turn_window": []}

    sections["identity"].append("You are Thoth, a personal AI operating layer. "
                                "Act only through declared tools; report honestly.")

    if project:
        sections["project"].append(f"active project: {project}")
        from . import session as session_mod
        wd = session_mod.workdir_of(conn, project)
        if wd:
            sections["project"].append(f"workdir: {wd}")

    task_state: list[str] = [f"goal: {goal}"]
    if project:
        for t in tasks_mod.list_open(conn, project)[:3]:
            task_state.append(f"open task [{t['status']}] {t['title']} (id {t['id']})")
    sections["task_state"] = task_state

    if project:
        try:
            rows = conn.execute(
                "SELECT n.body FROM notes_fts JOIN notes n ON n.rowid = notes_fts.rowid "
                "WHERE notes_fts MATCH ? AND n.superseded_by IS NULL "
                "AND (n.project = ? OR n.project IS NULL) ORDER BY rank LIMIT 5",
                (goal[:64] or "thoth", project),
            ).fetchall()
            sections["memories"] = [f"note: {r['body']}" for r in rows]
        except sqlite3.OperationalError:
            pass  # no FTS match / no table yet — memories stay empty

    for turn in history[-4:]:
        verify_ok = bool(turn.get("verify", {}).get("ok"))
        sections["turn_window"].append(
            f"turn {turn['turn']}: {turn.get('tool')} ok={verify_ok}"
            + (f" → {turn['next_intent']}" if turn.get("next_intent") else "")
        )

    sizes: dict[str, int] = {}
    lines: list[str] = []
    for name, entries in sections.items():
        kept, used = _fit(entries, SECTION_BUDGETS[name])
        sizes[name] = used
        lines.append(f"## {name}")
        lines.extend(kept if kept else ["(empty)"])
    return "\n".join(lines), sizes


# ---------------------------------------------------------------------------
# runs view helpers (materialized from run.* events, same discipline as sessions)
# ---------------------------------------------------------------------------

def start_run(conn: sqlite3.Connection, project: str | None, goal: str,
              max_turns: int = 25, tool_calls_budget: int = 20,
              deadline: str | None = None) -> str:
    run_id = uuid.uuid4().hex[:8]
    ts = now_iso()
    conn.execute(
        "INSERT INTO runs (id, project, goal, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'running', ?, ?)",
        (run_id, project, goal, ts, ts),
    )
    emit(conn, K_RUN_STARTED, {
        "run_id": run_id, "project": project, "goal": goal,
        "bounds": {"max_turns": max_turns, "tool_calls_budget": tool_calls_budget,
                   "deadline": deadline},
    })
    conn.commit()
    return run_id


def current_run(conn: sqlite3.Connection, project: str | None = None) -> dict[str, Any] | None:
    if project:
        row = conn.execute(
            "SELECT * FROM runs WHERE project = ? AND status = 'running' "
            "ORDER BY created_at DESC LIMIT 1", (project,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM runs WHERE status = 'running' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def last_parked(conn: sqlite3.Connection, project: str | None = None) -> dict[str, Any] | None:
    """Most recently parked run for a project, with its own park event payload.

    The park-event subquery is correlated to the run (not merely the latest park
    event overall) so per-project answers stay truthful when several projects
    have parked runs.
    """
    sql = ("SELECT r.*, "
           "(SELECT e.payload_json FROM events e WHERE e.kind = 'run.parked' "
           " AND json_extract(e.payload_json, '$.run_id') = r.id "
           " ORDER BY e.ts DESC LIMIT 1) AS park_payload "
           "FROM runs r WHERE r.status = 'parked'")
    params: list[Any] = []
    if project:
        sql += " AND r.project = ?"
        params.append(project)
    sql += " ORDER BY r.updated_at DESC LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    out = dict(row)
    if out.get("park_payload"):
        import json
        out["park_reason"] = json.loads(out.pop("park_payload")).get("reason")
    return out


def last_turn_event(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    import json
    rows = conn.execute(
        "SELECT payload_json FROM events WHERE kind = ? ORDER BY ts DESC",
        (K_TURN_COMPLETED,),
    ).fetchall()
    for r in rows:
        payload = json.loads(r["payload_json"])
        if payload.get("run_id") == run_id:
            return payload
    return None


def history_of(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    import json
    rows = conn.execute(
        "SELECT payload_json FROM events WHERE kind = ? ORDER BY ts",
        (K_TURN_COMPLETED,),
    ).fetchall()
    turns: list[dict[str, Any]] = []
    for r in rows:
        payload = json.loads(r["payload_json"])
        if payload.get("run_id") == run_id:
            turns.append(payload)
    return turns


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------

PARK_LIMIT = 3          # consecutive verify failures before parking
TOOL_TIMEOUT_NOTE = "see tools.py (15s subprocess cap)"


@dataclass
class RunResult:
    run_id: str
    status: str                  # done | parked | failed
    reason: str
    turns: int


def execute_run(
    conn: sqlite3.Connection,
    run_id: str,
    planner: Planner,
    registry: ToolRegistry,
    max_turns: int = 25,
    tool_calls_budget: int = 20,
    deadline: str | None = None,
) -> RunResult:
    """Drive the loop until the planner finishes or a bound trips (ADR-003 §1)."""
    run = current_run(conn)  # bounds may come from the row; params are the ceiling
    project = run["project"] if run else None
    goal = (run["goal"] if run else "") or ""
    turns_used = len(history_of(conn, run_id))
    tool_calls_used = turns_used
    verify_failures = 0

    while True:
        # --- bounds (enforced by the runner, never the model) ---------------
        if deadline and now_iso() > deadline:
            return _park(conn, run_id, project, "deadline exceeded")
        if turns_used >= max_turns:
            return _park(conn, run_id, project, "max turns reached")
        if tool_calls_used >= tool_calls_budget:
            return _park(conn, run_id, project, "tool-call budget exhausted")

        history = history_of(conn, run_id)
        context, sizes = build_context(conn, project, goal, run_id, history)

        # --- plan -----------------------------------------------------------
        plan = planner.decide(context, history)
        if plan.done or plan.tool is None:
            _finish(conn, run_id, plan.summary or "planner finished")
            return RunResult(run_id, "done", plan.summary or "planner finished", turns_used)

        # --- act (validate before invoke) ------------------------------------
        try:
            spec: ToolSpec = registry.get(plan.tool)
            clean_args = registry.validate(plan.tool, plan.args)
        except KeyError as exc:
            return _park(conn, run_id, project, f"unknown tool: {exc}")
        except ValueError as exc:
            emit(conn, "run.turn.failed", {"run_id": run_id, "error": str(exc)})
            conn.commit()
            return _park(conn, run_id, project, f"invalid tool args: {exc}")

        result = spec.run(**clean_args)
        tool_calls_used += 1
        turns_used += 1

        # --- verify (code, not model) ----------------------------------------
        report: VerifyReport = spec.verify(result)
        if report.ok:
            verify_failures = 0
        else:
            verify_failures += 1
            if verify_failures >= PARK_LIMIT:
                return _park(conn, run_id, project,
                             f"verify failed {PARK_LIMIT}x: {report.detail}")

        # --- checkpoint -------------------------------------------------------
        emit(conn, K_TURN_COMPLETED, {
            "run_id": run_id,
            "turn": turns_used,
            "tool": plan.tool,
            "tool_args_digest": _digest(clean_args),
            "verify": {"ok": report.ok, "detail": report.detail},
            "context_sections": sizes,
            "next_intent": plan.next_intent,
            "bounds": {"max_turns": max_turns, "tool_calls_budget": tool_calls_budget,
                       "deadline": deadline, "tool_calls_used": tool_calls_used},
        })
        conn.commit()


def _digest(args: dict[str, Any]) -> str:
    import hashlib
    import json
    blob = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def _finish(conn: sqlite3.Connection, run_id: str, reason: str) -> None:
    conn.execute(
        "UPDATE runs SET status = 'done', updated_at = ? WHERE id = ?",
        (now_iso(), run_id),
    )
    emit(conn, "run.completed", {"run_id": run_id, "reason": reason})
    conn.commit()


def _park(conn: sqlite3.Connection, run_id: str, project: str | None, reason: str) -> RunResult:
    conn.execute(
        "UPDATE runs SET status = 'parked', updated_at = ? WHERE id = ?",
        (now_iso(), run_id),
    )
    emit(conn, K_RUN_PARKED, {"run_id": run_id, "reason": reason})
    conn.commit()
    return RunResult(run_id, "parked", reason, 0)


def resume_run(
    conn: sqlite3.Connection,
    project: str | None,
    planner_factory: Callable[[str, list[dict[str, Any]]], Planner],
    registry: ToolRegistry,
    max_turns: int = 25,
    tool_calls_budget: int = 20,
) -> RunResult:
    """One resume path: find the parked run, rebuild from its own history, continue.

    bounds carry over: a crashed/parked run cannot escape its original budget by
    being resumed (ADR-003 §4).
    """
    parked = last_parked(conn, project)
    if parked is None:
        raise ValueError("no parked run to resume")
    run_id = parked["id"]

    # carry bounds from the last turn checkpoint (fallback: run.started event)
    last = last_turn_event(conn, run_id)
    if last and last.get("bounds"):
        max_turns = last["bounds"].get("max_turns", max_turns)
        tool_calls_budget = last["bounds"].get("tool_calls_budget", tool_calls_budget)
        deadline = last["bounds"].get("deadline")
    else:
        deadline = None

    conn.execute(
        "UPDATE runs SET status = 'running', updated_at = ? WHERE id = ?",
        (now_iso(), run_id),
    )
    conn.commit()

    history = history_of(conn, run_id)
    planner = planner_factory(parked.get("goal") or "", history)
    return execute_run(conn, run_id, planner, registry,
                       max_turns=max_turns, tool_calls_budget=tool_calls_budget,
                       deadline=deadline)
