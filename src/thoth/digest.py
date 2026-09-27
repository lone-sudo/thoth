"""The end-of-day digest: what got done, what's due, what's parked, what's next.

Mirrors the briefing's discipline (ADR: stored state only — zero network, zero AI,
≤7 items, "quiet" is a valid output), plus the one thing the briefing doesn't have:
**deadline awareness**. `today` is injectable so the digest is deterministically
testable and honest about "overdue" (computed, never guessed by a model).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import runner, session, tasks

MAX_ITEMS = 7
MAX_TASKS = 3
MAX_PARKED = 2
MAX_DONE = 2


def build(conn: sqlite3.Connection, project: str | None = None,
          today: str | None = None) -> dict[str, Any]:
    """Assemble the digest. `today` = YYYY-MM-DD; defaults to the real UTC date."""
    if today is None:
        from .events import now_iso
        today = now_iso()[:10]

    report: dict[str, Any] = {"project": project, "today": today, "sections": []}

    # -- deadlines: overdue first, then due today, then upcoming (7d window) ----
    rows = conn.execute(
        "SELECT id, title, project, deadline FROM tasks "
        "WHERE status != 'done' AND deadline IS NOT NULL "
        "AND (? IS NULL OR project = ? OR project IS NULL) "
        "ORDER BY deadline LIMIT ?",
        (project, project, MAX_TASKS),
    ).fetchall()
    overdue: list[str] = []
    due_today: list[str] = []
    upcoming: list[str] = []
    for r in rows:
        d = r["deadline"]
        label = f"{r['title']} (due {d}, id {r['id']})"
        if d < today:
            overdue.append(f"OVERDUE: {label}")
        elif d == today:
            due_today.append(f"DUE TODAY: {label}")
        elif d <= _plus_days(today, 7):
            upcoming.append(label)
    if overdue:
        report["sections"].append(("deadlines — overdue", overdue))
    if due_today:
        report["sections"].append(("deadlines — today", due_today))
    if upcoming:
        report["sections"].append(("deadlines — upcoming week", upcoming))

    # -- parked runs ------------------------------------------------------------
    parked = conn.execute(
        "SELECT id, project, goal FROM runs "
        "WHERE status = 'parked' AND (? IS NULL OR project = ?) "
        "ORDER BY updated_at DESC LIMIT ?",
        (project, project, MAX_PARKED),
    ).fetchall()
    if parked:
        report["sections"].append((
            "parked runs",
            [f"{r['id']} - {r['goal'] or '(no goal)'} [{r['project']}] "
             f"resume: thoth run resume --project {r['project']}" for r in parked],
        ))

    # -- accomplished today: tasks done today, from the append-only log ---------
    done = conn.execute(
        "SELECT DISTINCT json_extract(payload_json, '$.id') AS tid FROM events "
        "WHERE kind = 'task.status_changed' AND ts >= ? || 'T00:00:00Z' "
        "AND json_extract(payload_json, '$.status') = 'done'",
        (today,),
    ).fetchall()
    done_items: list[str] = []
    for d in done:
        row = conn.execute(
            "SELECT title, project FROM tasks WHERE id = ?", (d["tid"],)
        ).fetchone()
        if row is not None and (project is None
                                or row["project"] == project or row["project"] is None):
            done_items.append(f"{row['title']} [{row['project'] or 'no project'}]")
        if len(done_items) >= MAX_DONE:
            break
    if done_items:
        report["sections"].append(("accomplished today", done_items))

    # -- next up: dependency-aware suggestion ------------------------------------
    nxt = tasks.next_task(conn, project)
    if nxt is not None:
        report["sections"].append((
            "next up",
            [f"{nxt['title']} (id {nxt['id']})"
             + (f" - due {nxt['deadline']}" if nxt.get("deadline") else "")],
        ))

    n_items = sum(len(items) for _, items in report["sections"])
    report["item_count"] = n_items
    report["needs_you"] = n_items > 0
    return report


def render(report: dict[str, Any]) -> str:
    """Human-readable digest, ASCII-safe (Windows consoles), ≤ MAX_ITEMS lines."""
    project = report.get("project")
    lines = [f"End of day - {report['today']}"
             + (f" ({project})" if project else "")]
    count = 0
    for title, items in report["sections"]:
        lines.append(f"{title}:")
        for item in items:
            if count >= MAX_ITEMS:
                lines.append("  ... (capped)")
                break
            lines.append(f"  - {item}")
            count += 1
    if count == 0:
        lines.append("Quiet day. Nothing recorded, nothing due.")
    return "\n".join(lines)


def _plus_days(iso_date: str, days: int) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(iso_date)
    return (d + timedelta(days=days)).isoformat()
