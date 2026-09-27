"""Integration tests: full CLI flows against a real temp database.

Covers the V0 milestone: start → stop (with summary) → continue, plus git
resume snapshot, notes via CLI, and idempotent re-start.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from jarvis import cli, db


@pytest.fixture()
def repo(tmp_path):
    """A real git repo to point a project's workdir at."""
    r = tmp_path / "repo"
    r.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    def g(*a: str) -> None:
        subprocess.run(["git", *a], cwd=r, check=True, capture_output=True, env=env)
    g("init", "-q")
    (r / "f.txt").write_text("hello\n")
    g("add", ".")
    g("commit", "-qm", "initial")
    return r


@pytest.fixture()
def run(tmp_path, repo, monkeypatch):
    """CLI runner with a temp DB and cwd inside the repo."""
    db_path = tmp_path / "cli.db"
    monkeypatch.chdir(repo)

    def _run(*argv: str, cwd: Path | None = None) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--db", str(db_path), *argv])
        return code, buf.getvalue()

    return _run, db_path, repo


def test_start_stop_continue(run, repo):
    _run, db_path, repo = run

    code, out = _run("start", "--project", "rcc")
    assert code == 0
    assert "started" in out

    # idempotent re-start
    code, out = _run("start", "--project", "rcc")
    assert code == 0
    assert "already open" in out

    code, out = _run("stop", "--summary", "migrated schema; indexes pending")
    assert code == 0

    code, out = _run("continue", "--project", "rcc")
    assert code == 0
    assert "migrated schema; indexes pending" in out
    assert "branch" in out  # git snapshot is read from the repo workdir


def test_continue_json_and_zero_tasks(run):
    _run, _, _ = run
    code, out = _run("continue", "--json")
    assert code == 0
    info = json.loads(out)
    assert info["open_session"] is None
    assert info["last_session"] is None
    assert info["open_tasks"] == []


def test_task_flow(run):
    _run, _, _ = run
    _run("task", "add", "first")
    code, out = _run("task", "next")
    assert code == 0
    assert "first" in out

    code, out = _run("task", "list")
    assert code == 0 and "first" in out

    tid = re.search(r"\b([0-9a-f]{8})\b", out).group(1)
    code, out = _run("task", "update", tid, "done")
    assert code == 0
    code, out = _run("task", "next")
    assert code == 1  # nothing runnable


def test_task_dependency_next(run):
    _run, _, _ = run
    _, out1 = _run("task", "add", "first")
    t1 = re.search(r"\b([0-9a-f]{8})\b", out1).group(1)
    _run("task", "add", "second", "--after", t1)
    _, out = _run("task", "next")
    assert "first" in out and "second" not in out


def test_note_via_cli(run):
    _run, _, _ = run
    code, out = _run("note", "add", "prefer WAL mode", "--kind", "preference", "--project", "jarvis")
    assert code == 0
    code, out = _run("note", "list", "--kind", "preference")
    assert code == 0 and "prefer WAL mode" in out


def test_log_lists_events(run):
    _run, _, _ = run
    _run("start", "--project", "rcc")
    _run("stop")
    code, out = _run("log", "--limit", "5")
    assert code == 0
    assert "session.started" in out and "session.stopped" in out


def test_status(run):
    _run, _, _ = run
    _run("start", "--project", "rcc")
    code, out = _run("status")
    assert code == 0 and "open session" in out
