# Changelog

All notable changes to Thoth. Format based on Keep a Changelog; versions: 0.x.y (V0 phase).

## [Unreleased]

### Added — locate family: the gate measures multi-step generalization (2026-09-30, latest)
- **Third goal family in the model matrix** (the "beyond read + retrieve"
  trigger re-armed by the second family): the target file's name is never in
  the goal and `file.read` cannot list directories, so the ONLY path is
  `shell.read` to discover, then `file.read` to read — a genuine two-tool
  chain. Episodes seed a fresh temp workspace (target + keyword decoy) and
  run inside it, so `ls` and `file.read` resolve the workspace without any
  hint. Three-family canonical run (4 models x 3 families x 5 episodes,
  temp 0.2, floor ON, 60 episodes): **no local candidate passes the
  amended gate.** The incumbent qwen2.5-3b stays perfect on read and memory
  but scores 100% JSON with 0% tool turns on locate — deterministic
  filename-guessing from the goal's keyword instead of a listing step;
  verified a model failure, not a harness one (chain reachable end to end,
  goal delivered verbatim, all 60 parks clean). **The V3 pin changes basis,
  not value: DEFAULT_MODEL stays qwen2.5:3b-instruct as best-available
  under the $0 local-only floor, no longer gate-passing** — provenance
  comments, provider description, both V3 pin tests, and the ROADMAP record
  all amended. Suite 183, golden 38/38.

### Added — fast drift mode + CI gate (2026-09-30, latest)
- **`--fast` on the drift check** (sizes + first-1MiB fingerprints, no
  full-file sha256): the matrix preflight now runs it (invocation preflight
  ~90s -> ~0.2s) and a new pytest gate runs it over the real catalog GGUFs,
  so manifest drift fails CI in seconds instead of a ~90s full-hash pass
  (skipped where the smoke-session GGUFs are absent). The honest tradeoff is
  test-pinned: bytes past the head window are invisible to fast mode — the
  full `--check` remains the operator's last word before bytes enter a
  decision record. Suite 183, golden 38/38.

### Added — GGUF drift check: the scored bytes join the record (2026-09-30, latest)
- **ADR-006 Open Question 3 (the silent half) resolved:** re-quantized or
  re-downloaded catalog GGUFs can no longer change behavior behind the
  decision record's back. `evals/model_drift.py` builds and checks a
  committed sha256 manifest (`evals/model_manifest.json`) over the matrix
  catalog — streaming hashes (stdlib, $0, no server, no episodes, seconds of
  runtime), size + full sha256 + first-1MiB fingerprint per GGUF, file names
  only (no absolute paths, so the manifest survives storage moves).
  `--check` exits 3 on drift with the recovery procedure printed; the matrix
  preflight-checks before every invocation and warns loudly but proceeds —
  refusing is the operator's explicit `--check` call. `model_manifest.json`
  is evidence, not a runtime license: a manifest rebuild is deliberate
  (`--build`), keeping `--add` auditions of new weights a one-command path.
  Fourteen offline tests (every drift kind, CLI exit codes 0/1/2/3/4,
  committed-manifest catalog pins, preflight wiring); live-verified both
  directions (clean rc 0; tampered-manifest demo rc 3 + matrix DRIFT
  WARNING). Suite 179, golden 38/38.

### Added — second goal family in the model matrix (2026-09-30, latest)
- **The gate measures generalization instead of naming it** (the revisit
  trigger ADR-006 and the V3 record both listed): the matrix now scores each
  model over `GOAL_FAMILIES` — `read` (the exported hint-free PLAIN_GOAL,
  unchanged and still the `--plain` benchmark) and `memory` (FTS retrieval
  via `memory.search` over notes seeded into each episode's fresh DB, exactly
  like production memory). Per-family rows in one table (`family` column),
  `--family` to restrict a run, and the ADR-006 admissibility rule amended to
  **json% = tool% = 100% within EVERY family** (`gate_row` folds family rows;
  `gate_verdict` refuses partial records as `incomplete`, and a
  `--family`-restricted audition honestly reports it). Amended verdict:
  qwen2.5-3b passes in BOTH families (100/100 each, Wilson-reported), the
  0.5b pretender still separates (100% JSON both families, tool% 40/0), all
  40 two-family parks clean. DEFAULT_MODEL pin unchanged; suite 165, golden
  38/38.

