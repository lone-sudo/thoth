"""Ollama client tests (ADR-004 §2): gate-before-bytes, loopback-only, JSON-plan
contract, and the guard-deny path that keeps a stock V1 tree honest (park, not
guessed actions). HTTP is always patched — no test touches a real endpoint."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from thoth import db, ollama
from thoth.guard import Guard
from thoth.ollama import OllamaUnavailable, plan_from_json, probe


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


# ------------------------------------------------------------------ gate-first

def test_probe_allowed_by_local_branch_then_fails_on_unreachable_server(conn):
    """Post local-branch: the guard ALLOWS local: targets; with nothing listening
    (guaranteed: port 1 on loopback) the probe fails at the socket, honestly,
    as (False, reason). Port 1, not the default endpoint — tests must not care
    whether a real local server runs on this machine."""
    ok, reason = probe(conn, endpoint="http://127.0.0.1:1")
    assert ok is False
    assert "probe failed" in reason and "URLError" in reason
    events = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind='guard.decision'").fetchall()]
    assert events and events[0]["verdict"] == "allow"
    assert events[0]["rule"] == "local-egress"


def test_attempt_fails_honestly_without_server(conn):
    """With nothing listening on the (port-1) endpoint, attempt raises
    OllamaUnavailable — degradation, never a guessed action."""
    with pytest.raises(OllamaUnavailable):
        ollama.attempt(conn, "plan something", endpoint="http://127.0.0.1:1")


def test_non_loopback_endpoint_refused(conn):
    with pytest.raises(OllamaUnavailable, match="local means local"):
        probe(conn, endpoint="http://10.0.0.5:11434")


# ------------------------------------------------------------------ parsing

def test_plan_from_json_happy():
    raw = json.dumps({"tool": "shell.read", "args": {"command": "git status"},
                      "summary": "check repo", "next_intent": "diff", "done": False})
    plan = plan_from_json(raw)
    assert plan["tool"] == "shell.read" and plan["done"] is False
    assert plan["args"] == {"command": "git status"}


def test_plan_from_json_fenced_and_prose():
    fenced = "```json\n" + json.dumps({"tool": None, "args": {}, "done": True}) + "\n```"
    assert plan_from_json(fenced)["done"] is True
    prose = 'Here you go: {"tool": null, "args": {}, "done": true} hope it helps'
    assert plan_from_json(prose)["done"] is True


def test_plan_from_json_rejects_garbage():
    for bad in ["", "no json", '{"tool": 42}', '{"args": "not-an-object"}', "[1,2]"]:
        with pytest.raises(OllamaUnavailable):
            plan_from_json(bad)


# ------------------------------------------------------------------ happy path (patched guard + HTTP)

class _AllowGuard:
    """Stand-in for the post-ADR-004-branch Guard: local provider egress allowed."""

    def __init__(self, conn, actor="test"):
        pass

    def check_egress(self, kind, target, data_class=0):
        class D:
            allowed = True
            reason = "local provider allowed (test double)"
            rule = "local-egress"
        return D()


def _fake_chat(content: str):
    def _fake(endpoint, path, payload):
        assert path == "/api/chat"
        return {"message": {"content": content}}
    return _fake


def test_attempt_happy_path_with_patched_guard(conn, monkeypatch):
    monkeypatch.setattr(ollama, "_guarded", lambda conn, target: None)
    raw = json.dumps({"tool": "file.read", "args": {"path": "a.txt"},
                      "summary": "read it", "next_intent": "verify", "done": False})
    with patch.object(ollama, "_post_json", _fake_chat(raw)):
        answer = ollama.attempt(conn, "read a.txt")
    plan = plan_from_json(answer)
    assert plan["tool"] == "file.read"


def test_probe_happy_path_with_patched_guard(conn, monkeypatch):
    monkeypatch.setattr(ollama, "_guarded", lambda conn, target: None)
    with patch.object(ollama, "_post_json",
                      lambda endpoint, path, payload: {"models": [{"name": "qwen2.5:3b-instruct"}]}):
        ok, reason = probe(conn)
    assert ok is True and "qwen2.5" in reason


def test_probe_empty_model_list_is_unavailable(conn, monkeypatch):
    monkeypatch.setattr(ollama, "_guarded", lambda conn, target: None)
    with patch.object(ollama, "_post_json", lambda endpoint, path, payload: {"models": []}):
        ok, reason = probe(conn)
    assert ok is False and "no models" in reason


# ------------------------------------------------- parser robustness (live-detour)

def test_plan_from_json_tolerates_trailing_prose():
    """Small local models emit JSON + rambling; the first JSON object is the plan
    (caught live: SmolLM2-135M on llama-server, journal 2026-W39)."""
    plan = plan_from_json(
        '{"tool": "file.read", "args": {"path": "README.md"}, '
        '"summary": "read it", "next_intent": "finish", "done": false}\n'
        'I will now read the README file as requested.')
    assert plan["tool"] == "file.read"
    assert plan["done"] is False


def test_plan_from_json_still_rejects_leading_prose_without_object():
    plan = plan_from_json('Sure! {"tool": null, "args": {}, "done": true}')
    assert plan["done"] is True
    with pytest.raises(OllamaUnavailable):
        plan_from_json("no object here at all")


# ------------------------------------------------------------------ CI whitelist integrity

def test_urllib_only_in_ollama_module():
    """The no-bypass scan's whitelist is exactly one file: ollama.py."""
    from pathlib import Path
    src_root = Path(__file__).resolve().parents[1] / "src" / "thoth"
    offenders = []
    for p in src_root.rglob("*.py"):
        if p.name in ("ollama.py",):
            continue
        text = p.read_text(encoding="utf-8")
        if "urllib.request" in text or "urlopen" in text:
            offenders.append(p.name)
    assert offenders == []
