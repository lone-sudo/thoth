"""Tests for git.add (gittools.py): the chain's staging half.

Design pins under test:
- the branch protocol holds in tool code: stages only while ON a thoth/*
  branch, at every permission ceiling;
- one existing FILE per call - escapes, .git internals, missing paths and
  directories are refused before git runs;
- verifier evidence is index truth: the name-only diff of the staged index
  must contain exactly the staged path; an unchanged, already-committed file
  stages nothing and verifies False - honestly.
"""

from __future__ import annotations

import subprocess

import pytest

from thoth import filetools, gittools, guard, permissions, runner, tools


@pytest.fixture()
def repo(tmp_path):
    """A real tiny git repo: one seed commit on main, identity configured."""
    r = tmp_path / "ws"
    r.mkdir()

    def _git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=r, check=True, capture_output=True,
                       text=True)

    _git("init", "-b", "main")
    _git("config", "user.email", "thoth@test")
    _git("config", "user.name", "Thoth Test")
    (r / "README.md").write_text("seed", encoding="utf-8")
    _git("add", ".")
    _git("commit", "-m", "seed")
    return r


@pytest.fixture()
def conn(tmp_path):
    from thoth import db
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def _git_lines(repo, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True)
    return proc.stdout.strip()


# --------------------------------------------------- protocol + scoping

def test_add_refused_off_protocol(repo):
    """On main: refused before git runs, index untouched."""
    result = gittools._run_add("README.md", repo=str(repo))
    assert result["blocked"] is True and result["ok"] is False
    assert "branch protocol" in result["detail"] and "main" in result["detail"]
    assert _git_lines(repo, "diff", "--cached", "--name-only") == ""


def test_add_refuses_escapes_git_and_missing(repo):
    assert gittools._run_branch_create("thoth/work", repo=str(repo))["ok"]
    assert gittools._run_checkout("thoth/work", repo=str(repo))["ok"]
    (repo / "real.txt").write_text("x", encoding="utf-8")
    esc = gittools._run_add("../outside.txt", repo=str(repo))
    assert esc["blocked"] is True
    assert esc["detail"] == "path escapes the workspace"
    ig = gittools._run_add(".git/config", repo=str(repo))
    assert ig["blocked"] is True and ".git" in ig["detail"]
    miss = gittools._run_add("nope.txt", repo=str(repo))
    assert miss["blocked"] is True and "not a file" in miss["detail"]
    (repo / "subdir").mkdir()
    d = gittools._run_add("subdir", repo=str(repo))
    assert d["blocked"] is True and "not a file" in d["detail"]
    assert _git_lines(repo, "diff", "--cached", "--name-only") == ""


def test_add_rules_hold_at_any_permission_level(conn, repo):
    """The design pin, mirroring the git/file tools: raising the ceiling
    licenses staging INSIDE the rules, never around them."""
    permissions.set_ceiling(conn, "git", 4)  # unrestricted
    for bad in ("../escape.txt", ".git/config", "missing.txt"):
        result = gittools._run_add(bad, repo=str(repo))
        assert result["blocked"] is True, (bad, result.get("detail"))
    on_main = gittools._run_add("README.md", repo=str(repo))
    assert on_main["blocked"] is True
    assert "branch protocol" in on_main["detail"]


# ------------------------------------------------------------- round trip

def test_add_round_trip_stages_the_run_written_file(repo):
    assert gittools._run_branch_create("thoth/work", repo=str(repo))["ok"]
    assert gittools._run_checkout("thoth/work", repo=str(repo))["ok"]
    wrote = filetools._run_write("made-by-run.md", "chain content",
                                 workspace=str(repo))
    assert wrote["ok"] is True
    result = gittools._run_add("made-by-run.md", repo=str(repo))
    assert result["ok"] is True and result["staged_matches"] is True
    assert result["branch"] == "thoth/work" and result["staged_count"] == 1
    report = gittools._verify_add(result)
    assert report.ok and "index holds 1" in report.detail
    assert _git_lines(repo, "diff", "--cached", "--name-only") == "made-by-run.md"
    # idempotent: re-adding the same file is the same staged state
    again = gittools._run_add("made-by-run.md", repo=str(repo))
    assert again["ok"] is True and gittools._verify_add(again).ok


def test_add_unchanged_committed_file_fails_verify_honestly(repo):
    assert gittools._run_branch_create("thoth/work", repo=str(repo))["ok"]
    assert gittools._run_checkout("thoth/work", repo=str(repo))["ok"]
    result = gittools._run_add("README.md", repo=str(repo))
    assert result["ok"] is True               # git add ran clean...
    assert result["staged_matches"] is False  # ...but nothing was staged
    report = gittools._verify_add(result)
    assert not report.ok and "not in the index" in report.detail


# ------------------------------------------------------ guard + runner e2e

def test_default_ceiling_keeps_git_add_inert(conn, repo):
    d = guard.Guard(conn).check_tool("git.add", level=1,
                                     privacy_floor=guard.PRIVATE, run_id="r1",
                                     args={"path": "x.txt"})
    assert not d.allowed and d.rule == "domain-ceiling"
    assert _git_lines(repo, "diff", "--cached", "--name-only") == ""


def test_runner_write_stage_commit_chain_end_to_end(conn, repo):
    """The chain the V1 tools were built for: the run creates the content,
    stages it, and lands the commit - five real mutations, each behind its
    own typed confirmation, ending in a real commit on a real thoth/*
    branch on disk."""
    permissions.set_ceiling(conn, "git", 2)
    permissions.set_ceiling(conn, "file", 1)
    steps = [
        ("file.write", {"path": "made-by-run.md", "content": "chain test",
                        "workspace": str(repo)}),
        ("git.branch_create", {"name": "thoth/chain", "repo": str(repo)}),
        ("git.checkout", {"name": "thoth/chain", "repo": str(repo)}),
        ("git.add", {"path": "made-by-run.md", "repo": str(repo)}),
        ("git.commit", {"message": "ai: chain lands", "repo": str(repo)}),
    ]
    run_id = runner.start_run(conn, project="rcc", goal="write, stage, commit")
    reg = tools.default_registry()

    def _remaining():
        done = {t["tool"] for t in runner.history_of(conn, run_id)
                if t["verify"]["ok"]}
        return [(n, a) for n, a in steps if n not in done]

    result = None
    for _ in range(7):
        planner = runner.NoopPlanner(script=_remaining())
        result = runner.execute_run(conn, run_id, planner, reg,
                                    max_turns=8, tool_calls_budget=8)
        if result.status != "parked" or "awaiting typed confirmation" not in result.reason:
            break
        token = result.reason.split("awaiting typed confirmation ")[1].split(" ")[0]
        assert permissions.confirm(conn, token, token)

    assert result.status == "done", result.reason
    assert _git_lines(repo, "rev-parse", "--abbrev-ref", "HEAD") == "thoth/chain"
    assert _git_lines(repo, "log", "-1", "--pretty=%s") == "ai: chain lands"
    committed = _git_lines(repo, "show", "--name-only", "--pretty=format:", "HEAD")
    assert "made-by-run.md" in committed
    turns = runner.history_of(conn, run_id)
    assert [t["tool"] for t in turns if t["verify"]["ok"]] == [
        "file.write", "git.branch_create", "git.checkout", "git.add",
        "git.commit"]
    confirmed = conn.execute(
        "SELECT COUNT(*) FROM events WHERE kind = 'guard.confirmed'"
    ).fetchone()[0]
    assert confirmed == 5
