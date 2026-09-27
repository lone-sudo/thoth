"""CLI integration tests for `thoth run` and the parked-run block in `continue`."""

from __future__ import annotations

import contextlib
import io

import pytest

from thoth import cli


@pytest.fixture()
def run(tmp_path, monkeypatch):
    db_path = tmp_path / "cli.db"
    monkeypatch.chdir(tmp_path)

    def _run(*argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--db", str(db_path), *argv])
        return code, buf.getvalue()

    return _run


def test_run_start_status_execute_resume_cycle(run):
    code, out = run("run", "start", "--project", "rcc", "--goal", "inspect repo")
    assert code == 0 and "run" in out and "created" in out

    code, out = run("run", "status", "--project", "rcc")
    assert code == 0 and "running:" in out

    # execute with the NoopPlanner (empty script → finishes immediately)
    code, out = run("run", "execute", "--project", "rcc")
    assert code == 0 and "done" in out

    code, out = run("run", "status", "--project", "rcc")
    assert code == 0 and "no active or parked runs" in out


def test_parked_run_surfaces_in_continue_and_resumes(run):
    # park a run via a failing-tool execute: create a run, then force parking by
    # executing with a budget of 0 (immediate budget park).
    run("run", "start", "--project", "rcc", "--goal", "park me")
    code, out = run("run", "execute", "--project", "rcc", "--budget", "0")
    assert code == 1 and "parked" in out

    code, out = run("continue", "--project", "rcc")
    assert code == 0
    assert "parked run" in out and "budget" in out
    assert "resume with: thoth run resume" in out

    # resume: NoopPlanner (empty script) → planner finishes → status done
    code, out = run("run", "resume", "--project", "rcc")
    assert code == 0 and "done" in out

    code, out = run("continue", "--project", "rcc")
    assert "parked run" not in out


def test_run_start_requires_goal(run):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        cli.main(["--db", "x.db", "run", "start"])
