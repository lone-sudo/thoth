# Thoth operator handbook

The practical guide: install Thoth, drive the zero-AI memory layer, run the
checkpointed runner, wire in the real local planner brain, and verify the
whole floor with the eval harness. Written against the shipped CLI surface
(`src/thoth/cli.py`) and the eval modules it names -- when this file and the
code disagree, the code wins and this file is a bug. `thoth --help` and
`thoth <command> --help` always tell the truth.

The floor everything stands on (ADR-001, non-negotiable, enforced in code):

- **$0 automatic spending** -- no cloud API is ever called; providers carry a
  structural $0 invariant.
- **Declared tools only** -- no ambient computer control; today's tools are
  read-only (shell allowlist, workspace-scoped files, memory search).
- **Append-only events** -- raw history is never rewritten; derived views are
  regenerable (ADR-002).
- **Show my work** -- every mutation is logged; the event log answers what was
  done, why, and where to look.

## 0. Prerequisites

- Any OS with **Python 3.12+** and **git**. (Thoth is developed on
  Windows/Git Bash; the eval-harness paths below are the Windows ones.)
- **About 3 GB of disk** if you want the real brain: the llama.cpp server
  binary plus the catalog GGUFs (the largest is the 2 GB Qwen2.5-3B Q4_K_M
  quantization).
- **No GPU, no API keys, no accounts.** `pip install -e .` pulls zero
  dependencies -- the entire project is Python stdlib.

## 1. Install

```bash
git clone https://github.com/lone-sudo/thoth.git
cd thoth
pip install -e .        # stdlib-only; installs the `thoth` command
thoth status            # smoke test; creates the DB on first use
```

State lives in one SQLite database, `~/.thoth/thoth.db` by default. Every
command accepts a global `--db PATH` (before the subcommand) to redirect it --
useful for a second profile or a throwaway:

```bash
thoth --db /tmp/scratch/thoth.db status
```

## 2. The memory loop (zero AI, works offline)

The daily-driving layer: sessions, notes, tasks, and the reports that answer
"where did I leave off" and "what needs me today" purely from stored state.
It cannot hallucinate because it never asks a model. None of this section
needs a server.

Sessions:

```bash
thoth start --project structural-rcc-suite --task "Fix PG16 migration"
thoth stop --summary "Migrated schema; indexes pending"  # --summary saved as a note
thoth continue          # the reconstruction: last summary, recent events, open
                        # tasks, parked runs with resume commands, and read-only
                        # git status when the project is a repo
thoth status            # quick state check
thoth log --limit 20    # the append-only event log (show-my-work)
```

Notes -- four kinds (fact | decision | preference | lesson); superseded, never
deleted:

```bash
thoth note add "Deployment port of the thoth web app is 8031" --kind fact
thoth note add "Chose SQLite over Postgres for V0" --kind decision --project thoth
thoth note list --kind decision
```

Tasks -- dependency-aware (`--after <task-id>`) with deadlines
(`--deadline YYYY-MM-DD`):

```bash
thoth task add "Draft ADR-007" --project thoth --after <task-id>
thoth task next --project thoth     # suggests the next runnable task
thoth task list                     # open tasks
thoth task update <id> done         # todo | doing | done
```

Reports:

```bash
thoth briefing              # morning: at most 7 items; "Nothing needs you
                            # today." is a valid output; parked runs first
thoth digest                # end-of-day: deadlines, parked runs, accomplished
thoth briefing --project thoth
thoth digest --json         # machine-readable (continue also has --json)
thoth briefing --html       # the whole state as ONE local HTML file (a file,
                            # not a server) - sessions, work, runs, memory,
                            # the last 20 events
```

