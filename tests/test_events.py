"""Unit tests: append-only event log semantics (ADR-002)."""

from __future__ import annotations

import json

import pytest

from jarvis import db, events


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


def test_schema_created(conn):
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"meta", "events", "sessions", "notes", "tasks", "workdirs"} <= tables


def test_emit_appends_event(conn):
    eid = events.emit(conn, "test.kind", {"a": 1})
    row = conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    assert row is not None
    assert row["kind"] == "test.kind"
    assert json.loads(row["payload_json"]) == {"a": 1}


def test_emit_default_payload(conn):
    eid = events.emit(conn, "test.empty")
    payload = json.loads(
        conn.execute("SELECT payload_json FROM events WHERE id = ?", (eid,)).fetchone()[
            "payload_json"
        ]
    )
    assert payload == {}


def test_events_are_append_only(conn):
    """Nothing in the write path ever updates or deletes an event."""
    events.emit(conn, "kind.one", {"n": 1})
    before = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    # Mutating other state must never change the events table.
    tasks_module = pytest.importorskip("jarvis.tasks")
    tasks_module.add(conn, "t")
    after = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    assert after == before + 1  # exactly the task.added event, nothing rewritten


def test_timestamps_are_utc_iso(conn):
    events.emit(conn, "ts.check")
    ts = conn.execute("SELECT ts FROM events LIMIT 1").fetchone()["ts"]
    assert ts.endswith("Z")
    assert len(ts) == 20  # YYYY-MM-DDTHH:MM:SSZ
