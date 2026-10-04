"""The git branch protocol: Thoth's first real mutating tools (ROADMAP V1).

The protocol, as code - never as a prompt: **the AI works only on
``thoth/*`` branches.** Three tools carry it:

  git.branch_create  create a branch; the NAME must match thoth/*
  git.checkout       switch branches; the TARGET must match thoth/*
  git.commit         commit the staged index; allowed only while ON a thoth/*

Division of labor (ADR-003 / ADR-004): the guard answers *whether* git may
be mutated at all - the operator's {domain -> level} ceiling plus one typed
confirmation per action. These tools answer *what a legal mutation is*: the
thoth/* rule is checked here, at every permission level - raising the
ceiling licenses mutations inside the protocol, never around it. Verify is
code, not the model: each tool records repo evidence (branch exists, HEAD
moved) and its verifier pins that evidence.

``git.commit`` stages nothing. It commits the index exactly as it finds it:
the runner decides *when* and *with what message*; the content comes from
the operator's staging (or a future file-write tool behind its own gate).
A commit with nothing staged fails honestly - it never sweeps the tree.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from . import guard
from .events import emit
from .tools import ToolRegistry, ToolSpec, VerifyReport, _emit_tool_event, _result

_TIMEOUT = 15  # seconds; matches the shell tool's subprocess cap

# The protocol as a regex: exactly "thoth/", then a git-safe name body.
# First body character must be alphanumeric - no leading "-", "." or ".."
# games, no option injection (a branch name can never be mistaken for a
# flag), body limited to git-safe characters. Git's own refname rules stay
# behind this one as the second layer; a protocol pass with a name git
# rejects surfaces as an honest tool failure.
THOTH_BRANCH = re.compile(r"^thoth/[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")


def is_thoth_branch(name: str) -> bool:
    return isinstance(name, str) and bool(THOTH_BRANCH.match(name))


def _git_run(repo: Path, *args: str) -> tuple[int, str, str]:
    """One git command; (returncode, stdout, stderr). Never raises."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True,
            timeout=_TIMEOUT,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return 127, "", f"git unavailable: {exc}"


