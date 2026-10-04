"""The security choke point (ADR-004): one fail-closed guard for every
consequential crossing.

Egress policy (post surface branch, ADR-005):
- `provider` crossings to **`local:` targets** are ALLOWED — the local model
  path (Ollama on loopback; the caller module enforces loopback, the guard
  enforces the decision and logs it).
- `surface` crossings to **exactly** `https://api.telegram.org` are ALLOWED —
  the second surface's transport (ADR-005 §1); lookalike hosts, other hosts,
  and plaintext downgrades are denied by the same rule.
- Every other egress — cloud providers, generic network — stays DENIED: cloud
  spend branches land with the provider registry and are unoverridable.

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
from urllib.parse import urlparse

from .events import emit
from . import permissions

# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------

ALLOW = "allow"
DENY = "deny"
REQUIRE_CONFIRMATION = "require_confirmation"

KIND_TOOL = "tool"
KIND_NETWORK = "network"      # any outbound network call
KIND_PROVIDER = "provider"    # outbound AI request (subset of network)
KIND_SURFACE = "surface"      # outbound surface delivery (ADR-005: Telegram)

LOCAL_TARGET_PREFIX = "local:"  # e.g. "local:ollama@http://127.0.0.1:11434"

# ADR-005 §1: the surface's exact-host allowlist. Lookalike hosts and scheme
# downgrades fail the tuple match; there is no wildcard and no exception path.
SURFACE_ALLOWED_HOSTS = ("api.telegram.org",)

# Privacy classes (ADR-004): higher = more sensitive. Default for data of
# unknown provenance is PRIVATE (fail closed). SENSITIVE is local-only and
# cleared to no tool today.
PUBLIC, PERSONAL, PRIVATE, SENSITIVE = 0, 1, 2, 3

DEFAULT_DATA_CLASS = PRIVATE


@dataclass
class Decision:
    """The result of one guarded crossing. Always loggable, always explainable."""

    verdict: str                 # ALLOW | DENY | REQUIRE_CONFIRMATION
    rule: str                    # machine-readable rule id
    reason: str                  # human-readable explanation
    token: str | None = None     # typed-confirmation token (require_confirmation)

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
        return Decision(verdict, rule, reason, token=detail.get("token"))

    # -- tool crossings -------------------------------------------------------

    def check_tool(self, tool_name: str, *, level: int, privacy_floor: int,
                   data_class: int = DEFAULT_DATA_CLASS,
                   run_id: str | None = None,
                   args: dict[str, Any] | None = None) -> Decision:
        """Gate one tool invocation. Called by the runner BEFORE spec.run().

        Order of gates (ADR-004):
        1. privacy ceiling: the data class must sit within the tool's declared
           clearance - always first; a read of sensitive data denies at any
           level. SENSITIVE is denied to every tool until one is explicitly
           raised (none today).
        2. Observe tools (level 0) allow within clearance - the entire current
           registry travels this path.
        3. Mutating tools (level >= 1) must sit within the operator's
           {domain -> level} ceiling (permissions.py; no row = 0 = inert by
           default) AND carry a typed confirmation: the guard mints a token
           for exactly this (run_id, tool, args); the operator types the full
           token (`thoth run confirm <token>`), which lands as a
           `guard.confirmed` event and unlocks the identical action for this
           run only. No run-level privilege envelopes exist yet (ADR-004
           section 5), so the effective ceiling is the operator's table.

        Fail closed on any internal error.
        """
        try:
            # 1. clearance gate: data class must be within the tool's ceiling
            if data_class > privacy_floor:
                return self._decide(
                    KIND_TOOL, DENY, "privacy-ceiling",
                    f"data class {data_class} above tool clearance {privacy_floor}",
                    tool=tool_name, level=level, privacy_floor=privacy_floor,
                    data_class=data_class)

            # 2. observe tools: the read-only path
            if level <= 0:
                return self._decide(
                    KIND_TOOL, ALLOW, "within-clearance",
                    "read-only tool within declared privacy clearance",
                    tool=tool_name, level=level, privacy_floor=privacy_floor,
                    data_class=data_class)

            # 3. mutating tools: operator ceiling, then typed confirmation
            domain = permissions.domain_of(tool_name)
            op_ceiling = permissions.ceiling(self._conn, domain)
            if level > op_ceiling:
                return self._decide(
                    KIND_TOOL, DENY, "domain-ceiling",
                    f"level {level} above operator ceiling {op_ceiling} "
                    f"for domain '{domain}'",
                    tool=tool_name, level=level, domain=domain,
                    ceiling=op_ceiling, run_id=run_id, data_class=data_class)

            token = permissions.confirmation_token(tool_name, args, run_id)
            if permissions.is_confirmed(self._conn, token, run_id):
                return self._decide(
                    KIND_TOOL, ALLOW, "operator-confirmed",
                    "mutating action within ceiling; operator typed its token",
                    tool=tool_name, level=level, domain=domain,
                    token=token, run_id=run_id, data_class=data_class)
            return self._decide(
                KIND_TOOL, REQUIRE_CONFIRMATION, "typed-confirmation-required",
                f"mutating tool within ceiling: operator must type the token "
                f"({token}) to unlock it for this run",
                tool=tool_name, level=level, domain=domain,
                token=token, run_id=run_id, data_class=data_class)

        except Exception as exc:  # fail closed, loudly, as an event
            return self._decide(
                KIND_TOOL, DENY, "guard-error",
                f"fail closed: {type(exc).__name__}: {exc}",
                tool=tool_name)

    # -- network / provider crossings ------------------------------------------

    def check_egress(self, kind: str, target: str,
                     data_class: int = PUBLIC) -> Decision:
        """        Egress gate. Exactly two allow-rules exist:

        `provider` crossings whose target starts with ``local:`` — the local
        model path (Ollama on loopback; the caller module enforces loopback,
        the guard enforces the decision and logs it).

        `surface` crossings to exactly ``https://api.telegram.org`` — the
        Telegram surface transport (ADR-005); exact host + https only.

        Cloud providers and all generic network egress stay DENIED: the
        spend-guard branches land with the provider registry and are
        unoverridable (ADR-004 §2).
        """
        if kind == KIND_SURFACE:
            parts = urlparse(target) if isinstance(target, str) else None
            if (parts is not None and parts.scheme == "https"
                    and (parts.hostname or "") in SURFACE_ALLOWED_HOSTS):
                return self._decide(
                    KIND_SURFACE, ALLOW, "surface-endpoint-allowlist",
                    "exact allowlisted surface host over https",
                    target=target, data_class=data_class)
            return self._decide(
                KIND_SURFACE, DENY, "surface-endpoint-allowlist",
                "only https://api.telegram.org is allowlisted (exact host)",
                target=target, data_class=data_class)
        if kind == KIND_PROVIDER:
            if isinstance(target, str) and target.startswith(LOCAL_TARGET_PREFIX):
                return self._decide(
                    KIND_PROVIDER, ALLOW, "local-egress",
                    "local model path: loopback enforced by the caller module",
                    target=target, data_class=data_class)
            return self._decide(
                KIND_PROVIDER, DENY, "cloud-egress-denied",
                "cloud providers stay unreachable: $0 spend guard (unoverridable)",
                target=target, data_class=data_class)
        if kind == KIND_NETWORK:
            return self._decide(
                KIND_NETWORK, DENY, "egress-disabled",
                "no generic network egress; allowlisted fetch lands with the "
                "content inbox (V2)",
                target=target, data_class=data_class)
        return self._decide(
            "unknown", DENY, "unknown-crossing",
            f"unknown crossing kind: {kind} (fail closed)",
            target=target, data_class=data_class)