### Added — self-serve gate auditions (2026-09-29, latest)
- **`--add KEY GGUF_PATH PARAMS` on the model matrix** (ADR-006 Open Question
  4 was about pinning the catalog; this answers the audition ergonomics): a
  candidate GGUF anywhere on disk is resolved, checked against duplicate keys
  and the decision-reserved `qwen2.5-3b` tag, run through the standard
  protocol (5 episodes, pinned 0.2), and given an explicit gate verdict with
  the next step printed — PASS points at the decision record + pin, FAIL
  states "not routable; the matrix row is the evidence". Zero code edits to
  audition; persistence stays deliberate. Verified live (qwen3b-recheck:
  PASS at n=1). Gate pieces unit-tested offline including both full
  `main()` flows with the server monkeypatched; suite 157, golden 38/38.

### Added — ADR-006 (2026-09-29, latest)
- **ADR-006 (proposed): the model-selection gate** — the matrix protocol as
  the only path by which a model becomes routable (5 episodes/model, pinned
  0.2 temperature, guard-gated episodes over the exported PLAIN_GOAL), the
  four honestly-computed metrics (pooled-answers JSON validity, verified
  tool turns, clean-park, earned finishes), Wilson 95% intervals over each
  rate's own trials, the admissibility rule (json% = tool% = 100%), the
  pin-test mechanism that makes a silent model swap fail CI, and the
  re-decision procedure. First verdict recorded: qwen2.5-3b. Staged for
  Team-B with four open questions (clean% in the rule, pure-question
  finishes, re-run cadence, catalog pinning).

### Added — V3 decision wired into production routing (2026-09-29, latest)
- `ollama.DEFAULT_MODEL` (`qwen2.5:3b-instruct`) now carries the decision's
  provenance: a comment binding it to the ROADMAP record and the matrix gate,
  with the re-decision procedure stated (run the matrix, update the record,
  then change the tag). The `ollama-local` provider description names
  Qwen2.5-3B-Instruct and "matrix-gated". Two pin tests: the constant equals
  the recorded selection, and the declared provider carries it — a silent
  model swap now fails CI instead of drifting from the decision record.

### Added — anti-hollow-finish floor (2026-09-29, latest)
- **Runner policy:** a done claim with zero verified tool turns is refused —
  the run parks diagnostically (`finish floor: done claimed with zero verified
  turns`) and a `run.finish.refused` event records the claimed summary.
  Verified turns count across resumes, so a run that acted in a prior episode
  can still finish. Scripted no-work callers (the V0.2 CLI execute/resume
  planners) opt out explicitly via `allow_finish_without_turns=True` — the
  floor is an anti-hallucination guard, not a no-op policy. The
  finish-confirmation probe is unaffected by construction (it only fires from
  the repeat-breaker, which requires a prior verified turn).
- **Re-measured matrix under the floor** (same 5-episode/0.2-temp protocol):
  finish% before → after: smollm2-135m 100%→0%, qwen2.5-0.5b 100%→0%,
  smollm2-360m 0%→0%, **qwen2.5-3b 100%→100% — zero cost to honest models**.
  finish% is now a truth test: it measures who EARNS completion, not who
  claims it. clean% 100 on all four again (20 more episodes, zero crashes).
- ROADMAP's V3 decision record amended: the revisit trigger fired, the small
  models did not re-rank. Suite 149 (3 new floor-contract tests), golden
  38/38; matrix clean-park list now includes `finish floor`.

### Added — V3 planner-model decision recorded (2026-09-29, latest)
- ROADMAP V3+ gains a decision record: **Qwen2.5-3B-Instruct (Q4_K_M, local)
  is Thoth's default planner brain**, evidenced by the stabilized model matrix
  and gated by an admissibility rule — json% = tool% = 100% over the matrix
  protocol (Wilson bounds reported) before anything routes to a model. New
  candidate GGUFs earn a row in the same protocol; revisit triggers: a smaller
  model passing the gate, failures on new goal families, runner policy changes
  such as the anti-hollow-finish floor. Local-floor scope only — the $0
  structural invariant is untouched.

