# Changelog

All notable changes to Thoth. Format based on Keep a Changelog; versions: 0.x.y (V0 phase).

## [Unreleased]

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
