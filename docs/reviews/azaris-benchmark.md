# Benchmark: Azaris (azaris.ai) vs Thoth at $0

- **Date:** 2026-09-27
- **Question:** *how close can Thoth get to Azaris under the $0 automatic-spend policy?*
- **Answer:** ~80–85% of the experience is reachable at $0 on the existing roadmap;
  the unreachable remainder is iMessage, cloud-browser web actions, integration
  breadth, and metered-token autonomy depth.

## What Azaris actually is

A $97/month orchestration platform: the operator's **own metered API keys**
("bring your own model key"), their cloud infrastructure (browser, uptime,
connectors), and — the real product — an integration library (Gmail, Calendar,
iMessage, QuickBooks, Meta Ads, Slack/Telegram), proactive overnight loops, and
approval cards before consequential actions. Pricing itself concedes the strategic
point: **the tokens were never the moat; the plumbing is.**

## Capability map at $0

| Azaris capability | Thoth $0 path | Verdict |
|---|---|---|
| Always-on agent | `lone1` runs 24/7 (owned hardware; systemd per ADR-001) | **Match** |
| Persistent memory | SQLite (ADR-002) + full audit trail they don't offer | **Beat** |
| Approval cards | `guard.require_confirmation` (ADR-004) + per-domain levels | **Beat** |
| Overnight reports | `thoth briefing` from stored state (V1) | **Match** |
| Telegram channel | Bot API is free; another surface for briefing + approvals | **Match** (V1.5) |
| Gmail/Calendar read | Official APIs, OAuth, free at personal scale, read-only first | **Match** (V2) |
| Email actions | Gmail send = free; L3 + typed confirmation | **Match**, later |
| iMessage | Needs a Mac | ✗ gap |
| Cloud browser (web actions) | Local browser only; headless is fragile + ToS-gray | ✗ / partial |
| 1,000+ tools | Each = engineering time, not money; MCP-era connectors help | ◑ time curve |
| Voice | Local Whisper + TTS, $0, V3 | ◑ quality below theirs |
| White-glove setup | Their business model, not a feature | n/a |

## Where Thoth is structurally ahead

- **Stricter than Azaris's own model:** they bill $97/mo *and* run on metered keys;
  Thoth runs on subscriptions + local models with a fail-closed guard (ADR-004) and
  an append-only audit log (ADR-002).
- Privacy and auditability are architecture, not a trust promise.

## Where $0 caps parity (honest)

The LLM budget. Azaris agents burn metered tokens — deep multi-hour autonomous
runs, heavy daily proactive work. Thoth lives inside subscription quotas + weak-ish
local hardware (Team-A §15: the P520 is a floor, not an engine). Expect tighter run
budgets, more parking, more local-model grunt work. **Match their breadth; not their
depth-per-day of autonomy.** That is the price of $0 — not missing features.

## Pulled forward into the roadmap (the three capabilities actually worth using)

1. **Overnight briefing** — pulled from V1-general into the next build step; pure
   stored state, zero network, zero AI (V1 parity now).
2. **Telegram channel** — V1.5: Bot API (free), delivers the briefing, renders
   approval cards wired to `guard.require_confirmation`, grants **no** new
   permissions (ADR-004: the guard computes ceilings from stored state, never from
   the channel).
3. **Email triage (Gmail read-only)** — V2: official API, OAuth, scope discipline,
   ingestion into the content inbox with ADR-004 origin tainting.

## Anti-goal (§45 discipline intact)

Do **not** chase the 1,000-tools logo wall. Pick the capabilities actually used,
wire them into the existing phase gates, keep every integration behind the guard
and the inbox.
