# ROADMAP

Sequencing rule: **build the invariant core first; sequence on measured
evidence** (ADR-007 dissolved the old Team-B gate — no second reviewer
exists; caution about what stays unbuilt remains the discipline, the
external review is not).

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
- [x] One provider, manual override; no learned routing *(the real ModelPlanner
      is wired into the CLI — `thoth run execute --model` / `thoth run resume
      --model` route the V3 decision brain through the ladder, guard-gated,
      finish floor ON, fail-closed probe; Ollama wire, loopback-only; scripted
      NoopPlanner stays the default without `--model`. Pin basis: best-available,
      not gate-passing — the locate family gap stands. Handbook:
      `docs/HANDBOOK.md`.)*

## V1 — the operating layer

*Security design: **ADR-004** (choke point, spend guard, privacy floors, injection
defense) — lands together with the provider registry, not after.*

- [x] Provider registry + availability cache + capability floors + degradation ladder
      *(skeleton: providers declared, local-first ladder, fail-closed availability,
      `attempt()` raises for all — no clients yet; runner parks on exhausted ladder)*
- [x] $0 spend guard + privacy floor at the single outbound choke point (`guard.py`,
      fail-closed, every decision an event; CI test: no provider import outside guard)
      *(wired and shipped: both real outbound paths - ollama.py, telegram.py - go
      through the guard; structural tests pin the no-bypass invariants)*
- [x] Permission table `{domain -> level}` + typed confirmation for destructive
      actions *(shipped: `permissions.py` holds the operator table (meta-backed,
      observe-only by default), the guard computes every tool crossing from it,
      and the runner now actually crosses the guard - a mutating action inside
      the ceiling parks until the operator types its per-action token
      (`thoth run confirm`), outside it denies; every verdict an event)*
- [x] Morning briefing (`thoth briefing`): generated from stored state only, ≤7
      items, "nothing needs you" is a valid output, parked runs top-of-list with
      resume commands (parity track item 1; Azaris-benchmark)
- [x] Tasks get deadlines; end-of-day digest + weekly review *(digest shipped with
      deadlines; weekly review landed as `thoth review` - same stored-state-only
      discipline, item cap, plus counts that survive the item cap)*
- [ ] Telegram surface (V1.5): Bot API briefing delivery + approval cards wired to
      `guard.require_confirmation`; grants no new permissions (ADR-005;
      implemented — `thoth telegram send-briefing|send-digest|serve` with
      guard-allowlisted transport and card plumbing; live activation pending
      the operator's bot token, level ≥1 tools still absent by design)
- [x] Interface layer (ADR-008): the clock via the OS scheduler (`thoth telegram
      schedule` prints the exact lines, installs nothing), whole-state static
      dashboard (`thoth briefing --html` - a file, not a server), zero-typing
      menu on bare `thoth` dispatching real CLI commands; web panel deferred
      to V3+ with its costs on record
- [x] 5–8 tools; git branch protocol (AI works only on `thoth/*` branches) *(shipped: three mutating git tools - `git.branch_create` (1), `git.checkout` (1), `git.commit` (2) - behind the guard's ceiling + typed confirmation; the thoth/* rule is tool code, not a prompt, and holds at every ceiling; `git.commit` stages nothing. V1 is closed. Extended 2026-10-05: `file.write` (1, filetools.py) - the content producer the staging-only commit path was missing; create-only and workspace-scoped in tool code, the verifier pins a byte-for-byte readback; ships inert (file ceiling 0). Its menu line alone collapsed the planner's locate behavior (90% -> 5% verified turns at n=20, non-overlapping Wilson intervals; ADR-006 fifth verdict), so the operator decision re-shaped the harness: the action menu is now capability-gated - it advertises only tools within the operator's ceiling - and the gate re-passed on the final harness.)*

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
episodes/model PER GOAL FAMILY, temperature pinned 0.2, Wilson 95% intervals,
hint-free `--plain` goal — full protocol in `docs/journal/2026-W39.md`):

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
100%, Wilson bounds reported alongside) — **within every goal family** since
the 2026-09-30 amendment (below); any new candidate GGUF earns a row in
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

