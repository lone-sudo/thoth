"""SQLite storage layer (ADR-001). One database file, WAL, stdlib only."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

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


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the Jarvis database with WAL enabled."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO NOTHING",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn
