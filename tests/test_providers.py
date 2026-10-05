"""Provider registry + model-backed Planner tests (ADR-004 §2, Team-A §6).

Covers: the structural $0 invariant (non-local providers cannot be enabled),
ladder ordering (local-first, then priority, then name), availability fail-closed,
attempt() always raises in this build, registry duplicate/unknown guards, and the
ModelPlanner parking runs when the ladder is exhausted.
"""

from __future__ import annotations

import json

import pytest

from thoth import db, providers, runner
from thoth.planner_model import ModelPlanner
from thoth.providers import (
    AUTH_API_KEY_FREE,
    AUTH_LOCAL,
    AUTH_SUBSCRIPTION_WEB,
    AvailabilityCache,
    ProviderRegistry,
    ProviderSpec,
    ProviderUnavailable,
    attempt,
    default_registry,
    route,
)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


# ------------------------------------------------------------------ $0 invariant

def test_enabled_cloud_spec_unconstructible():
    with pytest.raises(ValueError, match="\\$0 violation"):
        ProviderSpec(name="evil", auth_type=AUTH_SUBSCRIPTION_WEB,
                     capabilities=frozenset({"plan"}), context_limit=1000,
                     enabled=True)


def test_disabled_cloud_spec_constructible():
    spec = ProviderSpec(name="cloud", auth_type=AUTH_SUBSCRIPTION_WEB,
                        capabilities=frozenset({"plan"}), context_limit=1000,
                        enabled=False)
    assert spec.enabled is False and spec.is_local is False


def test_registry_rejects_enabled_cloud():
    reg = ProviderRegistry()
    with pytest.raises(ValueError, match="\\$0 violation"):
        reg.register(ProviderSpec(name="cloud", auth_type=AUTH_SUBSCRIPTION_WEB,
                                  capabilities=frozenset({"plan"}),
                                  context_limit=1000, enabled=True))


def test_unknown_auth_type_rejected():
    with pytest.raises(ValueError, match="unknown auth_type"):
        ProviderSpec(name="x", auth_type="credit-card",
                     capabilities=frozenset(), context_limit=0)


# ------------------------------------------------------------------ ladder

def test_ladder_local_first_then_priority():
    reg = ProviderRegistry()
    reg.register(ProviderSpec(name="cloud-fast", auth_type=AUTH_SUBSCRIPTION_WEB,
                              capabilities=frozenset({"plan"}), context_limit=100000,
                              priority=1, enabled=False))
    reg.register(ProviderSpec(name="local-slow", auth_type=AUTH_LOCAL,
                              capabilities=frozenset({"plan"}), context_limit=8000,
                              priority=99, enabled=True))
    reg.register(ProviderSpec(name="local-fast", auth_type=AUTH_LOCAL,
                              capabilities=frozenset({"plan"}), context_limit=8000,
                              priority=10, enabled=True))
    ladder = route(reg, "plan")
    assert [s.name for s in ladder] == ["local-fast", "local-slow"]


def test_ladder_filters_capability_and_context():
    reg = default_registry()
    assert [s.name for s in route(reg, "plan")] == ["ollama-local"]
    assert [s.name for s in route(reg, "plan", min_context=32_000)] == []
    assert [s.name for s in route(reg, "transcribe")] == ["whisper-local"]
    assert route(reg, "nonexistent-class") == []


def test_availability_fail_closed():
    reg = default_registry()
    cache = AvailabilityCache()
    # never probed -> unavailable
    assert route(reg, "plan", availability=cache) == []
    cache.mark("ollama-local", ok=False, reason="model not loaded")
    assert route(reg, "plan", availability=cache) == []
    cache.mark("ollama-local", ok=True)
    assert [s.name for s in route(reg, "plan", availability=cache)] == ["ollama-local"]


def test_attempt_always_raises_in_this_build():
    for spec in default_registry().all():
        with pytest.raises(ProviderUnavailable):
            attempt(spec)


# ------------------------------------------------------------------ registry guards

def test_duplicate_registration_rejected():
    reg = ProviderRegistry()
    spec = ProviderSpec(name="a", auth_type=AUTH_LOCAL, capabilities=frozenset({"x"}),
                        context_limit=1)
    reg.register(spec)
    with pytest.raises(ValueError, match="duplicate"):
        reg.register(spec)


def test_unknown_provider_get_raises():
    with pytest.raises(KeyError):
        default_registry().get("nope")


# ------------------------------------------------------------------ planner parking

def _run(conn, planner):
    run_id = runner.start_run(conn, project="rcc", goal="plan something")
    reg = runner_registry()
    return run_id, runner.execute_run(conn, run_id, planner, reg)


def runner_registry():
    from thoth.tools import ToolRegistry, ToolSpec
    r = ToolRegistry()
    r.register(ToolSpec(
        name="noop", description="always succeeds", permission_level=0,
        idempotent=True, privacy_floor=2,
        input_schema={"n": "int"}, run=lambda **k: {"ok": True, "detail": "noop"},
        verify=lambda res: __import__("thoth.tools", fromlist=["VerifyReport"]).VerifyReport(True, "noop"),
    ))
    return r


def test_model_planner_parks_when_ladder_empty(conn):
    run_id, result = _run(conn, ModelPlanner(conn, task_class="nonexistent"))
    assert result.status == "parked"
    assert "no provider" in result.reason


def test_model_planner_parks_when_all_attempts_fail(conn):
    run_id, result = _run(conn, ModelPlanner(conn, task_class="plan"))
    assert result.status == "parked"
    assert "no provider" in result.reason and "ollama-local" in result.reason


# ------------------------------------------------------------------ V3 decision pins

def test_default_planner_model_is_the_v3_decision():
    """The V3 decision record (docs/ROADMAP.md, 2026-09-29; locate amendment
    2026-09-30; fourth verdict 2026-10-05) keeps Qwen2.5-3B-Instruct as the
    planner brain — gate-passing again across all THREE goal families after
    the operator-approved diagnostic lever (file.read misses carry the
    workspace's file names; the model did not change, the harness did).
    A new candidate that passes ALL families re-opens V3: run the matrix
    first, update the record, then this pin."""
    from thoth import ollama
    assert ollama.DEFAULT_MODEL == "qwen2.5:3b-instruct"


def test_local_provider_declares_the_decision():
    """The declared local provider names the selected brain and points at the
    gate that selected it (discoverability without reading the ADRs)."""
    spec = providers.default_registry().get("ollama-local")
    assert "Qwen2.5-3B-Instruct" in spec.description
    assert "matrix-gated" in spec.description
    assert "gate-passing" in spec.description


def test_model_planner_scripted_mode_plans(conn):
    script = [("noop", {}), ("noop", {"n": 2})]  # vary: repeat-breaker parks identical idempotent repeats
    run_id, result = _run(conn, ModelPlanner(conn, script=script))
    assert result.status == "done"
    turns = runner.history_of(conn, run_id)
    assert len(turns) == 2


def test_planner_route_events_recorded(conn):
    run_id, result = _run(conn, ModelPlanner(conn, task_class="plan"))
    events = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind = 'provider.route'").fetchall()]
    assert events and events[0]["candidates"] == ["ollama-local"]
    attempts = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind = 'provider.attempt'").fetchall()]
    assert attempts and attempts[0]["provider"] == "ollama-local"


def test_planner_park_reason_in_run_event(conn):
    _run(conn, ModelPlanner(conn, task_class="plan"))
    park = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind = 'run.parked'").fetchall()]
    assert park and "no provider" in park[0]["reason"]
