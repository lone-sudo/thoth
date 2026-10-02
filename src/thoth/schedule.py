"""The clock: wall-time for the reports, with no daemon of Thoth's own.

ADR-001 keeps Thoth CLI-first and stdlib-only: there is no background
process, so nothing wakes Thoth up by itself. The reports exist (briefing,
digest), the delivery surface exists (telegram.py); what ties them to
wall-clock time is the operating system's own scheduler. This module prints
the exact lines for THIS machine - it never installs anything itself. The
operator runs the command and stays the operator.

Token hygiene note (ADR-005): on Windows the token lives in a wrapper
script the operator creates once (outside any repo); the scheduled task
only references the wrapper. On cron the variables sit in the user's own
crontab, which is private to the user by construction.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":
    PLATFORM = "windows"
else:
    PLATFORM = "cron"

DEFAULT_BRIEFING_AT = "07:30"
DEFAULT_DIGEST_AT = "21:00"


def _windows_plan(db_path: str, briefing_at: str, digest_at: str) -> dict:
    db = db_path.replace('"', '')
    wrapper = str(Path.home() / ".thoth" / "thoth-reports.cmd")
    home_db = str(Path.home() / ".thoth" / "thoth.db")
    lines = [
        "# 1. create the wrapper once (paste your token + chat id into it;",
        "#    keep this file OUT of any repo - it holds the secret):",
        f'#    notepad "{wrapper}"',
        "# ---- wrapper content (thoth-reports.cmd) ----",
        "@echo off",
        "rem Thoth daily reports - secrets live here, never in the repo or the DB",
        "set THOTH_TG_TOKEN=paste-token-here",
        "set THOTH_TG_CHAT=paste-chat-id-here",
        'call thoth --db "' + home_db + '" telegram send-%1',
        "# ---- end wrapper ----",
        "# 2. install the two scheduled tasks (adjust times as you like):",
        f'schtasks /Create /TN "Thoth morning briefing" /SC DAILY /ST {briefing_at} '
        f'/TR "\\"{wrapper}\\" briefing"',
        f'schtasks /Create /TN "Thoth evening digest" /SC DAILY /ST {digest_at} '
        f'/TR "\\"{wrapper}\\" digest"',
        "# 3. prove one delivery by hand before trusting the clock:",
        f'"{wrapper}" briefing',
    ]
    return {"platform": "windows", "db": db, "lines": lines}


def _cron_plan(db_path: str, briefing_at: str, digest_at: str) -> dict:
    b_h, b_m = briefing_at.split(":")
    d_h, d_m = digest_at.split(":")
    thoth = f'cd "{Path.home() / "thoth"}" &&'
    lines = [
        "# crontab -e  (the crontab is private to your user by construction)",
        "# morning briefing:",
        f"{b_m} {b_h} * * * {thoth} THOTH_TG_TOKEN=... THOTH_TG_CHAT=... "
        f"thoth --db {db_path} telegram send-briefing",
        "# evening digest:",
        f"{d_m} {d_h} * * * {thoth} THOTH_TG_TOKEN=... THOTH_TG_CHAT=... "
        f"thoth --db {db_path} telegram send-digest",
    ]
    return {"platform": "cron", "db": db_path, "lines": lines}


def plan(db_path: str | None = None, briefing_at: str = DEFAULT_BRIEFING_AT,
         digest_at: str = DEFAULT_DIGEST_AT) -> dict:
    """Build the scheduler plan for this platform (pure function; prints
    nothing, installs nothing)."""
    db = db_path or str(Path.home() / ".thoth" / "thoth.db")
    if PLATFORM == "windows":
        return _windows_plan(db, briefing_at, digest_at)
    return _cron_plan(db, briefing_at, digest_at)


def render(p: dict) -> str:
    head = (f"scheduler plan - {p['platform']} - db: {p['db']}\n"
            "nothing is installed by this command; you run the lines\n")
    return head + "\n".join(p["lines"]) + "\n"
