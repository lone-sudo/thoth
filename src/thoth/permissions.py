"""The permission table {domain -> level} (ADR-004 section 5).

The operator sets one ceiling per tool domain; the guard computes the
effective ceiling per crossing from stored state - never from what a run
says about itself. Fail-closed default: a domain with no row ceilings at
0 (observe-only), so a future mutating tool ships inert until the
operator deliberately raises the domain.

Levels (ADR-003 ToolSpec vocabulary, 0..4): 0 = observe (read-only);
>= 1 = mutating. Every mutating invocation within the operator's ceiling
requires a TYPED confirmation (below); "destructive" actions are simply
mutating ones the operator chose to allow at all - they get the same
typed-confirmation gate, never a looser one.

Typed confirmation (ADR-004 section 1, the require_confirmation verdict):
the guard mints a token bound to (run_id, tool, args). The runner parks
with the token in the park reason; the operator confirms by typing the
FULL token - `thoth run confirm confirm:<16hex>` - which is recorded as
a `guard.confirmed` event. y/n and menu choices are refused by design:
the point is deliberate human intent, not reflex. A confirmation is
replayable only by the same run proposing the identical action; another
run, or different args, mints a different token.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .events import emit

DOMAINS = ("shell", "file", "memory", "git")

DEFAULT_CEILING = 0      # observe-only: fail closed
MAX_LEVEL = 4

LEVEL_NAMES = {
    0: "observe",
    1: "reversible-write",
    2: "write",
    3: "destructive",
    4: "unrestricted",
}

CONFIRM_PREFIX = "confirm:"


def domain_of(tool_name: str) -> str:
    """`shell.read` -> `shell`; unprefixed names are their own domain."""
    return tool_name.split(".", 1)[0] if "." in tool_name else tool_name


def ceiling(conn: sqlite3.Connection, domain: str) -> int:
    """The operator's ceiling for a domain. Unknown domain, missing row,
    or corrupt value all return the fail-closed default."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (f"perm:{domain}",)
    ).fetchone()
    if row is None:
        return DEFAULT_CEILING
    try:
        return max(0, min(MAX_LEVEL, int(row["value"])))
    except (TypeError, ValueError):
        return DEFAULT_CEILING


def table(conn: sqlite3.Connection) -> dict[str, int]:
    """The whole operator-set table (for `permission show`)."""
    return {d: ceiling(conn, d) for d in DOMAINS}


def set_ceiling(conn: sqlite3.Connection, domain: str, level: int) -> None:
    """Operator write path. Validates, stores in meta, emits the event."""
    if domain not in DOMAINS:
        raise ValueError(f"unknown domain: {domain} (known: {', '.join(DOMAINS)})")
    if not isinstance(level, int) or isinstance(level, bool) \
            or not (0 <= level <= MAX_LEVEL):
        raise ValueError(f"level must be an integer 0..{MAX_LEVEL}, got {level!r}")
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (f"perm:{domain}", str(level)),
    )
    emit(conn, "permission.changed", {
        "domain": domain, "level": level, "actor": "operator",
    })
    conn.commit()


# ------------------------------------------------------------- confirmation

def confirmation_token(tool_name: str, args: dict[str, Any] | None,
                       run_id: str | None) -> str:
    """A stable token for exactly this (run, tool, args) proposal."""
    blob = json.dumps(
        {"run_id": run_id or "", "tool": tool_name, "args": args or {}},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return CONFIRM_PREFIX + hashlib.sha256(blob.encode()).hexdigest()[:16]


def pending_for(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    """The latest unexpired-in-intent require_confirmation decision for a
    token (the guard minted it; the runner parked on it)."""
    row = conn.execute(
        "SELECT payload_json FROM events "
        "WHERE kind = 'guard.decision' "
        "AND json_extract(payload_json, '$.verdict') = 'require_confirmation' "
        "AND json_extract(payload_json, '$.token') = ? "
        "ORDER BY ts DESC LIMIT 1",
        (token,),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"])


def confirm(conn: sqlite3.Connection, token: str, typed: str) -> bool:
    """The operator's half of the handshake. The typed string must equal the
    token exactly; the token must reference a real guard decision. Every
    attempt - success or rejection - is an event."""
    pending = pending_for(conn, token) if isinstance(token, str) else None
    if pending is None:
        emit(conn, "guard.confirmation.rejected", {
            "actor": "operator", "reason": "unknown token", "token": token,
        })
        conn.commit()
        return False
    if typed != token:
        emit(conn, "guard.confirmation.rejected", {
            "actor": "operator", "reason": "typed text did not match the token",
            "token": token, "run_id": pending.get("run_id"),
        })
        conn.commit()
        return False
    emit(conn, "guard.confirmed", {
        "actor": "operator", "token": token,
        "run_id": pending.get("run_id"), "tool": pending.get("tool"),
    })
    conn.commit()
    return True


def is_confirmed(conn: sqlite3.Connection, token: str, run_id: str | None) -> bool:
    """Has the operator typed this token's confirmation for THIS run?"""
    if not token or not run_id:
        return False
    row = conn.execute(
        "SELECT id FROM events "
        "WHERE kind = 'guard.confirmed' "
        "AND json_extract(payload_json, '$.token') = ? "
        "AND json_extract(payload_json, '$.run_id') = ? LIMIT 1",
        (token, run_id),
    ).fetchone()
    return row is not None
