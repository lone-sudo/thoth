"""Session lifecycle: open / close sessions, materialized from session.* events.

Sessions answer "what am I working on?" and "what was I last doing?" (ADR-002).
The sessions table is a rebuildable view; the event log is the source of truth.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from .events import emit, now_iso

K_START = "session.started"
K_STOP = "session.stopped"


def start(
    conn: sqlite3.Connection,
    project: str | None = None,
    task: str | None = None,
) -> str:
    """Open a new session and emit the start event."""
    session_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO sessions (id, project, started_at) VALUES (?, ?, ?)",
        (session_id, project, now_iso()),
    )
    emit(conn, K_START, {"session": session_id, "project": project, "task": task})
    conn.commit()
    return session_id


def stop(
    conn: sqlite3.Connection,
    session_id: str,
    summary: str | None = None,
) -> None:
    """Close a session; the wrap-up summary lives in the stopped event (ADR-002)."""
    conn.execute(
        "UPDATE sessions SET stopped_at = ? WHERE id = ? AND stopped_at IS NULL",
        (now_iso(), session_id),
    )
    emit(conn, K_STOP, {"session": session_id, "summary": summary})
    conn.commit()


def current(
    conn: sqlite3.Connection,
    project: str | None = None,
) -> dict[str, Any] | None:
    """The open session for a project (most recent), or None."""
    if project:
        row = conn.execute(
            "SELECT * FROM sessions WHERE project = ? AND stopped_at IS NULL "
            "ORDER BY started_at DESC LIMIT 1",
            (project,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM sessions WHERE stopped_at IS NULL "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row is not None else None


def last_stopped(
    conn: sqlite3.Connection,
    project: str | None = None,
) -> dict[str, Any] | None:
    """Most recently closed session, optionally filtered by project."""
    if project:
        row = conn.execute(
            "SELECT * FROM sessions WHERE project = ? AND stopped_at IS NOT NULL "
            "ORDER BY stopped_at DESC LIMIT 1",
            (project,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM sessions WHERE stopped_at IS NOT NULL "
            "ORDER BY stopped_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row is not None else None


def summary_of(conn: sqlite3.Connection, session_id: str) -> str | None:
    """The wrap-up summary stamped onto a stopped session, if any."""
    row = conn.execute(
        "SELECT payload_json FROM events WHERE kind = ? ORDER BY ts DESC",
        (K_STOP,),
    ).fetchall()
    for r in row:
        payload = json.loads(r["payload_json"])
        if payload.get("session") == session_id:
            return payload.get("summary")
    return None


def set_workdir(conn: sqlite3.Connection, project: str, path: str) -> None:
    """Remember where work on a project happens (rebuildable cache row)."""
    conn.execute(
        "INSERT INTO workdirs (project, path, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(project) DO UPDATE SET path = excluded.path, "
        "updated_at = excluded.updated_at",
        (project, path, now_iso()),
    )


def workdir_of(conn: sqlite3.Connection, project: str) -> str | None:
    """Last working directory used for a project, or None."""
    row = conn.execute(
        "SELECT path FROM workdirs WHERE project = ?", (project,)
    ).fetchone()
    return str(row["path"]) if row else None
