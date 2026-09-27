"""Unit tests: append-only events, sessions, tasks, notes (ADR-002)."""

from __future__ import annotations

import json

import pytest

from jarvis import db, events, notes, session, tasks


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


# ------------------------------------------------------------------ events

def test_emit_appends_event(conn):
    eid = events.emit(conn, "test.kind", {"a": 1})
    row = conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    assert row is not None
    assert row["kind"] == "test.kind"
    assert json.loads(row["payload_json"]) == {"a": 1}


def test_events_table_is_append_only(conn):
    """Mutating other state must never rewrite the events table."""
    events.emit(conn, "test.first")
    before = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    tasks.add(conn, "some task")
    after = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    assert after == before + 1  # exactly task.added; nothing else touched


# ---------------------------------------------------------------- sessions

def test_session_start_and_stop(conn):
    sid = session.start(conn, project="rcc")
    assert session.current(conn, "rcc")["id"] == sid
    session.stop(conn, sid, summary="migrated schema")
    assert session.current(conn, "rcc") is None
    last = session.last_stopped(conn, "rcc")
    assert last["id"] == sid
    assert session.summary_of(conn, sid) == "migrated schema"


def test_sessions_isolated_by_project(conn):
    s1 = session.start(conn, project="rcc")
    s2 = session.start(conn, project="data-eng")
    assert session.current(conn, "rcc")["id"] == s1
    assert session.current(conn, "data-eng")["id"] == s2


def test_workdir_roundtrip(conn):
    session.set_workdir(conn, "rcc", "/tmp/rcc")
    assert session.workdir_of(conn, "rcc") == "/tmp/rcc"
    assert session.workdir_of(conn, "nope") is None


# ------------------------------------------------------------------- tasks

def test_task_dependency_ordering(conn):
    t1 = tasks.add(conn, "first")
    t2 = tasks.add(conn, "second", depends_on=t1)
    # second depends on first (not done) -> next is first
    assert tasks.next_task(conn)["id"] == t1
    tasks.update_status(conn, t1, "done")
    assert tasks.next_task(conn)["id"] == t2


def test_task_dependency_validation(conn):
    with pytest.raises(ValueError):
        tasks.add(conn, "orphan", depends_on="nope")


def test_done_task_cannot_be_depended_on(conn):
    t1 = tasks.add(conn, "done already")
    tasks.update_status(conn, t1, "done")
    with pytest.raises(ValueError):
        tasks.add(conn, "late", depends_on=t1)


def test_invalid_status_rejected(conn):
    t1 = tasks.add(conn, "x")
    with pytest.raises(ValueError):
        tasks.update_status(conn, t1, "finished")


# ------------------------------------------------------------------- notes

def test_note_add_list_supersede(conn):
    n1 = notes.add(conn, "uses WAL mode", kind="decision", project="jarvis")
    rows = notes.list_open(conn, project="jarvis", kind="decision")
    assert [r["id"] for r in rows] == [n1]

    n2 = notes.add(conn, "switched to WAL+checkpointing", kind="decision", project="jarvis")
    notes.supersede(conn, n1, n2)
    ids = [r["id"] for r in notes.list_open(conn, kind="decision")]
    assert n1 not in ids and n2 in ids


def test_note_kind_validation(conn):
    with pytest.raises(ValueError):
        notes.add(conn, "bad kind", kind="rumor")
