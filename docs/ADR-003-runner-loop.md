# ADR-003: The runner loop — context package, tool protocol, checkpoint format

- **Status:** Proposed (draft — written *before* the Team-B merge lands, per the
  roadmap sequencing rule; the merge may overturn details, the loop itself is invariant)
- **Date:** 2026-09-27
- **Depends on:** ADR-001 (monolith, SQLite, CLI), ADR-002 (append-only events,
  derived views)
- **Delivers:** the V0.2 milestone from `docs/ROADMAP.md`

## Context

Everything Thoth does today is a *command*: you ask, it reads or writes, it exits.
The missing piece — the thing that makes it an operating layer instead of a to-do list —
is the **runner**: a loop that loads context, plans, acts through tools, verifies, and
checkpoints, so that "resume after crash", "resume after quota/pause", "continue where
I left off", and "work autonomously until X" are all the *same mechanism* (the Team-A
review's core claim, now being made concrete).

ADR-002 already gives us the substrate: a run is just another event kind (`run.*`),
and "resume" is "read the last one." This ADR defines the loop and its three contracts:
the **context package** (what the model sees), the **tool protocol** (what the model
may do), and the **checkpoint format** (what survives an interruption).

## Decisions

### 1. Loop shape — five phases, one checkpoint per turn

```
load context package
  → plan (model; may be "no plan needed")
    → act (exactly one tool call per turn)
      → verify (deterministic check for that tool's result)
        → checkpoint (append run.* event; THEN plan next turn)
```

- **One tool call per turn.** Verification and checkpointing happen after *every*
  action, not at "natural stopping points" — those never arrive when things go wrong.
- **Verify is code, not the model.** Each tool declares its own verifier (e.g., shell:
  exit code + output shape; file: bytes written; git: expected branch). The model may
  *interpret* a verified result; it may never *certify* one.
- **The loop is bounded:** max turns, wall-clock deadline, and a per-run tool-call
  budget, all enforced by the runner (never by the model). Exceeding any bound parks
  the run gracefully — see §5.
- **One resume path.** `thoth continue` on a parked/interrupted run and `thoth
  continue` after a crash load the same checkpoint structure. There is no separate
  "crash recovery" mode to forget to test.

### 2. Context package — assembled by code, capped by budget

The package is a **fixed-order structure built by the runner from stored state**, never
a free-form prompt the model composes. It has five sections, each with a hard token
budget enforced in code (drop lowest-ranked items rather than truncate; never exceed):

| # | Section | Source | Budget |
|---|---------|--------|--------|
| 1 | identity & preferences | operating manual (V1; today: a static header) | 200 tok |
| 2 | project context | active project, workdir, Project Brief (V2) | 800 tok |
| 3 | task state | open task, its depends_on chain, last `run.*` outcome | 400 tok |
| 4 | retrieved memories | notes matching the task (FTS over `notes` — the only AI-free retriever in V0.2) | 1,200 tok |
| 5 | turn window | last N (default 4) turn summaries from the current run | 800 tok |

Total cap ≈ 3,400 tokens before tool results; tool output enters through the turn
window, truncated to its budget with an explicit `[truncated: N chars]` marker.

