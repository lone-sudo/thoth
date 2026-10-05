# ADR-006: The model-selection gate — matrix protocol, admissibility rule, pin tests

- **Status:** Accepted (ADR-007 dissolved the staging — no second reviewer exists;
  the gate is in force as amended).
  Amended 2026-09-30: goal families — the gate generalizes (section 4);
  same day, a third (multi-step) family demoted the incumbent to
  best-available (section 4, third verdict).
  Amended 2026-10-05: an operator-approved diagnostic lever (file.read
  misses carry the workspace's file names) re-gated the incumbent —
  first local candidate to pass all three families (section 4, fourth
  verdict). The model did not change; the harness did.
- **Date:** 2026-09-29
- **Depends on:** ADR-003 (the runner loop: verify-before-checkpoint — tool-turn
  truth comes from the code verifier, never the model), ADR-004 (degradation
  ladder, fail-closed availability; park is always preferable to guessing),
  the model matrix (`evals/model_matrix.py`), the V3 decision record
  (`docs/ROADMAP.md` §V3+)
- **Constraint inherited from the spec:** $0 automatic spending — the gate
  selects only within the local floor; cloud alternatives stay structurally
  disabled (the `ProviderSpec` invariant), so this ADR never trades capability
  against money.

## Context

Until the matrix existed, the planner model was a guess: the V1 placeholder
tagged `qwen2.5:3b-instruct` and nothing enforced or even recorded why. The
live smoke sessions produced real but qualitative findings — small models emit
valid plan JSON a turn or two but cannot sustain the protocol; a model can
look protocol-perfect while never actually acting — and no reproducible way to
compare candidates. Two kinds of decisions were waiting on the same missing
instrument: which brain routes the planner, and which floor policies the
runner needs to survive the candidates.

The finding that shaped everything: **qwen2.5-0.5b scores 100% JSON validity
and 100% finish rate while acting zero times.** A metrics set that cannot see
that difference does not measure a planner; it rewards lying. The gate below
is built so that specific lie is structurally visible.

## Decisions

### 1. The matrix is the acceptance gate

`evals/model_matrix.py` is the only path by which a model becomes routable:

- **One row per model per goal family, same test every time:** 5 episodes per
  model per family at a pinned temperature of **0.2** (the production
  planner's setting — rates measured at different temperatures are not
  comparable; the smoke demo keeps its 0.4 and is not the gate). The harness
  reuses the smoke driver's guard-gated shim and the exported hint-free
  `PLAIN_GOAL` verbatim for the `read` family, so those rows are literally
  the same protocol the `--plain` benchmark runs. Since the 2026-09-30
  amendment the matrix scores **goal families** (`GOAL_FAMILIES`):
  `read` (file.read over the workspace), `memory` (FTS retrieval via
  `memory.search` over notes seeded into the episode's own DB, exactly like
  production memory), and `locate` (multi-step: the target file's name is
  never in the goal and `file.read` cannot list directories, so the only
  path is `shell.read` to discover, then `file.read` to read; each episode
  gets a fresh temp workspace with a decoy file) — `--family` restricts a
  run; a verdict needs every family.
- **Every byte crosses the guard.** The matrix adds no I/O module; episodes
  are ordinary guarded local runs, and the CI no-bypass whitelist stays
  exactly three files.
- **Server swap is part of the harness:** llama-server is relaunched per GGUF
  and polled until `/v1/models` answers, so rows are reproducible on any box
  with the models on disk. Since 2026-09-30 the catalog GGUFs are also
  byte-pinned: a committed sha256 manifest (`evals/model_manifest.json`,
  `evals/model_drift.py`) is preflight-checked before rows are scored —
  re-quantized weights can no longer enter the record silently.

### 2. Four metrics, honestly computed

| Metric | Definition | Trials |
|---|---|---|
| `json_validity` | planner answers that parsed into a plan (pooled) | raw answers |
| `tool_turn_rate` | episodes with ≥1 **verified** tool turn | episodes |
| `park_cleanliness` | terminal state is done or a diagnostic park (fixed prefix list, incl. the finish floor); a transport crash is never clean | episodes |
| `self_finish` | episodes the model ended itself with done=true — **earned**, enforced by the finish floor | episodes |

Two subtleties are load-bearing:

- **`provider.attempt` is a planning round, not an answer** — a bounded retry
  consumes two model answers inside one round, so the answer counter reads
  `provider.outcome` events. Counting rounds undercounted invalid answers in
  the first stabilized run (instrument lesson, journal 2026-W39).
- **The finish floor (landed 2026-09-29) is what makes `self_finish` honest:**
  a done claim with zero verified tool turns is refused (`run.finish.refused`)
  and parks the run. Before the floor, finish% measured who *claims*
  completion; after, who *earns* it. Scripted no-work callers opt out
  explicitly (`allow_finish_without_turns=True`) — the floor is an
  anti-hallucination guard, not a no-op policy.

### 3. Wilson 95% intervals on every rate

Each rate carries a Wilson score interval over **the same trials as its point
estimate** (pooled answers for JSON validity, episodes for the rest — brackets
always bracket their own point). Wilson is chosen over the normal
approximation (which collapses to [0,0]/[100,100] at 0/5 and 5/5) and over
Laplace smoothing (which invents successes): at the edges, the
boundary-touching bound is exactly 0 or 1 by construction and the *opposite*
bound is the informative one — test-pinned. **Rates without bounds do not
enter decision records.**

### 4. The admissibility rule

> A planner model is admissible only when **json% = tool% = 100%** over the
> matrix protocol (Wilson bounds reported alongside) — **within every goal
> family** (amended 2026-09-30; `gate_verdict(gate_row(rows))` is this rule
> as code). A model that reads but cannot retrieve is not routable.

First verdict (2026-09-29, 5 episodes/model): **qwen2.5-3b 100% [72,100]
across the board** — the only candidate that both adheres to the protocol and
acts. The small models fail on tool% (0%); after the floor, their finish% is
0% too. `park_cleanliness` was 100% on all four: across 40 episodes the runner
diagnosed every failure and never crashed — the gate measures *models*, while
the degradation ladder absorbs their failures.

Second verdict (2026-09-30, two families, 5 episodes/model/family):
**qwen2.5-3b passed the then-amended gate — 100%/100% in BOTH `read` and
`memory`.** The generalization risk the first record named openly became a
measured quantity: the same protocol, composed over a second level-0 tool,
did not demote the incumbent. The pretender pattern survived the new family
intact (qwen2.5-0.5b: 100% JSON in both families, tool% 40/0, hollow
floor-refused finishes in memory), and the SmolLM2 pair degraded per family
without a single crash (park_cleanliness 100% across all 40 two-family
episodes). Full table in the ROADMAP decision record amendment.

Third verdict (2026-09-30, three families, 5 episodes/model/family, 60
episodes): **no local candidate passes the gate.** The `locate` family
demoted the incumbent: qwen2.5-3b stays perfect on `read` and `memory`
(100%/100% each) but scores 100% JSON with **0% tool turns on `locate`** —
it guesses file names from the goal's keyword (`deploy*`, `deploy`,
`deploy.txt`) straight into `file.read`, never planning the `shell.read`
listing step, deterministically at the pinned temperature. The failure was
verified to be the model's, not the harness's: the chain is reachable end
to end (a solo `ls` lists the target first; `file.read` reads it; the
turn-observation summary hands the discovered name back on line one; the
goal reaches the planner verbatim). Consequence, recorded in the decision
record: the pin changes basis — qwen2.5-3b remains DEFAULT_MODEL as
**best-available** under the $0 local-only floor (it dominates every
alternative on every family), no longer as a gate-passing model; any local
candidate passing ALL families re-opens V3.

