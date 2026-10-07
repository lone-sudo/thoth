"""Tests for the git branch protocol (ROADMAP V1): the AI works only on
``thoth/*`` branches, enforced in tool code - never a prompt - and holding
at every permission level, behind the guard's ceiling + typed confirmation.
"""

from __future__ import annotations

import subprocess

import pytest

from thoth import gittools, guard, permissions, runner, tools


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
    c = db_connect(tmp_path / "t.db")
    yield c
    c.close()


def db_connect(path):  # late import keeps the module list at top honest
    from thoth import db
    return db.connect(path)


def _git_lines(repo, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True)
    return proc.stdout.strip()


# ------------------------------------------------------------- the registry

def test_registry_levels_and_flags():
    reg = tools.default_registry()
    by_name = {t.name: t for t in reg.all()}
    assert set(by_name) >= {"shell.read", "file.read", "memory.search",
                            "git.branch_create", "git.checkout", "git.add",
                            "git.commit"}
    assert by_name["git.branch_create"].permission_level == 1
    assert by_name["git.checkout"].permission_level == 1
    assert by_name["git.add"].permission_level == 1
    assert by_name["git.commit"].permission_level == 2
    # honest idempotency: two commits are two commits
    assert by_name["git.commit"].idempotent is False
    assert by_name["git.branch_create"].idempotent is True
    assert by_name["git.checkout"].idempotent is True
    assert by_name["git.add"].idempotent is True
    for name in ("git.branch_create", "git.checkout", "git.add", "git.commit"):
        assert by_name[name].privacy_floor == guard.PRIVATE


def test_branch_name_regex():
    assert gittools.is_thoth_branch("thoth/fix-login")
    assert gittools.is_thoth_branch("thoth/v2/nested-name.1")
    for bad in ("master", "main", "feature-x", "thoth", "thoth/",
                "thoth/-leading", "thoth/../evil", "-thoth/x", "",
                "thoth/" + "x" * 300):
        assert not gittools.is_thoth_branch(bad), bad


# ------------------------------------------------- protocol refusals (tool)

def test_branch_create_refuses_non_thoth_names(repo):
    for bad in ("feature-x", "master", "thoth/../evil"):
        result = gittools._run_branch_create(bad, repo=str(repo))
        assert result["blocked"] is True and result["ok"] is False, bad
        assert "branch protocol" in result["detail"]
    # nothing was created
    assert _git_lines(repo, "branch", "--list", "thoth/*") == ""


