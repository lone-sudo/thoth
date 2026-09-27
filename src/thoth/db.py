"""SQLite storage layer (ADR-001). One database file, WAL, stdlib only.

Schema migrations: numbered, append-only (the existing jarvis-era V2 line used the
same pattern); SCHEMA_VERSION tracks the latest applied migration.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Append-only event log (ADR-002). Never updated, never deleted.
CREATE TABLE IF NOT EXISTS events (
    id           TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);

-- Sessions: materialized view of session.* events; rebuildable from the log.
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    project    TEXT,
    started_at TEXT NOT NULL,
    stopped_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions (started_at);

-- Atomic facts/decisions/preferences. Soft-superseded, never hard-deleted.
CREATE TABLE IF NOT EXISTS notes (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL DEFAULT 'fact',
    body          TEXT NOT NULL,
    project       TEXT,
    created_at    TEXT NOT NULL,
    superseded_by TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    project    TEXT,
    status     TEXT NOT NULL DEFAULT 'todo',
    depends_on TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Last known working directory per project (rebuildable cache, not history).
CREATE TABLE IF NOT EXISTS workdirs (
    project    TEXT PRIMARY KEY,
    path       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# --- migration 2 (ADR-003 V0.2): runs index + notes FTS ---------------------

# --- migration 3: task deadlines (digest requires due-date tracking) --------

_MIGRATION_3 = """
ALTER TABLE tasks ADD COLUMN deadline TEXT;
"""

_MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    project      TEXT,
    goal         TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- FTS over notes bodies: the AI-free retriever for the runner context package.
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    body,
    content='notes',
    content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS notes_fts_insert AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, body) VALUES (new.rowid, new.body);
END;
CREATE TRIGGER IF NOT EXISTS notes_fts_delete AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, body) VALUES ('delete', old.rowid, old.body);
END;
CREATE TRIGGER IF NOT EXISTS notes_fts_update AFTER UPDATE OF body, superseded_by
    ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, body) VALUES ('delete', old.rowid, old.body);
    INSERT INTO notes_fts(rowid, body) VALUES (new.rowid, new.body);
END;
"""


def _applied_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row is not None else 0


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations in order; stamp schema_version at the end."""
    applied = _applied_version(conn)
    if applied < 2:
        conn.executescript(_MIGRATION_2)
    if applied < 3:
        try:
            conn.executescript(_MIGRATION_3)
        except Exception:
            pass  # column exists on DBs created post-v3; idempotent

        # Backfill FTS for any notes created before this migration (bulk, fast).
        conn.execute(
            "INSERT INTO notes_fts(rowid, body) "
            "SELECT rowid, body FROM notes WHERE superseded_by IS NULL"
        )
    if applied != SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the Thoth database with WAL enabled."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn
