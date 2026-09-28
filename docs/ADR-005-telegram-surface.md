# ADR-005: Telegram as Thoth's second surface — briefing delivery, approval cards, no new permissions

- **Status:** Proposed (draft — pre-merge; staged for the Team-B review alongside
  ADR-003/004 outcomes)
- **Date:** 2026-09-28
- **Depends on:** ADR-002 (append-only event log; every surface act is an event),
  ADR-003 (runs park; bounds and privilege envelope travel in checkpoints),
  ADR-004 (the guard is the only consequential crossing; `require_confirmation`
  exists as a verdict), Azaris-benchmark (parity track: "always-on, pings you
  where you already are" — item 4, pulled forward to V1.5)
- **Constraint inherited from the spec (§3, §7):** $0 automatic spending, and the
  surface grants **no new permissions** — those two claims are load-bearing and
  are enforced structurally (§4), not by convention.

## Context

Thoth's surfaces so far are on-machine only: the CLI (`briefing`, `digest`,
`continue`) requires the operator to come to the workstation. The Azaris
benchmark identified the gap precisely: an "always-on" experience needs the
system to reach the operator where they already are — and to collect a decision
when an autonomous run parks on `require_confirmation` at 3 AM. Today that
decision waits, unbatched, in the next briefing; ADR-004 left the question open
(its Open Question 4: briefing-batched vs interrupting channel).

The candidate channels split quickly under the constraints:

| Channel | $0? | Operator-present? | Inbound port? | Cards? | Verdict |
|---|---|---|---|---|---|
| Telegram Bot API | yes (free tier) | yes (phone) | **no — poll-only** | yes (inline keyboards) | **chosen** |
| Email/SMTP | yes | laggy | n/a | no (out-of-band links) | rejected |
| Webhooks (TG mode) | yes | yes | **yes — public URL** | yes | rejected for now |
| Matrix/IRC/Slack | mostly | no / workspace-bound | varies | poor | rejected |
| GUI notification | yes | passive only | n/a | no | not a surface |

The decisive property of Telegram's **long-polling** `getUpdates` model: Thoth
makes *outbound* HTTPS calls only. There is no listener, no port forward, no
public URL — the inbound-attack surface is zero, which matters for a system
whose threat model (ADR-004) already treats the network as hostile.

## Decisions

### 1. The surface is a guarded crossing, not a side door

`telegram.py` becomes the package's second I/O module (after `ollama.py`), and
every one of its network calls passes `guard.check_egress("surface", target)`:

- **Endpoint allowlist, exact-host:** only `https://api.telegram.org` is
  constructible as a target. The guard denies every other host by rule
  (`surface-endpoint-allowlist`); there is no "fetch arbitrary URL" code path.
- **The bot token never enters the log.** It lives in the environment/config
  outside git; guard events and all surface events redact it (and any
  `bot<token>` path fragment) before `emit`. A token leak into the event log
  would be worse than a delivery failure.
- **Chat-id allowlist.** The token alone is not authentication — anyone who
  finds it could otherwise talk to Thoth. Updates from chats outside the
  single-operator allowlist are ignored, and the ignore is logged
  (`surface.rejected`), so probing is visible.
- **Polling is bounded and audited:** one `getUpdates` call per fixed cadence
  (default 30s) while the surface is enabled; each poll is a guard decision,
  so "was Thoth phoning out?" is answerable from the event log.

### 2. Privacy ceiling at delivery — the render is the redaction

Briefing/digest content is stored state, which includes private notes. Outbound
delivery therefore applies a **class ceiling at render time**, not a promise:

- `PUBLIC`/`PERSONAL`: delivered in full (default).
- `PRIVATE`: rendered as counts only — "2 private notes matched" — never bodies.
- `SENSITIVE`: excluded entirely; a placeholder notes that content was withheld.

This is the same taint discipline as ADR-004 §3, applied to the new direction of
travel (outbound to a third-party-hosted channel). The ceiling is enforced in
the render path, so a future richer delivery format inherits it for free.

### 3. Approval cards — the interrupting channel, fail-closed

ADR-004 Open Question 4 is resolved: the interrupting channel exists, it is
Telegram, and it is *pull-safe*:

- A run parking on `require_confirmation` emits `approval.requested` (run id,
  level, tool class, digested args) and the surface delivers a card with
  Approve / Reject inline buttons.
- The operator's tap arrives as a callback on the next poll; Thoth records
  `approval.granted` / `approval.denied` as events and the runner proceeds
  accordingly. **Timeout defaults to reject** (fail closed), with the duration
  carried in the card so silence is never ambiguous.
