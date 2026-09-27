# Jarvis — personal AI operating layer

> **Status: V0 — walking skeleton.** ✅ Sessions · events · notes · tasks · "where did I leave off"
> 🚧 Runner loop (plan → act → verify → checkpoint) · routing · $0 guard · content inbox

Six principles (ADR-001 §5): understand me · remember what I'm doing · choose the right
intelligence/tool · act on my machines **only through declared tools with auditable
permission levels** · resume work intelligently · turn useful information into useful
action without overwhelming me — and a seventh: **show my work** (always answerable:
what was done, why, and how to undo it).

## Install

```bash
cd jarvis
pip install -e .            # stdlib-only, no dependencies (ADR-001)
```

## Usage

```bash
jarvis start --project structural-rcc-suite --task "Fix PG16 migration"   # begin session
jarvis task add "Draft ADR-003" --project jarvis --after "Fix PG16 migration"
jarvis task next --project jarvis                                        # dependency-aware suggestion
jarvis status                                                            # what's open, what's next
jarvis log --limit 5                                                     # recent events
jarvis stop --summary "Migrated schema; indexes pending"                 # checkpoint session
jarvis continue                                                          # where did I leave off?
jarvis note add "Prefer WAL mode" --kind preference --project jarvis     # atomic note
jarvis note list --kind decision                                         # active notes
```

`task` subcommands: `add` (with `--after` for dependencies), `list`, `next`, `update`.
`note` subcommands: `add` (kinds: fact / decision / preference / lesson), `list`.

`continue` is deliberately **zero-AI**: it reads stored state (last session summary,
events, open tasks) and, when the project is a git repo, read-only repo status. It works
offline, costs $0, and cannot hallucinate. LLM-generated summaries arrive with the V0.2
runner loop, not before.

## Layout

```
src/jarvis/       paths, schema, db, events, session, resume, tasks, cli
tests/            unit + integration (real SQLite via tmp_path)
docs/             vision, roadmap, architecture (adrs/, reviews/, journal/)
CHANGELOG.md
```

## Hard rules (non-negotiable, enforced in code later)

- **$0 automatic spending** — Jarvis never triggers paid API usage.
- **Declared tools only** — no ambient computer control.
- **Append-only events** — raw history is never rewritten; derived views are regenerable (ADR-002).
- **Show my work** — every mutation is logged with an event.
