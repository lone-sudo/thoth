# Thoth user guide

Written against the tree at 5b5703a as amended 2026-10-07 (git.add, the
eighth tool; Suite 258, golden 38/38, model verdict 6). When this file and
the code disagree, the code wins
and this file is a bug. `thoth --help` and `thoth <command> --help` always
tell the truth. The deep operator manual - evals, GGUF drift, internals, the
Telegram runbook - is `docs/HANDBOOK.md`; this file is the task-oriented
front door.

## 1. What Thoth is - and what it is not

Thoth is a personal AI operating layer that runs entirely on your machine:

- **$0, structurally.** No cloud API is ever called. No keys, no accounts,
  no spending. The whole project is Python stdlib (`pip install -e .`
  installs zero dependencies).
- **One owner.** You are the sole operator. Every permission, every
  confirmation, every mutation is yours. Nothing acts on its own authority.
- **A local brain.** The planner is Qwen2.5-3B-Instruct running on your
  hardware (Ollama in production, llama.cpp server in evals), pinned and
  gate-tested per ADR-006.
- **Declared tools only.** The model cannot touch your computer except
  through eight registered tools, and every crossing passes a permission
  gate you control.

It is not a chat bot, not a cloud agent, and not autonomous: every mutating
action stops and waits for you to type a token.

## 2. What the model can do today - the honest answer

The model is the run's decision brain. You give a run a goal; each turn it
receives the goal, a context package, and its tool menu, and answers with
one JSON action (call a tool, or finish). Deterministic code executes the
action, verifies it, and checkpoints it to the append-only event log. The
model proposes; code disposes.

Measured on the canonical gate (60-episode matrix over three goal families,
temperature 0.2; verdicts 5-6, 2026-10-05/07) plus an n=20 refinement of the
same instrument, for the pinned brain qwen2.5-3b-instruct:

| Goal family | Example goal | Measured result |
|---|---|---|
| read | "Read README.md and finish with its title" | 100% of episodes (20/20) |
| memory | "What port is deploy on?" (a seeded note) | 100% (Wilson 95% [84,100], n=20) |
| locate | "Read the file that defines the permission domains" (name never given) | 90% (Wilson 95% [70,97], n=20) |
| protocol (every family) | - | 100% valid JSON plans; 60/60 clean parks |

In practice: give it goals answerable by looking things up - read this
file, find the file that does X, remember this fact - and it does them.
Locate success is reactive recovery, not foresight: on a wrong guess the
`file.read` miss carries a listing of real workspace names, the model
corrects, reads, finishes. The ~10% miss parks honestly with a diagnosis.

What is NOT measured and NOT promised:

- **Answer quality.** The gate measures protocol (valid plans, real tool
  turns, clean parks), not whether answers are good (ADR-006 section 6).
- **Foresight.** Locate success is recovery from a miss, not multi-step
  planning. Do not hand it long chains expecting navigation.
- **Anything off its menu.** The planner only sees tools your permission
  ceiling permits, and only those run. It cannot invent tools.
- **Unconfirmed mutations.** Even inside a raised ceiling, every mutating
  action parks until you type a token.

## 3. The eight tools

| Tool | Domain | Level | Mutating? | What it does |
|---|---|---|---|---|
| shell.read | shell | 0 | no | run one allowlisted read-only shell command |
| file.read | file | 0 | no | read a file inside the workspace |
| memory.search | memory | 0 | no | search your stored notes |
| file.write | file | 1 | yes | create a NEW text file in the workspace (64 KiB cap) |
| git.branch_create | git | 1 | yes | create a `thoth/*` branch |
| git.checkout | git | 1 | yes | check out a `thoth/*` branch |
| git.add | git | 1 | yes | stage one existing file for the next commit (on `thoth/*` only) |
| git.commit | git | 2 | yes | commit the index as it finds it (on `thoth/*` only) |

`file.write` is create-only, in tool code, at every ceiling: the path must
resolve inside the workspace, an existing file is refused, parent
directories must already exist, text only, 64 KiB cap. No overwrite, no
delete - different tools with higher levels, deliberately unbuilt. The
verifier reads the bytes back off disk and pins the readback.

