"""Notes: atomic facts / decisions / preferences / lessons (ADR-002 derived view).

Supersede, never delete — matching "decay is ranking, not deletion".
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from .events import emit, now_iso

KINDS = {"fact", "decision", "preference", "lesson"}


def add(
    conn: sqlite3.Connection,
    body: str,
    kind: str = "fact",
    project: str | None = None,
) -> str:
    """Record one atomic note and emit note.added."""
    if kind not in KINDS:
        raise ValueError(f"invalid note kind: {kind} (expected one of {sorted(KINDS)})")
    note_id = uuid.uuid4().hex[:8]
    conn.execute(
        "INSERT INTO notes (id, kind, body, project, created_at) VALUES (?, ?, ?, ?, ?)",
        (note_id, kind, body, project, now_iso()),
    )
    emit(conn, "note.added", {"id": note_id, "kind": kind, "project": project})
    conn.commit()
    return note_id


def list_open(
    conn: sqlite3.Connection,
    project: str | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Active (non-superseded) notes, newest first."""
    sql = (
        "SELECT id, kind, body, project, created_at FROM notes "
        "WHERE superseded_by IS NULL"
    )
    params: list[Any] = []
    if project:
        sql += " AND (project = ? OR project IS NULL)"
        params.append(project)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY created_at DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def supersede(conn: sqlite3.Connection, note_id: str, by_id: str) -> None:
    """Mark one note superseded by another. Notes are never hard-deleted."""
    for nid in (note_id, by_id):
        row = conn.execute("SELECT id FROM notes WHERE id = ?", (nid,)).fetchone()
        if row is None:
            raise ValueError(f"unknown note id: {nid}")
    conn.execute(
        "UPDATE notes SET superseded_by = ? WHERE id = ?", (by_id, note_id)
    )
    emit(conn, "note.superseded", {"id": note_id, "by": by_id})
    conn.commit()
