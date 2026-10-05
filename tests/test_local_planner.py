"""Local-allow branch + live planner path (guard *and* registry enforced).

HTTP is always patched at the urllib boundary; the Guard, the ladder, the
registry validation, and the event trail are all real.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from thoth import db, guard, ollama, providers, runner
from thoth.guard import Guard, KIND_NETWORK, KIND_PROVIDER
from thoth.planner_model import ModelPlanner
from thoth.tools import ToolRegistry, ToolSpec, VerifyReport


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def _tool_registry():
    r = ToolRegistry()
    r.register(ToolSpec(
        name="noop", description="always succeeds", permission_level=0,
        idempotent=True, privacy_floor=2, input_schema={},
        run=lambda **k: {"ok": True, "detail": "noop"},
        verify=lambda res: VerifyReport(True, "noop"),
    ))
    return r


def _patch_chat(content: str, monkeypatch):
    monkeypatch.setattr(ollama, "_post_json",
                        lambda endpoint, path, payload: {"message": {"content": content}})


def _plan_json(**over):
    base = {"tool": "noop", "args": {}, "summary": "do noop",
            "next_intent": "finish", "done": False}
    base.update(over)
    return json.dumps(base)


# ------------------------------------------------------------------ guard branch

def test_local_provider_target_allowed(conn):
    d = Guard(conn).check_egress(KIND_PROVIDER, "local:ollama@http://127.0.0.1:11434")
    assert d.allowed and d.rule == "local-egress"
    events = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind='guard.decision'").fetchall()]
    assert events[0]["verdict"] == "allow" and events[0]["target"].startswith("local:")


def test_cloud_provider_still_denied(conn):
    d = Guard(conn).check_egress(KIND_PROVIDER, "api.openai.com")
    assert not d.allowed and d.rule == "cloud-egress-denied"


def test_generic_network_still_denied(conn):
    d = Guard(conn).check_egress(KIND_NETWORK, "https://example.com")
    assert not d.allowed and d.rule == "egress-disabled"


def test_local_prefix_not_spoofable_by_whitespace(conn):
    d = Guard(conn).check_egress(KIND_PROVIDER, " local:ollama@http://127.0.0.1:1")
    assert not d.allowed  # startswith is exact; leading space is not local


# ------------------------------------------------------------------ planner live path

def test_full_ladder_goal_to_tool_call(conn, monkeypatch):
    """The headline: goal in, validated tool-call Plan out, all real except HTTP."""
    _patch_chat(_plan_json(), monkeypatch)
    planner = ModelPlanner(conn, tool_registry=_tool_registry())
    plan = planner.decide("CONTEXT: probe", history=[])
    assert plan.tool == "noop" and plan.args == {}
    events = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind = 'provider.outcome'").fetchall()]
    assert any(e["outcome"] == "ok" and e["provider"] == "ollama-local" for e in events)
    assert any("local-egress" == e["rule"] for e in
               [json.loads(r["payload_json"]) for r in conn.execute(
                   "SELECT payload_json FROM events WHERE kind='guard.decision'").fetchall()])


def test_planner_finishes_when_model_says_done(conn, monkeypatch):
    _patch_chat(_plan_json(done=True, tool=None, summary="nothing to do"), monkeypatch)
    planner = ModelPlanner(conn, tool_registry=_tool_registry())
    plan = planner.decide("CONTEXT: x", history=[])
    assert plan.done and plan.tool is None


def test_model_invalid_tool_parks_run(conn, monkeypatch):
    """Model invents a tool -> registry rejects -> ladder continues -> park."""
    _patch_chat(_plan_json(tool="delete_everything", args={}), monkeypatch)
    run_id = runner.start_run(conn, project="rcc", goal="be evil")
    result = runner.execute_run(conn, run_id, ModelPlanner(conn, tool_registry=_tool_registry()),
                                _tool_registry())
    assert result.status == "parked" and "no provider" in result.reason
    outcomes = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind='provider.outcome'").fetchall()]
    assert outcomes and outcomes[-1]["outcome"] == "error"
    assert "invalid action" in outcomes[-1]["reason"]


def test_model_invalid_args_park(conn, monkeypatch):
    # noop accepts no args; model sends one
    _patch_chat(_plan_json(args={"evil": 1}), monkeypatch)
    run_id = runner.start_run(conn, project="rcc", goal="sneak args")
    result = runner.execute_run(conn, run_id, ModelPlanner(conn, tool_registry=_tool_registry()),
                                _tool_registry())
    assert result.status == "parked" and "no provider" in result.reason


def test_cloud_only_ladder_parks_without_attempts(conn, monkeypatch):
    """Disabled cloud providers never reach the ladder: route() excludes them,
    the planner raises 'ladder empty', and NO attempt/outcome events exist for
    cloud names. Cloud is structurally unreachable, not merely failing."""
    reg = providers.ProviderRegistry()
    reg.register(providers.ProviderSpec(
        name="claude-subscription", auth_type=providers.AUTH_SUBSCRIPTION_WEB,
        capabilities=frozenset({"plan"}), context_limit=200_000, priority=10,
        enabled=False))
    planner = ModelPlanner(conn, registry=reg, task_class="plan")
    with pytest.raises(runner.PlannerUnavailable, match="ladder empty"):
        planner.decide("CONTEXT: x", history=[])
    events = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind LIKE 'provider.%'").fetchall()]
    assert not any(e.get("provider") == "claude-subscription" for e in events), str(events)
    assert any(e.get("candidates") == [] for e in events)  # route event proves it


def test_ollama_down_parks(conn, monkeypatch):
    """Local allowed by guard but server unreachable -> honest park."""
    import urllib.error

    def _raise(endpoint, path, payload):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ollama, "_post_json", _raise)
    run_id = runner.start_run(conn, project="rcc", goal="probe down")
    result = runner.execute_run(conn, run_id, ModelPlanner(conn, tool_registry=_tool_registry()),
                                _tool_registry())
    assert result.status == "parked" and "no provider" in result.reason


# ------------------------------------------------- capability-gated menu

def test_menu_hides_tools_above_operator_ceiling(conn):
    """The action menu advertises only tools the ceiling permits: level-0
    tools always show; a mutating tool above its domain ceiling is absent -
    advertising an unrunnable tool parked runs on hallucinated calls and
    (measured, n=20) collapsed the planner's locate behavior. The menu is
    presentation, never policy: the guard stays the authority."""
    from thoth import permissions, tools as tools_mod
    planner = ModelPlanner(conn, task_class="plan",
                           tool_registry=tools_mod.default_registry())
    prompt = planner._build_prompt("CONTEXT", None)
    for shown in ("- shell.read:", "- file.read:", "- memory.search:"):
        assert shown in prompt, shown
    for hidden in ("- file.write:", "- git.branch_create:", "- git.checkout:",
                   "- git.commit:"):
        assert hidden not in prompt, hidden


def test_menu_reveals_tools_when_ceiling_rises(conn):
    from thoth import permissions, tools as tools_mod
    permissions.set_ceiling(conn, "file", 1)
    permissions.set_ceiling(conn, "git", 2)
    planner = ModelPlanner(conn, task_class="plan",
                           tool_registry=tools_mod.default_registry())
    prompt = planner._build_prompt("CONTEXT", None)
    for shown in ("- file.write:", "- git.branch_create:",
                  "- git.checkout:", "- git.commit:"):
        assert shown in prompt, shown


def test_menu_never_shows_anything_above_ceiling(conn):
    """Belt over braces: for EVERY registered tool, menu presence implies
    ceiling coverage. Registry grows, menu stays honest automatically."""
    from thoth import permissions, tools as tools_mod
    planner = ModelPlanner(conn, task_class="plan",
                           tool_registry=tools_mod.default_registry())
    prompt = planner._build_prompt("CONTEXT", None)
    for spec in tools_mod.default_registry().all():
        advertised = f"- {spec.name}:" in prompt
        permitted = (spec.permission_level <= 0 or
                     permissions.ceiling(
                         conn, permissions.domain_of(spec.name))
                     >= spec.permission_level)
        assert advertised == permitted, spec.name