### Added — model matrix eval, stabilized (2026-09-29, latest)
- **`evals/model_matrix.py`** — one row per local model, four protocol scores:
  JSON validity, tool-turn rate, park cleanliness, self-finish. Swaps
  llama-server across the four sha-verified GGUFs (taskkill → relaunch → poll
  `/v1/models`) and runs the exact `--plain` hint-free protocol test per model
  (the smoke driver's goal is now the exported `PLAIN_GOAL`, so matrix rows are
  comparable with the smoke benchmark). Every byte still crosses
  `guard.check_egress` via the smoke driver's shim; no new I/O modules.
- **Stabilized protocol (2026-09-29):** 5 episodes per model, temperature
  pinned at 0.2 (the production planner's setting; the smoke demo keeps 0.4 —
  rates at different temperatures are not comparable), and **Wilson 95%
  intervals on every rate** — 0%/100% at n=5 still state a real bound.
  json_validity pools raw answers (attempts are the trials: `provider.attempt`
  is a planning *round* — a bounded retry consumes two answers — so the
  counter reads `provider.outcome` events, not rounds).
- Scoring core (`wilson` / `episode_scores` / `score_model` / `render_table`)
  is offline and unit-tested (`tests/test_model_matrix.py`, 17 tests): Wilson
  edges/midpoints/narrowing, pooling-vs-mean-of-ratios, clean-park = done or a
  diagnostic reason (never a transport crash), zero-episode/zero-attempt
  floors, interval-aware fixed-width table, protocol pins (n=5, temp 0.2).
- Stabilized matrix (5 episodes/model, live): smollm2-135m 83% JSON [44,97] /
  0% tool turns (hollow finishes at the new temp); smollm2-360m 8% JSON [1,35]
  (its `args`-as-list malformation is deterministic — lower temperature served
  it); **qwen2.5-0.5b protocol-perfect and never acts — 100% JSON, 100%
  finish, 0% tool turns, all five episodes** (the failure mode the tool-turn
  column exists to expose; on finish% alone the liar and the worker tie);
  **qwen2.5-3b 100% [72,100] across the board** — reads README.md, then
  finishes on the evidence, every episode. clean% 100 on all four: across 20
  episodes the runner diagnosed every failure, never crashed.
- Suite at 146, golden 38/38; smoke `--plain` regression-checked green after
  the `PLAIN_GOAL` extraction and the shim's temperature pin.

### Added — Telegram surface implemented (2026-09-28, latest)
- **`telegram.py` — the third I/O module (ADR-005 V1.5):** guard-allowlisted
  transport (exact-host `api.telegram.org`, https-only — lookalikes and
  plaintext downgrades denied), token from env (never the repo, redacted in
  every event), chat-id allowlist with logged rejections, briefing/digest
  delivery behind `apply_privacy_ceiling` (PRIVATE renders as counts,
  SENSITIVE withholds), approval-card plumbing with fail-closed expiry
  (timeout = reject), bounded audited polling, delivery failures degrade to
  `surface.delivery_failed` events and never crash. Zero ToolSpecs — the
  surface grants nothing it does not already have.
- Guard gains the `surface` crossing kind: exactly two allow-rules now exist
  (`local:` providers, allowlisted surface hosts).
- CLI: `thoth telegram send-briefing|send-digest|serve [--max-cycles]`.
- CI: no-bypass whitelist is exactly three files (guard/ollama/telegram) and
  telegram.py may import neither the tool registry nor the runner.
- 16 new tests (token hygiene, deny-closed, ceiling, cards, allowlisted
  polling); suite at 129, golden 38/38. Live activation pending the
  operator's bot token.

### Added — ADR-005 (2026-09-28, latest)
- **ADR-005 (proposed): Telegram as Thoth's second surface** — Bot API briefing
  and digest delivery, fail-closed approval cards for
  `guard.require_confirmation` (resolving ADR-004 Open Question 4: the
  interrupting channel exists and is pull-safe), privacy ceiling at delivery
  (PRIVATE renders as counts, SENSITIVE withheld), exact-host endpoint
  allowlist, token redaction, chat-id allowlist, and structural
  no-new-permissions guarantees (zero ToolSpecs, read-only pulls, CI whitelist
  of exactly three I/O modules). Polling only — no inbound ports. Staged for
  Team-B.