Prompt-level repair attempt (2026-09-30, same day): a discover-then-act
heuristic in `PLAN_SYSTEM` ("never guess a name you have not seen in a tool
result; discover first, then act") was measured through this same matrix
(incumbent, n=5, all three families) before shipping. Result: locate
unchanged (json 100%, tool 0%, identical filename-guessing), read
unchanged, and the memory family wobbled for the first time on the
incumbent (json 100% → 80% [55,93]). Reverted — a neutral-to-harmful policy
does not ship — and the constant now carries the experiment's provenance so
it is not silently re-litigated. Conclusion: the locate gap is a reasoning
limit of the current local floor, not a prompt deficit; the next lever is a
stronger brain, which stays out of scope by the $0 floor, not by this ADR.

Fourth verdict (2026-10-05, 5 episodes/model/family, 60 episodes): **the
incumbent passes the gate — json% = tool% = 100% within EVERY family**
(read 100/100, memory 100/100, locate 100/100 with json 100% [84,100]
pooled over 16 answers; park_cleanliness 100% across all 60 episodes).
The lever was **data in a tool result, not an instruction** — the one
channel the prompt-repair experiment did not touch. Episode evidence from
the failing baseline: the model guessed `file.read("deploy*")` twice and
the old miss diagnostic (`not a file in the workspace: deploy*`) carried
zero grounding, so nothing in the loop could correct it. The operator
approved the lever explicitly before the instrument changed (sole-decider,
ADR-007). Honest framing of what locate now measures: discovery is
*reactive* (guess, read the diagnostic's real file names, read the target)
rather than purely *foresighted* — a failed-guess-and-recover chain, which
is itself a real operating skill; the always-list-first skill is no longer
required to pass. The smaller models did not re-rank: qwen2.5-0.5b stays a
hollow-finish pretender (tool% 0/0/20, one lucky locate episode), the
SmolLM2 pair fails on JSON validity, and clean% held at 100% everywhere.
Consequence: the DEFAULT_MODEL pin stands unchanged on a restored basis —
**gate-passing**, no longer merely best-available. Any future change to
what tools say on failure is a harness change and re-runs this gate.

### 5. The selection is pinned to the record by tests

`ollama.DEFAULT_MODEL` (`qwen2.5:3b-instruct`) carries a provenance comment
binding it to the ROADMAP record and this gate, and two pin tests enforce it:
the constant equals the recorded selection, and the `ollama-local` provider
description names the brain and "matrix-gated". **A silent model swap fails
CI.** Re-decision procedure, stated where the swap would happen: run the
matrix on the candidate → update the decision record → change the tag.

### 6. Scope: the gate measures the protocol, not task quality

Comprehension and answer quality are out of scope for V3 — the plain goal's
semantic summary is a smoke criterion, not a matrix one. The gate asks one
question: *does this model sustain the plan→act→verify→finish protocol inside
Thoth's actual prompts, verifiers, and bounds?* And it selects within the
local floor only; the $0 invariant is untouched. The `locate` family
sharpens the question into *can it chain tools to reach an answer it cannot
guess?* — and the measured answer for every local candidate is currently
no, which is exactly the kind of finding the gate exists to surface (the
larger models this points at remain out of scope by the $0 floor, not by
this ADR).

## Consequences

- **Auditioning a candidate is one command** — `python -m evals.model_matrix
  --add KEY /path/to.gguf PARAMS` resolves the GGUF, refuses duplicate and
  decision-reserved keys, runs the protocol **across every goal family** (one
  server load per model), and prints the folded verdict with the next step
  baked in ($0, zero code edits). Persistence is deliberate: a PASS
  still requires updating the decision record and the DEFAULT_MODEL pin, so
  the evidence trail cannot be skipped by convenience.
- The event log plus committed tables now answer "why is this model the
  brain?" end to end: protocol in this ADR, numbers in the journal and the
  ROADMAP record, enforcement in CI.
- **The bytes behind a verdict are part of the record** (2026-09-30): the
  committed manifest pins exactly which GGUFs the gate scored; a matrix run
  on drifted bytes prints the drift warning, and
  `python -m evals.model_drift --check` is the operator's explicit refusal
  (exit 3). Recovery is re-run the matrix → rebuild the manifest (`--build`)
  → amend the record. The manifest is evidence, not a runtime license:
  warn-and-proceed keeps `--add` auditions of new weights a one-command path.
- Honest costs: **n=5 gives wide intervals** ([57,100] at a perfect 100%) —
  the gate separates doers from pretenders, not good from slightly-better;
  raising n is a protocol parameter, not a redesign. The SmolLM2 pair's JSON
  rates also wobble between invocations (variance is itself a finding); only
  canonical single-invocation tables are recorded as evidence.
- The instrument and the policy co-evolve: the matrix *found* the hollow-finish
  failure, the finish floor *sharpened* the matrix (finish% became a truth
  test). Decision records must be re-verified when runner policy changes —
  this happened once already, as a ROADMAP amendment.

## Alternatives rejected (for the record)

- **Vendor/model-card benchmarks** (MT-bench class): they measure general chat
  quality, not protocol adherence under Thoth's exact prompts, verifiers, and
  bounds — and they are not $0-reproducible offline.
- **Mean-of-episode-ratios for JSON validity:** an episode with 1 attempt
  would weigh as much as one with 12; pooling raw answers is the honest
  denominator.
- **Normal approximation or Laplace smoothing for intervals:** see Decision 3 —
  both fail exactly where small-n rates live.
- **Bigger n before a decision needs it:** n=2 was directional, n=5 separated
  the classes cleanly; more episodes would have been precision theater.
- **A/B-ing models in production:** the ladder is local and free — a
  controlled matrix is cheaper, faster, and reproducible; production runs are
  for work, not experiments.

## Revisit triggers

## Revisit triggers

- ~~A second goal family lands~~ → **resolved 2026-09-30**: the `memory`
  family (retrieval via `memory.search`) landed; the incumbent was re-gated
  and passed in both families (second verdict, section 4). The next family —
  anything beyond read + retrieve, e.g. a synthesis goal needing multi-tool
  chains — re-opens this trigger.
- A smaller model passes the gate (e.g., a 1.5B at json% = tool% = 100%) →
  re-record: a better $0 hardware floor changes the V3 economics.
- Runner policy changes that alter what "done" means → re-run the matrix and
  amend the decision record (precedent: the finish floor).
- Two admissible candidates ever tie → raise n before arguing.

## Open questions staged for the Team-B review

1. Should admissibility also require **clean% = 100%** (currently implied by
   every passing model but not spelled in the rule)?
2. Should the finish floor ever admit pure-question goals that legitimately
   need no tool? (Current: no exceptions beyond the explicit scripted-caller
   opt-out.)
3. ~~Matrix cadence: per-candidate auditions only, or periodic re-runs to
   catch model-pack drift (GGUF re-quantizations silently changing
   behavior)?~~ → **resolved 2026-09-30** (the silent half): drift is no
   longer silent — the catalog GGUFs are pinned by a committed sha256
   manifest (`evals/model_manifest.json`; `evals/model_drift.py --build` /
   `--check`), the matrix preflight-checks it before every invocation, and a
   mismatch prints the evidence warning ("rows scored on the old bytes are
   no longer evidence") with the recovery procedure. The check hashes bytes
   only (no server, no episodes). Two speeds since the same-day fast-mode
   amendment: `--fast` (sizes + first-1MiB fingerprints, ~0.2s — what the
   matrix preflight and the CI gate run, so manifest drift fails pytest in
   seconds) and the full check (adds the full-file sha256 pass, ~90s — the
   last word before bytes enter a decision record). Whether full periodic
   re-runs add value on top of byte-pinning stays open for Team-B.
4. Should the pin-test mechanism extend to the matrix catalog itself (the
   `MODELS` list pinned to the decision record's table)?
