"""Runner loop tests (ADR-003 §1, §2, §4): turns, bounds, parking, resume."""

from __future__ import annotations

import pytest

from thoth import db, runner, tools


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def _registry_ok():
    reg = tools.default_registry()
    # a bulletproof tool for happy-path tests; optional "n" param lets tests
    # vary args (repeat-breaker and digest-distinctness scenarios)
    reg.register(tools.ToolSpec(
        name="noop", description="always succeeds", permission_level=0,
        idempotent=True, privacy_floor=0, input_schema={"n": "int"},
        run=lambda **k: {"ok": True, "detail": "noop"},
        verify=lambda r: tools.VerifyReport(True, "noop"),
    ))
    return reg


# ------------------------------------------------------------------ happy path

def test_run_completes_via_noop_planner(conn):
    run_id = runner.start_run(conn, project="rcc", goal="probe the loop")
    planner = runner.NoopPlanner(script=[("noop", {}), ("noop", {"n": 2})])
    result = runner.execute_run(conn, run_id, planner, _registry_ok())
    assert result.status == "done"
    turns = runner.history_of(conn, run_id)
    assert len(turns) == 2
    assert all(t["verify"]["ok"] for t in turns)


def test_empty_script_finishes_immediately(conn):
    run_id = runner.start_run(conn, project="rcc", goal="nothing to do")
    result = runner.execute_run(conn, run_id, runner.NoopPlanner(), _registry_ok())
    assert result.status == "done"


# ------------------------------------------------------------------ bounds

def test_max_turns_parks_the_run(conn):
    run_id = runner.start_run(conn, project="rcc", goal="loop forever")
    reg = _registry_ok()

    class EndlessPlanner:
        # args vary per call: the repeat-breaker (identical idempotent repeats)
        # would park first, and this test's subject is the max-turns bound.
        def __init__(self):
            self.calls = 0

        def decide(self, context, history):
            self.calls += 1
            return runner.Plan(tool="noop", args={"n": self.calls}, summary="go")

    result = runner.execute_run(conn, run_id, EndlessPlanner(), reg, max_turns=5,
                                tool_calls_budget=100)
    assert result.status == "parked"
    assert "max turns" in result.reason


def test_budget_parks_the_run(conn):
    run_id = runner.start_run(conn, project="rcc", goal="loop forever")
    reg = _registry_ok()

    class EndlessPlanner:
        # args vary per call: see the repeat-breaker note in the max-turns test.
        def __init__(self):
            self.calls = 0

        def decide(self, context, history):
            self.calls += 1
            return runner.Plan(tool="noop", args={"n": self.calls}, summary="go")

    result = runner.execute_run(conn, run_id, EndlessPlanner(), reg, max_turns=100,
                                tool_calls_budget=3)
    assert result.status == "parked"
    assert "budget" in result.reason


def test_deadline_parks_the_run(conn):
    run_id = runner.start_run(conn, project="rcc", goal="too slow")
    result = runner.execute_run(conn, run_id, runner.NoopPlanner(), _registry_ok(),
                                deadline="2020-01-01T00:00:00Z")
    assert result.status == "parked"
    assert "deadline" in result.reason


# ------------------------------------------------------------------ verify failures

def test_consecutive_verify_failures_park(conn):
    run_id = runner.start_run(conn, project="rcc", goal="flaky tool")

    reg = tools.default_registry()
    reg.register(tools.ToolSpec(
        name="always_fails", description="broken", permission_level=0,
        idempotent=True, privacy_floor=0, input_schema={},
        run=lambda **k: {"ok": False, "detail": "broken"},
        verify=lambda r: tools.VerifyReport(False, "broken"),
    ))

    class StubbornPlanner:
        def decide(self, context, history):
            return runner.Plan(tool="always_fails", args={}, summary="try")

    result = runner.execute_run(conn, run_id, StubbornPlanner(), reg)
    assert result.status == "parked"
    assert "verify failed" in result.reason


def test_invalid_tool_args_park_not_crash(conn):
    run_id = runner.start_run(conn, project="rcc", goal="bad args")
    result = runner.execute_run(conn, run_id,
                                runner.NoopPlanner(script=[("file.read", {})]),
                                tools.default_registry())
    assert result.status == "parked"
    assert "invalid tool args" in result.reason


# ------------------------------------------------------------------ checkpoint

def test_checkpoint_contains_bounds_and_context_sizes(conn):
    run_id = runner.start_run(conn, project="rcc", goal="audit me")
    runner.execute_run(conn, run_id, runner.NoopPlanner(script=[("noop", {})]),
                       _registry_ok())
    turn = runner.history_of(conn, run_id)[0]
    assert turn["bounds"]["max_turns"] == 25
    assert turn["bounds"]["tool_calls_used"] == 1
    assert set(runner.SECTION_BUDGETS) == set(turn["context_sections"])


# ------------------------------------------------------------------ resume

