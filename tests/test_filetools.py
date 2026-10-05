"""Tests for file.write (filetools.py): the AI's first content producer.

Design pins under test:
- workspace-scoped and create-only, enforced in TOOL CODE at any permission
  ceiling (the same discipline as the git branch protocol's thoth/* rule);
- ships INERT: the file domain's default ceiling is 0, so the guard denies
  until the operator raises it and types the per-action token;
- verifier evidence is disk truth: a byte-for-byte readback, pinned by verify.
"""

from __future__ import annotations

import pytest

from thoth import filetools, guard, permissions, runner, tools


@pytest.fixture()
def conn(tmp_path):
    from thoth import db
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture()
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w


# ------------------------------------------------------------- the registry

def test_registry_pin_levels_and_flags():
    reg = tools.default_registry()
    by_name = {t.name: t for t in reg.all()}
    assert "file.write" in by_name
    spec = by_name["file.write"]
    assert spec.permission_level == 1          # reversible-write class
    assert spec.idempotent is True             # create-only: re-run refuses, disk state identical
    assert spec.privacy_floor == guard.PRIVATE
    assert spec.required == {"path", "content"}
    assert permissions.domain_of("file.write") == "file"  # shares file.read's domain


# --------------------------------------------------- create-only + scoping

def test_write_creates_new_file_with_readback_evidence(ws):
    result = filetools._run_write("report.md", "hello thoth", workspace=str(ws))
    assert result["ok"] is True
    assert result["readback_matches"] is True
    assert result["bytes"] == len(b"hello thoth")
    report = filetools._verify_write(result)
    assert report.ok and "readback ok" in report.detail
    # disk truth: the file exists with exactly the payload
    assert (ws / "report.md").read_text(encoding="utf-8") == "hello thoth"


def test_write_refuses_to_overwrite(ws):
    (ws / "exists.txt").write_text("operator content", encoding="utf-8")
    result = filetools._run_write("exists.txt", "replacement", workspace=str(ws))
    assert result["ok"] is False and result.get("blocked") is True
    assert "refusing to overwrite" in result["detail"]
    # the operator's content is untouched - and verify says so honestly
    assert (ws / "exists.txt").read_text(encoding="utf-8") == "operator content"
    assert not filetools._verify_write(result).ok


def test_write_blocks_path_escape(ws, tmp_path):
    secret = tmp_path / "outside.txt"
    result = filetools._run_write("../outside.txt", "exfil", workspace=str(ws))
    assert result["ok"] is False and result.get("blocked") is True
    assert result["detail"] == "path escapes the workspace"
    assert not secret.exists()


def test_write_requires_existing_parent_dir(ws):
    result = filetools._run_write("sub/dir/file.txt", "x", workspace=str(ws))
    assert result["ok"] is False and result.get("blocked") is True
    assert "parent directory does not exist" in result["detail"]
    assert not (ws / "sub").exists()


def test_write_validates_content_and_path(ws):
    for args in ({"path": "", "content": "x"},
                 {"path": "ok.txt", "content": ""},
                 {"path": "ok.txt", "content": None},
                 {"path": None, "content": "x"}):
        result = filetools._run_write(workspace=str(ws), **args)
        assert result["ok"] is False and result.get("blocked") is True, args
        assert not list(ws.iterdir()), args


def test_write_caps_payload_size(ws):
    result = filetools._run_write("big.txt", "x" * (filetools._MAX_WRITE_BYTES + 1),
                                  workspace=str(ws))
    assert result["ok"] is False and result.get("blocked") is True
    assert "content too large" in result["detail"]
    assert not list(ws.iterdir())


def test_write_overwrite_refusal_is_not_a_broken_state(ws):
    """A failed create leaves nothing behind: the next legal write works."""
    (ws / "note.txt").write_text("v1", encoding="utf-8")
    first = filetools._run_write("note.txt", "v2", workspace=str(ws))
    assert first["ok"] is False
    second = filetools._run_write("other.txt", "v2", workspace=str(ws))
    assert second["ok"] is True and filetools._verify_write(second).ok


# ---------------------------------------------- protocol holds at ceiling 4

def test_write_rules_hold_at_any_permission_level(conn, ws):
    """The design pin, mirroring the git protocol test: raising the file
    ceiling licenses writes INSIDE the tool's rules, never around them."""
    permissions.set_ceiling(conn, "file", 4)  # unrestricted
    for args in ({"path": "../escape.txt", "content": "x"},
                 {"path": "present.txt", "content": "x"}):
        (ws / "present.txt").write_text("exists", encoding="utf-8")
        result = filetools._run_write(workspace=str(ws), **args)
        assert result["blocked"] is True, args
        assert not result["ok"]


# ------------------------------------------------------ guard + runner e2e

def test_default_ceiling_keeps_file_write_inert(conn, ws):
    d = guard.Guard(conn).check_tool("file.write", level=1,
                                     privacy_floor=guard.PRIVATE, run_id="r1",
                                     args={"path": "x.txt", "content": "y"})
    assert not d.allowed and d.rule == "domain-ceiling"
    assert not list(ws.iterdir())


def test_runner_write_end_to_end(conn, ws):
    """The money test, mirroring test_gittools: the run parks for a typed
    confirmation, the operator types the token, and the resumed run leaves a
    real file on disk - readback-pinned."""
    permissions.set_ceiling(conn, "file", 1)
    steps = [("file.write", {"path": "made-by-run.md", "content": "ai wrote this",
                             "workspace": str(ws)})]
    run_id = runner.start_run(conn, project="rcc", goal="create made-by-run.md")
    reg = tools.default_registry()

    def _remaining():
        done = {t["tool"] for t in runner.history_of(conn, run_id)
                if t["verify"]["ok"]}
        return [s for s in steps if s[0] not in done]

    result = None
    for _ in range(3):
        planner = runner.NoopPlanner(script=_remaining())
        result = runner.execute_run(conn, run_id, planner, reg,
                                    max_turns=4, tool_calls_budget=4)
        if result.status != "parked" or "awaiting typed confirmation" not in result.reason:
            break
        token = result.reason.split("awaiting typed confirmation ")[1].split(" ")[0]
        assert permissions.confirm(conn, token, token)

    assert result.status == "done", result.reason
    assert (ws / "made-by-run.md").read_text(encoding="utf-8") == "ai wrote this"
    turns = runner.history_of(conn, run_id)
    assert [t["tool"] for t in turns if t["verify"]["ok"]] == ["file.write"]
    assert "readback ok" in turns[0]["verify"]["detail"]
    confirmed = conn.execute(
        "SELECT COUNT(*) FROM events WHERE kind = 'guard.confirmed'"
    ).fetchone()[0]
    assert confirmed == 1
