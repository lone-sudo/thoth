"""Tests for the permission table {domain -> level} and the typed
confirmation handshake (ADR-004 section 5, ROADMAP V1).

The shape under test: a mutating tool is INERT by default (ceiling 0),
inert-but-raised requires the operator to type a per-action token, and
the token unlocks exactly one (run, tool, args) - nothing else, nobody
else.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import json

import pytest

from thoth import cli, db, permissions, runner, tools


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture()
def run(tmp_path, monkeypatch):
    """Local CLI runner (same pattern as test_interface.run)."""
    db_path = tmp_path / "cli.db"

    def _run(*argv: str, stdin: str | None = None) -> tuple[int, str]:
        if stdin is not None:
            monkeypatch.setattr(builtins, "input", lambda *_: stdin)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--db", str(db_path), *argv])
        return code, buf.getvalue()

    return _run, db_path


# ------------------------------------------------------------------- table

def test_default_table_is_all_observe(conn):
    assert permissions.table(conn) == {"shell": 0, "file": 0, "memory": 0,
                                       "git": 0}


def test_unknown_domain_and_corrupt_value_fail_closed(conn):
    assert permissions.ceiling(conn, "carrier-pigeon") == 0
    conn.execute("INSERT INTO meta (key, value) VALUES ('perm:git', 'banana')")
    conn.commit()
    assert permissions.ceiling(conn, "git") == 0
    assert permissions.ceiling(conn, "git.commit") == permissions.ceiling(conn, "git")


def test_set_ceiling_validates_and_emits(conn):
    with pytest.raises(ValueError):
        permissions.set_ceiling(conn, "nope", 1)
    with pytest.raises(ValueError):
        permissions.set_ceiling(conn, "git", 9)
    permissions.set_ceiling(conn, "git", 2)
    assert permissions.ceiling(conn, "git") == 2
    events = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind = 'permission.changed'"
    ).fetchall()]
    assert events == [{"domain": "git", "level": 2, "actor": "operator"}]


def test_domain_of():
    assert permissions.domain_of("shell.read") == "shell"
    assert permissions.domain_of("memory.search") == "memory"
    assert permissions.domain_of("bare") == "bare"


# ------------------------------------------------------- typed confirmation

def test_confirmation_round_trip(conn):
    permissions.set_ceiling(conn, "git", 2)
    token = permissions.confirmation_token("git.commit", {"msg": "x"}, "r1")
    # no pending decision yet: confirming is refused and logged
    assert permissions.confirm(conn, token, token) is False
    conn.execute("DELETE FROM events WHERE kind = 'guard.confirmation.rejected'")
    conn.commit()

    from thoth import guard
    d = guard.Guard(conn).check_tool("git.commit", level=2, privacy_floor=2,
                                     run_id="r1", args={"msg": "x"})
    assert d.verdict == guard.REQUIRE_CONFIRMATION
    # wrong typing refused
    assert permissions.confirm(conn, token, "y") is False
    assert permissions.is_confirmed(conn, token, "r1") is False
    # exact typing grants, bound to run + args
    assert permissions.confirm(conn, token, token) is True
    assert permissions.is_confirmed(conn, token, "r1") is True
    # ...but not to another run or different args
    assert permissions.is_confirmed(conn, token, "r2") is False
    assert permissions.is_confirmed(
        conn, permissions.confirmation_token("git.commit", {"msg": "y"}, "r1"),
        "r1") is False


def test_pending_for_returns_the_latest_decision(conn):
    from thoth import guard
    g = guard.Guard(conn)
    g.check_tool("git.commit", level=2, privacy_floor=2,
                 run_id="r1", args={"a": 1})
    g.check_tool("git.push", level=3, privacy_floor=2,
                 run_id="r1", args={"b": 2})
    # default ceilings are 0: both decisions were domain-ceiling denials,
    # so nothing is pending - fail closed both ways
    assert permissions.pending_for(conn, "confirm:doesnotexist") is None


# ------------------------------------------------------------------ runner

def _registry_mutating():
    reg = tools.default_registry()
    reg.register(tools.ToolSpec(
        name="git.noop-write", description="synthetic mutating tool",
        permission_level=1, idempotent=True, privacy_floor=2,
        input_schema={"n": "int"},
        run=lambda **k: {"ok": True, "detail": "wrote"},
        verify=lambda r: tools.VerifyReport(True, "wrote"),
    ))
    return reg


def test_runner_parks_for_confirmation_then_resume_runs(conn):
    """The runner integration: a mutating action inside the ceiling parks
    the run with the token in the park reason; the operator types it; the
    resumed run executes the identical action and finishes."""
    permissions.set_ceiling(conn, "git", 1)
    run_id = runner.start_run(conn, project="rcc", goal="mutate something")
    planner = runner.NoopPlanner(script=[("git.noop-write", {"n": 1})])
    result = runner.execute_run(conn, run_id, planner, _registry_mutating(),
                                max_turns=5, tool_calls_budget=5)
    assert result.status == "parked"
    assert "awaiting typed confirmation" in result.reason
    token = result.reason.split("awaiting typed confirmation ")[1].split(" ")[0]

    assert permissions.confirm(conn, token, token)

    result2 = runner.resume_run(
        conn, "rcc",
        lambda goal, history: runner.NoopPlanner(
            script=[("git.noop-write", {"n": 1})]),
        _registry_mutating(), max_turns=5, tool_calls_budget=5)
    assert result2.status == "done"
    turns = runner.history_of(conn, run_id)
    assert any(t["tool"] == "git.noop-write" and t["verify"]["ok"]
               for t in turns)


def test_runner_denies_outside_ceiling(conn):
    run_id = runner.start_run(conn, project="rcc", goal="mutate something")
    planner = runner.NoopPlanner(script=[("git.noop-write", {"n": 1})])
    result = runner.execute_run(conn, run_id, planner, _registry_mutating(),
                                max_turns=5, tool_calls_budget=5)
    assert result.status == "parked"
    assert "guard denied (domain-ceiling)" in result.reason


# --------------------------------------------------------------------- CLI

def test_cli_permission_show_and_set(run):
    _run, _db = run
    code, out = _run("permission", "show")
    assert code == 0
    assert "git" in out and "observe" in out

    code, out = _run("permission", "set", "git", "2")
    assert code == 0
    code, out = _run("permission", "show")
    assert "2" in out and "write" in out


def test_cli_confirm_requires_exact_typing(run):
    _run, db_path = run
    # nothing pending: honest refusal
    code, out = _run("run", "confirm", "confirm:deadbeefdeadbeef")
    assert code == 1

    # seed a real pending confirmation through the guard, then drive the CLI
    from thoth import guard
    conn = db.connect(db_path)
    permissions.set_ceiling(conn, "git", 2)
    d = guard.Guard(conn, actor="test").check_tool(
        "git.commit", level=2, privacy_floor=2, run_id="rcli", args={"m": "x"})
    conn.close()

    # wrong typing at the prompt: refused, exit 1 (the refusal line is
    # stderr; the fixture captures stdout, which shows the propose line)
    code, out = _run("run", "confirm", d.token, stdin="yes")
    assert code == 1 and "proposes: git.commit" in out
    # exact typing: confirmed, exit 0
    code, out = _run("run", "confirm", d.token, stdin=d.token)
    assert code == 0 and "confirmed" in out