def test_resume_carries_bounds_and_completes(conn):
    run_id = runner.start_run(conn, project="rcc", goal="park me then resume")
    reg = _registry_ok()

    class FailOnce:
        def __init__(self):
            self.calls = 0

        def decide(self, context, history):
            self.calls += 1
            if self.calls <= 4:
                # varying args: identical consecutive idempotent repeats are
                # parked by the repeat-breaker before this crash can happen
                return runner.Plan(tool="noop", args={"n": self.calls}, summary="go")
            raise KeyboardInterrupt  # simulate mid-run crash after 4 turns

    with pytest.raises(KeyboardInterrupt):
        runner.execute_run(conn, run_id, FailOnce(), reg, max_turns=50,
                           tool_calls_budget=50)

    # simulate the park (a real crash leaves 'running'; mark parked like the
    # recovery path does)
    conn.execute("UPDATE runs SET status='parked' WHERE id=?", (run_id,))
    conn.commit()

    # resume with a planner that finishes immediately — total turns must
    # respect the *carried* budget from checkpoint, not fresh defaults
    result = runner.resume_run(
        conn, "rcc",
        lambda goal, history: runner.NoopPlanner(script=[("noop", {})]),
        reg, max_turns=99, tool_calls_budget=99,
    )
    assert result.status == "done"
    # 4 turns happened pre-crash; bounds carried from checkpoint (50), so the
    # resumed turn is allowed.
    assert len(runner.history_of(conn, run_id)) == 5


# ------------------------------------------------------------------ repeat-breaker

def test_repeat_breaker_parks_identical_idempotent_repeat(conn):
    """Live finding (journal 2026-W39): a model loops forever on a successful
    pointless action. Two identical idempotent turns in one episode -> park
    before the second executes."""
    run_id = runner.start_run(conn, project="rcc", goal="loop on success")
    planner = runner.NoopPlanner(script=[("noop", {}), ("noop", {})])
    result = runner.execute_run(conn, run_id, planner, _registry_ok(),
                                max_turns=10, tool_calls_budget=10)
    assert result.status == "parked"
    assert "repeat-breaker" in result.reason
    turns = runner.history_of(conn, run_id)
    assert len(turns) == 1  # the second identical call never executed


def test_repeat_breaker_allows_non_idempotent_and_varying_args(conn):
    run_id = runner.start_run(conn, project="rcc", goal="same call, non-idempotent")
    reg = _registry_ok()
    reg.register(tools.ToolSpec(
        name="tick", description="not idempotent", permission_level=0,
        idempotent=False, privacy_floor=0, input_schema={},
        run=lambda **k: {"ok": True, "detail": "tick"},
        verify=lambda r: tools.VerifyReport(True, "tick")))
    result = runner.execute_run(conn, run_id,
                                runner.NoopPlanner(script=[("tick", {}), ("tick", {})]),
                                reg, max_turns=10, tool_calls_budget=10)
    assert result.status == "done"

    run_id2 = runner.start_run(conn, project="rcc", goal="varying args")
    result2 = runner.execute_run(
        conn, run_id2,
        runner.NoopPlanner(script=[("noop", {"n": 1}), ("noop", {"n": 2})]),
        _registry_ok(), max_turns=10, tool_calls_budget=10)
    assert result2.status == "done"


def test_repeat_breaker_fresh_per_episode(conn):
    """Deliberately re-running the same action across a resume is a designed
    ADR-003 pattern (continuation); the breaker must not trip cross-episode."""
    run_id = runner.start_run(conn, project="rcc", goal="resume continuation")
    reg = _registry_ok()
    runner.execute_run(conn, run_id,
                       runner.NoopPlanner(script=[("noop", {})]),
                       reg, max_turns=50, tool_calls_budget=50)
    conn.execute("UPDATE runs SET status='parked' WHERE id=?", (run_id,))
    conn.commit()
    result = runner.resume_run(conn, "rcc",
                               lambda goal, history: runner.NoopPlanner(script=[("noop", {})]),
                               reg, max_turns=50, tool_calls_budget=50)
    assert result.status == "done"  # same action as the prior episode: allowed


def test_resume_without_parked_run_raises(conn):
    with pytest.raises(ValueError):
        runner.resume_run(conn, None, lambda g, h: runner.NoopPlanner(),
                          tools.default_registry())


# ------------------------------------------------------------------ context package

def test_context_package_respects_budgets(conn):
    from thoth import notes as notes_mod
    for i in range(30):
        notes_mod.add(conn, f"filler note number {i} about postgres indexes",
                      project="rcc")
    run_id = runner.start_run(conn, project="rcc", goal="postgres indexes")
    context, sizes = runner.build_context(conn, "rcc", "postgres indexes", run_id, [])
    assert all(sizes[k] <= runner.SECTION_BUDGETS[k] for k in sizes)
    assert "identity" in context and "goal: postgres indexes" in context


def test_context_window_shows_recent_turns(conn):
    run_id = runner.start_run(conn, project="rcc", goal="windowed")
    runner.execute_run(conn, run_id, runner.NoopPlanner(script=[("noop", {})]),
                       _registry_ok())
    history = runner.history_of(conn, run_id)
    context, _ = runner.build_context(conn, "rcc", "windowed", run_id, history)
    assert "turn 1" in context
