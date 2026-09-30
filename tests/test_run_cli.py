"""CLI integration tests for `thoth run` and the parked-run block in `continue`."""

from __future__ import annotations

import contextlib
import io

import pytest

from thoth import cli, runner
from thoth.planner_model import ModelPlanner


class _StubModelPlanner:
    """Stands in for ModelPlanner in --model CLI tests: one verified turn,
    then an honest finish (the floor accepts it)."""

    def __init__(self, *a, **k) -> None:
        self._plan = runner.Plan(tool="file.read", args={"path": "hello.txt"},
                                 summary="read", next_intent="finish")

    def decide(self, context, history):
        if self._plan is not None:
            plan, self._plan = self._plan, None
            return plan
        return runner.Plan(tool=None, done=True,
                           summary="hello thoth says the workspace greets you",
                           next_intent="")


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


@pytest.fixture()
def workspace(tmp_path):
    """Minimal local stand-in for the tools-test workspace: one file for the
    stub planner to read."""
    (tmp_path / "hello.txt").write_text("hello thoth")
    return tmp_path, None


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


def test_run_execute_model_flag_real_planner(run, monkeypatch, workspace):
    """--model wires the real planner path (ModelPlanner over the default
    registry), and the finish floor stays ON: a zero-turn done would be
    refused. The planner is stubbed; the runner, registry, and floor are the
    production ones."""
    ws, _ = workspace
    monkeypatch.chdir(ws)
    seen: dict = {}

    def _fake_model_planner(conn):
        seen["called"] = True
        return _StubModelPlanner()

    monkeypatch.setattr(cli, "_model_planner", _fake_model_planner)
    run("run", "start", "--project", "rcc", "--goal", "read hello.txt")
    code, out = run("run", "execute", "--project", "rcc", "--model")
    assert seen["called"] is True
    assert code == 0 and "done" in out
    assert "finish floor ON" in out          # the floor announcement is visible


def test_run_execute_model_unavailable_fails_closed(run, monkeypatch, tmp_path):
    """No local server -> --model refuses honestly. It must NOT fall back to
    the NoopPlanner (a scripted run wearing the model's name would be a
    dishonest record)."""
    import thoth.ollama as ollama_mod

    def _dead_probe(conn):
        return False, "local server failed: ConnectionRefusedError"

    monkeypatch.setattr(ollama_mod, "probe", _dead_probe)
    run("run", "start", "--project", "rcc", "--goal", "nothing to do")
    with pytest.raises(SystemExit) as ei:    # fail closed, message intact
        cli.main(["--db", str(tmp_path / "cli.db"),
                  "run", "execute", "--project", "rcc", "--model"])
    msg = str(ei.value)
    assert "--model: local planner unavailable" in msg
    assert "no scripted fallback" in msg
    # and the run was NOT executed by a silent scripted fallback
    code2, out2 = run("run", "status", "--project", "rcc")
    assert "running:" in out2                # still waiting, un-executed


def test_run_resume_model_flag_plans_with_model(run, monkeypatch, workspace):
    """`thoth run resume --model` resumes the parked run with the real
    planner path."""
    ws, _ = workspace
    monkeypatch.chdir(ws)
    seen: dict = {}

    def _fake_model_planner(conn):
        seen["called"] = True
        return _StubModelPlanner()

    monkeypatch.setattr(cli, "_model_planner", _fake_model_planner)
    run("run", "start", "--project", "rcc", "--goal", "read hello.txt")
    # park it first: execute without --model at zero budget parks instantly
    code, out = run("run", "execute", "--project", "rcc", "--budget", "0")
    assert code == 1 and "parked" in out
    code, out = run("run", "resume", "--project", "rcc", "--model")
    assert seen["called"] is True
    assert code == 0 and "done" in out


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
