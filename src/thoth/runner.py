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
from .tools import ToolRegistry, ToolSpec, VerifyReport, default_summarize

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


class PlannerUnavailable(RuntimeError):
    """A planner cannot produce a plan — e.g. the provider ladder is empty or
    every attempt failed. The runner parks the run: this is the degradation
    ladder's terminal state (ADR-004 §2), never a fallback to a paid API."""


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

# Finish floor (journal 2026-W39, measured with qwen2.5-0.5b): a model can
# claim done without having acted — 100% finish rate, 0% verified tool turns.
# A finish is only honest over evidence, so by default the runner refuses a
# done claim until the run carries at least one verified tool turn. Scripted
# no-work callers (the V0.2 CLI planner) pass
# allow_finish_without_turns=True explicitly — the floor is an
# anti-hallucination guard, not a no-op policy.
FINISH_FLOOR_REASON = "finish floor: done claimed with zero verified turns"


def _verified_turns(conn: sqlite3.Connection, run_id: str) -> int:
    """Verified tool turns for THIS run across all episodes (resume-safe)."""
    import json
    n = 0
    for r in conn.execute(
            "SELECT payload_json FROM events WHERE kind = ?", (K_TURN_COMPLETED,)):
        payload = json.loads(r["payload_json"])
        if payload.get("run_id") == run_id and (payload.get("verify") or {}).get("ok"):
            n += 1
    return n


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
    allow_finish_without_turns: bool = False,
) -> RunResult:
    """Drive the loop until the planner finishes or a bound trips (ADR-003 §1)."""
    run = current_run(conn)  # bounds may come from the row; params are the ceiling
    project = run["project"] if run else None
    goal = (run["goal"] if run else "") or ""
    prior = history_of(conn, run_id)
    turns_used = len(prior)
    tool_calls_used = turns_used
    verify_failures = 0
    # Repeat-breaker state (journal 2026-W39), scoped to THIS episode: repetition
    # detection polices an in-run model loop; deliberately re-running an action
    # across a resume (a designed ADR-003 pattern) must not trip it. Cross-
    # invocation loops remain bounded per invocation.
    last_digest: str | None = None

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
        try:
            plan = planner.decide(context, history)
        except PlannerUnavailable as exc:
            return _park(conn, run_id, project, f"no provider: {exc}")
        if plan.done or plan.tool is None:
            summary = plan.summary or "planner finished"
            # --- finish floor (journal 2026-W39) ----------------------------
            # Refuse a done claim with zero verified turns: the model has not
            # acted, so it cannot honestly be finished (park diagnostically
            # instead). Counts verified turns across resumes, so continuing a
            # run that already acted can still finish. Scripted no-work
            # callers opt out explicitly (see FINISH_FLOOR_REASON).
            if not allow_finish_without_turns and _verified_turns(conn, run_id) < 1:
                emit(conn, "run.finish.refused", {
                    "run_id": run_id,
                    "claimed_summary": summary[:200],
                    "verified_turns": 0,
                })
                conn.commit()
                return _park(conn, run_id, project, FINISH_FLOOR_REASON)
            _finish(conn, run_id, summary)
            return RunResult(run_id, "done", summary, turns_used)

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

        # --- repeat-breaker (journal 2026-W39): a model can loop forever on a
        # successful-but-pointless action (observed live: identical file.read
        # digest four turns running). Repeating a VERIFIED-OK idempotent action
        # adds zero information — park diagnostically before executing it again.
        # Failing idempotent repeats stay under the 3-strikes verify regime:
        # retrying a flaky failure can still earn information.
        digest = _digest(clean_args)
        if digest == last_digest and spec.idempotent:
            # Finish-confirmation probe (journal 2026-W39): with an action menu
            # visible, small models re-act instead of finishing — so ask the
            # question tool-free, exactly once, before parking. Optional planner
            # capability; without it the diagnostic park stands.
            probe = getattr(planner, "finish_check", None)
            if probe is not None:
                done_plan = probe(goal, history)
                if done_plan is not None and done_plan.done:
                    summary = done_plan.summary or "finish check passed"
                    _finish(conn, run_id, summary)
                    return RunResult(run_id, "done", summary, turns_used)
            return _park(conn, run_id, project,
                         "repeat-breaker: identical idempotent action repeated")

        result = spec.run(**clean_args, _conn=conn)
        # _conn wiring (journal 2026-W39, latent bug found building the
        # summarizer layer): memory.search needs the connection the runner
        # already holds; without it every in-run memory.search failed verify.
        tool_calls_used += 1
        turns_used += 1

        # --- verify (code, not model) ----------------------------------------
        report: VerifyReport = spec.verify(result)
        if report.ok:
            verify_failures = 0
            last_digest = digest  # only verified-ok actions arm the breaker
        else:
            verify_failures += 1
            if verify_failures >= PARK_LIMIT:
                # Diagnosable from the event log alone (journal 2026-W39): the
                # park message carries WHAT was attempted, not just that it
                # failed — a row of identical guesses reads differently from
                # three distinct attempts.
                return _park(conn, run_id, project,
                             f"verify failed {PARK_LIMIT}x: {report.detail} "
                             f"(last action: {plan.tool} {clean_args})")

        # --- checkpoint -------------------------------------------------------
        # Output observation (journal 2026-W39): the planner self-terminates on
        # SEMANTIC result lines, never on raw payloads (measured). Tools own
        # their summaries (ToolSpec.summarize); default_summarize is the floor.
        snippet = (spec.summarize(result) if spec.summarize
                   else default_summarize(result))[:200]
        emit(conn, K_TURN_COMPLETED, {
            "run_id": run_id,
            "turn": turns_used,
            "tool": plan.tool,
            "tool_args_digest": _digest(clean_args),
            "output": snippet,
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
    allow_finish_without_turns: bool = False,
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
                       deadline=deadline,
                       allow_finish_without_turns=allow_finish_without_turns)
