# VISION

Thoth (working title: Jarvis; renamed 2026-09-27 — a separate, unrelated lone-sudo/jarvis
project already exists) is a personal AI operating layer: one person's digital chief of staff.

It understands what I'm working on, remembers what I was doing, picks the right
intelligence/tool for the job, acts on my machines **only through declared tools with
auditable permission levels**, resumes work after interruptions, and turns useful
information into useful action without burying me in it — and it always shows its work
(what was done, why, how to undo it).

## The seven principles

1. **Understand me** — context comes from the operating manual + memory, not magic.
2. **Remember what I'm doing** — append-only events, derived views (ADR-002).
3. **Choose the right intelligence/tool** — "right" = available × capable × permitted.
4. **Act through declared tools only** — no ambient computer control, ever.
5. **Resume work intelligently** — one resume mechanism for crashes, deadlines, and
   "where did I leave off?" alike.
6. **Reduce overload, don't create it** — "not worth your attention" is a first-class
   answer; briefings have caps; trust is built by brevity + hit rate.
7. **Show my work** — every mutation is answerable: what, why, undo how.

## Non-negotiables

- **$0 automatic spending.** Thoth never triggers paid API usage. In V0 there is no
  code path that *can* spend; the spend guard arrives with routing in V1, as code at a
  single choke point — not as policy text.
- **Declared tools only.** The computer is operated exclusively through tool schemas
  with permission levels. GUI automation is never a foundation.
- **Append-only history.** Events are never rewritten or deleted; derived views are
  rebuildable (ADR-002).
- **No platform scraping as a foundation.** Content enters via paste/export/official
  APIs; a dead integration must never take the pipeline with it.

## What V0 is (and is not)

V0 is a **walking skeleton**: sessions, events, notes, tasks, and a trustworthy
"where did I leave off?" — with zero AI calls. It is not the runner loop, not routing,
not the content inbox. Those arrive in V0.2/V1 per the ROADMAP, sequenced on
measured evidence (ADR-007 dissolved the old pre-merge review gate).