The git tools carry one rule as code, not as a prompt: Thoth works only on
`thoth/*` branches. `git.commit` stages nothing itself - you stage, the run
decides when and with what message; nothing staged fails honestly.

## 4. The four-layer safety spine

1. **Fail-closed ceilings.** Every tool domain (shell, file, memory, git)
   has one ceiling you set; the default is 0 (observe-only), so every
   mutating tool ships inert.
2. **The capability-gated menu.** The planner's prompt advertises only
   tools within your ceilings: level-0 tools always, a mutating tool only
   when its domain ceiling reaches its level. The menu is presentation -
   the guard remains the authority.
3. **Typed per-action confirmation.** Inside a raised ceiling a mutating
   action still does not run on the model's word: the run parks and mints
   a token bound to exactly (run, tool, args). You type the FULL token;
   replays unlock nothing else; every grant or refusal is logged.
4. **Verifiers, not promises.** Every action is verified by deterministic
   code (file.write reads its bytes back; git tools pin repo evidence) and
   the whole trail lands in `thoth log`.

## 5. First ten minutes

```bash
git clone https://github.com/lone-sudo/thoth.git
cd thoth
pip install -e .            # stdlib only; installs the `thoth` command
thoth status                # smoke test; creates ~/.thoth/thoth.db
python -m pytest            # 258 tests, offline, $0
python -m evals.run_golden  # 38/38 stored-state questions, DB only
```

For the real brain: install Ollama and `ollama pull qwen2.5:3b-instruct`
(the tag must match exactly - a silent model swap is a record violation).
No Ollama? Everything in section 6 needs no server at all.

## 6. The daily memory loop (no AI, works offline)

Sessions, notes, tasks, and reports answer "where was I" and "what needs me
today" purely from stored state. It cannot hallucinate because it never
asks a model.

```bash
thoth start --project myproj --task "Draft the weekly review"
thoth stop --summary "Schema migrated; indexes pending"
thoth continue              # the "where did I leave off?" reconstruction
thoth status                # quick state check
thoth log --limit 20        # the append-only event log

thoth note add "Deploy port is 8031" --kind fact      # fact|decision|preference|lesson
thoth note add "Chose SQLite for V0" --kind decision --project thoth
thoth note list --kind decision

thoth task add "Draft ADR-007" --project thoth --after 3 --deadline 2026-10-12
thoth task next --project thoth
thoth task update 3 done    # todo | doing | done

thoth briefing              # morning: at most 7 items; parked runs first
thoth digest                # end of day: deadlines, parked, accomplished
thoth review                # weekly roll-up
thoth briefing --html       # whole state as ONE local HTML file, no server
```

Zero-typing mode: run **`thoth` with no subcommand** for a numbered menu
built from live state; every menu move dispatches the real CLI command.
Every command also accepts a global `--db PATH` (before the subcommand) for
a second or throwaway profile: `thoth --db /tmp/scratch.db status`.

## 7. Giving the model a job: runs

A run is one goal driven turn by turn. Bounds (`--max-turns 25`,
`--budget 20` by default) are enforced by the runner, never the model, and
travel inside the checkpoints so a resumed run cannot escape them. A run
that hits a wall parks with a diagnosis instead of crashing. Without
`--model`, `run execute` drives a scripted planner with zero AI calls - the
mechanics rig, not a capability claim. With `--model`, the finish floor is
ON (a "done" claim backed by zero verified tool turns is refused) and the
CLI probes the server first, failing closed if it is down.

### 7.1 A read-only goal (no permissions needed)

```bash
cd some-project-with-a-README
thoth run start --project demo --goal "Read README.md and finish with its first heading" --max-turns 10
thoth run execute --model
thoth log --limit 10        # the turns, tool calls, and verification
```

### 7.2 A goal that creates a file (file.write, end to end)

```bash
thoth permission set file 1       # reversible-write; logged; reversible
thoth run start --project demo --goal "Create the file notes/idea.txt containing: buy milk"
thoth run execute --model
# the run parks: the guard mints a token; the park reason carries it
thoth run status
thoth run confirm confirm:1a2b3c4d5e6f7a8b   # type the FULL token at the prompt
thoth run resume                  # the confirmed action now executes
thoth log --limit 10              # tool.file_write, guard.confirmed, ...
thoth permission set file 0       # put the ceiling back when done
```

