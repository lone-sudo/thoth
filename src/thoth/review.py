"""The weekly review: seven days, rolled up from stored state only.

Third in the report ladder - briefing (morning), digest (evening), review
(end of week). Same discipline as its siblings (stored state only - zero
network, zero AI, <=7 items, "a quiet week" is a valid output) plus the one
thing a daily report cannot show: numbers that survive the item cap. Items
are samples; `counts` is the week's arithmetic. `today` is injectable so the
window is deterministic and testable; accomplished-work comes from the
append-only log (ADR-002), never from the mutable tasks table.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import tasks

MAX_ITEMS = 7
MAX_DONE = 5
MAX_PARKED = 2
MAX_NOTES = 3
MAX_DEADLINES = 3


def build(conn: sqlite3.Connection, project: str | None = None,
          today: str | None = None) -> dict[str, Any]:
    """Assemble the weekly review. `today` = YYYY-MM-DD (the week's last day);
    defaults to the real UTC date. Window = the 7 days ending on `today`."""
    if today is None:
        from .events import now_iso
        today = now_iso()[:10]
    week_start = _minus_days(today, 6)

    report: dict[str, Any] = {
        "project": project, "today": today, "week_start": week_start,
        "sections": [], "counts": {},
    }
    counts = report["counts"]

    # -- accomplished this week: from the append-only log, not the table -------
    done = conn.execute(
        "SELECT DISTINCT json_extract(payload_json, '$.id') AS tid FROM events "
        "WHERE kind = 'task.status_changed' "
        "AND json_extract(payload_json, '$.status') = 'done' "
        "AND substr(ts, 1, 10) >= ? AND substr(ts, 1, 10) <= ?",
        (week_start, today),
    ).fetchall()
    done_rows: list[dict[str, Any]] = []
    for d in done:
        row = conn.execute(
            "SELECT title, project FROM tasks WHERE id = ?", (d["tid"],)
        ).fetchone()
        if row is not None and (project is None or row["project"] == project
                                or row["project"] is None):
            done_rows.append(row)
    counts["tasks_done"] = len(done_rows)
    if done_rows:
        report["sections"].append((
            "accomplished this week",
            [f"{r['title']} [{r['project'] or 'no project'}]"
             for r in done_rows[:MAX_DONE]],
        ))

    # -- runs started this week: arithmetic plus parked samples ----------------
    run_rows = conn.execute(
        "SELECT status, project, goal, id FROM runs "
        "WHERE substr(created_at, 1, 10) >= ? AND substr(created_at, 1, 10) <= ? "
        "AND (? IS NULL OR project = ?)",
        (week_start, today, project, project),
    ).fetchall()
    counts["runs_started"] = len(run_rows)
    counts["runs_done"] = sum(1 for r in run_rows if r["status"] == "done")
    counts["runs_parked"] = sum(1 for r in run_rows if r["status"] == "parked")
    parked = [r for r in run_rows if r["status"] == "parked"]
    if run_rows:
        section: list[str] = [
            f"{counts['runs_done']} finished, {counts['runs_parked']} parked "
            f"of {counts['runs_started']} started"
        ]
        for r in parked[:MAX_PARKED]:
            section.append(f"{r['id']} - {r['goal'] or '(no goal)'} "
                           f"resume: thoth run resume --project {r['project']}")
        report["sections"].append(("runs this week", section))

    # -- notes captured this week (never superseded ones) ----------------------
    note_rows = conn.execute(
        "SELECT kind, body, project FROM notes "
        "WHERE superseded_by IS NULL "
        "AND substr(created_at, 1, 10) >= ? AND substr(created_at, 1, 10) <= ? "
        "AND (? IS NULL OR project = ? OR project IS NULL)",
        (week_start, today, project, project),
    ).fetchall()
    counts["notes_captured"] = len(note_rows)
    if note_rows:
        report["sections"].append((
            "notes captured",
            [f"[{r['kind']}] {r['body']}" for r in note_rows[:MAX_NOTES]],
        ))

    # -- deadlines landing next week -------------------------------------------
    deadline_rows = conn.execute(
        "SELECT id, title, deadline FROM tasks "
        "WHERE status != 'done' AND deadline IS NOT NULL "
        "AND deadline > ? AND deadline <= ? "
        "AND (? IS NULL OR project = ? OR project IS NULL) "
        "ORDER BY deadline LIMIT ?",
        (today, _plus_days(today, 7), project, project, MAX_DEADLINES),
    ).fetchall()
    if deadline_rows:
        report["sections"].append((
            "deadlines - next week",
            [f"{r['title']} (due {r['deadline']}, id {r['id']})"
             for r in deadline_rows],
        ))

    # -- carry-over: what stays open into next week ----------------------------
    open_rows = tasks.list_open(conn, project)
    counts["open_tasks"] = len(open_rows)
    if open_rows:
        nxt = tasks.next_task(conn, project)
        carry = (f"{len(open_rows)} open task(s) carry over"
                 + (f"; next: {nxt['title']} (id {nxt['id']})"
                    if nxt is not None else ""))
        report["sections"].append(("carry-over", [carry]))

    # -- sessions held (count only; the work above is the substance) -----------
    held = conn.execute(
        "SELECT COUNT(*) AS n FROM sessions "
        "WHERE substr(started_at, 1, 10) >= ? AND substr(started_at, 1, 10) <= ? "
        "AND (? IS NULL OR project = ?)",
        (week_start, today, project, project),
    ).fetchone()
    counts["sessions_held"] = held["n"] if held is not None else 0
    if counts["sessions_held"]:
        report["sections"].append((
            "sessions",
            [f"{counts['sessions_held']} session(s) held this week"],
        ))

    n_items = sum(len(items) for _, items in report["sections"])
    report["item_count"] = n_items
    report["needs_you"] = n_items > 0
    return report


def render(report: dict[str, Any]) -> str:
    """Human-readable weekly review, ASCII-safe (Windows consoles),
    <= MAX_ITEMS body lines."""
    project = report.get("project")
    lines = [f"Week {report['week_start']} to {report['today']}"
             + (f" ({project})" if project else "")]
    count = 0
    for title, items in report["sections"]:
        if count >= MAX_ITEMS:
            break
        lines.append(f"{title}:")
        for item in items:
            if count >= MAX_ITEMS:
                lines.append("  ... (capped)")
                break
            lines.append(f"  - {item}")
            count += 1
    if count == 0:
        lines.append("A quiet week. Nothing recorded, nothing due.")
    return "\n".join(lines)


def _minus_days(iso_date: str, days: int) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(iso_date)
    return (d - timedelta(days=days)).isoformat()


def _plus_days(iso_date: str, days: int) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(iso_date)
    return (d + timedelta(days=days)).isoformat()