### Added — result summarizer + finish-confirmation probe (2026-09-28, latest)
- **Per-tool result summarization** (the documented V2 layer, pulled forward):
  `ToolSpec.summarize` gives each tool its semantic observation — the planner
  self-terminates on semantic result lines, never raw payloads (measured).
  `default_summarize` is the floor; runner caps at 200 chars.
- **Finish-confirmation probe**: a repeat-breaker hit now triggers one tool-
  free decision question (FINISH_SYSTEM) through the full guarded ladder —
  "yes" completes the run gracefully with the model's own summary, "no"
  keeps the diagnostic park. Optional planner capability; fully event-logged.
- **Latent bug fixed:** runner now passes `_conn` into tool calls —
  memory.search was silently unusable inside runs; regression test added.
- **Measured finding (journal):** with an action menu visible, Qwen2.5-3B
  re-acts instead of finishing (0/6 vs 2/2 with the menu removed) — the probe
  asks the decision question without the menu. `smoke --plain` now PASSES
  2/2: hint-free goal → verified action → probe → self-driven done.
- Suite at 110; golden 38/38.

### Added — Qwen2.5-3B proof + planner/runner hardening (2026-09-28, later)
- **Repeat-breaker** (`runner.py`): a repeat of a VERIFIED-OK idempotent action
  parks the run before executing — turns the observed live loop (identical
  file.read digest four turns running) into a diagnostic terminal state.
  Within-episode scope; failing repeats stay under 3-strikes verify.
- **Planner prompt fixes** (`planner_model.py`): tool lists now carry required
  args (undocumented schemas were unguessable); turn history reaches the model
  (the observation loop actually loops); tail instruction against re-acquiring
  results already held.
- **Output observations in checkpoints** (`runner.py`): turns carry a 200-char
  observation (tool detail first, payload sample second) so the planner can
  read what happened; digests alone cannot end a loop.
- **Bounded retry**: `_ATTEMPTS_PER_PROVIDER = 2` — a stochastic model gets one
  second chance before the ladder falls through; `provider.retry` events keep
  it auditable; structural failures never retry.
- Smoke driver: `--plain` mode (hint-free protocol benchmark, strict gate:
  done AND ≥1 verified turn), bounds coherence, temp 0.4.
- Journal: the 2.1GB windowed-download record (sha256-verified against HF LFS)
  and the session's key finding — Qwen3B self-terminates on SEMANTIC result
  lines, not raw payloads; per-tool result summarization is the V2 layer that
  completes the self-driven run.
- Suite at 102 passing; golden 38/38.

