"""Append-only event log (ADR-002).

Events are facts: once written, never mutated or deleted. All other state
(sessions, summaries, derived notes) is a *view* of the event stream.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    """UTC timestamp, ISO-8601 with seconds precision, Z-suffixed."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(conn: sqlite3.Connection, kind: str, payload: dict[str, Any] | None = None) -> str:
    """Append one event and return its id. The only write path to `events`."""
    event_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO events (id, ts, kind, payload_json) VALUES (?, ?, ?, ?)",
        (event_id, now_iso(), kind, json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)),
    )
    return event_id
