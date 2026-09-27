"""Task list with simple dependency awareness — SQLite rows, not a DAG engine."""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from .events import emit, now_iso


def add(
    conn: sqlite3.Connection,
    title: str,
    project: str | None = None,
    depends_on: str | None = None,
    deadline: str | None = None,
) -> str:
    """Create a task; deadline is an ISO date (YYYY-MM-DD), validated."""
    """Create a task. depends_on must reference an existing, not-yet-done task."""
    if deadline is not None:
        from datetime import date
        try:
            date.fromisoformat(deadline)
        except ValueError as exc:
            raise ValueError(f"deadline must be YYYY-MM-DD: {deadline}") from exc
    if depends_on is not None:
        dep = conn.execute("SELECT status FROM tasks WHERE id = ?", (depends_on,)).fetchone()
        if dep is None:
            raise ValueError(f"unknown task id: {depends_on}")
        if dep["status"] == "done":
            raise ValueError(f"dependency {depends_on} is already done")
    task_id = uuid.uuid4().hex[:8]
    ts = now_iso()
    conn.execute(
        "INSERT INTO tasks (id, title, project, status, depends_on, deadline, created_at, updated_at) "
        "VALUES (?, ?, ?, 'todo', ?, ?, ?, ?)",
        (task_id, title, project, depends_on, deadline, ts, ts),
    )
    emit(conn, "task.added", {"id": task_id, "title": title, "project": project,
                              "depends_on": depends_on, "deadline": deadline})
    conn.commit()
    return task_id


def list_open(conn: sqlite3.Connection, project: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT id, title, project, status, depends_on, deadline, created_at FROM tasks WHERE status != 'done'"
    params: list[Any] = []
    if project:
        sql += " AND (project = ? OR project IS NULL)"
        params.append(project)
    sql += " ORDER BY created_at"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def next_task(conn: sqlite3.Connection, project: str | None = None) -> dict[str, Any] | None:
    """First task whose dependencies are done (or none), oldest first."""
    open_tasks = list_open(conn, project)
    for t in open_tasks:
        dep = t["depends_on"]
        if dep is None:
            return t
        dep_row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (dep,)
        ).fetchone()
        if dep_row is None or dep_row["status"] == "done":
            return t
    return None


def update_status(conn: sqlite3.Connection, task_id: str, status: str) -> None:
    if status not in {"todo", "doing", "done"}:
        raise ValueError(f"invalid status: {status}")
    row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown task id: {task_id}")
    conn.execute(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
        (status, now_iso(), task_id),
    )
    emit(conn, "task.status_changed", {"id": task_id, "status": status})
    conn.commit()
