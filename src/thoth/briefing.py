"""The morning briefing (Azaris-parity track, V1 item pulled forward).

Generated **from stored state only** — never from live scraping, never from the
network, never from a model. That is what makes it unable to fail because a website
changed (Team-A §7) and free under the $0 policy. Caps: ≤7 items total; "Nothing
needs you today." is a valid, honest output.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import runner, session, tasks

MAX_ITEMS = 7
MAX_TASKS = 3
MAX_PARKED = 3


def build(conn: sqlite3.Connection, project: str | None = None) -> dict[str, Any]:
    """Assemble the briefing report (pure read; no network, no model)."""
    report: dict[str, Any] = {"project": project, "sections": []}

    parked_rows = conn.execute(
        "SELECT id, project, goal, updated_at FROM runs "
        "WHERE status = 'parked' AND (? IS NULL OR project = ?) "
        "ORDER BY updated_at DESC LIMIT ?",
        (project, project, MAX_PARKED),
    ).fetchall()
    if parked_rows:
        report["sections"].append((
            "parked runs (resume when ready)",
            [f"{r['id']} - {r['goal'] or '(no goal)'} "
             f"[{r['project']}] resume: thoth run resume --project {r['project']}"
             for r in parked_rows],
        ))

    open_task_rows = tasks.list_open(conn, project)[:MAX_TASKS]
    if open_task_rows:
        report["sections"].append((
            "open work",
            [f"[{t['status']}] {t['title']} ({t['id']})" for t in open_task_rows],
        ))

    last = session.last_stopped(conn, project)
    if last is not None:
        summary = session.summary_of(conn, last["id"])
        if summary:
            report["sections"].append((
                "last wrap-up",
                [f"{last['stopped_at']}: {summary}"],
            ))

    n_items = sum(len(items) for _, items in report["sections"])
    report["item_count"] = n_items
    report["needs_you"] = n_items > 0
    return report


def render(report: dict[str, Any]) -> str:
    """Human-readable briefing, ≤ MAX_ITEMS body lines."""
    project = report.get("project")
    lines = [f"Good morning{f' - {project}' if project else ''}."]
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
        lines.append("Nothing needs you today.")
    return "\n".join(lines)