*(Amended 2026-09-30: a second goal family landed — the matrix now scores each
model over `read` (the PLAIN_GOAL file.read test) AND `memory` (retrieval via
`memory.search` over seeded notes), and the admissibility rule requires
json% = tool% = 100% within EVERY family — ADR-006 section 4 as amended. The
generalization risk the V3 record named openly is now measured. Two-family
canonical run, 5 episodes/model/family, temp 0.2, floor ON:*

| model         | family |  n |          json% |          tool% |         clean% |        finish% |
| smollm2-135m  | read   |  5 |    83% [44,97] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-135m  | memory |  5 |    22% [6,55]  |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-360m  | read   |  5 |    15% [4,42]  |     40% [12,77] |  100% [57,100] |      0% [0,43] |
| smollm2-360m  | memory |  5 |     8% [1,35]  |     20% [4,62] |  100% [57,100] |      0% [0,43] |
| qwen2.5-0.5b  | read   |  5 |  100% [70,100] |     40% [12,77] |  100% [57,100] |     40% [12,77] |
| qwen2.5-0.5b  | memory |  5 |  100% [57,100] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| qwen2.5-3b    | read   |  5 |  100% [72,100] |  100% [57,100] |  100% [57,100] |  100% [57,100] |
| qwen2.5-3b    | memory |  5 |  100% [76,100] |  100% [57,100] |  100% [57,100] |  100% [57,100] |

*Verdict: qwen2.5-3b passes the amended gate — 100% JSON validity and 100%
tool turns in BOTH families — so the V3 decision stands on measured
generalization, not the single README-reading goal it was first gated on. The
small models re-confirm their failure modes per family (the 0.5b pretender:
100% JSON in both families, tool% 40/0 — and its memory-family episodes are
pure hollow finishes, floor-refused). All 40 parks clean across all four
models. No change to the DEFAULT_MODEL pin; the evidence trail just got
wider.)*

*(Amended 2026-09-30 (same day): the scored bytes joined the record — the
catalog GGUFs are pinned by a committed sha256 manifest
(`evals/model_manifest.json`, `evals/model_drift.py`), preflight-checked by
the matrix before every invocation. A re-quantized GGUF now announces
itself: the run warns that rows scored on the old bytes are no longer
evidence, and `python -m evals.model_drift --check` is the operator's
explicit refusal. Recovery: re-run the matrix, rebuild the manifest,
amend the record.)*

*(Amended 2026-09-30 (later): a third goal family — `locate` — landed: the
target file's name is never in the goal, `file.read` cannot list
directories, so the ONLY path is `shell.read` to discover, then `file.read`
to read — a genuine two-tool chain, seeded per episode in a temp workspace
with a decoy. Three-family canonical run, 5 episodes/model/family, temp
0.2, floor ON, 60 episodes:*

| model         | family |  n |          json% |          tool% |         clean% |        finish% |
| smollm2-135m  | read   |  5 |    71% [36,92] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-135m  | memory |  5 |    50% [22,78] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-135m  | locate |  5 |    83% [44,97] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-360m  | read   |  5 |     0% [0,28]  |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-360m  | memory |  5 |     9% [2,38]  |     20% [4,62] |  100% [57,100] |      0% [0,43] |
| smollm2-360m  | locate |  5 |    30% [11,60] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| qwen2.5-0.5b  | read   |  5 |  100% [57,100] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| qwen2.5-0.5b  | memory |  5 |  100% [65,100] |     40% [12,77] |  100% [57,100] |     40% [12,77] |
| qwen2.5-0.5b  | locate |  5 |  100% [65,100] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| qwen2.5-3b    | read   |  5 |  100% [72,100] |  100% [57,100] |  100% [57,100] |  100% [57,100] |
| qwen2.5-3b    | memory |  5 |  100% [72,100] |  100% [57,100] |  100% [57,100] |  100% [57,100] |
| qwen2.5-3b    | locate |  5 |  100% [80,100] |      0% [0,43] |  100% [57,100] |      0% [0,43] |

*Verdict: NO local candidate passes the amended gate across all three
families. qwen2.5-3b — the incumbent — fails exactly the multi-step family
(locate: 100% JSON, 0% tool turns; it guesses file names from the goal's
keyword instead of planning a listing step, deterministically at the pinned
temperature) while remaining perfect on both single-tool families. The pin
changes basis, not value: qwen2.5-3b stays DEFAULT_MODEL as BEST-AVAILABLE
under the $0 local-only floor (it dominates every alternative on every
family), no longer as a gate-passing model. Revisit trigger: any local
candidate passing ALL families re-opens V3. The gap is a planner-behavior
finding, not a harness one — the chain was verified reachable end to end
(solo `ls` + `file.read` succeed from inside the workspace; the context
delivers the goal verbatim), and all 60 parks stayed clean. A prompt-level
repair (discover-then-act in PLAN_SYSTEM) was measured and reverted the
same day: locate unchanged, first-ever memory wobble on the incumbent —
the gap is a reasoning limit, not a prompt or messaging deficit (full
story in ADR-006).)*

