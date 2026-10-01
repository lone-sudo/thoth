# ADR-002: Append-only events, derived views

- **Status:** Accepted (ADR-007 dissolved the pre-merge qualifier — no second reviewer exists)
- **Date:** 2026-09-27
- **Deciders:** Owner + Team-A review (`docs/reviews/team-a-architecture-review.md`)

## Context

Thoth must answer "where did I leave off?", "what did we decide?", and "what changed?"
reliably, without hallucination and without AI calls. That requires history that can be
trusted and derived views (sessions, summaries, notes) that can be corrected or
regenerated without touching the raw record.

## Decision

1. **One append-only `events` table is the source of truth:**
   `id, ts, kind, payload_json`. Written only through `events.emit()`. Never updated,
   never deleted.
2. **Everything else is a materialized view** rebuilt from events:
   - `sessions` (open/closed work sessions),
   - `notes` (facts, decisions, preferences, lessons) — superseded, never hard-deleted,
   - `tasks` with dependency edges,
   - `workdirs` (rebuildable cache of last working directory per project).
3. **Write-path discipline:** every state mutation goes through a module function that
   updates the view, emits exactly one event, and commits. The CLI layer never writes
   SQL directly.
4. **Supersede, never delete** (`notes.superseded_by`), matching the "decay is ranking,
   not deletion" principle from the Team-A review.

## Consequences

- `thoth continue` and `thoth status` are pure reads: offline, $0, unhallucinatable.
- The future runner loop (V0.2) gets checkpoint/resume for free: a run is just another
  event kind (`run.*`); "resume" is "read the last one."
- Full auditability: `thoth log` is the complete answer to "show your work."
- Storage grows forever in V0 — acceptable for one person; consolidation arrives in V2.

## Alternatives rejected (for now)

- **State-only storage** (no event log): simpler, but "where did I leave off?" becomes a
  guess and decisions lose provenance.
- **Git as the event store:** commits-as-events is tempting but too coarse for sessions
  and notes; git stays a read-only input for resume in V0.
- **Sync/CRDT-first design:** no second machine writes to the DB yet. When multi-machine
  writes arrive (lone1 ↔ ZBook), this ADR is revisited, not bypassed.

## Revisit triggers

- Event volume makes view rebuilds expensive (→ periodic snapshots, still append-only).
- Multi-machine concurrent writes (→ sync ADR).
- Note churn makes `superseded_by` chains unreadable (→ consolidation job, V2).
