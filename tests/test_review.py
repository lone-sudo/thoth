"""Tests for the weekly review (third rung of the report ladder).

The clock is frozen per seed step so the 7-day window is deterministic;
every assertion runs against a real tmp_path DB. No network, no model -
that is the whole point of the report (stored state only).
"""

from __future__ import annotations

import contextlib
import io
import json
from unittest import mock

import pytest

from thoth import cli, db, notes, review, runner, session, tasks

TODAY = "2026-10-03"      # the review anchor every test pins
IN_WINDOW = "2026-09-30"  # inside the 7 days ending TODAY
BEFORE = "2026-09-01"     # safely outside it


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@contextlib.contextmanager
def frozen(day: str):
    """Pin every timestamp-stamping module to `day` at 10:00Z."""
    ts = day + "T10:00:00Z"
    with contextlib.ExitStack() as es:
        for mod in ("events", "tasks", "notes", "session", "runner"):
            es.enter_context(mock.patch(f"thoth.{mod}.now_iso", return_value=ts))
        yield


@pytest.fixture()
def run(tmp_path):
    """Local CLI runner (same pattern as test_interface.run)."""
    db_path = tmp_path / "cli.db"

    def _run(*argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--db", str(db_path), *argv])
        return code, buf.getvalue()

    return _run, db_path


# ------------------------------------------------------------------ build

def test_review_rolls_up_the_week(conn):
    with frozen(IN_WINDOW):
        sid = session.start(conn, project="p")
        session.stop(conn, sid, summary="wrapped")
        tid = tasks.add(conn, "ship the ladder", project="p")
        tasks.update_status(conn, tid, "done")
        notes.add(conn, "guard is the only outbound path",
                  kind="decision", project="p")
        runner.start_run(conn, "p", "read the README")
        tasks.add(conn, "what is next", project="p")

    report = review.build(conn, project="p", today=TODAY)
    counts = report["counts"]
    assert counts["tasks_done"] == 1
    assert counts["sessions_held"] == 1
    assert counts["notes_captured"] == 1
    assert counts["runs_started"] == 1
    assert counts["runs_parked"] == 0
    assert counts["open_tasks"] == 1

    titles = [t for t, _ in report["sections"]]
    assert "accomplished this week" in titles
    assert "runs this week" in titles
    assert "notes captured" in titles
    assert "carry-over" in titles
    assert "sessions" in titles
    assert report["needs_you"] is True


def test_review_window_excludes_older_work(conn):
    with frozen(BEFORE):
        tid = tasks.add(conn, "ancient", project="p")
        tasks.update_status(conn, tid, "done")
        notes.add(conn, "old fact", project="p")
        runner.start_run(conn, "p", "old goal")
        session.start(conn, project="p")

    report = review.build(conn, project="p", today=TODAY)
    assert report["counts"]["tasks_done"] == 0
    assert report["counts"]["notes_captured"] == 0
    assert report["counts"]["runs_started"] == 0
    assert report["counts"]["sessions_held"] == 0
    assert report["sections"] == []
    assert "A quiet week" in review.render(report)


def test_review_carry_over_names_the_next_task(conn):
    with frozen(IN_WINDOW):
        dep = tasks.add(conn, "first thing", project="p")
        tasks.add(conn, "second thing", project="p", depends_on=dep)
        tasks.add(conn, "last thing", project="p")

    report = review.build(conn, project="p", today=TODAY)
    assert report["counts"]["open_tasks"] == 3
    carry = dict(report["sections"])["carry-over"][0]
    assert "3 open task(s)" in carry
    assert "next:" in carry and "first thing" in carry


def test_review_parked_runs_show_their_resume_line(conn):
    with frozen(IN_WINDOW):
        run_id = runner.start_run(conn, "p", "stuck on the matrix")
    # The write path (parking) is tested in test_runner.py; the review only
    # reads the runs table, so seed the parked state directly.
    conn.execute("UPDATE runs SET status = 'parked' WHERE id = ?", (run_id,))
    conn.commit()

    report = review.build(conn, project="p", today=TODAY)
    assert report["counts"]["runs_parked"] == 1
    runs_section = dict(report["sections"])["runs this week"]
    assert "0 finished, 1 parked of 1 started" in runs_section[0]
    assert "thoth run resume --project p" in runs_section[1]


def test_review_project_filter(conn):
    with frozen(IN_WINDOW):
        session.start(conn, project="p")
        tasks.add(conn, "thoth task", project="p")
        notes.add(conn, "thoth note", project="p")
        tasks.add(conn, "other task", project="q")
        notes.add(conn, "other note", project="q")

    report = review.build(conn, project="q", today=TODAY)
    blob = json.dumps(report)
    assert "thoth task" not in blob and "thoth note" not in blob
    assert report["counts"]["sessions_held"] == 0
    assert report["counts"]["open_tasks"] == 1


def test_review_deadlines_next_week(conn):
    with frozen(IN_WINDOW):
        tasks.add(conn, "prep demo", project="p", deadline="2026-10-07")
        tasks.add(conn, "far away", project="p", deadline="2026-12-01")

    report = review.build(conn, project="p", today=TODAY)
    titles = dict(report["sections"])
    assert "deadlines - next week" in titles
    assert any("prep demo" in i for i in titles["deadlines - next week"])
    assert not any("far away" in i for i in
                   [x for items in report["sections"] for x in items])


# ------------------------------------------------------------------- render

def test_review_render_is_ascii_and_capped(conn):
    with frozen(IN_WINDOW):
        for i in range(12):
            tid = tasks.add(conn, f"task {i}", project="p")
            tasks.update_status(conn, tid, "done")

    report = review.build(conn, project="p", today=TODAY)
    assert report["counts"]["tasks_done"] == 12          # arithmetic survives
    text = review.render(report)
    text.encode("ascii")                                  # cp1252-console safe
    assert text.count("\n  - ") == review.MAX_DONE        # item cap, not silent


# ---------------------------------------------------------------------- CLI

def test_review_cli_json(run):
    _run, _db = run
    code, out = _run("review", "--today", TODAY, "--json")
    assert code == 0
    data = json.loads(out)
    assert data["week_start"] == "2026-09-27"
    assert data["counts"]["tasks_done"] == 0


def test_review_cli_quiet_week(run):
    _run, _db = run
    code, out = _run("review", "--today", TODAY)
    assert code == 0
    assert "A quiet week" in out
