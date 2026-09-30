"""Tool protocol tests: registry validation, allowlist, workspace escape, FTS."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from thoth import db, notes, tools


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture()
def registry():
    return tools.default_registry()


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.txt").write_text("hello thoth\n")
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret\n")
    return ws, secret


# ------------------------------------------------------------------ summarizers

def test_summarize_file_names_path_and_head():
    out = tools._summarize_file({"path": "ws/hello.txt", "detail": "11 chars",
                                 "content": "hello thoth\n"})
    assert "hello.txt" in out and "11 chars" in out and "hello thoth" in out


def test_summarize_shell_reports_first_output_line():
    out = tools._summarize_shell({"detail": "exit=0, 11 chars", "output": "alpha\nbeta"})
    assert "alpha" in out and "beta" not in out


def test_summarize_memory_reports_matches_and_misses():
    assert "no notes" in tools._summarize_memory({"detail": "0 notes", "results": []})
    out = tools._summarize_memory({"detail": "2 notes", "results": [
        {"body": "postgres indexes rock"}, {"body": "docker tips"}]})
    assert "2 notes" in out and "postgres" in out


def test_default_summarize_is_detail_then_payload_capped():
    out = tools.default_summarize({"detail": "exit=0", "output": "x" * 300})
    assert out.startswith("exit=0 | ") and len(out) == 200


# ------------------------------------------------------------------ registry

def test_validate_rejects_missing_required(registry):
    with pytest.raises(ValueError):
        registry.validate("file.read", {})


def test_validate_rejects_unknown_tool(registry):
    with pytest.raises(KeyError):
        registry.validate("nope.tool", {})


def test_validate_rejects_wrong_type_and_extras(registry):
    with pytest.raises(ValueError):
        registry.validate("memory.search", {"query": 42})
    with pytest.raises(ValueError):
        registry.validate("memory.search", {"query": "x", "evil": True})
    clean = registry.validate("memory.search", {"query": "x", "limit": 3})
    assert clean == {"query": "x", "limit": 3}


def test_duplicate_registration_rejected(registry):
    spec = registry.get("file.read")
    with pytest.raises(ValueError):
        registry.register(spec)


# ------------------------------------------------------------------ shell.read

def test_shell_allowlist_passes(registry):
    spec = registry.get("shell.read")
    out = spec.run(command="git log --oneline -1", workdir=".")
    assert out["ok"] is True or "not a git repository" in out.get("output", "")


def test_shell_blocks_dangerous_commands(registry):
    spec = registry.get("shell.read")
    for bad in ("rm -rf /", "cat x; rm y", "echo hi && rm y", "git push origin main",
                "cat `whoami`", "ls | grep x"):
        out = spec.run(command=bad)
        assert out["ok"] is False and out.get("blocked"), bad
        assert tools._verify_shell(out).ok is False


# ------------------------------------------------------------------ file.read

def test_file_read_within_workspace(registry, workspace):
    ws, _ = workspace
    spec = registry.get("file.read")
    out = spec.run(path="hello.txt", workspace=str(ws))
    assert out["ok"] and "hello thoth" in out["content"]
    assert tools._verify_file(out).ok is True


def test_file_read_blocks_escape(registry, workspace):
    """Escapes stay blocked — and the verifier now says WHICH failure, so a
    planner can tell an attack from a typo (journal 2026-W39, locate finding)."""
    ws, secret = workspace
    spec = registry.get("file.read")
    out = spec.run(path=str(secret), workspace=str(ws))
    assert out["ok"] is False and out.get("blocked")
    assert out["detail"] == "path escapes the workspace"
    assert tools._verify_file(out).ok is False
    assert tools._verify_file(out).detail == "path escapes the workspace"


def test_file_read_reports_missing_files_honestly(registry, workspace):
    """A missing file is not an escape. The old single message ('path escapes
    workspace or is not a file') read as 'the file is hidden' to a planner
    and likely killed its recovery (locate-family finding, 2026-09-30)."""
    ws, _ = workspace
    spec = registry.get("file.read")
    out = spec.run(path="ghost.txt", workspace=str(ws))
    assert out["ok"] is False and out.get("blocked")
    assert out["detail"] == "not a file in the workspace: ghost.txt"
    v = tools._verify_file(out)
    assert v.ok is False and v.detail == "not a file in the workspace: ghost.txt"
    # a path that merely CONTAINS 'escape-ish' wording still reads honestly
    out2 = spec.run(path="../outside.txt", workspace=str(ws))
    assert "escapes the workspace" in out2["detail"]


# ------------------------------------------------------------------ memory.search

def test_memory_search_finds_notes(conn):
    notes.add(conn, "postgres index btree partial", project="data-eng")
    notes.add(conn, "docker compose healthcheck", project="data-eng")
    spec = tools.default_registry().get("memory.search")
    out = spec.run(query="postgres index", project="data-eng", _conn=conn)
    assert out["ok"] is True
    assert len(out["results"]) == 1
    assert "postgres" in out["results"][0]["body"]


def test_memory_search_respects_superseded(conn):
    n1 = notes.add(conn, "old fact about indexes")
    n2 = notes.add(conn, "new fact about indexes")
    notes.supersede(conn, n1, n2)
    spec = tools.default_registry().get("memory.search")
    out = spec.run(query="fact about indexes", _conn=conn)
    bodies = [r["body"] for r in out["results"]]
    assert "new fact about indexes" in bodies
    assert "old fact about indexes" not in bodies


def test_memory_search_neutralizes_fts_syntax(conn):
    notes.add(conn, "plain body text")
    spec = tools.default_registry().get("memory.search")
    out = spec.run(query='"(weird*)query"', _conn=conn)
    assert out["ok"] is True  # no fts syntax error
