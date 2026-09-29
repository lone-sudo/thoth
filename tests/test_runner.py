"""Runner loop tests (ADR-003 §1, §2, §4): turns, bounds, parking, resume."""

from __future__ import annotations

import pytest

from thoth import db, notes, runner, tools


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
    """Scripted no-work finishes are a designed V0.2 pattern (the CLI's
    execute/resume planners) — they opt out of the finish floor explicitly."""
    run_id = runner.start_run(conn, project="rcc", goal="nothing to do")
    result = runner.execute_run(conn, run_id, runner.NoopPlanner(), _registry_ok(),
                                allow_finish_without_turns=True)
    assert result.status == "done"


# ------------------------------------------------------------------ finish floor

def test_finish_floor_refuses_done_without_verified_turns(conn):
    """The measured qwen2.5-0.5b failure (journal 2026-W39): a model claims
    done having acted zero times. The runner refuses: diagnostic park, an
    honest event, no run.completed with an unearned summary."""

    class InstantDonePlanner:
        def decide(self, context, history):
            return runner.Plan(tool=None, done=True, summary="all done, trust me")

    run_id = runner.start_run(conn, project="rcc", goal="claim without doing")
    result = runner.execute_run(conn, run_id, InstantDonePlanner(), _registry_ok())
    assert result.status == "parked"
    assert result.reason == runner.FINISH_FLOOR_REASON
    refused = conn.execute(
        "SELECT payload_json FROM events WHERE kind='run.finish.refused'").fetchall()
    assert len(refused) == 1
    import json
    payload = json.loads(refused[0]["payload_json"])
    assert payload["claimed_summary"] == "all done, trust me"
    assert payload["verified_turns"] == 0
    # the run is parked, not done
    row = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row["status"] == "parked"
    assert not conn.execute(
        "SELECT 1 FROM events WHERE kind='run.completed'").fetchone()


def test_finish_floor_allows_finish_after_a_verified_turn(conn):
    """Act first, then finish: the normal honest loop is untouched."""
    run_id = runner.start_run(conn, project="rcc", goal="work then finish")
    planner = runner.NoopPlanner(script=[("noop", {})])
    result = runner.execute_run(conn, run_id, planner, _registry_ok())
    assert result.status == "done"
    assert len(runner.history_of(conn, run_id)) == 1


def test_finish_floor_counts_verified_turns_across_resume(conn):
    """Resume-safe: a run that already acted in a prior episode can finish on
    resume WITHOUT the opt-out — the floor counts across episodes."""
    run_id = runner.start_run(conn, project="rcc", goal="act, crash, resume, finish")
    reg = _registry_ok()

    class ActThenCrash:
        def __init__(self):
            self.calls = 0

        def decide(self, context, history):
            self.calls += 1
            if self.calls == 1:
                return runner.Plan(tool="noop", args={"n": 1}, summary="go")
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        runner.execute_run(conn, run_id, ActThenCrash(), reg,
                           max_turns=50, tool_calls_budget=50)
    conn.execute("UPDATE runs SET status='parked' WHERE id=?", (run_id,))
    conn.commit()

    result = runner.resume_run(
        conn, "rcc", lambda goal, history: runner.NoopPlanner(), reg,
        max_turns=50, tool_calls_budget=50)
    assert result.status == "done"   # prior verified turn satisfies the floor

    # negative control: a fresh run with zero turns still refuses
    run_id2 = runner.start_run(conn, project="rcc", goal="fresh claim")
    result2 = runner.execute_run(conn, run_id2, runner.NoopPlanner(), reg)
    assert result2.status == "parked"
    assert result2.reason == runner.FINISH_FLOOR_REASON


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


# ------------------------------------------------------------------ observations

def test_checkpoint_carries_semantic_observation(conn):
    """Every turn checkpoint carries WHAT happened (default_summarize floor) —
    the planner self-terminates on semantic result lines, never raw digests."""
    run_id = runner.start_run(conn, project="rcc", goal="be observable")
    runner.execute_run(conn, run_id, runner.NoopPlanner(script=[("noop", {})]),
                       _registry_ok())
    turn = runner.history_of(conn, run_id)[0]
    assert turn["output"]


def test_memory_search_works_inside_run(conn):
    """Regression: the runner now passes _conn, so memory.search is usable
    inside a run (it silently failed verify before the wiring fix)."""
    notes.add(conn, "postgres indexes rock", project="rcc")
    run_id = runner.start_run(conn, project="rcc", goal="find the note")
    result = runner.execute_run(
        conn, run_id,
        runner.NoopPlanner(script=[("memory.search", {"query": "postgres"})]),
        tools.default_registry())
    assert result.status == "done"
    turn = runner.history_of(conn, run_id)[0]
    assert turn["verify"]["ok"]
    assert "postgres" in turn["output"].lower()


# ------------------------------------------------------------------ repeat-breaker

class FinishCheckPlanner:
    """NoopPlanner + scripted finish_check answers (the probe contract is:
    called once with (goal, history); a done Plan completes the run, None parks)."""

    def __init__(self, script, answers):
        self._nop = runner.NoopPlanner(script=script)
        self.answers = list(answers)
        self.probes: list[tuple[str, list]] = []

    def decide(self, context, history):
        return self._nop.decide(context, history)

    def finish_check(self, goal, history):
        self.probes.append((goal, list(history)))
        return self.answers.pop(0) if self.answers else None


def test_repeat_breaker_finish_check_completes(conn):
    """Probe says yes -> graceful finish instead of a diagnostic park."""
    run_id = runner.start_run(conn, project="rcc", goal="read the readme")
    planner = FinishCheckPlanner(
        script=[("noop", {}), ("noop", {})],
        answers=[runner.Plan(tool=None, done=True, summary="goal achieved")])
    result = runner.execute_run(conn, run_id, planner, _registry_ok(),
                                max_turns=10, tool_calls_budget=10)
    assert result.status == "done"
    assert result.reason == "goal achieved"
    assert len(planner.probes) == 1
    goal, hist = planner.probes[0]
    assert goal == "read the readme" and len(hist) == 1


def test_repeat_breaker_finish_check_decline_parks(conn):
    """Probe says no -> the diagnostic park stands (one probe, no looping)."""
    run_id = runner.start_run(conn, project="rcc", goal="not done yet")
    planner = FinishCheckPlanner(script=[("noop", {}), ("noop", {})], answers=[None])
    result = runner.execute_run(conn, run_id, planner, _registry_ok(),
                                max_turns=10, tool_calls_budget=10)
    assert result.status == "parked"
    assert "repeat-breaker" in result.reason
    assert len(planner.probes) == 1

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
