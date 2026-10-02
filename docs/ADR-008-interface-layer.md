# ADR-008: The interface layer - clock, static dashboard, menu; the web panel stays deferred

- **Status:** Accepted (decided by the owner 2026-10-02, per the ADR-007
  pattern: one owner, with evidence)
- **Date:** 2026-10-02
- **Depends on:** ADR-001 (CLI-first, stdlib-only, no ambient processes),
  ADR-004 (every network crossing through the guard), ADR-005 (the Telegram
  surface grants no new permissions)

## Context

V1's surfaces were built but three experience gaps remained: nothing tied
the reports to wall-clock time (no daemon exists by design, so nothing
wakes Thoth up); the whole state had no at-a-glance view (the CLI is
sequential text, the briefing is capped at 7 items); and bare `thoth` was
an error message instead of an entry point. A full web panel was the
obvious "make it feel like an app" answer - and the obvious violation of
three invariants at once (a server process, a new I/O surface, a new guard
crossing kind over private data).

## Decision

Four moves, all inside the invariants:

1. **The clock is the operating system's scheduler.** `thoth telegram
   schedule` prints the exact platform lines (schtasks on Windows, cron
   elsewhere) and installs nothing itself - the operator stays the
   operator. The bot token lives in an operator-owned wrapper script
   outside any repo; the scheduled task references only the wrapper.
2. **The whole-state view is a file, not a server.** `thoth briefing
   --html` writes one self-contained HTML file next to the database: no
   port, no process, no JavaScript, no external assets, every dynamic
   value HTML-escaped (the render is the redaction, as with Telegram).
3. **Zero-typing entry dispatches real commands.** Bare `thoth` is a
   numbered menu that rebuilds argv and calls `cli.main` - every menu move
   is literally the documented command, so the menu can never diverge from
   the surface it fronts.
4. **A localhost web panel stays deferred (V3+).** Costs: a new always-on
   I/O surface, a new guard crossing kind over PRIVATE/PERSONAL data, a
   second process to keep honest, an XSS surface over private state.
   Benefit: richer interaction. The file + the phone + the menu cover the
   actual need at $0 and near-zero risk.

## Consequences

- No daemon exists; the clock is external and says so. If the scheduler
  line is not installed, no report arrives - which is the honest state.
- The dashboard is an artifact like the database itself: regenerable,
  never authoritative (ADR-002: events are truth; the file is a view).
- The menu is a thin skin over the CLI; docs remain canonical. Menu
  options that need new commands must land as real CLI commands first.
- Revisit trigger: a genuinely interactive need the cards + menu cannot
  express (the V2 knowledge review's merge-with-click is the first
  candidate) re-opens this ADR with a new design, not by accretion.