### Added — live local-brain milestone (2026-09-28)
- **`ollama.py` client (the package's single I/O module):** gate-before-bytes
  (`guard.check_egress` on every probe/attempt), loopback-only endpoints, JSON
  plan contract (`plan_from_json` never trusts the model); whitelisted in the
  CI no-bypass scan alongside guard.py and nothing else.
- **Guard `local:` allow-branch:** the single egress rule — `provider` crossings
  to `local:` targets are allowed (and logged); cloud stays denied; prefix-spoof
  (leading whitespace) tested.
- **ModelPlanner live path:** route → guard-gated attempt → parse → registry
  validation → Plan; outcomes `ok`/`unavailable`/`error` all emitted (the V3
  router dataset includes wins). An invalid model proposal parks the run.
- **`evals/smoke_local_planner.py`:** end-to-end smoke — guard-gated probe,
  llama-server wire shim (OpenAI format), full runner loop, event trail; pass
  gate = ≥1 verified tool turn on a real local model.
- Parser robustness from live findings: trailing-prose answers parse (first JSON
  object via `raw_decode`); probe/attempt tests pinned to a dead port so they
  don't depend on the machine's running services.
- Suite at 99 passing; golden 38/38.

### Added
- **Guard skeleton (ADR-004 implemented):** `guard.py` — fail-closed tool gate
  (level>0 denied until the confirmation flow lands; privacy-ceiling enforcement;
  SENSITIVE cleared to no tool), cloud/provider egress denied (the `local:`
  allow-branch arrives in the milestone cluster above), every
  verdict a `guard.decision` event; no-bypass CI scan (no network primitives or
  provider imports outside guard.py; the guard itself performs no I/O).
- **Provider registry + degradation ladder:** `providers.py` — the $0 invariant is
  *structural* (a non-local provider cannot be enabled; constructible only
  disabled), local-first route(), fail-closed availability cache (unprobed =
  unavailable), `attempt()` raises for all providers in this build (no clients).
- **ModelPlanner** (`planner_model.py`): the first model-backed Planner over the
  ladder — emits `provider.route` / `provider.attempt` / `provider.outcome` (the V3
  learned-router dataset starts here); scripted mode for tests/resume; the runner
  catches exhausted ladders and parks with "no provider". Live now via the
  guard-gated `ollama.py` client (see the milestone cluster above).
- 27 new tests (guard 12, providers/planner 15); suite at 77 passing.
- **`thoth briefing`** (Azaris-parity track, V1 item pulled forward): morning report
  generated from stored state only — parked runs (with resume commands), open work,
  last wrap-up. ≤7 items, "Nothing needs you today." is a valid output; zero network,
  zero AI, Windows-console-safe ASCII rendering. Golden checks 31–33 added (33 total).
- Benchmark: `docs/reviews/azaris-benchmark.md` — capability map vs azaris.ai at $0,
  pulled-forward track (briefing → Telegram V1.5 → Gmail triage V2).
- **Golden-set eval harness** (V0 gate): deterministic seed world (two projects,
  fixed timestamps, known sessions/tasks/notes/runs) + 30 stored-state questions
  that continue/status/run must answer from the DB alone, zero AI calls.
  `python -m evals.run_golden` for a scoreboard; also wired into pytest. Fixing the
  harness surfaced and fixed a real bug: `last_parked` correlated park events to
  the latest overall instead of the run's own event.
- ADR-004 (draft): the security choke point — one fail-closed gate for outbound AI
  requests, network calls, level-≥1 tool invocations, and externally-sourced memory
  writes; $0 spend rules as unoverridable code; privacy classes traveling with data
  (sensitive never leaves the machine); prompt-injection defense via origin tainting,
  content-as-data rendering, no-privilege-crossing-from-tainted-input, and floors
  that rise but never fall. Four open questions staged for the Team-B review.
- **V0.2 runner loop (ADR-003 implemented):**
  - `runs` table + notes FTS5 with sync triggers and backfill (schema migration v2)
  - Tool protocol: `ToolSpec` registry with schema validation, permission levels,
    idempotency flags, and per-tool deterministic verifiers
  - Three read-only (level 0) tools: `shell.read` (allowlisted, chaining-blocked),
    `file.read` (workspace-escape-proof), `memory.search` (FTS, syntax-neutralized)
  - Runner: load context package → plan → act → verify → checkpoint per turn; hard
    bounds (max turns, tool-call budget, deadline) enforced in code; parking on
    budget/deadline/3× verify-fail; `resume_run` carries bounds from checkpoints
  - Context package: five fixed sections with per-section token budgets, sizes
    recorded in every checkpoint
  - `Planner` Protocol with deterministic `NoopPlanner` (zero AI calls; model planner
    plugs in without touching the loop)
  - CLI: `thoth run start|status|execute|resume`; `thoth continue` now surfaces
    parked runs with their resume command
- ADR-003 (draft): runner-loop design — five-phase loop, fixed-order context package
  with per-section token budgets, declared tool protocol (permission level, idempotency
  flag, verifier), and the checkpoint-as-event format (`run.turn.*`). Pre-merge draft;
  four explicit questions staged for the Team-B review.

### Changed
- **Project renamed: Jarvis → Thoth.** Python package `jarvis` → `thoth`, CLI `jarvis`
  → `thoth`, default DB `~/.jarvis/jarvis.db` → `~/.thoth/thoth.db`. Reason: an unrelated,
  still-active `lone-sudo/jarvis` project already exists; Thoth is a separate codebase
  and stays fully independent of it.

## [0.1.0] — 2026-09-27

### Added
- Walking skeleton (V0): SQLite storage (WAL mode), append-only event log, sessions
  (`thoth start/stop/continue/status`), notes store, task list with dependency-aware
  next-task suggestion, read-only git inspection for resume ("where did I leave off?",
  zero AI calls).
- CLI: `start`, `stop`, `continue`, `status`, `log`, `task add|list|next|update`,
  `note add|list`.
- Docs: VISION, ROADMAP, ADR-001 (monolith/SQLite/CLI), ADR-002 (events + derived notes),
  Team-A architecture review, build journal.
