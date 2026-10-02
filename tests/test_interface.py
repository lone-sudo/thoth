"""Tests for the interface layer: the clock (schedule), the static dashboard
(briefing --html), and the bare-thoth menu.

The network is never touched; the DB is real (tmp_path); the menu's input is
piped. Every dispatched menu move must be a REAL cli command - asserted by
the argv tails these tests pin.
"""

from __future__ import annotations

import builtins
import contextlib
import io

import pytest

from thoth import cli, dashboard, db, menu, schedule, session, tasks


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture()
def state(conn):
    """A little world: an OPEN session (so the menu offers 'close') and two
    chained todo tasks (depends_on must be not-yet-done at add time)."""
    session.start(conn, project="p")
    dep = tasks.add(conn, "first", project="p")
    tid = tasks.add(conn, "second", project="p", depends_on=dep)
    return {"task_id": tid, "dep_id": dep}


@pytest.fixture()
def run(tmp_path):
    """Local CLI runner (same pattern as test_cli.run, without the repo)."""
    db_path = tmp_path / "cli.db"

    def _run(*argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--db", str(db_path), *argv])
        return code, buf.getvalue()

    return _run, db_path


# ----------------------------------------------------------------- schedule

def test_schedule_plan_windows_shape():
    p = schedule.plan(db_path="C:/x/thoth.db", briefing_at="07:30",
                      digest_at="21:00")
    assert p["platform"] in ("windows", "cron")
    text = schedule.render(p)
    assert "nothing is installed" in text
    assert "briefing" in text and "digest" in text
    assert "07:30" in text or "30 7" in text


def test_schedule_never_installs_and_uses_default_db():
    p = schedule.plan(None)
    assert str(p["db"]).endswith("thoth.db")


# ---------------------------------------------------------------- dashboard

def test_dashboard_build_and_render(conn, state):
    r = dashboard.build(conn, "p")
    text = dashboard.render_html(r)
    assert "<!DOCTYPE html>" in text
    assert "NEXT: [todo] first" in text
    assert "open session" in text  # the fixture leaves one open
    assert "session.started" in text  # recent events from the log


def test_dashboard_escapes_payload_html(conn):
    """User-entered strings (task titles here) are data, never markup
    (injection defense, ADR-004)."""
    tasks.add(conn, "<script>alert(1)</script>", project="p")
    text = dashboard.render_html(dashboard.build(conn, None))
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_write_html_default_location(tmp_path, monkeypatch, conn, state):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    out = dashboard.write_html(conn, None)
    assert str(out).replace("\\", "/").endswith(".thoth/thoth-dashboard.html")
    assert out.exists() and "DOCTYPE" in out.read_text(encoding="utf-8")


# -------------------------------------------------------------------- menu

def test_menu_options_are_real_commands(conn, state):
    labels = [label for label, _ in menu.build_options(conn)]
    tails = [tail for _, tail in menu.build_options(conn)]
    assert any(t == ["stop"] for t in tails)  # open session -> close it
    assert any(t[:2] == ["task", "update"] for t in tails)
    assert ["briefing"] in tails and ["continue"] in tails
    assert any("first" in lbl for lbl in labels)


def test_menu_dispatches_through_cli_main(conn, state, monkeypatch, capsys):
    """Choosing option 2 flips the task to doing - via the real command."""
    opts = menu.build_options(conn)
    work_idx = next(i for i, (_, tail) in enumerate(opts, 1)
                    if tail[:2] == ["task", "update"])
    answers = iter([str(work_idx), "q"])

    class Args:
        db = str(conn.execute("PRAGMA database_list").fetchone()[2])

    monkeypatch.setattr(builtins, "input", lambda *_: next(answers))
    menu.run_menu(Args(), conn)
    # next_task returns the dependency-free task ("first") - that is the one flipped
    row = conn.execute("SELECT status FROM tasks WHERE id = ?",
                       (state["dep_id"],)).fetchone()
    assert row["status"] == "doing"


def test_menu_survives_garbage_and_eof(conn, state, monkeypatch):
    answers = iter(["banana", "99", ""])
    monkeypatch.setattr(builtins, "input", lambda *_: next(answers))
    with pytest.raises(StopIteration):
        menu.run_menu(type("A", (), {"db": None})(), conn)  # loops until EOF


# ------------------------------------------------------------- cli wiring

def test_briefing_html_flag_writes_file(run, state):
    _run, db_path = run
    code, out = _run("briefing", "--html")
    assert code == 0
    assert "dashboard written" in out


def test_telegram_schedule_prints_plan(run):
    _run, _db_path = run
    code, out = _run("telegram", "schedule", "--briefing-at", "08:00")
    assert code == 0
    assert "08:00" in out or "0 8" in out
    assert "nothing is installed" in out
