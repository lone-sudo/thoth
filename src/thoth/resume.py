"""Resume: "Where did I leave off?" — stored state + read-only git, zero AI calls."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import gitinfo, session


def _open_tasks(conn: sqlite3.Connection, project: str | None) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, title, status, project FROM tasks "
        "WHERE status != 'done'"
    )
    params: list[Any] = []
    if project:
        sql += " AND (project = ? OR project IS NULL)"
        params.append(project)
    sql += " ORDER BY created_at DESC LIMIT 5"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _last_stopped(conn: sqlite3.Connection, project: str | None) -> dict[str, Any] | None:
    row = session.last_stopped(conn, project)
    if row is None:
        return None
    return {
        "id": row["id"],
        "project": row["project"],
        "stopped_at": row["stopped_at"],
        "summary": session.summary_of(conn, row["id"]),
    }


def _git_block(conn: sqlite3.Connection, project: str | None) -> dict[str, Any] | None:
    workdir = session.workdir_of(conn, project) if project else None
    if not workdir or not Path(workdir).exists():
        return None
    snap = gitinfo.resume_snapshot(Path(workdir))
    if not snap.get("repo"):
        return None
    return snap


def build(conn: sqlite3.Connection, project: str | None = None) -> dict[str, Any]:
    """Assemble everything 'continue' needs. Reads only; never mutates."""
    info: dict[str, Any] = {"project": project}

    open_row = session.current(conn, project)
    info["open_session"] = (
        {"id": open_row["id"], "started_at": open_row["started_at"]} if open_row else None
    )

    info["last_session"] = _last_stopped(conn, project)
    info["git"] = _git_block(conn, project)
    info["open_tasks"] = _open_tasks(conn, project)
    return info


def render(info: dict[str, Any]) -> str:
    """Human-readable resume card."""
    lines: list[str] = []
    project = info.get("project")
    header = "Where you left off" + (f" — {project}" if project else "")
    lines.append(header)

    open_s = info.get("open_session")
    last_s = info.get("last_session")
    if open_s:
        lines.append(f"  session OPEN since {open_s['started_at']} (id {open_s['id']})")
    elif last_s:
        lines.append(f"  last session stopped {last_s['stopped_at']} (id {last_s['id']})")
        if last_s.get("summary"):
            lines.append(f"  wrap-up: {last_s['summary']}")
    else:
        lines.append("  no sessions recorded yet")

    git = info.get("git")
    if git:
        b = gitinfo._clean_branch(git.get("branch"))
        if b:
            lines.append(f"  git: branch {b}")
        lc = git.get("last_commit")
        if lc:
            lines.append(f"  last commit: {lc['hash']} {lc['subject']} ({lc['when']})")
        dirty = git.get("dirty") or []
        if dirty:
            lines.append(f"  uncommitted changes ({len(dirty)}):")
            lines += [f"    {d}" for d in dirty[:5]]
            if len(dirty) > 5:
                lines.append(f"    … and {len(dirty) - 5} more")
        if git.get("stashes"):
            lines.append(f"  stashes: {git['stashes']}")

    tasks = info.get("open_tasks") or []
    if tasks:
        lines.append("  open tasks:")
        lines += [f"    [{t['status']}] {t['title']}" for t in tasks]
    else:
        lines.append("  open tasks: none")
    return "\n".join(lines)
