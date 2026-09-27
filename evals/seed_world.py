"""Deterministic world-builder for the golden set.

Creates a small, fully-known Thoth database: two projects, closed + open sessions,
tasks with dependencies, notes (incl. superseded + FTS terms), and runs (done +
parked, with turn checkpoints). Timestamps are injected so ordering assertions are
stable. `FACTS` records the expected answers the golden set asserts against — the
seed is the ground truth, the harness only checks Thoth can *read* it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from thoth import db, events, notes, runner, session, tasks

T = [  # fixed UTC timestamps, seconds resolution
    "2026-09-20T08:00:00Z", "2026-09-20T10:30:00Z",   # rcc session 1
    "2026-09-21T09:00:00Z", "2026-09-21T11:15:00Z",   # data-eng session 1
    "2026-09-22T14:00:00Z", "2026-09-22T16:45:00Z",   # rcc session 2 (last rcc)
    "2026-09-23T07:30:00Z",                            # thoth project session (open)
]
TS = iter(T)


@dataclass
class Facts:
    db_path: Path
    rcc_task1: str = ""
    rcc_task2: str = ""
    eng_task1: str = ""
    eng_task2: str = ""
    done_run: str = ""
    parked_run: str = ""
    last_rcc_summary: str = "Migrated schema; indexes pending"
    parked_goal: str = "Audit the event log"
    parked_reason: str = "tool-call budget exhausted"
    note_counts: dict = field(default_factory=dict)


def seed(tmp: Path) -> Facts:
    facts = Facts(db_path=tmp / "golden.db")
    conn = db.connect(facts.db_path)

    # ---- sessions (clock-injected via direct rows + events) ------------------
    def _session(sid, project, start, stop=None):
        conn.execute(
            "INSERT INTO sessions (id, project, started_at, stopped_at) VALUES (?,?,?,?)",
            (sid, project, start, stop),
        )
        events.emit(conn, session.K_START, {"session": sid, "project": project})
        if stop:
            events.emit(conn, session.K_STOP, {"session": sid})

    _session("s_rcc1", "rcc-suite", T[0], T[1])
    _session("s_eng1", "data-eng", T[2], T[3])
    _session("s_rcc2", "rcc-suite", T[4], T[5])
    _session("s_thoth", "thoth", T[6], None)

    # the wrap-up summaries the golden set must recover
    events.emit(conn, session.K_STOP, {"session": "s_rcc2",
                                       "summary": facts.last_rcc_summary})
    events.emit(conn, session.K_STOP, {"session": "s_rcc1",
                                       "summary": "Bootstrapped migration folder"})

    session.set_workdir(conn, "rcc-suite", "/tmp/rcc-suite")
    session.set_workdir(conn, "data-eng", "/tmp/data-eng")

    # ---- tasks ----------------------------------------------------------------
    facts.rcc_task1 = tasks.add(conn, "Fix PG16 migration", project="rcc-suite", deadline="2026-09-25")
    facts.rcc_task2 = tasks.add(conn, "Add beam index", project="rcc-suite",
                                depends_on=facts.rcc_task1)
    facts.eng_task1 = tasks.add(conn, "Model airflow dags", project="data-eng")
    facts.eng_task2 = tasks.add(conn, "Backfill_fact_orders", project="data-eng",
                                depends_on=facts.eng_task1, deadline="2026-09-21")
    tasks.update_status(conn, facts.eng_task1, "done")  # completes AFTER child exists

    # ---- notes ------------------------------------------------------------------
    for body, kind, project in [
        ("Thoth uses SQLite WAL as the store", "decision", "thoth"),
        ("RCC beam design uses limit state method", "fact", "rcc-suite"),
        ("Postgres partial index on status speeds queries", "fact", "data-eng"),
        ("Docker multi-stage builds shrink images", "fact", "data-eng"),
        ("Old belief: indexes always help writes", "fact", "data-eng"),
    ]:
        notes.add(conn, body, kind=kind, project=project)
    old = conn.execute("SELECT id FROM notes WHERE body LIKE 'Old belief%'").fetchone()["id"]
    new = notes.add(conn, "Revised: write-heavy tables need selective indexes", "fact",
                    "data-eng")
    notes.supersede(conn, old, new)
    facts.note_counts = {
        "data-eng_active": len(notes.list_open(conn, project="data-eng")),
        "total_rows": conn.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"],
    }

    # ---- runs --------------------------------------------------------------------
    facts.done_run = runner.start_run(conn, project="rcc-suite", goal="Snapshot schema")
    events.emit(conn, runner.K_TURN_COMPLETED, {
        "run_id": facts.done_run, "turn": 1, "tool": "shell.read",
        "tool_args_digest": "sha256:seed", "verify": {"ok": True, "detail": "seeded"},
        "context_sections": {"identity": 30, "project": 20, "task_state": 40,
                             "memories": 0, "turn_window": 0},
        "next_intent": "next", "bounds": {"max_turns": 25, "tool_calls_budget": 20,
                                          "deadline": None, "tool_calls_used": 1},
    })
    conn.execute("UPDATE runs SET status='done' WHERE id=?", (facts.done_run,))
    events.emit(conn, "run.completed", {"run_id": facts.done_run,
                                        "reason": "planner finished"})

    facts.parked_run = runner.start_run(conn, project="data-eng", goal=facts.parked_goal,
                                        max_turns=25, tool_calls_budget=0)
    conn.execute("UPDATE runs SET status='parked' WHERE id=?", (facts.parked_run,))
    events.emit(conn, runner.K_RUN_PARKED, {"run_id": facts.parked_run,
                                            "reason": facts.parked_reason})
    conn.commit()
    conn.close()
    return facts