def _current_branch(repo: Path) -> str | None:
    code, out, _ = _git_run(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return out if code == 0 and out not in ("", "HEAD") else None


def _workspace(repo: str | None) -> tuple[Path | None, dict[str, Any] | None]:
    """Resolve the repo path; an error result when it is not a directory."""
    path = Path(repo) if repo else Path.cwd()
    if not path.is_dir():
        return None, _result(False, f"not a directory: {path}", blocked=True)
    return path, None


# ---------------------------------------------------------------------------
# git.branch_create  (level 1, reversible-write: a branch is a pointer)
# ---------------------------------------------------------------------------

def _run_branch_create(name: str, repo: str | None = None,
                       _conn: sqlite3.Connection | None = None,
                       **_: Any) -> dict[str, Any]:
    if not is_thoth_branch(name):
        return _result(False,
                       f"branch protocol: name must match thoth/* (got {name!r})",
                       blocked=True)
    path, err = _workspace(repo)
    if err is not None:
        return err
    code, _out, git_err = _git_run(path, "branch", name)
    if code != 0:
        return _result(False, f"git branch failed: {git_err[:200]}")
    exists = _git_run(path, "rev-parse", "--verify", "--quiet",
                      f"refs/heads/{name}")[0] == 0
    head = _git_run(path, "rev-parse", "--short", "HEAD")[1]
    result = _result(True, f"branch {name} created at {head}", name=name,
                     branch_exists=exists, head=head, repo=str(path))
    _emit_tool_event(_conn, "tool.git_branch_create", {"name": name, "ok": True})
    return result


def _verify_branch_create(result: dict[str, Any]) -> VerifyReport:
    if result.get("blocked"):
        return VerifyReport(False, str(result.get("detail", "blocked")))
    ok = result.get("ok") is True and result.get("branch_exists") is True
    return VerifyReport(ok,
                        f"branch_exists={result.get('branch_exists')} at {result.get('head')}")


def _summarize_branch_create(result: dict[str, Any]) -> str:
    if result.get("ok") is not True:
        return f"branch_create failed: {result.get('detail', '')}"
    return (f"Created branch {result.get('name')} at {result.get('head')} "
            f"({result.get('detail', '')})")


# ---------------------------------------------------------------------------
# git.checkout  (level 1, reversible-write: switch back is the same action)
# ---------------------------------------------------------------------------

def _run_checkout(name: str, repo: str | None = None,
                  _conn: sqlite3.Connection | None = None,
                  **_: Any) -> dict[str, Any]:
    if not is_thoth_branch(name):
        return _result(False,
                       f"branch protocol: checkout restricted to thoth/* "
                       f"branches (got {name!r})",
                       blocked=True)
    path, err = _workspace(repo)
    if err is not None:
        return err
    code, _out, git_err = _git_run(path, "checkout", name)
    if code != 0:
        return _result(False, f"git checkout failed: {git_err[:200]}")
    branch = _current_branch(path)
    result = _result(True, f"now on branch {branch}", name=name, branch=branch,
                     repo=str(path))
    _emit_tool_event(_conn, "tool.git_checkout",
                     {"name": name, "branch": branch, "ok": True})
    return result


def _verify_checkout(result: dict[str, Any]) -> VerifyReport:
    if result.get("blocked"):
        return VerifyReport(False, str(result.get("detail", "blocked")))
    ok = result.get("ok") is True and result.get("branch") == result.get("name")
    return VerifyReport(ok, f"on branch {result.get('branch')} "
                            f"(wanted {result.get('name')})")


def _summarize_checkout(result: dict[str, Any]) -> str:
    if result.get("ok") is not True:
        return f"checkout failed: {result.get('detail', '')}"
    return f"Switched to branch {result.get('branch')} ({result.get('detail', '')})"


# ---------------------------------------------------------------------------
# git.commit  (level 2, write: NOT idempotent - two calls are two commits)
# ---------------------------------------------------------------------------

def _run_commit(message: str, repo: str | None = None,
                _conn: sqlite3.Connection | None = None,
                **_: Any) -> dict[str, Any]:
    if not isinstance(message, str) or not message.strip():
        return _result(False, "message must be a non-empty string", blocked=True)
    path, err = _workspace(repo)
    if err is not None:
        return err
    branch = _current_branch(path)
    if branch is None or not is_thoth_branch(branch):
        return _result(False,
                       f"branch protocol: commits only on thoth/* branches "
                       f"(on {branch!r})",
                       blocked=True)
    head_before = _git_run(path, "rev-parse", "--short", "HEAD")[1]
    code, out, git_err = _git_run(path, "commit", "-m", message)
    if code != 0:
        return _result(False, f"git commit failed: {(git_err or out)[:200]}")
    head_after = _git_run(path, "rev-parse", "--short", "HEAD")[1]
    result = _result(True, f"committed {head_after} on {branch}", message=message,
                     branch=branch, hash=head_after, head_before=head_before,
                     repo=str(path))
    _emit_tool_event(_conn, "tool.git_commit",
                     {"branch": branch, "hash": head_after,
                      "message": message[:120], "ok": True})
    return result


def _verify_commit(result: dict[str, Any]) -> VerifyReport:
    if result.get("blocked"):
        return VerifyReport(False, str(result.get("detail", "blocked")))
    ok = (result.get("ok") is True
          and bool(result.get("hash"))
          and is_thoth_branch(str(result.get("branch") or ""))
          and result.get("hash") != result.get("head_before"))
    return VerifyReport(ok, f"new commit {result.get('hash')} on "
                            f"{result.get('branch')} (was {result.get('head_before')})")


def _summarize_commit(result: dict[str, Any]) -> str:
    if result.get("ok") is not True:
        return f"commit failed: {result.get('detail', '')}"
    return (f"Committed {result.get('hash')} on {result.get('branch')}: "
            f"{str(result.get('message', ''))[:60]}")


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def register(reg: ToolRegistry) -> None:
    reg.register(ToolSpec(
        name="git.branch_create",
        description="Create a git branch. Branch protocol: name must be thoth/*.",
        permission_level=1, idempotent=True, privacy_floor=guard.PRIVATE,
        input_schema={"name": "str", "repo": "str"}, required={"name"},
        run=_run_branch_create, verify=_verify_branch_create,
        summarize=_summarize_branch_create,
    ))
    reg.register(ToolSpec(
        name="git.checkout",
        description="Switch to an existing git branch. Branch protocol: target must be thoth/*.",
        permission_level=1, idempotent=True, privacy_floor=guard.PRIVATE,
        input_schema={"name": "str", "repo": "str"}, required={"name"},
        run=_run_checkout, verify=_verify_checkout,
        summarize=_summarize_checkout,
    ))
    reg.register(ToolSpec(
        name="git.commit",
        description="Commit the staged index with a message. Branch protocol: only while on a thoth/* branch; stages nothing.",
        permission_level=2, idempotent=False, privacy_floor=guard.PRIVATE,
        input_schema={"message": "str", "repo": "str"}, required={"message"},
        run=_run_commit, verify=_verify_commit,
        summarize=_summarize_commit,
    ))