*(Amended 2026-10-05: an operator-approved diagnostic lever re-gated the
catalog - `file.read` misses now carry the workspace's real file names,
data in a tool result, not a prompt instruction; the one channel the
prompt-repair experiment did not touch. Matrix re-run, 5 episodes/model/
family, 60 episodes, manifest-clean bytes:*

| model         | family |  n |          json% |          tool% |         clean% |        finish% |
| smollm2-135m  | read   |  5 |     0% [0,28]  |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-135m  | memory |  5 |    11% [2,44]  |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-135m  | locate |  5 |    22% [6,55]  |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-360m  | read   |  5 |    17% [5,45]  |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-360m  | memory |  5 |    11% [2,44]  |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-360m  | locate |  5 |     9% [2,38]  |     20% [4,62] |  100% [57,100] |      0% [0,43] |
| qwen2.5-0.5b  | read   |  5 |  100% [57,100] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| qwen2.5-0.5b  | memory |  5 |  100% [61,100] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| qwen2.5-0.5b  | locate |  5 |  100% [74,100] |     20% [4,62] |  100% [57,100] |     20% [4,62] |
| qwen2.5-3b    | read   |  5 |  100% [72,100] |  100% [57,100] |  100% [57,100] |  100% [57,100] |
| qwen2.5-3b    | memory |  5 |  100% [72,100] |  100% [57,100] |  100% [57,100] |  100% [57,100] |
| qwen2.5-3b    | locate |  5 |  100% [84,100] |  100% [57,100] |  100% [57,100] |  100% [57,100] |

*Verdict: qwen2.5-3b passes the amended gate for the FIRST time - json% =
tool% = 100% within EVERY family. Locate went 0% -> 100% tool turns; every
episode the same recover chain (guess, read the diagnostic's listing, read
the target, semantic finish). No regressions: clean% 100% across all 60
episodes; the 0.5b pretender stays hollow (tool% 0/0/20, one lucky locate
episode), the SmolLM2 pair still fails on JSON. The pin keeps its tag and
restores its basis: gate-passing, no longer best-available only. What
locate measures shifted honestly: discovery is now reactive
(failed-guess-and-recover) instead of purely foresighted - the
always-list-first skill is no longer required to pass. Full story in
ADR-006, fourth verdict.)*

*(Amended 2026-10-05 (same day, fifth verdict): `file.write` joined the
registry - one added menu line - and the canonical 60-episode re-run
FAILED for the first time. Three same-day runs: locate tool% 20%, 40%,
0% while read held 100% everywhere. Controlled attribution at n=20
(locate, incumbent only): seven-line menu 5% [1,24] vs six-tool control
90% [70,97] - non-overlapping Wilson intervals; 19/20 failures the
identical `file.read("deploy")` 3x park with the listing diagnostic
ignored, json% 100% throughout. The same measurement falsified the
fourth verdict's locate 100% as an n=5 overestimate: the six-tool truth
is ~90% [70,97]. Operator decision (ADR-007): the action menu is now
capability-gated - it advertises only tools within the operator's
{domain -> level} ceiling; level-0 tools always show; the guard stays
the authority (one hallucinated call to an advertised-but-forbidden
tool parks the run, so the old menu was a hazard independent of this
finding). Gate on the FINAL harness:*

| model         | family |  n |          json% |          tool% |         clean% |        finish% |
| smollm2-135m  | read   |  5 |  100% [57,100] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-135m  | memory |  5 |    20% [6,51]  |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-135m  | locate |  5 |    38% [14,69] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| smollm2-360m  | read   |  5 |    31% [13,58] |     60% [23,88] |  100% [57,100] |      0% [0,43] |
| smollm2-360m  | memory |  5 |    18% [5,48]  |     20% [4,62] |  100% [57,100] |      0% [0,43] |
| smollm2-360m  | locate |  5 |    23% [8,50]  |     40% [12,77] |  100% [57,100] |      0% [0,43] |
| qwen2.5-0.5b  | read   |  5 |  100% [68,100] |     60% [23,88] |  100% [57,100] |     60% [23,88] |
| qwen2.5-0.5b  | memory |  5 |  100% [70,100] |     40% [12,77] |  100% [57,100] |     40% [12,77] |
| qwen2.5-0.5b  | locate |  5 |  100% [57,100] |      0% [0,43] |  100% [57,100] |      0% [0,43] |
| qwen2.5-3b    | read   |  5 |  100% [72,100] |  100% [57,100] |  100% [57,100] |  100% [57,100] |
| qwen2.5-3b    | memory |  5 |  100% [76,100] |  100% [57,100] |  100% [57,100] |  100% [57,100] |
| qwen2.5-3b    | locate |  5 |  100% [80,100] |  100% [57,100] |  100% [57,100] |  100% [57,100] |

*Verdict: qwen2.5-3b passes the canonical gate on the FINAL harness -
json% = tool% = 100% within EVERY family, 60/60 clean parks, no candidate
re-ranked. n=20 refinement on the incumbent (memory + locate): memory
100% [84,100] (20/20), locate 90% [70,97] (18/20) - the same band as the
six-tool control: the capability-gated menu restores exactly the
pre-`file.write` behavior while the tool ships inert. The pin keeps its
tag - gate-passing on the canonical gate - with the locate variance
honestly on the record: single n=5 cells sit at the resolution limit
(open question 5: n=20 cells or a replication rule, operator's call).
Full story in ADR-006, fifth verdict.)*
