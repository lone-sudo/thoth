# JARVIS — INDEPENDENT ARCHITECTURE REVIEW (Team Member A: Buffy)

*Prepared as one input to a multi-AI architecture review. Written to be merged and argued with, not to win.*

**Reviewer stance:** senior AI systems architect + pragmatist. Optimized for "one person, evenings and weekends, $0 auto-spend, must survive provider failures and must actually ship."

---

## 0. EXECUTIVE SUMMARY

The vision is sound and unusually well-guarded (you've already identified the three failure modes that kill these projects: runaway automation, runaway memory, runaway billing). The architecture is **directionally correct but wrongly sequenced**. Nothing here is infeasible; the risk is that you build the cathedral and never consecrate it.

My core claims:

1. **Jarvis = 3 things in V1**: a persistent state store (memory + tasks), a router with a provider registry, and a small set of tool executors on your machines. Everything else is later.
2. **Your biggest blind spot is not architecture — it's the session/runner loop.** Your docs describe memory, routing, skills, permissions, but never the *agent loop*: observe context → plan → act → verify → checkpoint. That loop is Jarvis. Get it working end-to-end with two tools before building any subsystem.
3. **Bring Your Own AI is genuinely differentiated** but the hard, valuable part is the *provider abstraction with capability + quota awareness*, and almost no hobby project does it well. That's also where the money would be if it ever became a product.
4. **Scrap the idea of Jarvis "learning routing preferences" in V1** — static per-skill routing tables with manual override. Learning routers need hundreds of labeled outcomes you won't have for months. Same for knowledge graphs: flat tags + SQLite FTS beats a graph store until you have ~10k entities.
5. **The TikTok/Instagram "saved content" pipeline is the single most fragile, ToS-risky part of the plan.** Build the inbox around *materials you already possess* (downloads, exports, URLs pasted by you) and treat platform pull-sync as a V3 experiment behind a kill switch.

One-sentence verdict: **You are designing a good 3-year system. Cut it to a 3-month spine, then let the documentation discipline you've already specified grow the rest organically.**

---

## 1. THE CORE VISION (§1–2)

The loop Me → Intent → Context+Memory → Decide → Route → Execute → Verify → Remember → Report is right. Two corrections:

- **"Understand intent" is not a phase.** A small local model + rules can handle 80% of intent classification ("what project?", "read vs. write?", "needs which skill?"). Don't burn a frontier-model call on every utterance. This matters doubly because of your $0 policy — every classification costs quota you'll want for real work.
- **"The computer is the hands, AI decides" — correct, keep it sacred.** Formalize it: *AI never touches the machine except through a declared tool schema.* If an action can't be expressed as a tool call, it doesn't happen (no free-form mouse automation in any version you rely on; see §28).

**Team roles:** your ChatGPT/Claude/Gemini assignments match broad community experience; fine as defaults. But encode them as *default preferences, not identity*: any model should be able to do any task when others are unavailable, with a "quality penalty" you accept knowingly. The architecture must survive your subscriptions changing (they will — pricing, quotas, and even product shapes change quarterly).

**Missing role:** an **embedder**. Memory retrieval needs embeddings; you have no line item for that. Recommend a local embedding model (e.g., a small BGE/E5-class model on CPU) as the default so retrieval never costs quota. This is a concrete gap in your team diagram.

## 2. THE $0 SPENDING POLICY (§3)

Excellent constraint; it forces good architecture. Implementation, not aspiration:

- **Provider registry** is the heart. Each provider entry: `{ id, auth_type: subscription_web | api_key_free_tier | local, quota_state: {used, limit, resets_at}, enabled, capabilities, priority_by_skill }`. Where quota state is unknowable (web subscriptions), track *estimated usage* yourself (requests sent, tokens approximated) and treat thresholds as soft limits — better to over-conservatively switch providers than to dead-end.
- **Hard-coded, non-negotiable rules in the executor layer** (not in prompt, not in config the agent can edit): Jarvis may never (a) call any endpoint that requires a payment instrument, (b) accept/enable billing, (c) store card data, (d) upgrade plans. A "spend guard" check sits in the single choke point every outbound AI request passes through. Make it code, tests, and a CI check — not policy text.
- **Degradation ladder** per task: preferred cloud → alternate cloud → local model → degrade task scope (e.g., classify-only) → park task with state and notify. The park-and-resume path must be a *first-class feature*, because it's what makes quota resets usable ("Claude resets in 4h; task queued, will resume 14:00").
- One subtlety: browser-automation access to your ChatGPT/Claude subscriptions is technically "$0" but sits in a gray zone vs. provider ToS and is brittle. Design as if it's your *fallback*, never the primary path for anything you care about.

## 3. THE TWO-MACHINE ENVIRONMENT (§4)

Fine. Concrete guidance:

- Pick **one machine as the Jarvis brain**. Given Docker/Postgres/long-running jobs, that's the Ubuntu Latitude (`lone1`). The ZBook is where *you* work, so it needs a thin client (CLI + a tray app) that talks to lone1 over SSH/Tailscale. Rationale: long-running schedulers, schedulable tasks, storage for memory, and no "laptop fell asleep and my briefing didn't run" class of failure.
- Do **not** start with Docker-heavy deployment. V1 = Python venv + SQLite + systemd user services. Docker arrives when you have ≥3 long-running services and feel the pain.
- SSH access: key-based, and Jarvis should have its own dedicated key + a restricted shell wrapper, not your personal key. This becomes the natural permission boundary per machine.
- The "treat as one environment" goal is right but is a **V2 capability** (needs the resource registry: which machine, which tools installed, which reachable). Don't build it before you have tasks worth distributing.

## 4. CONTENT INTELLIGENCE (§6–12, 47) — the most fragile pillar

### Reality check on platforms

| Source | Reliable path | Verdict |
|---|---|---|
| YouTube | Official Data API (watch later via playlists needs OAuth; transcripts via `yt-dlp`/API), share-URL ingest | **Realistic now** |
| TikTok saved | No user-data API for saved items; scraping is ToS-violating and breakable | **Not a foundation.** Use: paste URL → `yt-dlp`-style download where permitted, or manual save-to-files |
| Instagram saved | No API; scraping bans accounts | **Not a foundation.** Manual export/share-to-Jarvis only |
| Browser bookmarks | Local file (Chrome/Firefox JSON) — fully yours | **Realistic now** |
| Notion | Official API, user token | **Realistic now** |
| RSS/articles | Fetch + readability extraction | **Realistic now** |
| Files/PDFs/screenshots | Local, you own it | **Realistic now** |

**Design rule that follows: the Inbox must be source-agnostic.** "An item" = `{content or URL, origin: manual|bookmark|watch-later|file|share-sheet, captured_at, initial_note?}`. Platform syncs, when they eventually exist, are just *producers* feeding the same inbox. Never let the pipeline know or care that something came from TikTok. This is your insulation against §7's "disappear overnight" risk, and it's the design I'd defend hardest against any other reviewer who proposes deep platform integrations.

### The pipeline (§9)

Your 10-stage pipeline is right in shape but **wrong in granularity** if built as one flow. It's three phases with different triggers:

1. **Capture (sync, cheap, no AI):** store raw item + metadata. Always succeeds, never blocks, never calls a model. A video you save at 1 AM costs 0 tokens.
2. **Process (batched, budgeted):** a nightly/weekly *budgeted* job picks N items by priority, does transcription (local Whisper-class on CPU is fine for short clips), summarization (cheap/local first, cloud only if needed), classification, dedup-candidate detection. **Explicit per-run budget:** "process max 20 items, max X local minutes, max Y cloud calls, then stop."
3. **Act (event-driven, human-in-loop):** surfacing results, proposing connections/tasks — via your digest, not via notifications per item.

The single most important correction to §9: **stages must be independently re-runnable and idempotent.** Transcription dies? Re-run just that stage. Model changes? Re-summarize from stored raw. Store *raw artifacts* (transcript text, extracted text) forever; derived artifacts (summary, tags) are cache, always regenerable.

### "Why did I save this?" (§10)

Model saving intent as a **single required-at-capture field with smart defaults, freely editable**: `save_reason ∈ {learn, build, reference, inspiration, fun, share, maybe, unknown}` + optional one-liner. Key decisions:

- Default is `unknown` — the system asks at most once, *in the digest*, never as an interrupting notification.
- Never auto-derive intent and present it as fact. "You tagged this 'learn' when you saved it" is the honest framing.
- The 3-month resurface prompt ("want me to remind you why?") is a **digest feature**, not a pipeline feature. Cheap, humane, useful.

### Dedup (§11)

Right idea, wrong anthropomorphism. Don't "detect duplicates and synthesize" as one magic step; run:

1. **Near-dup detection:** embedding cosine > threshold on transcript/summary → cluster candidates.
2. **Concept extraction:** each item contributes *claims/techniques* as short statements.
3. **Knowledge note merging (only in digest review):** "9 items mention B-tree indexes. Merge into one note with 9 sources?" — **you click, it merges.** Auto-merge silently is how trust dies; LLM merge mistakes are silent and unrecoverable unless raw is kept.
4. **Disagreement surfacing:** cluster where sources conflict → flag in digest as "these disagree." This is honestly the highest-value dedup output and I'd prioritize it over silent consolidation.

### Claim verification (§12)

Scope it hard, or it becomes a fact-checking startup:

- V1: no verification, just **provenance labels** ("origin: TikTok", "origin: official docs", "origin: peer-reviewed"). Labels alone beat weak checking.
- V2: **claim-matching, not fact-checking.** Extract claims → check against *your own knowledge base* first (docs, official sources you've saved) → only if absent, one search against a curated source whitelist (official docs, Wikipedia, standards bodies). Output: "matches your saved docs / conflicts with official docs / unverified — nothing reliable in your KB." Never assert truth; assert *agreement with sources you trust*.
- Never let verification block ingestion; it's an offline enrichment with its own budget.

## 5. MEMORY (§13–20, 46.G–I) — your best-designed section

Your storage/active-context separation is exactly right. Here is the concrete architecture I'd defend:

### Store (all local, all boring, all inspectable)

- **SQLite is the memory backbone.** WAL mode, FTS5 for lexical search, `sqlite-vec` for embeddings. Do **not** start with Postgres for memory — Postgres is for your data-engineering project data; memory wants zero-maintenance file-level backup (copy one file) and no server. (Expect another reviewer to say Postgres+pgvector because it's "more serious"; it's more ops for zero benefit at your scale, and it couples your two projects' failure domains.)
- Tables: `events` (append-only log: sessions, actions, outcomes), `notes` (atomic facts/decisions, versioned, with `source_event_id` provenance), `artifacts` (raw transcripts, files — content-addressed), `entities` (projects, people, tools — just rows, not a graph store), `links` (typed edges: note↔entity, note↔note, task↔note), `tasks`.
- **Append-only events + derived notes** is the key mechanic: the raw log is never rewritten; notes are LLM/rule-derived *views* that can be regenerated. This gives you §17 consolidation without data loss, and it makes "what did Jarvis believe in March" answerable.

### Retrieve (the part everyone overbuilds)

Retrieval = **one SQL query, then rerank, then cap.** Concretely per request:

1. Assemble *cheap* context first (no AI): active project (from current dir/app), open task, last session summary, user profile row.
2. One hybrid search: FTS5 + vector over `notes` (not events, not raw), filtered by project/scope tags.
3. Rerank top ~30 with cross-encoder (local, small) or simple recency+importance scoring; take **top 5–10**.
4. Hard **token budget per section** (e.g., identity 200, project 800, memories 1200, task state 400, ~2–3k total) enforced in code; if over, drop lowest-ranked, never truncate silently.
5. Log what was retrieved with the session (this is your §21 routing/learning dataset and your debugability lifeline).

### Write policy (the part everyone underbuilds)

Memory writes are the actual failure mode — more than reads. Rules:

- **No silent writes from chat.** End of each session (or task), a *structured* extraction proposes: new notes, updated notes, expirations. Diff shown in wrap-up/digest; you approve once ("apply all" allowed). This costs one small call per session and nothing else.
- **Notes are atomic and typed:** `decision {status, supersedes}`, `fact`, `preference`, `lesson`, `state`. Typed notes make decay and consolidation mechanical instead of clever.
- **Explicit > inferred, always:** "save this" beats auto-capture. A memory Jarvis wrote that you never see is a liability.

### Decay & consolidation (§16–17)

- **Decay applies to retrieval ranking, not to deletion.** Implement `effective_score = importance × recency_decay × access_boost × project_affinity`, all parameters in config. Items never vanish; they just stop being retrieved. Silently deleting important info (your §16 fear) becomes structurally impossible in V1.
- True deletion only via: explicit TTL on ephemeral notes (deadlines, "tomorrow I need to…" → auto-expire *after* completion confirmation), or your manual purge.
- **Consolidation = a scheduled job, project-scoped:** for each active project, when notes exceed N or age exceeds M, generate/update a **Project Brief** (state, decisions, known issues, next actions — exactly your §17 list) from `notes`, then mark superseded notes `archived`. Raw events untouched. Re-runnable any time; regenerate on demand.
- Session summaries: every session ends with a 3–5 line structured summary stored as an event + candidate notes. This is what powers "where did I leave off?" for free (§23) — that question is ~90% answered by "last session summary + git status + open tasks," which is cheap and reliable. The remaining 10% (IDE state, terminal history) isn't worth the surveillance cost yet.

### §19 task-scoped memory: correct, keep it strict-ish.

Enforce via the retrieval query itself (filter by project tag by default; cross-project only when explicitly asked or when the router detects a cross-cutting intent like "morning briefing"). Default-strict, opt-in-leaky beats default-leaky.

## 6. ROUTING & RESOURCE MANAGEMENT (§20–21, 53)

- **V1 routing = static table in config.** `{skill → [provider priorities]}` + availability checks + capability floor ("needs 32k context" → exclude X). Manual override commands (`use local`, `use chatgpt`) are just config overlays. This covers 95% of value at 5% of the complexity.
- **Learning-to-route is V3, and it needs a dataset you must start collecting now** (zero cost): every request logs `{task_class, chosen provider, latency, outcome (succeeded/failed/you-corrected), context_tokens}`. That's the "learn from actual results" you asked for in §21 — a logging schema today, a model someday. Say this in the docs so future-you doesn't discard the logs.
- **Capability/availability probe:** a lightweight health+quota loop (ping endpoints where API exists; local model loaded? web quotas estimated?) runs every few minutes and caches. Router consults cache, never probes synchronously.
- **The §20 pattern (compress locally → small package → available AI) is the correct default for everything except coding**, where losing 1% of context to a bad local compression costs more than the tokens saved. Make compression opt-in per task class, not universal.

## 7. TASKS, AUTONOMY, SCHEDULING (§22, 24–27)

- **Task graph: yes, but as rows in SQLite, not a DAG engine.** `tasks(id, project, title, status, depends_on[], created_by, created_at, deadline, notes_link)`. Sub-tasks via `parent_id`. You do not need a workflow engine; you need a to-do list Jarvis can read and write, with dependency awareness at *plan time* (when Jarvis plans work, it linearizes the graph).
- **Autonomous work (§24) is the highest-value, highest-risk feature.** Rules that make it safe:
  - Deadline = hard stop enforced by the *runner*, checked between steps, not by the model's good intentions.
  - **Checkpoint after every step:** state = plan + completed steps + artifacts + next action. Crash-safe by construction; "resume" is just re-entering the loop. This is the same discipline as the agent loop (§2 above) — it's one mechanism, not two.
  - Autonomy levels mirror your permission levels (§28): autonomous ≠ permission escalation. A Level-2 task still asks for Level-3 actions.
  - Spend/quota budget is part of the task plan ("this run may use N cloud calls") and the runner enforces it.
- Morning briefing / nightly wrap-up (§25–26): right instinct, one design rule — **briefings are generated from stored state, never from live scraping**, so they can't fail because a website changed. Keep the daily item count ≤ 7 and include "nothing needs you" as a legitimate output. A briefing that's always long trains you to ignore it.
- Focus mode (§27): trivially implementable V2 (DND via OS API, timer, session log). The "close distractions" part is where surveillance pressure starts; keep it to explicit user-invoked actions on a list you maintain, never ambient monitoring.

## 8. COMPUTER CONTROL (§28–29, 37, 32)

Your 5-level ladder is good. Refinements:

- **Make levels per-domain, not global.** I want Level 2 for `git` but Level 0 for `email` even in the same session. So: `{domain → level}` with a global ceiling. Simple table, massive safety win.
- The ladder maps cleanly onto *tool classes*: L0 read-only inspection; L1 reversible local (open app, create file in workspace); L2 dev actions (run tests, git commit on branch, install in venv); L3 external (network calls, push, email, calendar); L4 destructive (delete, force-push, system config). **L4 requires a typed confirmation phrase** — not click-through, not "yes," a phrase like "confirm: delete". Typing fatigue is a feature.
- **Approval UX matters more than the policy engine.** Every L2+ action shows: what will run, on which machine, what it touches, rollback command. One keypress to approve, one to reject-with-reason (reasons become training/eval data for §21 logs).
- **Git safety (§29): correct, and make it mechanical.** Before any AI write to a repo: `status` clean? → branch `jarvis/<task>`; dirty → auto-stash-tag or refuse with report. AI works *only on its own branch*, runs tests, then shows you a diff for apply. An "AI Sandbox → Test → Review → Apply" flow can literally be: branch + tests + `git diff` + your approval + fast-forward. Don't build containerized sandboxes in V1; branches + tests are 90% of the safety at 10% of the cost. (Sandboxed execution is worth adding when you run untrusted fetched code — that's a V3 need.)
- **Activity awareness (§37): default answer is no.** No keyloggers, no screenshot loops, no screen recording, no ambient mic, no reading IMs. The honest V1 "awareness" is: active window title (poll every 5s, local only, session-scoped retention of hours, opt-in) + which project dir your shell is in (shell integration) + explicit session context. That's enough for "when did I start data engineering today" (§47.7) without becoming surveillance. Everything else is opt-in, per-source, with visible indicators and short retention. Put these lines in your security doc as *never* items — non-negotiables survive scope creep; intentions don't.
- **Teach-me-a-workflow (§32): the most overrated item in the doc.** Learning arbitrary GUI procedures from observation is a research project (and the reliability of GUI automation is too low to be worth it). What's actually achievable and 10x simpler: **capture terminal-level workflows** — Jarvis watches your shell history within a declared "recording" session (explicit opt-in, you say "Jarvis, learn this") and proposes a script/skill. Shell workflows are the ones worth automating anyway. GUI recording → never, or V4 experiment.

## 9. PRIVACY, KNOWLEDGE BASE, SEARCH (§30–31, 38)

- **Privacy router (§30): right model, one addition — route the *data*, not just classify it.** `public | personal | private | sensitive` should be an attribute on every note/artifact/task with a default per source, and the router enforces `provider_min_privacy` (e.g., `sensitive` → local-only, full stop, no prompt cleverness can override because it's checked at the request choke point next to the spend guard). Add `sensitive` handling for credentials/keys: a local secret store, and a redaction pass over any context leaving the machine. The choke point pattern (one function all outbound requests pass) is the security centerpiece; it carries both the spend guard and the privacy guard.
- **Knowledge base (§31): SQLite+FTS+vec (as §5) over extracted text; PDFs via local extraction; code repos via file indexing. Don't stand up a full RAG framework.** Your RAG is: extract text → chunk (structure-aware for code, ~paragraph for prose) → embed (local model) → store → hybrid retrieve. That's a weekend, and every "framework" you adopt will fight your budget and inspectability goals.
- **Universal search (§38): make it a query over the memory DB + ripgrep over whitelisted dirs + git log.** That covers "that RCC calculation from last month" embarrassingly well. Cloud drive search is V3; it adds providers to protect for little gain if you keep working files local-first.

## 10. SKILLS, MANUAL, COMMAND LANGUAGE (§33, 39, 40)

- **Skills: yes, but define a skill as data + code hooks, not as a microservice.** Minimal viable skill = a folder: `{SKILL.md (what it does, context it needs, routing prefs, permission ceilings), tools.py (declared tool schemas), prompts/}`. A registry scans folders. This matches your §40 layout; just resist giving skills their own processes, queues, or IPC — they're libraries loaded by the runner.
- **The Operating Manual (§39): best cheap idea in the document.** It's a markdown file (or 3: `user.md`, `machines.md`, `conventions.md`) that Jarvis reads into context for nearly every task and *you* edit when preferences change. It also doubles as the seed of your personal-knowledge product later. Do it in week 1.
- **Command language (§33): keep only commands that map to a *state change or a mode*, and make them discoverable from plain English.** "Coding mode" = context preset (project + relevant skills + permission profile + routing prefs). Everything else ("where did we leave off", "save this") shouldn't be a special command at all — it should be plain English the intent classifier maps to an action. A rigid verb list that you must memorize is a UX tax; a small mode system + good intent handling is not.

## 11. CHALLENGE MODE & AI COUNCIL (§34–35)

- **Challenge mode: build this, it's cheap** — it's a prompt pattern + your existing providers: (1) extract claims/assumptions, (2) generate counterarguments + failure modes + simpler alternative, (3) optionally one web check. V1 = single-provider; multi-provider cross-exam is just running it twice.
- **Council (§35): good instinct, cap it hard.** Trigger only on explicit command or tasks tagged `high-stakes` (I'd define that as: irreversible, external, or >2h of work). Models should answer **blind** (no cross-visibility) to avoid anchoring; Jarvis then synthesizes agreements/disagreements/uniquely-claimed-points. "Hallucinated consensus" (two models confidently repeating the same wrong claim) is the failure mode you correctly fear — the mitigation is requiring *evidence citations* per point and downgrading consensus that cites nothing. Cost-wise this is 3–4x a normal request, which is exactly why it's command-gated. Your own prompt (this document) is the existence proof the pattern works.

## 12. KNOWLEDGE GRAPH (§48)

**Postpone.** Your diagram (Goal→Project→Task→Knowledge→Source) is a *relational schema with extra steps*. Typed `links` in SQLite + tags gives you every query you'll actually run this year ("all content connected to PostgreSQL under Data Engineering"). A dedicated graph store (Neo4j et al.) buys graph queries you don't need yet and costs backup/ops/learning. Add a real graph layer only when a query you *want* is painful in SQL — that's the honest trigger. (If another reviewer endorses graphs for V1, the test to propose: name 3 concrete questions you'll ask weekly that typed-links SQL can't answer. If they can't, it's premature.)

## 13. OVERLOAD & RESTRAINT (§49–50) — the part that makes or breaks adoption

This is your best product thinking. Implement it as code, not values:

- **All proactive output funnels into 2–3 surfaces:** morning briefing, end-of-day digest, weekly knowledge review. No other notifications in V1. (An "urgent" path exists only for failures affecting running tasks.)
- **Every proposed action has a cost-of-attention cap:** digests show top 5 with "show 3 more" expander; task proposals are batched into a weekly "proposed tasks (7)" list you triage once, not drip-fed.
- **"Not worth your attention" is a first-class answer** — the classifier's outputs include `dismiss` with a reason; dismissed items are visible in a review log (so the system stays accountable) but never resurface without new information. Metric to watch: **dismissal rate per surface.** Above ~60% on any surface = that surface is mis-calibrated; auto-suggest tuning it. You're optimizing for *trusting the briefings*, and trust is built by brevity + hit rate, not completeness.
- Ingest defaults to **capture-only**; processing is budgeted and batching is visible ("tonight's run processed 18/53 items"). The backlog being *honestly visible* beats it being *silently infinite*.

## 14. FAILURE RECOVERY (§51)

Classify failures by what they invalidate:

- **Provider failures** → router's degradation ladder (§2). Task parks with state; recovery is automatic on next availability probe.
- **Tool/machine failures** → tool adapters return structured errors; runner marks step failed, keeps checkpoint, either retries (idempotent steps only) or pauses for you. Every mutating tool must declare `idempotent: true|false` in its schema — this one flag decides retry policy everywhere.
- **Jarvis crash / reboot** → everything durable lives in SQLite + files; the runner is stateless-ish (loads checkpoint). A crashed run resumes as "continue task X from step N," which is the same code path as "continue" — **one resume mechanism for all interruptions** (§23, §24, §51) is the design principle to enforce.
- **Platform/API changes** → producers fail gracefully into the inbox's `failed_sources` list; nothing else breaks because nothing else depends on them (§4 design rule pays off here). Alert in weekly digest, not per-item.
- **Content un-retrievable later** → this is why §9 stores raw artifacts locally at capture time. A dead link with a stored transcript is a solved problem; a dead link with only a summary is a sad one; with neither, it never existed.
- **Corruption** → SQLite WAL + nightly file backup (to the other machine) + append-only events = you can always rebuild derived state. Test the restore path once, deliberately, in V1. (Untested backups are Schrödinger's backups.)

## 15. LOCAL AI (§41)

Realistic on i7-10810U / 32GB / Quadro P520 4GB:

- **Good fits (CPU, small models):** embeddings (BGE/E5-small class), Whisper-small/base transcription, classification/tagging/routing intent, summarization of short texts, reranking, structured extraction with a 3–8B instruct model (Qwen/Llama-class at Q4 via Ollama), light RAG answers.
- **Poor fits:** repository-level coding (context + quality), long-document multimodal analysis, anything latency-critical at scale. P520 4GB effectively means small quantized models only; treat GPU as a bonus, design for CPU.
- **Architectural role: the floor, not the rival.** Local handles: always-on classification, embedding, privacy-gated (`sensitive`) work, offline degradation, budget overflow. Cloud handles: reasoning-heavy generation. This maps exactly onto your $0 policy — local is what makes $0 *workable* rather than merely aspirational.
- One honest warning: local-model ops (downloading, quantization choice, Ollama updates, RAM pressure) is its own time sink. Budget it a fixed evening, pick models from a pinned list, and move on.

## 16. PRODUCT (§42–43)

**Bring Your Own AI is genuinely differentiated** — not because orchestration is new, but because the *combination* is: subscription-first (no metered billing as a product principle — your §3 hard constraint is actually a *feature* nearly no competitor has), local-first memory you own, personal automation with a real permission model, and task continuity. The moat is not any single piece; it's that all four are boring, trustworthy, and combined.

Honest caveats: (1) provider ToS around subscription access via automation is a real product risk, not just a personal one — a product can't lean on browser-driving someone's ChatGPT; (2) the "AI memory layer" space is crowded and moving fast — a big vendor shipping OS-level memory would compress your differentiation from the *memory* side, so the durable value skews toward **automation + computer control + personal workflows** (messy, local, unglamorous — defensible) over pure memory; (3) verticalization (Developer/Student/Engineering Jarvis via skills + manual templates) is the most realistic commercial path and conveniently costs nothing to enable architecturally — skills-as-folders *is* the verticalization mechanism.

Recommended posture: build for yourself, document in public (§44 gives you the material for free), keep monetization as a later option. The architecture you've specified doesn't need to change to allow a product later — that's a sign it's right.

## 17. THE SIX CORE FUNCTIONS (§54)

Verdict: **5 of 6 correct; one is dangerous as stated; one is missing.**

1. Understand me — ✅ (via manual + memory, not magic).
2. Remember what I'm doing — ✅ (the storage/active-context split carries this).
3. Choose the right intelligence/tool — ✅ with "right" defined as available+capable+permitted, not optimal.
4. **Operate my computer safely — keep, but restate as "Act on my machines only through declared tools, with auditable permission levels."** The word "operate" invites GUI automation creep; the rewording forecloses it.
5. Resume work intelligently — ✅ and it's the most underrated one; checkpointing is the spine.
6. Turn useful information into useful action without overwhelming me — ✅ but make "and sometimes correctly choose *no* action" explicit (your §50 instinct deserves principle status).
- **Missing 7th: "Show my work."** Jarvis must always be able to answer *what did you do, why, and how do I undo it*. This is what makes the other six trustworthy, and it falls naturally out of the event log + checkpoints you're already building. Add it.

## 18. DOCUMENTATION (§44, 46.Y–Z) — mostly right, prune it

Your structure is good but has redundancy: `docs/` (specs), `decisions/` (ADRs), `build-log/`, `failures/`, `experiments/` overlap heavily — failures are build-log entries; experiments usually produce decisions. Consolidation:

```
JARVIS/
├── README.md                 # what it is, quickstart, status banner
├── docs/
│   ├── 00_VISION.md          # the pitch + six/seven principles
│   ├── 01_ARCHITECTURE.md    # living spec (components, data flow)
│   ├── 02_MEMORY.md  03_ROUTING.md  04_SECURITY.md   # living specs
│   └── ROADMAP.md            # V1/V2/V3 with acceptance criteria
├── decisions/ADR-*.md        # immutable, numbered, one per real decision
├── journal/                  # build-log, one file per week (see below)
│   └── 2026-W40.md
└── CHANGELOG.md
```

- **ADR: yes, unambiguously** — context / decision / consequences / *alternatives rejected* / status. Number them and never rewrite (supersede instead). The alternatives-rejected section is where your §44 "what we considered" requirement actually lives. Write ADR-001 (overall architecture) and ADR-002 (memory store) *this week* — the act of writing them will force clarity.
- **Journal over build-log:** one file per week, chronological, *includes failures inline with full context* (a failure without surrounding context teaches nothing). Jarvis can draft entries; you edit and commit them. Separate `failures/` folder: skip — tag entries instead.
- **Experiments:** one doc per experiment with hypothesis / method / result / decision — but only for *real* experiments (you changed your mind based on data). Most "experiments" are just work; they're journal entries.
- **What Jarvis records automatically:** session summaries, task outcomes, routing log (the §21 dataset), retrieved-context logs, dismissed-item reasons, tool-call audit trail. **What Jarvis never writes unprompted:** ADRs, vision docs, journal *voice*, roadmap. Machine-drafted ADRs read like machine-drafted ADRs and rot trust.
- **Anti-burden rules:** no doc without an owner and a trigger ("update ROADMAP when a version ships"); a doc that hasn't been touched in 90 days gets a visible "stale" banner (script check), not deletion; README always states current truth in 10 lines ("status: V0.3 — memory + runner working, router stubbed"). If documenting a week took >20 minutes of *your* time, the system is wrong, not you.
- **Making docs useful to future-Jarvis:** ADRs and key docs get indexed into the knowledge base like any other content (they're text), so "why is memory SQLite?" is answerable from your own decisions. That closes the loop elegantly: **the documentation is Jarvis's first real RAG corpus.** That's also a great early test of the memory system — dogfooding with material where you know the ground truth.

## 19. OVERENGINEERING AUDIT (§45) — requested ruthlessness

**Cut / postpone entirely:**
1. Knowledge graph store (§48) — typed links suffice until proven otherwise.
2. Learned routing (§21) — static tables + logging; learn in V3.
3. GUI workflow learning (§32) — shell-level capture only; GUI never.
4. Universal search across cloud drives (§38) — local + git + memory first.
5. Docker deployment, microservices, message queues — monolith + SQLite + cron/systemd until real pain.
6. Any platform *pull*-sync for TikTok/Instagram (§6–7) — paste/share/manual-export producers only.
7. Auto-merging knowledge without review (§11) — propose, never apply.
8. Multi-agent "council" automation (§35) — explicit command only.
9. Voice (§33 implies it) — text-first until the core is trustworthy; voice is a V3 UX layer, not a subsystem.
10. Proactive anything beyond briefing/digest (§25) — until dismissal rates prove trust.

**Keep deceptively simple on purpose:** the task system (SQL rows, not a DAG engine), permissions (a table + a choke point), skills (folders), routing (config). The moment any of these needs a framework, you've drifted.

## 20. DIRECT ANSWERS TO §46 (the compressed ones)

- **A. Sound?** Yes in direction, no in sequencing — the agent-loop/checkpoint core is unspecified and everything else currently outranks it.
- **B. Missing:** (1) the runner/agent-loop spec; (2) the embedder in the AI team; (3) a *testing/evaluation story* — golden-set prompts per skill, run weekly, so routing and memory changes are measurable instead of vibes. Also missing: secrets handling, and a decision on where *code for Jarvis itself* lives (it should be a normal repo with its own git discipline — Jarvis editing Jarvis needs the §29 sandbox rules applied to itself).
- **C. Remove:** knowledge graph, GUI learning, platform pull-syncs, learned routing (for now).
- **D. V1 (see §21 roadmap):** runner loop, SQLite memory (events+notes+FTS+vec), provider registry + static router + degradation ladder, 5–8 tools, permission table + choke point, tasks table, session summaries + "where did I leave off," manual file, ADRs + journal.
- **E. V2:** content inbox (file/URL/bookmark/YouTube) with budgeted processing, digests, briefing/wrap-up jobs, skill folders, focus mode, multi-machine (lone1 registry), claim *provenance*, consolidation job.
- **F. Long-term:** platform producers where officially supported, claim verification pipeline, learned routing, cross-machine orchestration, voice, vertical skill packs, possibly product.
- **G–I. Memory:** see §5 — events+notes, hybrid retrieval with hard token budgets, decay-as-ranking not deletion, project-scoped consolidation into briefs, approval-gated writes.
- **J–O. Content:** capture→budgeted process→digest-review→act; intent field at capture; near-dup clustering + merge-with-click; claim provenance then claim-matching vs. whitelisted sources; connect to projects via explicit links proposed in digest; anti-overwhelm via surfaces + caps + dismissal metric (§4, §13).
- **P–Q. Integrations:** realistic = YouTube (API/yt-dlp), bookmarks (local), Notion (API), RSS, local files, calendar/email via OS APIs with L3 permission. Where APIs don't exist: manual/export/share-sheet producers, never scrapers — and the inbox abstraction means losing a producer loses nothing structural.
- **R–S. Routing/resources:** static skill→provider table + availability cache + capability floors + manual overrides; spend+privacy guards at the single choke point; degradation ladder; per-task quotas. Logs now, learning later.
- **T. Autonomous tasks:** plan→loop with per-step checkpoints, deadline+quota enforced by runner, autonomy ⊆ permissions, one resume path.
- **U. Computer control security:** per-domain levels, declared tools only, typed confirmation for L4, diff-review for repo writes, dedicated SSH identity per machine, choke-point audit log. Threat model: prompt injection via ingested content is your #1 real threat — treat every fetched text as attacker-controlled: content-derived instructions are never commands; ingested content gets the *lowest* permission floor regardless of session level. (This is the security item most likely to be underweighted by every reviewer including me; it deserves its own ADR.)
- **V. Recovery:** §14 — checkpoints + one resume mechanism + producers-that-fail-gracefully + tested backup restore.
- **W. Local AI:** the floor (embeddings, classification, transcription, sensitive/offline/degraded work) — see §15.
- **X. BYOA differentiated?** Yes, as a *bundle* (subscription-first + local memory + permissions + continuity); durable value skews to automation/control; see §16.
- **Y–Z. Documentation:** §18 — ADRs + weekly journal + living specs; Jarvis drafts logs/summaries only; humans own narrative and decisions; docs are Jarvis's first RAG corpus.

## 21. SCENARIO WALKTHROUGHS (§47) — compressed

1. **Python-technique TikTok:** paste URL → capture (0 tokens) → nightly: transcript (local Whisper) → summary + claims + tags (local model) → dedup check → appears in next digest under "learn," linked to Data Engineering if matched → you decide: keep as reference note, promote to task, or dismiss. No task auto-created, ever.
2. **10 Docker videos over 3 weeks:** cluster candidates surface in weekly review → "merge 7 into one note, keep sources?" → you merge → one consolidated Docker note with 7 citations + disagreements flagged. Old summaries stay as archived sources.
3. **RCC video:** matches project tag via entities → stored under RCC research; if it contradicts a note in your KB (e.g., a code-detail), flag, don't overwrite; offer "attach to project."
4. **Saved + ignored 6 months:** decay drops it from retrieval; it survives in `unknown`-intent review lists ("never processed, still saved — triage?") at the weekly review. Honest backlog > fake cleanliness.
5. **Misinformation:** provenance label ("origin: TikTok, unverified"); claim-matching (V2) marks conflicts with your KB/whitelist; it never merges into consolidated notes without the conflict annotation.
6. **"Build this project" video:** classification proposes task *in the weekly proposed-tasks list* with your why-did-you-save answer as context; you triage. Direct-create only if you say "save this as a task."
7. **"Remind me when I start data engineering tomorrow":** "start" = context signal (active window/project-dir match) — opt-in window-title polling makes this reliable enough; fallback = morning briefing includes it pinned until dismissed. Never guess via ambient inference.
8. **Duplicate tutorial:** near-dup at capture → digest shows "similar to 3 saved items; new point: X" → enriches existing note with source ref rather than new note.
9. **Useful, not actionable:** lands as a `reference`/`fact` note with tags; that's a *successful* outcome — the system must not equate "no task" with "no value." Your §50 principle, encoded.
10. **"I want to actually learn local AI":** promote intent: creates a *learning goal* entity + proposes a structured path from your saved cluster (foundations→setup→build→project) as *proposed tasks*, converts future saves on the topic into "path material" automatically, and tracks progress in the weekly review. This is the best-case version of the whole content pillar — a save becomes a curriculum, not a hoard.

## 22. IF I WERE BUILDING THIS (§55.10) — my architecture in one page

**One monolith, one machine, one database.** Python package, runs on lone1, CLI-first (`jarvis "…"` and `jarvis task start …`), thin SSH client from the ZBook.

- **Core loop (runner):** load context package → plan (any available model) → act via tools → verify → checkpoint → repeat. All task state in SQLite. Interruptible/resumable by construction.
- **Memory:** events (append-only) + notes (typed, versioned) + artifacts (raw), SQLite+FTS5+sqlite-vec, local embedder, hybrid retrieval with hard budgets, decay-as-ranking, project-scoped consolidation into briefs, approval-gated writes.
- **Providers:** registry with quota/availability cache, capability floors, static skill→provider routing, degradation ladder, spend+privacy guards at the single outbound choke point.
- **Tools:** 5–8 to start — shell (domain-scoped), git (branch protocol), files (workspace-scoped), http fetch, notes/memory CRUD, task CRUD, scheduler trigger. Each declares idempotency + permission level + privacy floor.
- **Permissions:** `{domain → level}` table + choke point + typed confirmation for destructive + full audit log.
- **Surfaces:** CLI, morning briefing (generated from state), end-of-day digest, weekly review (knowledge merges, proposed tasks, backlog triage).
- **Content:** inbox table + capture-only default + budgeted nightly processing + digest surfacing. Producers: manual, files, URLs, bookmarks. YouTube first-class; everything else via paste/share.
- **Docs:** ADRs + weekly journal + living specs; docs indexed into the KB as the first RAG corpus.

What I'd *deliberately not build* for months: voice, GUI control, graphs, learning routers, platform syncs, Docker, multi-agent anything, and any UI beyond the terminal.

---

## 23. CLOSING — FOR THE MERGE

Where I expect genuine disagreement with other reviewers, flagged for synthesis:

1. **Postgres+pgvector vs SQLite for memory** — I say SQLite until pain; expect a "more serious DB" recommendation. The tiebreaker: what does backup/restore look like at 2 AM in each proposal?
2. **How much autonomy in V1** — I cap it hard (checkpointed loops, human approval at L2+). Some will argue agentic autonomy is the whole point. The tiebreaker: which version do you *leave running overnight* in month one?
3. **Platform integrations** — I say inbox-agnostic with no scraping; some will propose aggressive browser automation for saved-content sync. The tiebreaker: account-ban risk asymmetry — losing saved TikToks costs hours; losing an account costs the account.
4. **Knowledge graph** — I say typed links now, graph later; expect graph enthusiasm. Tiebreaker: the three-weekly-questions test in §12.
5. **Monolith vs services** — I say monolith; expect "clean microservices" advice. Tiebreaker: count the deploy scripts each requires at week 4.

Questions I'd put to the other reviewer: How do *they* handle prompt injection from ingested content? What's their concrete token budget mechanism for memory? What exactly is their agent-loop checkpoint format? And what's the first thing that breaks when a provider changes its API?

— Buffy, Team Member A
