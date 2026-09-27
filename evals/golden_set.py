"""The golden set: 30 stored-state questions (Team-A §46.B / ROADMAP V0 gate).

Every question must be answerable from the database alone — via `resume.build`,
`runner.*`, `session.*`, `tasks.*`, or `notes.*` — with zero AI calls. Each check
returns (name, ok, detail); the runner scores them. Wrong answers here mean the
surfaces lie about stored state, which is exactly what this harness exists to catch.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from thoth import briefing, notes, resume, runner, session, tasks

from .seed_world import Facts

Check = Callable[[sqlite3.Connection, Facts], tuple[bool, str]]


def _check(name: str, fn: Callable[[sqlite3.Connection, Facts], object]) -> Check:
    def run(conn: sqlite3.Connection, facts: Facts) -> tuple[bool, str]:
        try:
            result = fn(conn, facts)
            ok, detail = result if isinstance(result, tuple) else (bool(result), "")
            return ok, detail
        except Exception as exc:  # a golden question failing hard is a fail, not a crash
            return False, f"exception: {type(exc).__name__}: {exc}"
    run.__name__ = name
    return run


# ===========================================================================
# A. resume / continue (1–10)
# ===========================================================================

def _c01(conn, f):
    info = resume.build(conn, "rcc-suite")
    s = info["last_session"]
    return s is not None and s["summary"] == f.last_rcc_summary, f"summary={s and s['summary']!r}"

def _c02(conn, f):
    info = resume.build(conn, "thoth")
    return info["open_session"] is not None, "expected an open session for thoth"

def _c03(conn, f):
    info = resume.build(conn, "thoth")
    return info["last_session"] is None, "thoth has no closed sessions yet"

def _c04(conn, f):
    info = resume.build(conn, "data-eng")
    tasks_listed = [t["title"] for t in info["open_tasks"]]
    return "Backfill_fact_orders" in tasks_listed, f"open_tasks={tasks_listed}"

def _c05(conn, f):
    info = resume.build(conn, "rcc-suite")
    titles = [t["title"] for t in info["open_tasks"]]
    return titles == ["Fix PG16 migration", "Add beam index"], f"{titles}"

def _c06(conn, f):
    info = resume.build(conn, "rcc-suite")
    return info["git"] is None, "seeded workdir isn't a git repo — git block must be None"

def _c07(conn, f):
    info = resume.build(conn, "no-such-project")
    return (info["last_session"] is None and info["open_session"] is None
            and info["open_tasks"] == []), f"{info}"

def _c08(conn, f):
    info = resume.build(conn, None)
    got = (info["last_session"] or {}).get("summary")
    return got == f.last_rcc_summary, f"global latest closed = {got!r}"

def _c09(conn, f):
    info = resume.build(conn, "rcc-suite")
    s = info["last_session"]
    return s["id"] == "s_rcc2", f"latest rcc closed session = {s['id']}"

def _c10(conn, f):
    parked = runner.last_parked(conn, "data-eng")
    return (parked is not None and parked["id"] == f.parked_run
            and parked.get("park_reason") == f.parked_reason), f"{parked and parked.get('park_reason')!r}"


# ===========================================================================
# B. status / sessions (11–18)
# ===========================================================================

def _c11(conn, f):
    return session.current(conn, "thoth")["id"] == "s_thoth", ""

def _c12(conn, f):
    return session.current(conn, "rcc-suite") is None, "rcc sessions are closed"

def _c13(conn, f):
    n = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
    return n == 4, f"{n} sessions"

def _c14(conn, f):
    s = session.last_stopped(conn, "data-eng")
    return (s["id"] == "s_eng1"
            and session.summary_of(conn, "s_eng1") is None), "eng session closed without summary"

def _c15(conn, f):
    s1 = session.summary_of(conn, "s_rcc1")
    return s1 == "Bootstrapped migration folder", f"{s1!r}"

def _c16(conn, f):
    return session.workdir_of(conn, "rcc-suite") == "/tmp/rcc-suite", ""

def _c17(conn, f):
    rows = conn.execute(
        "SELECT id FROM sessions WHERE stopped_at IS NULL").fetchall()
    return [r["id"] for r in rows] == ["s_thoth"], "exactly one open session"

def _c18(conn, f):
    info = resume.build(conn, "thoth")
    # open session must NOT masquerade as last_session
    return info["last_session"] is None and info["open_session"]["id"] == "s_thoth", f"{info}"


# ===========================================================================
# C. tasks (19–24)
# ===========================================================================

def _c19(conn, f):
    nxt = tasks.next_task(conn, "rcc-suite")
    return nxt["id"] == f.rcc_task1, "dependency gates the second task"

def _c20(conn, f):
    tasks.update_status(conn, f.rcc_task1, "done")
    nxt = tasks.next_task(conn, "rcc-suite")
    ok = nxt["id"] == f.rcc_task2
    tasks.update_status(conn, f.rcc_task1, "todo")  # restore world for later checks
    return ok, "done dep unlocks the child"

def _c21(conn, f):
    nxt = tasks.next_task(conn, "data-eng")
    return nxt["id"] == f.eng_task2, "done dep already unblocks backfill"

def _c22(conn, f):
    open_titles = [t["title"] for t in tasks.list_open(conn, "data-eng")]
    return open_titles == ["Backfill_fact_orders"], f"{open_titles}"

def _c23(conn, f):
    try:
        tasks.add(conn, "orphan", depends_on="zzzz")
        return False, "accepted unknown dependency"
    except ValueError:
        return True, ""

def _c24(conn, f):
    n = conn.execute("SELECT COUNT(*) c FROM tasks WHERE status='done'").fetchone()["c"]
    return n == 1, "exactly one done task (Model airflow dags)"


# ===========================================================================
# D. notes / memory (25–27)
# ===========================================================================

def _c25(conn, f):
    out = tools_search(conn, "postgres partial index", "data-eng")
    bodies = [r["body"] for r in out]
    return any("partial index" in b for b in bodies), f"{bodies}"

def _c26(conn, f):
    out = tools_search(conn, "indexes always help writes", "data-eng")
    return out == [], "superseded note must vanish from retrieval"

def _c27(conn, f):
    out = tools_search(conn, "Revised write-heavy selective", "data-eng")
    bodies = [r["body"] for r in out]
    return any("write-heavy" in b for b in bodies), f"{bodies}"


def tools_search(conn, query, project):
    from thoth.tools import _run_memory
    result = _run_memory(query=query, project=project, _conn=conn)
    return result.get("results", []) if result.get("ok") else []


# ===========================================================================
# E. runs (28–30)
# ===========================================================================

def _c28(conn, f):
    cur = runner.current_run(conn, "data-eng")
    return cur is None, "no run is 'running' in the seeded world"

def _c29(conn, f):
    last = runner.last_turn_event(conn, f.done_run)
    return (last is not None and last["verify"]["ok"] is True
            and last["bounds"]["tool_calls_used"] == 1), f"{last and last['bounds']}"

def _c30(conn, f):
    turns = runner.history_of(conn, f.done_run)
    return len(turns) == 1 and turns[0]["run_id"] == f.done_run, f"{len(turns)} turns"


# ===========================================================================
# F. briefing (31–33) — Azaris-parity track, pulled forward
# ===========================================================================

def _c31(conn, f):
    report = briefing.build(conn, "data-eng")
    # parked data-eng run must top the briefing, with its own reason traceable
    parked = runner.last_parked(conn, "data-eng")
    ok = (parked is not None and parked["id"] == f.parked_run
          and parked.get("park_reason") == f.parked_reason)
    return ok and report["needs_you"], f"parked={parked and parked['id']}"

def _c32(conn, f):
    report = briefing.build(conn, "thoth")
    rendered = briefing.render(report)
    return "Nothing needs you today." in rendered, f"{rendered!r}"

def _c33(conn, f):
    report = briefing.build(conn, None)
    rendered = briefing.render(report)
    body = sum(len(items) for _, items in report["sections"])
    return body <= 7 and ("Nothing needs you" in rendered or body > 0), f"items={body}"


GOLDEN: list[Check] = [
    _check("01.continue returns last rcc wrap-up summary", _c01),
    _check("02.continue finds the open thoth session", _c02),
    _check("03.continue returns no last_session for thoth", _c03),
    _check("04.continue lists data-eng open task", _c04),
    _check("05.rcc open tasks in creation order", _c05),
    _check("06.git block None for non-repo workdir", _c06),
    _check("07.unknown project returns empty state", _c07),
    _check("08.global continue returns latest closed summary", _c08),
    _check("09.latest rcc session is s_rcc2", _c09),
    _check("10.parked run surfaces with per-run reason", _c10),
    _check("11.current session for thoth", _c11),
    _check("12.no open rcc session", _c12),
    _check("13.exactly four sessions", _c13),
    _check("14.eng last session has no summary", _c14),
    _check("15.s_rcc1 summary recoverable from log", _c15),
    _check("16.rcc workdir remembered", _c16),
    _check("17.exactly one open session", _c17),
    _check("18.open session not reported as last_session", _c18),
    _check("19.next task respects dependency", _c19),
    _check("20.done dep unlocks child task", _c20),
    _check("21.data-eng next task is backfill", _c21),
    _check("22.data-eng open list correct", _c22),
    _check("23.unknown dependency rejected", _c23),
    _check("24.exactly one done task", _c24),
    _check("25.fts finds postgres note", _c25),
    _check("26.superseded note not retrievable", _c26),
    _check("27.revised note retrievable", _c27),
    _check("28.no running run", _c28),
    _check("29.done run checkpoint intact", _c29),
    _check("30.run history scoped to its run", _c30),
    _check("31.briefing tops parked run with own reason", _c31),
    _check("32.quiet project honestly reports nothing-needs-you", _c32),
    _check("33.briefing respects the 7-item cap", _c33),
]
