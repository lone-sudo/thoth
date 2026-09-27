# ROADMAP

Sequencing rule: **build the invariant core first; anything the Team-B merge could
overturn stays unbuilt until the merge lands.**

## V0 — walking skeleton (now)

- [x] Sessions: `thoth start / stop / continue / status`
- [x] Append-only event log (`thoth log`)
- [x] Tasks with dependencies (`thoth task add/list/next/update`)
- [x] Read-only git snapshot in `continue` (branch, dirty files, last commit, stashes)
- [x] Notes store (facts / decisions / preferences / lessons; supersede, never delete)
- [x] ADR-001 (monolith, SQLite, CLI, stdlib-only), ADR-002 (events → derived views)
- [ ] Golden-set eval harness (30 stored-state questions answered from the DB only)

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
- [ ] Routing log schema (`task_class, provider, latency, outcome`) — collected from
      day one, *used* in V3 *(partially: per-turn outcome recorded; provider fields
      arrive with the first model planner)*
- [ ] One provider, manual override; no learned routing *(blocked on model planner)*

## V1 — the operating layer

- [ ] Provider registry + availability cache + capability floors + degradation ladder
- [ ] $0 spend guard + privacy floor at the single outbound choke point
- [ ] Permission table `{domain → level}` + typed confirmation for destructive actions
- [ ] Tasks get deadlines; morning briefing + end-of-day digest (generated from stored
      state only, ≤7 items, "nothing needs you" is a valid output)
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
