"""The Telegram surface (ADR-005): briefing/digest delivery, approval-card
plumbing, allowlisted polling — the operator's phone as a pane into the event
log with a decision button.

Structural guarantees (ADR-005):
- **Guarded transport.** This is the package's third I/O module (guard.py gates,
  ollama.py providers, telegram.py the surface). Every network call passes
  `guard.check_egress("surface", ...)` first; the guard allowlists exactly
  `https://api.telegram.org` (exact host, https only).
- **Token hygiene.** The bot token comes from the environment (THOTH_TG_TOKEN),
  never the repo; guard events and surface events carry a redacted target, and
  API-failure reasons are redacted before emit.
- **Chat-id allowlist.** The token alone is not authentication: updates from
  chats outside the allowlist are ignored *and logged* (`surface.rejected`).
- **No new permissions.** Zero ToolSpecs; delivery is read-only rendering of
  stored state; cards grant nothing by themselves — they relay the operator's
  half of a `require_confirmation` handshake, and the approved action still
  crosses the guard like any other.
- **Fail closed, degrade gracefully.** Guard deny raises (nothing leaves the
  machine); API failure is an event (`surface.delivery_failed`) and never a
  crash — parking and the local briefing remain the source of truth.

Privacy ceiling at delivery (ADR-005 §2) applies to the briefing path via
`apply_privacy_ceiling`; the digest inherits it when digest content grows
classed fields (V2).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any
from urllib import request as _urlrequest
from urllib.error import URLError

from . import briefing as briefing_mod
from . import digest as digest_mod
from .events import emit
from .guard import Guard, KIND_SURFACE, PRIVATE, PERSONAL, SENSITIVE

API_BASE = "https://api.telegram.org"
POLL_TIMEOUT_S = 25            # server-side long-poll wait per getUpdates
DEFAULT_CADENCE_NOTE = "one audited poll per cycle; each poll is a guard event"

# re-exported for render policies and tests
PERSONAL_CLASS = PERSONAL
PRIVATE_CLASS = PRIVATE
SENSITIVE_CLASS = SENSITIVE

MAX_MESSAGE = 4000             # Telegram's hard limit is 4096; keep headroom


class SurfaceConfigError(RuntimeError):
    """Missing/malformed surface configuration (token or chat allowlist)."""


class SurfaceUnavailable(RuntimeError):
    """Guard-denied or transport-failed surface call — logged, never fatal."""


def _redact(text: str, token: str) -> str:
    return text.replace(token, "[redacted]") if token else text


# ---------------------------------------------------------------------------
# privacy ceiling (ADR-005 §2): the render is the redaction
# ---------------------------------------------------------------------------

def apply_privacy_ceiling(report: dict[str, Any], ceiling: int) -> dict[str, Any]:
    """`ceiling` is the maximum data class delivered in full. Sections may
    carry an explicit class as a third element; unclassed sections default to
    PERSONAL (working state). PRIVATE-classed sections render as counts;
    SENSITIVE content withholds the whole report (conservative, and the only
    shape V1 needs — classed sections arrive with V2 content)."""
    sections = report.get("sections", [])
    for section in sections:
        if len(section) > 2 and section[2] >= SENSITIVE:
            return {"project": report.get("project"), "withheld": True,
                    "needs_you": bool(report.get("needs_you")),
                    "note": "content withheld (sensitive)"}
    kept = []
    for section in sections:
        title, items = section[0], section[1]
        cls = section[2] if len(section) > 2 else PERSONAL
        if cls > ceiling:
            kept.append((title, [f"{len(items)} items withheld (private)"]))
        else:
            kept.append((title, list(items)))
    out = dict(report)
    out["sections"] = kept
    return out


def _render_report(report: dict[str, Any]) -> str:
    lines = [f"Thoth briefing - project: {report.get('project') or 'all'}"]
    for title, items in report.get("sections", []):
        lines.append(f"\n{title}:")
        lines.extend(f"  {item}" for item in items)
    if not report.get("needs_you"):
        lines.append("\nNothing needs you today.")
    return "\n".join(lines)[:MAX_MESSAGE]


def _render_card(run_id: str, summary: str, level: int, timeout_hours: int) -> str:
    return (f"APPROVAL REQUIRED (run {run_id})\n"
            f"action class: level {level}\n"
            f"{summary}\n"
            f"timeout: {timeout_hours}h - silence = REJECT")


# ---------------------------------------------------------------------------
# transport (the single network primitive; callers gate + redact)
# ---------------------------------------------------------------------------

def _api(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = _urlrequest.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _urlrequest.urlopen(req, timeout=POLL_TIMEOUT_S + 10) as resp:  # noqa: S310 (guard-allowlisted host only)
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError) as exc:
        raise SurfaceUnavailable(
            f"telegram api failed: {type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# the surface object
# ---------------------------------------------------------------------------

class Surface:
    def __init__(self, conn, token: str, chat_id: str) -> None:
        self._conn = conn
        self._token = token
        self.chat_id = chat_id
        self._guard = Guard(conn, actor="telegram-surface")
        self._cards: dict[str, dict[str, Any]] = {}

    # -- guarded transport ----------------------------------------------------

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{API_BASE}/bot{self._token}/{method}"
        # the guard sees a redacted target: the token never enters the log
        target = f"{API_BASE}/bot[redacted]/{method}"
        decision = self._guard.check_egress(KIND_SURFACE, target)
        if not decision.allowed:
            raise SurfaceUnavailable(f"guard denied: {decision.reason}")
        try:
            return _api(url, payload)
        except SurfaceUnavailable as exc:
            emit(self._conn, "surface.delivery_failed",
                 {"method": method, "reason": _redact(str(exc), self._token)[:200]})
            self._conn.commit()
            raise

    # -- delivery --------------------------------------------------------------

    def send_message(self, text: str) -> None:
        self._call("sendMessage",
                   {"chat_id": self.chat_id, "text": text[:MAX_MESSAGE]})

    def deliver_briefing(self, project: str | None = None,
                         ceiling: int = PERSONAL_CLASS) -> bool:
        """Returns True iff the message actually left the machine. Failure
        stays an event (surface.delivery_failed), never a raise - the serve
        loop degrades - but one-shot callers get the truth."""
        report = briefing_mod.build(self._conn, project)
        body = _render_report(apply_privacy_ceiling(report, ceiling))
        try:
            self.send_message(body)
            emit(self._conn, "surface.delivered",
                 {"kind": "briefing", "items": report.get("item_count", 0),
                  "ceiling": ceiling})
            self._conn.commit()
            return True
        except SurfaceUnavailable:
            return False  # already an event; delivery is never load-bearing

    def deliver_digest(self, project: str | None = None) -> bool:
        """Returns True iff the message actually left the machine."""
        body = digest_mod.render(digest_mod.build(self._conn, project))
        try:
            self.send_message(body)
            emit(self._conn, "surface.delivered", {"kind": "digest"})
            self._conn.commit()
            return True
        except SurfaceUnavailable:
            return False

    # -- approval cards (ADR-005 §3: plumbing before need) ----------------------

    def open_card(self, run_id: str, timeout_hours: int = 12) -> str:
        req_id = f"ar-{uuid.uuid4().hex[:8]}"
        self._cards[req_id] = {
            "run_id": run_id, "expires": time.time() + timeout_hours * 3600}
        emit(self._conn, "approval.requested",
             {"request_id": req_id, "run_id": run_id,
              "timeout_hours": timeout_hours})
        self._conn.commit()
        return req_id

    def send_card(self, run_id: str, summary: str, level: int,
                  timeout_hours: int = 12) -> str:
        req_id = self.open_card(run_id, timeout_hours)
        self._call("sendMessage", {
            "chat_id": self.chat_id,
            "text": _render_card(run_id, summary, level, timeout_hours),
            "reply_markup": {"inline_keyboard": [[
                {"text": "Approve", "callback_data": f"approve:{req_id}"},
                {"text": "Reject", "callback_data": f"reject:{req_id}"},
            ]]}})
        emit(self._conn, "surface.card_sent",
             {"request_id": req_id, "run_id": run_id})
        self._conn.commit()
        return req_id

    def record_decision(self, req_id: str, decision: str) -> None:
        card = self._cards.get(req_id)
        emit(self._conn, f"approval.{decision}",
             {"request_id": req_id,
              "run_id": card["run_id"] if card else None})
        self._conn.commit()
        if card is not None:
            card["decision"] = decision

    def card_decision(self, req_id: str) -> str:
        """The decision state of one card. Timeout = denied (fail closed)."""
        card = self._cards.get(req_id)
        if card is None:
            return "unknown"
        if card.get("decision"):
            return card["decision"]
        if time.time() >= card["expires"]:
            card["decision"] = "denied"
            emit(self._conn, "approval.denied",
                 {"request_id": req_id, "reason": "timeout"})
            self._conn.commit()
            return "denied"
        return "pending"

    # -- polling (bounded, audited, allowlisted) --------------------------------

    def poll_once(self) -> list[str]:
        """One long-poll getUpdates; every poll is a guard decision; updates
        from unlisted chats are ignored and logged. Returns handled items."""
        data = self._call("getUpdates", {"timeout": POLL_TIMEOUT_S})
        handled: list[str] = []
        for upd in data.get("result") or []:
            msg = upd.get("message") or {}
            cb = upd.get("callback_query") or {}
            chat = ((msg.get("chat") or {})
                    or ((cb.get("message") or {}).get("chat") or {})).get("id")
            if str(chat) != self.chat_id:
                emit(self._conn, "surface.rejected",
                     {"rule": "chat-allowlist", "chat_id": str(chat)})
                self._conn.commit()
                continue
            text = msg.get("text") or ""
            if text.startswith("/"):
                self._handle_command(text)
                handled.append(text)
            elif cb.get("data"):
                self._handle_callback(cb["data"])
                handled.append(cb["data"])
        return handled

    def _handle_command(self, text: str) -> None:
        cmd = text.split()[0].split("@")[0].lower()
        try:
            if cmd == "/briefing":
                self.deliver_briefing()
            elif cmd == "/digest":
                self.deliver_digest()
            elif cmd == "/status":
                cur = self._conn.execute(
                    "SELECT id, goal FROM runs WHERE status='running' LIMIT 1"
                ).fetchone()
                self.send_message(
                    f"running: {cur['id']} ({cur['goal']})" if cur
                    else "no running run")
            else:
                self.send_message("commands: /briefing /digest /status")
        except SurfaceUnavailable:
            pass  # delivery failure already an event

    def _handle_callback(self, data: str) -> None:
        action, _, req_id = data.partition(":")
        if action in ("approve", "reject") and req_id in self._cards:
            self.record_decision(
                req_id, "granted" if action == "approve" else "denied")


# ---------------------------------------------------------------------------
# construction + the serve loop
# ---------------------------------------------------------------------------

def build_surface(conn, chat_id: str | None = None) -> Surface:
    token = os.environ.get("THOTH_TG_TOKEN", "").strip()
    if not token:
        raise SurfaceConfigError(
            "THOTH_TG_TOKEN not set - the token lives in the environment, "
            "never in the repo or the log (ADR-005)")
    chat = (chat_id or os.environ.get("THOTH_TG_CHAT", "") or "").strip()
    if not chat:
        raise SurfaceConfigError(
            "chat_id required (THOTH_TG_CHAT) - the chat-id allowlist is the "
            "second factor; a token alone must not command Thoth")
    return Surface(conn, token, chat)


def serve(conn, chat_id: str | None = None,
          max_cycles: int | None = None) -> None:
    """The V1.5 poll loop: continuous long-polling while enabled. Each cycle is
    a guard-decided, logged crossing; failures degrade to events. Stop with
    Ctrl-C; `max_cycles` bounds it for tests/smoke."""
    surface = build_surface(conn, chat_id)
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        try:
            surface.poll_once()
        except SurfaceUnavailable:
            pass  # logged; retry after the next long-poll
        cycles += 1