Zero-typing mode: run **`thoth` with no subcommand** and you get a numbered
menu built from live state (close the session / work on the next task /
resume the parked run / briefing / continue / dashboard / log). Every menu
move dispatches the real CLI command, so the menu can never lie about what
it does.
```

## 3. Runs: the checkpointed loop (ADR-003)

A run is a goal driven turn by turn: load the context package, plan, act
through ONE declared tool, verify with deterministic code, append a
`run.turn.*` checkpoint to the event log, repeat. Bounds (`--max-turns`,
`--budget`) are enforced by the runner -- never the model -- and travel
inside the checkpoint, so a resumed run cannot escape them. "Execute" and
"resume" are the same code path.

```bash
thoth run start --goal "Audit the event log for failed tools" --project thoth
thoth run start --goal "..." --max-turns 10 --budget 8    # tighter bounds
thoth run status          # current or parked run, with its bounds
thoth run execute         # drive it (two engines, next two sections)
thoth run resume          # resume a parked run (bounds carry over)
```

A run that hits a wall is **parked, not crashed**: verify failed 3 times, no
provider on the ladder, bounds exhausted -- every park is a diagnosis in the
event log (the park message names the reason and the last action), and `thoth
continue` surfaces parked runs at the top of the briefing.

### 3.1 Scripted engine (the default)

Without `--model`, `run execute` drives the scripted `NoopPlanner`: zero AI
calls, fully deterministic. It is the loop's mechanics rig -- tools,
verification, checkpointing, parking -- not a capability claim. Scripted runs
finish without needing a verified tool turn (the floor is off for them;
there is nothing to lie).

### 3.2 The real brain: `--model`

```bash
thoth run execute --model
thoth run resume --model
```

What `--model` changes:

- The planner is the real `ModelPlanner` (the V3 decision brain,
  qwen2.5-3b), routed through the provider ladder with routing/attempt/
  outcome events on the log.
- The **finish floor is ON**: the runner refuses a "done" claim backed by
  zero verified tool turns.
- **Fail-closed availability**: the CLI probes the local server first. If it
  is not up, you get an honest exit and no run --
  `--model: local planner unavailable: <reason> (start llama-server/Ollama first; no scripted fallback)`
  -- never a scripted fallback wearing the model's name.
- **Degradation, not guessing**: a malformed or unparseable plan retries on
  the same candidate, then the ladder parks the run cleanly.
- Every byte is **guard-gated**, and the endpoint is **loopback-only**
  (127.0.0.1 / localhost / ::1 -- anything else is refused; local means
  local).

Wire-format note, because it will bite someone eventually: the CLI speaks the
**Ollama API** (`/api/tags` probe, `/api/chat` completion) on
`http://127.0.0.1:11434`. The eval harness drives llama.cpp's `llama-server`
through an OpenAI-format wire shim registered inside the harness only
(section 4). Production is Ollama wire; evals are llama-server plus shim. The
`DEFAULT_MODEL` tag names the Ollama-side model, and the matrix validated the
weights that tag refers to.

### 3.3 Wiring the brain

Either server works, as long as it answers on 127.0.0.1:11434:

- **Ollama (production path):** install Ollama, then `ollama pull
  qwen2.5:3b-instruct`. The tag must match `ollama.DEFAULT_MODEL` exactly --
  a silent model swap is a CI-pinned decision-record violation. Then `thoth
  run execute --model` just works.
- **llama.cpp (eval path):** `llama-server.exe` (build b11223) serving a GGUF
  on port 11434, used by the eval harness through its shim (section 4).

Which model, honestly: qwen2.5-3b is pinned as **best-available under the $0
local floor** per the V3+ decision record in `docs/ROADMAP.md` -- perfect on
the `read` and `memory` goal families, 0% tool turns on the multi-step
`locate` family. No local candidate passes all three families today.
Changing the pin re-opens the V3 decision: run the matrix on the candidate
first, update the record, then the tag.

## 4. The eval harness: prove the brain before you trust it

Three tools in escalating order. Prerequisite for all of them: a local server
on port 11434. The harness looks for `llama-server.exe` at
`%TEMP%/thoth-smoke/llama/llama-server.exe` (override with the `LLAMA_SERVER`
environment variable) with the catalog GGUFs in `%TEMP%/thoth-smoke/`; it
restarts the server per GGUF itself (kill, relaunch, poll /v1/models for up
to 90 seconds). No Ollama binary is needed for evals.

### 4.1 Smoke -- does the loop work end to end?

```bash
python -m evals.smoke_local_planner --plain
```

Hint-free goal, no few-shot, no suggested plan; every byte crosses the guard.
Exit 0 means the run finished with status done AND at least one verified tool
turn. That is the minimum bar for "the brain is alive". (Plain `python -m
evals.smoke_local_planner` is the hint-fed mechanics demo.)

### 4.2 The model matrix -- the acceptance gate (ADR-006)

```bash
python -m evals.model_matrix                       # full catalog: 5 episodes
                                                   # per model PER family
python -m evals.model_matrix --models qwen2.5-3b   # one model
python -m evals.model_matrix --json                # machine rows + episodes
python -m evals.model_matrix --add cand /path/to.gguf 1.5B
                                                   # audition a new candidate,
                                                   # no code edits
```

