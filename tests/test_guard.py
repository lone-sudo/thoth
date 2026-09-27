"""Guard tests (ADR-004): gates, fail-closed behavior, event logging, and the
no-bypass source scan (the CI check lives in the suite — pytest IS the CI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from thoth import db, guard
from thoth.guard import DEFAULT_DATA_CLASS, DENY, Guard, KIND_NETWORK, KIND_PROVIDER
from thoth.tools import default_registry


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture()
def guard_(conn):
    return Guard(conn)


def _guard_events(conn):
    return [
        json.loads(r["payload_json"])
        for r in conn.execute(
            "SELECT payload_json FROM events WHERE kind = 'guard.decision' ORDER BY ts"
        ).fetchall()
    ]


# ------------------------------------------------------------------ tool gate

def test_read_only_tool_within_clearance_allowed(guard_, conn):
    d = guard_.check_tool("memory.search", level=0, privacy_floor=2)
    assert d.allowed and d.rule == "within-clearance"
    events = _guard_events(conn)
    assert len(events) == 1
    assert events[0]["verdict"] == "allow" and events[0]["tool"] == "memory.search"


def test_level_tool_denied_confirmation_not_built(guard_, conn):
    d = guard_.check_tool("git.commit", level=2, privacy_floor=2)
    assert not d.allowed and d.rule == "level-gate"
    assert _guard_events(conn)[0]["verdict"] == "deny"


def test_data_above_tool_clearance_denied(guard_, conn):
    d = guard_.check_tool("shell.read", level=0, privacy_floor=2,
                          data_class=guard.SENSITIVE)
    assert not d.allowed and d.rule == "privacy-ceiling"


def test_sensitive_denied_to_all_current_tools(guard_):
    reg = default_registry()
    for spec in reg.all():
        d = guard_.check_tool(spec.name, level=spec.permission_level,
                              privacy_floor=spec.privacy_floor,
                              data_class=guard.SENSITIVE)
        assert not d.allowed, spec.name


def test_unknown_crossing_fails_closed(guard_, conn):
    d = guard_.check_egress("carrier-pigeon", "somewhere")
    assert not d.allowed and d.rule == "unknown-crossing"


def test_guard_error_fails_closed(guard_, conn):
    # force an internal error: privacy_floor of the wrong type
    d = guard_.check_tool("x", level=0, privacy_floor=None)  # type: ignore[arg-type]
    assert not d.allowed and d.rule == "guard-error"
    assert "fail closed" in d.reason


# ------------------------------------------------------------------ egress

def test_provider_egress_denied(guard_, conn):
    d = guard_.check_egress(KIND_PROVIDER, "api.openai.com")
    assert not d.allowed and d.rule == "no-provider-clients"


def test_network_egress_denied(guard_, conn):
    d = guard_.check_egress(KIND_NETWORK, "https://example.com")
    assert not d.allowed and d.rule == "egress-disabled"


# ------------------------------------------------------------------ event trail

def test_every_decision_is_one_event(guard_, conn):
    guard_.check_tool("file.read", level=0, privacy_floor=2)
    guard_.check_tool("git.push", level=3, privacy_floor=2)
    guard_.check_egress(KIND_NETWORK, "x")
    events = _guard_events(conn)
    assert len(events) == 3
    assert [e["verdict"] for e in events] == ["allow", "deny", "deny"]
    assert all(e["actor"] == "runner" for e in events)


# ------------------------------------------------------------------ CI no-bypass

def _project_src() -> list[Path]:
    """Every module that must contain no network primitives. Exactly two files
    may: guard.py (the gate) and ollama.py (the one I/O module it gates)."""
    root = Path(__file__).resolve().parents[1] / "src" / "thoth"
    allowed = {"guard.py", "ollama.py"}
    return [p for p in root.rglob("*.py") if p.name not in allowed]


def test_no_network_primitives_outside_guard():
    """CI no-bypass check (ADR-004 §2): network-capable primitives may appear
    only in guard.py. urllib/socket/requests have no business anywhere else."""
    forbidden = ("import socket", "import urllib", "import requests",
                 "import http.client", "from urllib", "from socket")
    violations = []
    for path in _project_src():
        src = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in src:
                violations.append(f"{path.name}: {marker}")
    assert violations == [], "network primitives outside guard.py:\n" + "\n".join(violations)


def test_no_provider_imports_outside_guard():
    """The provider client (when it exists) may only be imported by guard.py."""
    for path in _project_src():
        src = path.read_text(encoding="utf-8")
        assert "import openai" not in src and "import anthropic" not in src, path.name


def test_guard_module_uses_no_network_itself():
    """The guard gates; it does not egress. It must not contain live calls."""
    src = (Path(__file__).resolve().parents[1] / "src" / "thoth" / "guard.py").read_text(
        encoding="utf-8")
    assert "urlopen" not in src and "socket.socket" not in src
    assert "subprocess" not in src  # the guard never executes anything either