Parent directories must exist, existing files are refused, and the content
is capped at 64 KiB - a failed write parks with the reason.

### 7.3 A goal that commits (the git protocol)

```bash
thoth permission set git 2        # reversible-write + write for git
thoth run start --project demo --goal "Create branch thoth/experiment-1 and check it out"
thoth run execute --model         # park -> confirm -> resume, as above
```

The tools only ever act on `thoth/*` branches, at every ceiling. The
full write-to-landed-commit chain is one run now: `file.write` creates
the file (7.2), `git.add` stages it (one file per call), `git.commit`
lands it (7.3) - five mutating actions, five typed confirmations.

Parked runs are surfaced at the top of `thoth continue`, with the exact
resume command.

## 8. Command cheat sheet

Every command verified against `src/thoth/cli.py`. Global: `--db PATH`
before the subcommand.

```
thoth                          numbered menu from live state

start --project P [--task ""]  start a session (records the workdir)
stop [--project P] [--summary] stop it; --summary saved as a note
continue [--project P] [--json]  where did I leave off? (parked runs first)
status [--project P]           quick state check
log [--limit N]                recent events (default 20)

note add "body" [--kind fact|decision|preference|lesson] [--project P]
note list [--project P] [--kind K]

task add "title" [--project P] [--after ID] [--deadline YYYY-MM-DD]
task list [--project P]
task next [--project P]
task update ID todo|doing|done

permission show                the {domain -> level} table
permission set DOMAIN LEVEL    DOMAIN: shell|file|memory|git; LEVEL: 0..4

run start --goal "" [--project P] [--max-turns 25] [--budget 20]
run status [--project P]       current or parked run, with its bounds
run execute [--project P] [--max-turns N] [--budget N] [--model]
run resume  [--project P] [--max-turns N] [--budget N] [--model]
run confirm TOKEN              type the FULL confirm:<16hex> at the prompt

briefing [--project P] [--json] [--html]
digest [--project P] [--today YYYY-MM-DD] [--json]
review [--project P] [--today YYYY-MM-DD] [--json]

telegram send-briefing [--project P]
telegram send-digest [--project P]
telegram serve [--project P] [--max-cycles N]
telegram schedule [--briefing-at 07:30] [--digest-at 21:00]
```

Telegram needs `THOTH_TG_TOKEN` and `THOTH_TG_CHAT` in the environment and
is delivery-only (read-only rendering of stored state); activation runbook
in HANDBOOK section 6.

## 9. When something looks wrong

- **`--model` refuses to start:** "local planner unavailable" - the server
  is down or not on 127.0.0.1:11434. Start it; the probe fails closed and
  there is never a scripted fallback wearing the model's name.
- **The run parks:** parks are diagnoses, not crashes. The park reason
  names what happened and the last action. `thoth run status`, then
  confirm (if it awaits a token) and `thoth run resume`.
- **The guard denied a tool:** the domain ceiling is below the tool's
  level. `thoth permission show`, raise deliberately, raise back later.
  The denial is in `thoth log`.
- **"confirmation refused - nothing unlocked":** the typed text must equal
  the token exactly, and the token is bound to one (run, tool, args).
- **The model guesses a wrong filename:** locate sits at ~90%; a miss
  parks honestly. Re-run or name the file in the goal.
- **"What did it actually do?":** `thoth log --limit 50` - append-only,
  never rewritten.
- **Reports look empty:** they render stored state; add notes/tasks first.
- **Telegram delivery FAILED:** the message did not leave the machine; the
  reason is logged (`surface.delivery_failed`, token redacted). Check the
  env vars and network.

## 10. Where to go deeper

- `docs/HANDBOOK.md` - the operator manual: eval harness, GGUF drift,
  provider ladder, Telegram runbook, honest limits.
- `docs/VISION.md` - what Thoth is for.
- `docs/ROADMAP.md` - sequencing plus the full V3+ decision record with
  every measured table.
- `docs/ADR-001` through `docs/ADR-007` - the governing decisions.
- `docs/journal/2026-W39.md` - the measured stories behind every number.
- `CHANGELOG.md` - what changed, when, and why.
