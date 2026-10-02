"""Telegram surface tests (ADR-005): guarded transport, token hygiene, privacy
ceiling at delivery, fail-closed approval cards, allowlisted polling.

The network is NEVER touched — `telegram._api` is patched in every test. The
guard is real: a stock-guard deny must fail the call closed.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from thoth import briefing, db, events, session, telegram
from thoth.guard import Guard


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture()
def token(monkeypatch):
    monkeypatch.setenv("THOTH_TG_TOKEN", "123:ABC-DEF")
    monkeypatch.setenv("THOTH_TG_CHAT", "42")
    return "123:ABC-DEF"


def _guard_events(conn):
    return [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind='guard.decision'")]


# ------------------------------------------------- privacy ceiling (ADR-005 §2)

def test_privacy_ceiling_private_renders_counts_only():
    report = {"project": "p", "item_count": 2, "needs_you": True,
              "sections": [("open work", ["[todo] secret task (t1)",
                                          "[todo] another (t2)"], telegram.PRIVATE_CLASS)]}
    out = telegram.apply_privacy_ceiling(report, telegram.PERSONAL_CLASS)
    assert "secret" not in str(out)
    assert any("2 items withheld" in line
               for _, lines in out["sections"] for line in lines)


def test_privacy_ceiling_sensitive_withholds_entirely():
    report = {"project": "p", "sections": [("open work", ["x"], telegram.SENSITIVE_CLASS)]}
    out = telegram.apply_privacy_ceiling(report, telegram.PRIVATE_CLASS)
    assert out.get("withheld") is True and "sections" not in out


def test_privacy_ceiling_personal_delivers_in_full():
    report = {"project": "p", "sections": [("open work", ["plain item"])]}
    out = telegram.apply_privacy_ceiling(report, telegram.PERSONAL_CLASS)
    assert out["sections"] == report["sections"]


# ------------------------------------------------------------- token hygiene

def test_missing_token_raises_clean_error(conn):
    with pytest.raises(telegram.SurfaceConfigError):
        telegram.build_surface(conn, chat_id="42")


def test_token_never_enters_the_log(conn, token):
    s = telegram.build_surface(conn, chat_id="42")
    with patch.object(telegram, "_api", return_value={"ok": True}) as api:
        s.send_message("hello")
    assert api.called
    payload = json.dumps(_guard_events(conn))
    assert "ABC-DEF" not in payload


# ----------------------------------------------------------- guarded egress

def test_send_goes_through_guard_allowlist(conn, token):
    s = telegram.build_surface(conn, chat_id="42")
    with patch.object(telegram, "_api", return_value={"ok": True}) as api:
        s.send_message("briefing body")
    called_url = api.call_args[0][0]
    assert called_url.startswith("https://api.telegram.org/bot")
    kinds = [(e["kind"], e["verdict"]) for e in _guard_events(conn)]
    assert ("surface", "allow") in kinds


def test_guard_deny_fails_call_closed(conn, token, monkeypatch):
    """The surface honors the guard contract: a deny must block the send —
    nothing leaves the machine even when the transport would succeed."""
    from thoth.guard import Decision, DENY

    class DenyingGuard:
        def __init__(self, *a, **k):
            pass

        def check_egress(self, *a, **k):
            return Decision(DENY, "surface-endpoint-allowlist", "stub deny")

    monkeypatch.setattr(telegram, "Guard", DenyingGuard)
    s = telegram.build_surface(conn, chat_id="42")
    with patch.object(telegram, "_api", return_value={"ok": True}) as api:
        with pytest.raises(telegram.SurfaceUnavailable):
            s.send_message("should not leave the machine")
    assert not api.called


# ------------------------------------------------- delivery privacy ceiling

def _seed_briefing_world(conn):
    # sessions are a derived table (ADR-002): seed via the facade, not raw events
    sid = session.start(conn, project="p")
    session.stop(conn, sid, summary="did things")


def test_briefing_delivery_public_renders_in_full(conn, token):
    _seed_briefing_world(conn)
    s = telegram.build_surface(conn, chat_id="42")
    with patch.object(telegram, "_api", return_value={"ok": True}) as api:
        s.deliver_briefing()
    body = api.call_args[0][1]["text"]
    assert "did things" in body


def test_digest_delivery_uses_digest_renderer(conn, token):
    from thoth import digest as digest_mod
    _seed_briefing_world(conn)
    s = telegram.build_surface(conn, chat_id="42")
    with patch.object(digest_mod, "render",
                      return_value="DIGEST-BODY") as r, \
         patch.object(telegram, "_api", return_value={"ok": True}) as api:
        s.deliver_digest()
    assert r.called
    assert api.call_args[0][1]["text"] == "DIGEST-BODY"


# --------------------------------------------------------- approval cards

def test_card_sent_with_timeout_and_keyboard(conn, token):
    s = telegram.build_surface(conn, chat_id="42")
    with patch.object(telegram, "_api", return_value={"ok": True}) as api:
        s.send_card(run_id="r1", summary="deploy to prod", level=3, timeout_hours=12)
    payload = api.call_args[0][1]
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert buttons[0]["callback_data"].startswith("approve:ar-")
    assert buttons[1]["callback_data"].startswith("reject:ar-")
    assert "12h" in payload["text"] and "reject" in payload["text"].lower()


def test_card_expiry_rejects_fail_closed(conn, token):
    """A card whose timeout elapsed resolves to DENY, never silently open."""
    s = telegram.build_surface(conn, chat_id="42")
    req_id = s.open_card(run_id="r1", timeout_hours=0)
    assert s.card_decision(req_id) == "denied"


def test_card_decision_recorded_and_delivered(conn, token):
    s = telegram.build_surface(conn, chat_id="42")
    with patch.object(telegram, "_api", return_value={"ok": True}):
        req_id = s.open_card(run_id="r1", timeout_hours=1)
        s.record_decision(req_id, "granted")
    assert s.card_decision(req_id) == "granted"
    kinds = [r["kind"] for r in conn.execute(
        "SELECT kind FROM events WHERE kind LIKE 'approval.%'")]
    assert "approval.requested" in kinds and "approval.granted" in kinds


def test_callback_from_unlisted_chat_is_ignored(conn, token):
    s = telegram.build_surface(conn, chat_id="42")
    updates = [{"update_id": 1, "callback_query": {
        "data": "approve:r1", "message": {"chat": {"id": 999}}}}]
    with patch.object(telegram, "_api", return_value={"ok": True,
                                                      "result": updates}):
        handled = s.poll_once()
    assert handled == []
    rej = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind='surface.rejected'")]
    assert rej and rej[0]["rule"] == "chat-allowlist"


# ----------------------------------------------------------------- polling

def test_poll_once_processes_allowed_chat_and_ignores_others(conn, token):
    s = telegram.build_surface(conn, chat_id="42")
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/briefing"}},
        {"update_id": 2, "message": {"chat": {"id": 43}, "text": "/briefing"}},
    ]
    with patch.object(telegram, "_api", return_value={"ok": True,
                                                      "result": updates}) as api:
        handled = s.poll_once()
    assert len(handled) == 1
    assert api.call_count == 2  # one poll + one delivery for the allowed chat


def test_poll_delivery_failure_never_raises(conn, token):
    """Degradation: a failed Telegram API call is an event, not a crash."""
    s = telegram.build_surface(conn, chat_id="42")
    with patch.object(telegram, "_api",
                      side_effect=telegram.SurfaceUnavailable("net down")):
        s.deliver_briefing()  # must not raise
    fails = [e for e in _guard_events(conn) if e.get("kind") == "surface"]
    # delivery failure is surfaced via surface.* events, not silence
    surfaced = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind='surface.delivery_failed'")]
    assert surfaced


# --------------------------------- one-shot truthfulness (the CLI contract)

def test_deliver_briefing_returns_true_on_success(conn, token):
    _seed_briefing_world(conn)
    s = telegram.build_surface(conn, chat_id="42")
    with patch.object(telegram, "_api", return_value={"ok": True}):
        assert s.deliver_briefing() is True


def test_deliver_digest_returns_true_on_success(conn, token):
    s = telegram.build_surface(conn, chat_id="42")
    with patch.object(telegram, "_api", return_value={"ok": True}):
        assert s.deliver_digest() is True


def test_deliver_briefing_returns_false_when_transport_fails(conn, token):
    """The serve loop may degrade to events, but the one-shot CLI must be able
    to say the truth: the message did not leave the machine. The failure is
    still an event, never a raise."""
    _seed_briefing_world(conn)
    s = telegram.build_surface(conn, chat_id="42")
    with patch.object(telegram, "_api",
                      side_effect=telegram.SurfaceUnavailable("net down")):
        assert s.deliver_briefing() is False
    surfaced = [json.loads(r["payload_json"]) for r in conn.execute(
        "SELECT payload_json FROM events WHERE kind='surface.delivery_failed'")]
    assert surfaced
