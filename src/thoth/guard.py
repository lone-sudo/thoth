"""The security choke point (ADR-004): one fail-closed guard for every
consequential crossing.

V1 skeleton scope: tool invocations (privacy clearance + level gate) and
network/provider crossings (denied outright — no provider clients exist; the
spend-guard branches land with the provider registry and are *unoverridable*).

Design invariants (ADR-004):
- fail closed: unknown crossing / guard error => deny
- every decision is an event (`guard.decision`) — silent denies are
  indistinguishable from bugs
- data class travels with the data; enforcement is `data_class <= tool clearance`,
  never a prompt
- no privilege crossing justified by content alone: authority comes from the
  operator-set goal and the tool's declared spec, nothing else
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .events import emit

# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------

ALLOW = "allow"
DENY = "deny"

KIND_TOOL = "tool"
KIND_NETWORK = "network"      # any outbound network call
KIND_PROVIDER = "provider"    # outbound AI request (subset of network)

# Privacy classes (ADR-004): higher = more sensitive. Default for data of
# unknown provenance is PRIVATE (fail closed). SENSITIVE is local-only and
# cleared to no tool today.
PUBLIC, PERSONAL, PRIVATE, SENSITIVE = 0, 1, 2, 3

DEFAULT_DATA_CLASS = PRIVATE


@dataclass
class Decision:
    """The result of one guarded crossing. Always loggable, always explainable."""

    verdict: str                 # ALLOW | DENY
    rule: str                    # machine-readable rule id
    reason: str                  # human-readable explanation

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------


class Guard:
    """Fail-closed checkpoint. Every decision is appended to the event log
    before it is returned — the log answers "what did the guard see?" end to
    end (ADR-004 §1)."""

    def __init__(self, conn: sqlite3.Connection, actor: str = "runner") -> None:
        self._conn = conn
        self._actor = actor

    # -- the one write path for decisions ------------------------------------

    def _decide(self, kind: str, verdict: str, rule: str, reason: str,
                **detail: Any) -> Decision:
        emit(self._conn, "guard.decision", {
            "actor": self._actor,
            "kind": kind,
            "verdict": verdict,
            "rule": rule,
            "reason": reason,
            **detail,
        })
        self._conn.commit()
        return Decision(verdict, rule, reason)

    # -- tool crossings -------------------------------------------------------

    def check_tool(self, tool_name: str, *, level: int, privacy_floor: int,
                   data_class: int = DEFAULT_DATA_CLASS) -> Decision:
        """Gate one tool invocation. Called by the runner BEFORE spec.run().

        privacy_floor semantics (ADR-004 §3): the highest data class the tool is
        cleared to touch. Local read tools are cleared to PRIVATE; SENSITIVE is
        denied to every tool until one is explicitly raised (none today).
        """
        try:
            # 1. level gate: mutating tools have no path in the V1 skeleton —
            #    the confirmation flow lands with the approval surface (V1.5).
            if level > 0:
                return self._decide(
                    KIND_TOOL, DENY, "level-gate",
                    f"level {level} tool denied: confirmation flow not built",
                    tool=tool_name, level=level, data_class=data_class)

            # 2. clearance gate: data class must be within the tool's ceiling
            if data_class > privacy_floor:
                return self._decide(
                    KIND_TOOL, DENY, "privacy-ceiling",
                    f"data class {data_class} above tool clearance {privacy_floor}",
                    tool=tool_name, level=level, privacy_floor=privacy_floor,
                    data_class=data_class)

            return self._decide(
                KIND_TOOL, ALLOW, "within-clearance",
                "read-only tool within declared privacy clearance",
                tool=tool_name, level=level, privacy_floor=privacy_floor,
                data_class=data_class)

        except Exception as exc:  # fail closed, loudly, as an event
            return self._decide(
                KIND_TOOL, DENY, "guard-error",
                f"fail closed: {type(exc).__name__}: {exc}",
                tool=tool_name)

    # -- network / provider crossings ------------------------------------------

    def check_egress(self, kind: str, target: str,
                     data_class: int = PUBLIC) -> Decision:
        """V1 skeleton: no network path may exist outside the future provider
        registry. Deny is the only verdict — by construction and by test."""
        if kind == KIND_PROVIDER:
            return self._decide(
                KIND_PROVIDER, DENY, "no-provider-clients",
                "no provider clients exist; spend-guard branches land with the "
                "provider registry (unoverridable)",
                target=target, data_class=data_class)
        if kind == KIND_NETWORK:
            return self._decide(
                KIND_NETWORK, DENY, "egress-disabled",
                "no network egress in V1; allowlisted fetch lands with the "
                "content inbox (V2)",
                target=target, data_class=data_class)
        return self._decide(
            "unknown", DENY, "unknown-crossing",
            f"unknown crossing kind: {kind} (fail closed)",
            target=target, data_class=data_class)
