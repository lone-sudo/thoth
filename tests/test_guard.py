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


def test_level_tool_denied_by_default_table(guard_, conn):
    """No row in the permission table = ceiling 0 = inert by default (ADR-004
    section 5): a mutating tool ships unable to run until the operator acts."""
    d = guard_.check_tool("git.commit", level=2, privacy_floor=2)
    assert not d.allowed and d.rule == "domain-ceiling"
    assert _guard_events(conn)[0]["verdict"] == "deny"


def test_mutating_within_ceiling_requires_typed_confirmation(guard_, conn):
    """The full handshake: raise the ceiling -> require_confirmation with a
    token -> operator types it -> the identical action unlocks for this run."""
    from thoth import permissions
    permissions.set_ceiling(conn, "git", 2)
    d = guard_.check_tool("git.commit", level=2, privacy_floor=2,
                          run_id="r1", args={"msg": "x"})
    assert d.verdict == guard.REQUIRE_CONFIRMATION and d.token
    assert not d.allowed
    # the pending decision event carries the token and the run binding
    pending = permissions.pending_for(conn, d.token)
    assert pending["run_id"] == "r1" and pending["tool"] == "git.commit"
    # typing the token unlocks exactly this (run, tool, args)
    assert permissions.confirm(conn, d.token, d.token)
    d2 = guard_.check_tool("git.commit", level=2, privacy_floor=2,
                           run_id="r1", args={"msg": "x"})
    assert d2.allowed and d2.rule == "operator-confirmed"
    # a different run does NOT inherit the grant (different token)
    d3 = guard_.check_tool("git.commit", level=2, privacy_floor=2,
                           run_id="r2", args={"msg": "x"})
    assert d3.verdict == guard.REQUIRE_CONFIRMATION


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
    assert not d.allowed and d.rule == "cloud-egress-denied"


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
    """Every module that must contain no network primitives. Exactly three files
    may: guard.py (the gate), ollama.py (providers), telegram.py (the surface
    it gates — ADR-005)."""
    root = Path(__file__).resolve().parents[1] / "src" / "thoth"
    allowed = {"guard.py", "ollama.py", "telegram.py"}
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


# ------------------------------------------------- surface branch (ADR-005)

def test_surface_egress_exact_host_allowed(conn):
    g = Guard(conn, actor="test")
    d = g.check_egress("surface", "https://api.telegram.org/bot123:AA/getUpdates")
    assert d.allowed
    assert d.rule == "surface-endpoint-allowlist"


def test_surface_egress_lookalike_hosts_denied(conn):
    g = Guard(conn, actor="test")
    for target in (
        "https://api.telegram.org.evil.io/botX/getMe",
        "https://api.telegram-org.com/botX/getMe",
        "https://evil.io/api.telegram.org/botX/getMe",
        "http://api.telegram.org/botX/getMe",      # plaintext downgraded
        "https://telegram.org/botX/getMe",         # wrong host entirely
    ):
        d = g.check_egress("surface", target)
        assert not d.allowed, target


def test_surface_egress_unknown_kind_fails_closed(conn):
    g = Guard(conn, actor="test")
    d = g.check_egress("carrier-pigeon", "https://api.telegram.org/x")
    assert not d.allowed


def test_telegram_imports_no_registry_or_runner():
    """ADR-005 §4: the surface relays decisions; it never grants or makes them."""
    src = (Path(__file__).resolve().parents[1] / "src" / "thoth" / "telegram.py").read_text(
        encoding="utf-8")
    assert "ToolRegistry" not in src and "default_registry" not in src
    assert "from .runner" not in src and "import runner" not in src
