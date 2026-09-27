# Changelog

All notable changes to Thoth. Format based on Keep a Changelog; versions: 0.x.y (V0 phase).

## [Unreleased]

### Added
- **Guard skeleton (ADR-004 implemented):** `guard.py` — fail-closed tool gate
  (level>0 denied until the confirmation flow lands; privacy-ceiling enforcement;
  SENSITIVE cleared to no tool), provider/network egress denied outright, every
  verdict a `guard.decision` event; no-bypass CI scan (no network primitives or
  provider imports outside guard.py; the guard itself performs no I/O).
- **Provider registry + degradation ladder:** `providers.py` — the $0 invariant is
  *structural* (a non-local provider cannot be enabled; constructible only
  disabled), local-first route(), fail-closed availability cache (unprobed =
  unavailable), `attempt()` raises for all providers in this build (no clients).
- **ModelPlanner** (`planner_model.py`): the first model-backed Planner over the
  ladder — emits `provider.route` / `provider.attempt` / `provider.outcome` (the V3
  learned-router dataset starts here); scripted mode for tests/resume; the runner
  catches exhausted ladders and parks with "no provider". Client pending: local
  Ollama first, guard-gated.
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
