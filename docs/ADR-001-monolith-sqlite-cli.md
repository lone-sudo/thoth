# ADR-001: Monolith, SQLite, CLI-first, stdlib-only V0

- **Status:** Accepted (draft — pre-merge; reopen if the Team-B review overturns a listed trigger)
- **Date:** 2026-09-27
- **Deciders:** Owner + Team-A review (`docs/reviews/team-a-architecture-review.md`)

## Context

Jarvis is a personal AI operating layer built by one person, part-time. The full
architecture (memory, routing, content inbox, autonomy) is specified in the Team-A
review, but building everything at once is how this project dies. We need the smallest
core that is genuinely useful this week: sessions, an event log, notes, tasks, and a
reliable "where did I leave off?" — with zero AI calls and zero spending.

## Decision

1. **One process, one package.** No microservices, no queues, no Docker in V0/V1.
   A single Python package (`jarvis`) with a CLI entry point.
2. **SQLite is the database**, WAL mode, one file (`~/.jarvis/jarvis.db`).
   Postgres stays for the data-engineering projects; it must not become a Jarvis
   dependency or a shared failure domain.
3. **CLI-first.** `jarvis start / stop / continue / status / log / task …`.
   No GUI, no server, no voice. A thin SSH client on the ZBook can run the same CLI
   against `lone1` later without new code.
4. **Stdlib only for V0.** No runtime dependencies. First dependency allowed only when
   a concrete V1 feature demands it (expected: an HTTP client or an embedding lib).

## Consequences

- Backup = copy one file. Crash recovery = reopen it. Works offline by construction.
- The `$0` policy is trivially enforceable in V0: there is no code path that can spend.
- Single-process concurrency is fine for one user; revisit only if background jobs
  (digests, content processing) fight the CLI for the DB — WAL makes that unlikely.

## Alternatives rejected (for now)

- **Postgres + pgvector** — better at scale/semantics, worse at "copy one file to back
  up"; couples Jarvis's uptime to a server. Revisit when retrieval needs ANN at >100k notes.
- **Modular monorepo of services** — deploy scripts before features; the thing §45 warns about.
- **Web/TUI shell first** — UI work before the loop exists. CLI is also what an agent
  (the future runner) will drive, so the CLI is the real product surface.

## Revisit triggers

- Background digest/content jobs corrupt or block interactive use (→ consider a
  scheduler process, still same DB).
- Memory retrieval needs vector search at scale (→ add `sqlite-vec`, stay SQLite).
- A second machine must run Jarvis code (→ package it properly; still no services).
