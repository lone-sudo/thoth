# ROADMAP

Sequencing rule: **build the invariant core first; anything the Team-B merge could
overturn stays unbuilt until the merge lands.**

Parity track (see `docs/reviews/azaris-benchmark.md`): ~80–85% of Azaris-class
capability is reachable at $0. Pulled forward: **overnight briefing (V1, now)**,
**Telegram surface (V1.5, free Bot API, no new permissions)**, **email triage
(V2, Gmail read-only via official API, guard-tainted ingestion)**.

## V0 — walking skeleton (now)

- [x] Sessions: `thoth start / stop / continue / status`
- [x] Append-only event log (`thoth log`)
- [x] Tasks with dependencies (`thoth task add/list/next/update`)
- [x] Read-only git snapshot in `continue` (branch, dirty files, last commit, stashes)
- [x] Notes store (facts / decisions / preferences / lessons; supersede, never delete)
- [x] ADR-001 (monolith, SQLite, CLI, stdlib-only), ADR-002 (events → derived views)
- [x] Golden-set eval harness (30 stored-state questions answered from the DB only —
      `python -m evals.run_golden`, also wired into pytest)

**Milestone test:** tell Thoth what you're working on, close the laptop, come back in
two days, run `thoth continue` — it reconstructs where you were. Zero AI calls.

## V0.2 — the runner loop

*Design: **ADR-003** (pre-merge draft — built as skeleton; merge review may refine).*

- [x] Checkpointed loop: load context package → plan → act via one tool → verify →
      checkpoint → repeat; "resume after crash" and "continue" = same code path
- [x] 3 tools, read-only: shell (allowlist-scoped), files (workspace-scoped), memory
      search (FTS over notes)
- [x] `run.*` checkpoint events with bounds carried on resume; parked-run surfacing
      in `thoth continue`
- [x] Routing log schema (`task_class, provider, latency, outcome`) — collected from
      day one, *used* in V3 *(provider.route / provider.attempt / provider.outcome
      events now emitted by the ModelPlanner; latency arrives with the first client)*
- [ ] One provider, manual override; no learned routing *(planner exists; client
      pending — Ollama local first, guard-gated; planner model selected:
      qwen2.5-3b, per the V3+ decision record below)*

## V1 — the operating layer

*Security design: **ADR-004** (choke point, spend guard, privacy floors, injection
defense) — lands together with the provider registry, not after.*

- [x] Provider registry + availability cache + capability floors + degradation ladder
      *(skeleton: providers declared, local-first ladder, fail-closed availability,
      `attempt()` raises for all — no clients yet; runner parks on exhausted ladder)*
- [ ] $0 spend guard + privacy floor at the single outbound choke point (`guard.py`,
      fail-closed, every decision an event; CI test: no provider import outside guard)
- [ ] Permission table `{domain → level}` + typed confirmation for destructive actions
- [x] Morning briefing (`thoth briefing`): generated from stored state only, ≤7
      items, "nothing needs you" is a valid output, parked runs top-of-list with
      resume commands (parity track item 1; Azaris-benchmark)
- [ ] Tasks get deadlines; end-of-day digest + weekly review
- [ ] Telegram surface (V1.5): Bot API briefing delivery + approval cards wired to
      `guard.require_confirmation`; grants no new permissions (ADR-005;
      implemented — `thoth telegram send-briefing|send-digest|serve` with
      guard-allowlisted transport and card plumbing; live activation pending
      the operator's bot token, level ≥1 tools still absent by design)
- [ ] 5–8 tools; git branch protocol (AI works only on `thoth/*` branches)

## V2 — memory + content

- [ ] Content inbox (file/URL/bookmark/YouTube) — capture-first, budgeted nightly
      processing, digest surfacing; platform pull-sync is never a foundation
- [ ] Weekly knowledge review: near-dup clustering, merge-with-click, disagreement flags
- [ ] Project consolidation: regenerate Project Briefs from notes; superseded, not deleted
- [ ] lone1 ↔ ZBook registry (thin SSH client); claim *provenance* labels

## V3+ — explicitly deferred

Learned routing (from V0.2 logs), claim verification pipeline, official platform
producers, voice, cross-machine autonomy, vertical skill packs, product packaging.

### Decision record — V3 planner model (2026-09-29)

**Decision: Qwen2.5-3B-Instruct (Q4_K_M, local) is Thoth's default planner
brain.** Evidence: the model matrix (`evals/model_matrix.py`; protocol: 5
episodes/model, temperature pinned 0.2, Wilson 95% intervals, hint-free
`--plain` goal — full protocol in `docs/journal/2026-W39.md`):

| model         |  n |          json% |          tool% |         clean% |        finish% |
| smollm2-135m  |  5 |    83% [44,97] |      0% [0,43] |  100% [57,100] |  100% [57,100] |
| smollm2-360m  |  5 |      8% [1,35] |     20% [4,62] |  100% [57,100] |      0% [0,43] |
| qwen2.5-0.5b  |  5 |  100% [57,100] |      0% [0,43] |  100% [57,100] |  100% [57,100] |
| qwen2.5-3b    |  5 |  100% [72,100] |  100% [57,100] |  100% [57,100] |  100% [57,100] |

Rationale: qwen2.5-3b is the only candidate that both adheres to the protocol
and *acts* — a verified tool turn in every episode, then a semantic finish on
the evidence. qwen2.5-0.5b is the documented trap: protocol-perfect JSON and a
100% finish rate with zero acting; on finish% alone it ties the worker, only
tool% separates them. The SmolLM2 pair fails earlier (JSON validity; the 360m's
`args`-as-list malformation is deterministic). All parks clean on all four:
across 20 episodes the runner's degradation ladder diagnosed every failure and
never crashed.

Selection rule going forward: a planner model is admissible only when every
matrix episode shows a parsed plan and ≥1 verified tool turn (json% = tool% =
100%, Wilson bounds reported alongside); any new candidate GGUF earns a row in
the same protocol before it can be routed to. The matrix is the acceptance
gate; this table is its first verdict. Cloud alternatives stay structurally
disabled (ProviderSpec $0 invariant) — this decision selects within the local
floor only.

Revisit triggers: a smaller model reaching 100/100 on the matrix (better $0
hardware floor); matrix failures on new goal families (generalization beyond
README-reading); runner-side policy changes that would re-rank the small
models. *(Amended 2026-09-29: the anti-hollow-finish floor landed — the runner
refuses a done claim with zero verified tool turns — and the small models did
NOT re-rank: finish% collapsed to 0% for all three pretenders, qwen2.5-3b
stayed 100/100/100/100. The gate was always tool%-based; the floor stopped
finish% from flattering the liars.)*