- A card grants nothing by itself. The approved action still crosses the guard
  like any other; the card only supplies the operator confirmation the verdict
  demanded. Cards cannot elevate: they are the operator's half of a
  `require_confirmation` handshake, never a permission grant.
- V1.5 ships the card *plumbing* with zero level ≥1 tools registered — it is
  integration-tested with scripted cards and sits unused until the
  confirmation-flow milestone lands its first level ≥1 tool. Honest scope:
  plumbing before need.

### 4. "No new permissions," enforced structurally

The claim is only real as a test:

- The surface module registers **zero ToolSpecs**; the tool registry is
  untouched. Delivery is read-only rendering of stored state.
- It cannot start runs, cannot alter tasks/notes, cannot touch the guard's
  verdicts. Its write-path is limited to *relaying operator decisions the guard
  would honor anyway* and emitting surface events.
- CI (the no-bypass suite) whitelists exactly three I/O modules — `guard.py`
  (the gate), `ollama.py` (providers), `telegram.py` (this surface) — and
  asserts the whitelist stays minimal; a scan also asserts `telegram.py`
  imports neither the tool registry nor the runner.
- Commands the operator can issue in chat are read-only pulls only (`/status`,
  `/briefing`, `/digest` — the stored-state reports that already exist). Free
  natural-language command of runs is **out of scope** until the surface has
  earned trust; the chat is a read/report/approve channel, not a cockpit.

### 5. V1.5 scope

In: briefing + digest delivery on schedule or on `/briefing`; parked-run
notifications; approval-card send/receive/timeout; read-only pull commands;
surface events in the log. Out: group chats, media ingestion (voice notes →
whisper is a V2 content-pipeline path, not a surface feature), conversational
control, any level ≥1 action.

## Consequences

- The "always-on" parity item lands at literal $0: a bot token, a poll loop,
  and the stored-state renderers Thoth already has. The operator's phone
  becomes a pane into the event log with a decision button.
- The event log now answers two new questions end to end: "what left the
  machine, redacted how?" and "who approved what, when?" — both previously
  unanswerable for any remote channel.
- Honest costs: polling latency (≤1 cadence period) instead of instant push;
  delivery depends on Telegram's availability (degradation is visible as
  `surface.delivery_failed` and never blocks a run — parking and the local
  briefing remain the source of truth); one more module under the no-bypass
  scan.
- The privacy ceiling trades completeness for safety in exactly one place
  (outbound render); on-machine surfaces keep full fidelity.

## Alternatives rejected (for the record)

- **Webhook mode:** requires a public URL/reverse proxy — the inbound surface
  ADR-004's threat model exists to avoid. Revisit only if the operator
  deliberately hosts one.
- **Email as the channel:** asynchronous, no inline decision primitive, and
  card-by-link is phishing-shaped. The V2 Gmail triage item is *ingestion*,
  not this surface.
- **Chat as a command cockpit (NL control of runs):** permission creep — a
  channel that can *do* everything the operator can is a second operator.
  Read-only pulls now; revisit via a separate ADR if ever.
- **Pushing raw event-log lines:** the §49/§50 discipline — a firehose the
  operator mutes in a week is not a surface. Delivery is capped renderings
  (briefing/digest/card), never raw logs.

## Revisit triggers

- Second operator / shared household use → chat-id allowlist becomes a real
  ACL with per-chat ceilings (new ADR, not a flag).
- Telegram API/ToS shifts (pricing, bot rules) → the surface is one module;
  swap to the next poll-style channel without touching guard or runner.
- First level ≥1 tool lands → card timeout duration and audit review become
  operational parameters, not design afterthoughts.
- Operator hosts a reverse proxy anyway → webhook mode may reduce latency;
  keep polling as the fail-safe default.

## Open questions staged for the Team-B review

1. Card timeout default (current: 12h, reject on expiry) — right horizon for
   overnight runs, or should expiry be per-level?
2. Should `/briefing` in chat honor a per-chat unlock for PRIVATE content
   (explicit re-auth gesture), or is counts-only permanent? (Current:
   counts-only, no unlock.)
3. Poll cadence 30s vs adaptive (fast while a card is pending, slow otherwise)?
   Cost is battery/quota-neutral at personal scale; simplicity favors fixed.
4. Does the surface client belong in the process (current) or a hardened
   subprocess like ADR-004 Open Question 3 considers for providers?