Why fixed-order + budgets: it makes context **auditable and diffable** — every
`run.turn.*` event records which sections were included and at what size, so "why did
the runner do that?" is always answerable from the log (ADR-002 discipline, applied to
the loop). Relevance ranking beyond FTS/recency is deliberately out of scope until V2
(decay-as-ranking per ADR-002's revisit triggers).

### 3. Tool protocol — declared, leveled, idempotency-flagged

A tool is a **Python object with a schema**, registered in a registry — not a free
function the model can discover at runtime. Minimum contract:

```python
@dataclass
class ToolSpec:
    name: str                      # "shell.read", "file.read", "memory.search", ...
    permission_level: int          # 0 observe .. 4 destructive (per-domain table, V1)
    idempotent: bool               # decides retry policy on failure
    privacy_floor: int             # min privacy class of data it may touch
    input_schema: dict             # JSON-schema-ish; validated before invocation
    verifier: Callable[[Result], VerifyReport]   # deterministic postcondition
```

- **Plan→act shape:** the plan proposes `tool + arguments`; the runner validates
  arguments against the schema *before* invocation; a failed validation is a failed
  turn, not an exception escape.
- **Every invocation is an event:** `tool.invoked` (name, args-digest, permission
  level) → result → `tool.verified` (ok/fail + verifier detail). The event log remains
  the complete audit trail — "show my work" extends to the loop for free.
- **Retry policy is mechanical:** idempotent tools may be retried by the runner;
  non-idempotent ones park the run for human confirmation. The model never decides
  whether to retry.
- **V0.2 toolset (3, all read-only, level 0):** `shell.read` (allowlisted read-only
  git/ls/cat class commands), `file.read` (workspace-scoped), `memory.search` (FTS
  over notes). Write-capable tools arrive with the V1 permission table (ADR-001's
  choke point becomes the enforcement point for both spend and permission).

### 4. Checkpoint format — one event, rebuildable, minimal

The checkpoint **is** the `run.turn.*` event (ADR-002: no state outside the log).

```json
{
  "kind": "run.turn.completed",
  "payload": {
    "run_id": "r3f9c2",
    "turn": 4,
    "goal": "Fix PG16 migration",
    "plan": "read migration script, then pg_dump schema diff",
    "tool": "shell.read",
    "tool_args_digest": "sha256:9f2a…",
    "verify": {"ok": true, "detail": "exit=0, output 4.2KB"},
    "context_sections": {"identity": 198, "project": 741, "task": 380,
                          "memories": 1200, "turns": 620},
    "next_intent": "diff schema against expected",
    "bounds": {"deadline": "2026-09-27T23:40:00Z", "max_turns": 25,
                "tool_calls_used": 4, "tool_calls_budget": 20}
  }
}
```

- **Derived state (run index: "which run is active? what's the latest turn?") lives in
  a small `runs` table** — materialized view, rebuildable, same discipline as
  `sessions` in ADR-002.
- **Resume = load latest `run.turn.completed` → rebuild context package (§2) → plan
  next turn.** Nothing else is trusted: in-flight Python state is disposable by design.
- **Bounds live *in* the checkpoint** so a resumed run inherits its deadline/budget —
  a crash cannot be used to reset a budget.
- Parking a run = final `run.parked` event with a reason (deadline | budget |
  verify-fail ×3 | no-provider); resuming is ordinary.

### 5. Boundaries and rejections (the pre-merge fence)

- **No parallel tools, no multi-agent, no self-modifying plans in V0.2.** Sequential,
  single-tool, single-plan — the failure modes we're buying immunity to are exactly
  the ones parallelism introduces.
- **The plan is data, not code** (JSON in the event). If a future version generates
  code, it goes through the tool protocol like everything else — never executed
  because the model "said so."
- **The runner never calls a paid API** (ADR-001's $0-by-construction holds); the
  provider hook is a *single function* where the V1 spend-guard lands.
- **Rejected alternatives (for the record):** LangChain/CrewAI-style frameworks
  (opaque control flow, fights inspectability — the same reason ADR-001 rejected
  service meshes); checkpointing to files (a second source of truth — events *are*
  the checkpoint, per ADR-002); plan-then-execute-in-batch (defeats per-turn
  verification, the entire safety mechanism).

## Consequences

- Crash mid-run loses at most the current turn; `thoth continue` reconstructs from
  the log, inheriting bounds. The §51 failure-recovery story becomes one code path.
- Every run is replayable turn-by-turn from `thoth log` — debugging the loop is
  reading, not archaeology.
- The V3 learned-router dataset starts collecting here (per-turn outcome in the
  checkpoint payload costs nothing now).
- Honest cost: per-turn events make the log chattier; consolidation of `run.*`
  history joins the V2 consolidation job rather than growing unbounded.

## Revisit triggers

- Turn latency makes one-tool-per-turn painful for long reads (→ consider verified
  read *batches*, still checkpointed per batch — not parallelism).
- Verify functions prove too weak for a tool class (→ add sandboxing, not trust).
- Context budgets routinely overflow (→ FTS → hybrid retrieval with local embedder,
  V2, per ADR-001's trigger list).

## Pre-merge note (Team-B input wanted on)

1. Per-turn checkpoint granularity vs. per-phase — is the event volume justified?
2. FTS-only retrieval for the V0.2 context package — sufficient or premature?
3. Verify-as-code (verifier per tool) vs. verify-as-model-judgment — where is the line
   for *your* proposed toolset?
4. Budget inheritance on resume — any failure mode we've missed where bounds should
   *reset* rather than carry?
