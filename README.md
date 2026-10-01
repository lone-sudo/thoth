# Thoth — personal AI operating layer

> **Status: V0.2 — runner loop + briefing.** ✅ Sessions · events · notes · tasks · "where did I leave off" ·
> checkpointed runner (plan → act → verify → checkpoint) · 3 read-only tools · park/resume ·
> morning briefing (stored state, ≤7 items, "Nothing needs you today." is valid)
> 🚧 Model planner · routing + $0 guard · permission levels ≥1 · Telegram surface (V1.5) · content inbox

Six principles (ADR-001 §5): understand me · remember what I'm doing · choose the right
intelligence/tool · act on my machines **only through declared tools with auditable
permission levels** · resume work intelligently · turn useful information into useful
action without overwhelming me — and a seventh: **show my work** (always answerable:
what was done, why, and how to undo it).

## Install

```bash
cd thoth
pip install -e .            # stdlib-only, no dependencies (ADR-001)
```

## Usage

```bash
thoth start --project structural-rcc-suite --task "Fix PG16 migration"   # begin session
thoth task add "Draft ADR-003" --project thoth --after "Fix PG16 migration"
thoth task next --project thoth                                          # dependency-aware suggestion
thoth status                                                             # what's open, what's next
thoth log --limit 5                                                      # recent events
thoth stop --summary "Migrated schema; indexes pending"                  # checkpoint session
thoth continue                                                           # where did I leave off?
thoth note add "Prefer WAL mode" --kind preference --project thoth       # atomic note
thoth note list --kind decision                                          # active notes
thoth run start --project thoth --goal "Audit the event log"             # create a run
thoth run execute --project thoth                                        # drive it (scripted NoopPlanner)
thoth run execute --model --project thoth                                # real local brain (needs a server)
thoth run resume --project thoth                                         # resume a parked run (bounds carry over)
thoth briefing                                                           # morning report from stored state (≤7 items)
thoth briefing --project data-eng                                        # scoped briefing; parked runs first
```

**The runner (ADR-003):** each turn loads a fixed-order context package (hard
per-section token budgets), plans, acts through *one* declared tool, verifies the
result with deterministic code, and appends a `run.turn.*` checkpoint to the event
log. Max-turns / tool-budget / deadline bounds are enforced by the runner — never the
model — and travel inside the checkpoint, so a resumed run can't escape them. V0.2's
planner is a scripted `NoopPlanner` (zero AI calls); a model planner implements the
same `Planner` protocol without touching the loop. `--model` on `run
execute` / `run resume` switches to the real local planner (qwen2.5-3b,
best-available per the V3+ decision record), guard-gated and fail-closed —
see `docs/HANDBOOK.md` for the full operator guide.

`task` subcommands: `add` (with `--after` for dependencies), `list`, `next`, `update`.
`note` subcommands: `add` (kinds: fact / decision / preference / lesson), `list`.

`continue` is deliberately **zero-AI**: it reads stored state (last session summary,
events, open tasks) and, when the project is a git repo, read-only repo status. It works
offline, costs $0, and cannot hallucinate. LLM-generated summaries arrive with the V0.2
runner loop, not before.

## Layout

```
src/thoth/        paths, schema, db, events, session, resume, tasks, tools, runner, cli
tests/            unit + integration (real SQLite via tmp_path)
docs/             vision, roadmap, handbook, architecture (adrs/, reviews/, journal/)
CHANGELOG.md
```

## Hard rules (non-negotiable, enforced in code later)

- **$0 automatic spending** — Thoth never triggers paid API usage.
- **Declared tools only** — no ambient computer control.
- **Append-only events** — raw history is never rewritten; derived views are regenerable (ADR-002).
- **Show my work** — every mutation is logged with an event.