def test_checkout_refuses_non_thoth_targets(repo):
    for bad in ("main", "master", "thoth/../evil"):
        result = gittools._run_checkout(bad, repo=str(repo))
        assert result["blocked"] is True and result["ok"] is False, bad
        assert "branch protocol" in result["detail"]
    assert _git_lines(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_commit_refused_off_protocol(repo):
    head_before = _git_lines(repo, "rev-parse", "HEAD")
    result = gittools._run_commit("ai: should not land", repo=str(repo))
    assert result["blocked"] is True and result["ok"] is False
    assert "branch protocol" in result["detail"] and "main" in result["detail"]
    assert _git_lines(repo, "rev-parse", "HEAD") == head_before


def test_protocol_holds_at_any_permission_level(conn, repo):
    """The design pin: raising the git ceiling licenses mutations INSIDE the
    protocol, never around it - the prefix check is tool code, not a gate
    the table can open."""
    permissions.set_ceiling(conn, "git", 4)  # unrestricted
    for run_fn, name in ((gittools._run_checkout, "main"),
                         (gittools._run_commit, "irrelevant"),
                         (gittools._run_branch_create, "hotfix-now")):
        result = run_fn(name, repo=str(repo))
        assert result["blocked"] is True, (run_fn.__name__, name)
        assert "branch protocol" in result["detail"]


# ---------------------------------------------------------- happy paths

def test_branch_create_round_trip(repo):
    result = gittools._run_branch_create("thoth/fix-x", repo=str(repo))
    assert result["ok"] is True and result["branch_exists"] is True
    assert gittools._verify_branch_create(result).ok
    assert _git_lines(repo, "branch", "--list", "thoth/fix-x").strip()
    # still on main: branch_create is pointer creation, not a switch
    assert _git_lines(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    # duplicate create: honest failure, no verify
    dup = gittools._run_branch_create("thoth/fix-x", repo=str(repo))
    assert dup["ok"] is False and not gittools._verify_branch_create(dup).ok


def test_checkout_round_trip(repo):
    assert gittools._run_branch_create("thoth/work", repo=str(repo))["ok"]
    result = gittools._run_checkout("thoth/work", repo=str(repo))
    assert result["ok"] is True and result["branch"] == "thoth/work"
    assert gittools._verify_checkout(result).ok
    assert _git_lines(repo, "rev-parse", "--abbrev-ref", "HEAD") == "thoth/work"
    # nonexistent target: honest failure
    miss = gittools._run_checkout("thoth/nope", repo=str(repo))
    assert miss["ok"] is False and not gittools._verify_checkout(miss).ok


def test_commit_round_trip_on_thoth_branch(repo):
    assert gittools._run_branch_create("thoth/work", repo=str(repo))["ok"]
    assert gittools._run_checkout("thoth/work", repo=str(repo))["ok"]
    (repo / "note.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "note.txt"], cwd=repo, check=True,
                   capture_output=True)
    result = gittools._run_commit("ai: add note", repo=str(repo))
    assert result["ok"] is True
    report = gittools._verify_commit(result)
    assert report.ok and "new commit" in report.detail
    assert result["hash"] != result["head_before"]
    assert _git_lines(repo, "log", "-1", "--pretty=%s") == "ai: add note"
    # the event trail recorded it
    # (checked in the runner test; here the repo state is the evidence)


def test_commit_stages_nothing(repo):
    """The minimal-blast-radius pin: git.commit commits the index as it
    finds it. Unstaged changes are never swept in; an empty index fails
    honestly instead."""
    assert gittools._run_branch_create("thoth/work", repo=str(repo))["ok"]
    assert gittools._run_checkout("thoth/work", repo=str(repo))["ok"]
    (repo / "loose.txt").write_text("unstaged", encoding="utf-8")
    head_before = _git_lines(repo, "rev-parse", "HEAD")
    result = gittools._run_commit("ai: sweep attempt", repo=str(repo))
    assert result["ok"] is False
    assert not gittools._verify_commit(result).ok
    assert _git_lines(repo, "rev-parse", "HEAD") == head_before


def test_commit_empty_message_refused(repo):
    result = gittools._run_commit("   ", repo=str(repo))
    assert result["blocked"] is True and "message" in result["detail"]


# ---------------------------------------------------- guard + runner e2e

def test_default_ceiling_keeps_git_inert(conn, repo):
    d = guard.Guard(conn).check_tool("git.branch_create", level=1,
                                     privacy_floor=guard.PRIVATE, run_id="r1",
                                     args={"name": "thoth/x"})
    assert not d.allowed and d.rule == "domain-ceiling"
    assert _git_lines(repo, "branch", "--list", "thoth/*") == ""


def test_runner_full_protocol_end_to_end(conn, repo):
    """The money test: three real mutations, each gated - the run parks for
    a typed confirmation per action, the operator types each token, and the
    resumed run leaves a real commit on a real thoth/* branch on disk."""
    permissions.set_ceiling(conn, "git", 2)
    # the operator stages content; the runner only decides when + message
    # (git.commit stages nothing - see test_commit_stages_nothing)
    (repo / "change.txt").write_text("staged work", encoding="utf-8")
    subprocess.run(["git", "add", "change.txt"], cwd=repo, check=True,
                   capture_output=True)
    steps = [
        ("git.branch_create", {"name": "thoth/e2e", "repo": str(repo)}),
        ("git.checkout", {"name": "thoth/e2e", "repo": str(repo)}),
        ("git.commit", {"message": "ai: e2e commit", "repo": str(repo)}),
    ]
    run_id = runner.start_run(conn, project="rcc", goal="ship on a thoth branch")
    reg = tools.default_registry()

    def _remaining() -> list[tuple[str, dict]]:
        done = {t["tool"] for t in runner.history_of(conn, run_id)
                if t["verify"]["ok"]}
        return [(n, a) for n, a in steps if n not in done]

    result = None
    for _ in range(5):
        planner = runner.NoopPlanner(script=_remaining())
        result = runner.execute_run(conn, run_id, planner, reg,
                                    max_turns=6, tool_calls_budget=6)
        if result.status != "parked" or "awaiting typed confirmation" not in result.reason:
            break
        token = result.reason.split("awaiting typed confirmation ")[1].split(" ")[0]
        assert permissions.confirm(conn, token, token)

    assert result.status == "done", result.reason
    # the mutations are real, on disk
    assert _git_lines(repo, "rev-parse", "--abbrev-ref", "HEAD") == "thoth/e2e"
    assert _git_lines(repo, "log", "-1", "--pretty=%s") == "ai: e2e commit"
    # every action verified, in protocol order, each behind its own token
    turns = runner.history_of(conn, run_id)
    assert [t["tool"] for t in turns if t["verify"]["ok"]] == [
        "git.branch_create", "git.checkout", "git.commit"]
    confirmed = conn.execute(
        "SELECT COUNT(*) FROM events WHERE kind = 'guard.confirmed'"
    ).fetchone()[0]
    assert confirmed == 3
