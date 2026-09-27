"""Read-only Git inspection for "where did I leave off?" — zero AI calls, zero writes.

V0 scope: the *current* repo only (the directory Thoth is asked about). No remote
calls, no mutation; anything risky belongs to the V0.2+ permission model.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

_TIMEOUT = 10  # seconds; never hang the CLI on a wedged repo


def _git(repo: Path, *args: str) -> str | None:
    """Run one read-only git command; None if not a repo or git is missing."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def is_repo(path: Path) -> bool:
    return _git(path, "rev-parse", "--is-inside-work-tree") == "true"


def branch(path: Path) -> str | None:
    return _git(path, "rev-parse", "--abbrev-ref", "HEAD")


def dirty_files(path: Path) -> list[str]:
    """Uncommitted changes, porcelain format: `XY path` per line."""
    out = _git(path, "status", "--porcelain")
    if not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def last_commit(path: Path) -> dict[str, str] | None:
    """Subject + short hash of HEAD, or None (e.g. freshly-initialized repo)."""
    out = _git(path, "log", "-1", "--pretty=%h%x1f%s%x1f%cr")
    if not out or "\x1f" not in out:
        return None
    short, subject, when = (out.split("\x1f") + ["", "", ""])[:3]
    return {"hash": short, "subject": subject, "when": when}


def stash_count(path: Path) -> int:
    out = _git(path, "stash", "list")
    return len(out.splitlines()) if out else 0


def resume_snapshot(path: Path) -> dict[str, Any]:
    """Everything resume needs from the repo, read-only."""
    repo = Path(path)
    if not is_repo(repo):
        return {"repo": False}
    return {
        "repo": True,
        "branch": branch(repo),
        "dirty": dirty_files(repo),
        "last_commit": last_commit(repo),
        "stashes": stash_count(repo),
    }


def _clean_branch(b: str | None) -> str | None:
    """Fresh repos report branch as literal 'HEAD'; treat that as unknown."""
    return None if b in (None, "HEAD") else b