Protocol: 5 episodes per model per goal family, temperature pinned at 0.2
(the production planner's setting), Wilson 95% intervals on every rate,
finish floor ON. Three goal families, because generalization is measured,
not assumed:

- **read** -- read the workspace README via `file.read` (single tool).
- **memory** -- retrieve a seeded fact via `memory.search` (single tool).
- **locate** -- the target file's name is never in the goal and `file.read`
  cannot list directories: `shell.read` to discover, then `file.read`. A
  genuine two-tool chain, seeded per episode in a temp workspace with a
  keyword decoy.

The gate passes a model only when JSON validity AND tool-turn rate are 100%
within EVERY family. `--family memory` narrows a run for debugging; its
output says honestly that it is incomplete -- a verdict needs every family.
A new candidate GGUF auditions with `--add` across all families in one
server load. Before any row is scored, a drift preflight compares the
catalog GGUFs against the committed manifest (`evals/model_manifest.json`)
and warns loudly if the bytes moved.

### 4.3 GGUF drift -- do the bytes still match the evidence?

Re-quantized or re-downloaded GGUFs silently change model behavior; a matrix
row is only evidence for the bytes it actually scored.

```bash
python -m evals.model_drift          # full streaming sha256 vs the manifest
python -m evals.model_drift --fast   # sizes + first-1MiB fingerprints (~0.2s)
python -m evals.model_drift --check  # explicit check; exit 3 on drift
python -m evals.model_drift --build  # (re)build the manifest from disk
```

Exit codes: 0 clean | 1 missing artifact | 3 drift (size/hash/fingerprint) |
4 no manifest (run `--build` first) | 2 bad arguments.

Drift invalidates old rows as *evidence* but does not refuse by default --
that call is the operator's. The recovery procedure: re-run the matrix on
the new bytes, rebuild the manifest, amend the decision record.

## 5. Verify the install

```bash
python -m pytest                # unit + integration suite (188 tests)
python -m evals.run_golden      # 38/38 stored-state questions, DB only
```

The exit code is the verdict; both run offline, cost $0, and need no server.

## 6. Telegram surface (V1.5, optional)

```bash
thoth telegram send-briefing    # deliver the morning briefing once
thoth telegram send-digest      # deliver the end-of-day digest once
thoth telegram serve            # poll loop: reports + approval cards
                                # (--max-cycles N bounds it; default Ctrl-C)
```

Activation, start to finish:

```bash
# 1. @BotFather in Telegram: /newbot, then copy the token
export THOTH_TG_TOKEN="123456:ABC-your-token"   # environment only, never the repo
# 2. message your bot once, then read the chat id back:
curl -s https://api.telegram.org/bot$THOTH_TG_TOKEN/getUpdates
export THOTH_TG_CHAT="the numeric id from that reply"
# 3. prove it:
thoth telegram send-briefing
```

`send-briefing` / `send-digest` tell the truth: on any transport failure the
message did not leave the machine, the command exits 1, and the reason is
already in the event log (`surface.delivery_failed`, token redacted).
`thoth telegram serve` is the long-running mode: each poll is a
guard-decided, logged crossing, failures degrade to events, and updates
from chats outside the allowlist are ignored and logged. The surface
grants no new permissions - delivery is read-only rendering of stored
state, and approval cards only relay the operator's half of a
`require_confirmation` handshake.

The clock is the OS scheduler, not a daemon: `thoth telegram schedule`
prints the exact lines for this machine (`schtasks` on Windows, cron
elsewhere) and installs nothing itself. On Windows the token lives in an
operator-owned wrapper script (`~/.thoth/thoth-reports.cmd`, outside any
repo) that the scheduled task calls; on cron the variables sit in your
private crontab. Reports then arrive daily with no Thoth process running.

## 7. Honest limits (read before trusting it)

- **The locate gap.** The pinned brain cannot plan a discover-then-act
  chain; it guesses file names from the goal's keywords, deterministically
  at the pinned temperature. Two repairs were measured and reverted (an
  honest-verifier message change, a prompt-level heuristic) -- the gap is a
  reasoning limit of the current local floor, and fixing it needs a stronger
  brain, which today conflicts with the $0 floor.
- **Read-only tools.** The runner can look, not touch. Write tools and
  permission levels are V1 work behind the security choke point (ADR-004).
- **No cloud, structurally.** Cloud provider entries raise "unavailable"
  honestly rather than existing as broken configs.
- **Protocol, not quality.** The matrix measures whether a model follows the
  planning protocol (valid plans, real tool turns, clean parks), not whether
  its answers are good.
- **Drift is your call.** The harness warns loudly about changed GGUF bytes;
  only you can refuse them or re-base the record.

## 8. Where to read more

- `docs/VISION.md` -- what Thoth is for.
- `docs/ROADMAP.md` -- sequencing plus the full V3+ decision record with
  every measured table.
- `docs/ADR-001` through `docs/ADR-006` -- monolith/events, the runner loop,
  the security choke point, the Telegram surface, and the model-selection
  gate.
- `docs/journal/2026-W39.md` -- the measured stories behind every number in
  this handbook.
- `evals/model_matrix.py`, `evals/model_drift.py`,
  `evals/smoke_local_planner.py` -- module docstrings are the precise
  protocol references.
- `CHANGELOG.md` -- what changed, when, and why.
