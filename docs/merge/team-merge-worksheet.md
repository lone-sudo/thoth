# Team Merge Worksheet — Team-A × Team-B

*Fill the Team-B column **only from the actual Team-B review text** (paste verbatim,
with section refs). Nothing in the "outcome" column gets written until both sides are
quoted. Outcomes land as ADR status updates or superseding ADRs; disagreements are
preserved in each ADR's alternatives-rejected section, not erased.*

**Rules of engagement (from the original §56 brief):**

1. Adopt Team-B's position only with evidence, not plausibility.
2. Disagreements are documented, not smoothed over — a named disagreement with both
   quotes beats a fake consensus.
3. Where Team-B is silent, mark "no input" — silence is not agreement.
4. Every adopted change gets an ADR line: *what changed, why, source quote*.
5. Team-A's author (Buffy) does not get to "win" — the owner decides with both texts
   on the table.

---

## Part 1 — Architecture-level claims

| # | Topic | Team-A position (quote/ref) | Team-B position (quote from review) | Owner decision | Outcome → where it lands |
|---|-------|------------------------------|--------------------------------------|----------------|--------------------------|
| 1 | Memory store | SQLite + FTS5 + sqlite-vec; "Postgres is more ops for zero benefit at your scale" (§5) | | | |
| 2 | Knowledge graph | Postpone; typed links in SQLite until 3 weekly queries are unanswerable in SQL (§12) | | | |
| 3 | Platform pull-sync | Never a foundation; paste/export/official-API producers only (§4) | | | |
| 4 | Learned routing | Static tables + logs now; learn in V3 (§6) | | | |
| 5 | Monolith vs services | One process, one package; no Docker/queues until real pain (§19.5, ADR-001) | | | |
| 6 | GUI automation | Never a foundation; shell-level capture only (§8) | | | |
| 7 | Autonomy in V1 | Capped hard: checkpoints, per-domain levels, autonomy ⊆ permissions (§7, §8) | | | |
| 8 | BYOA differentiation | Genuinely differentiated as a bundle; durable value skews to automation/control (§16) | | | |
| 9 | Overload control | 2–3 surfaces, caps, dismissal-rate metric, "not worth your attention" first-class (§13) | | | |
| 10 | Documentation | ADRs + weekly journal + living specs; docs are Jarvis's first RAG corpus (§18) | | | |

## Part 2 — ADR-003's four staged questions

| # | Question (ADR-003 §"Pre-merge note") | Team-A stance | Team-B position (quote) | Owner decision | Outcome → where it lands |
|---|--------------------------------------|---------------|--------------------------|----------------|--------------------------|
| Q1 | Per-turn checkpoint granularity vs per-phase — is the event volume justified? | Per-turn; volume accepted, consolidation deferred to V2 | | | |
| Q2 | FTS-only retrieval for V0.2 context package — sufficient or premature? | Sufficient until V2 (hybrid + local embedder per ADR-001 triggers) | | | |
| Q3 | Verify-as-code vs verify-as-model-judgment — where is the line? | Code-only verifiers; model may interpret, never certify | | | |
| Q4 | Budget inheritance on resume — any failure mode where bounds should *reset*? | Carry, never reset; deadline may be re-set only by a new explicit run | | | |

## Part 3 — Things Team-B sees that Team-A missed

| # | Claim (quote) | Team-A response | Owner decision | Outcome |
|---|---------------|------------------|----------------|---------|
| M1 | | | | |
| M2 | | | | |
| M3 | | | | |

## Part 4 — Merge output checklist

- [ ] Every adopted change → ADR status line or superseding ADR, with source quote
- [ ] Every rejected Team-B claim → documented in alternatives-rejected, with reason
- [ ] ADR-001/002/003 statuses updated (or superseding ADR-005+ created)
- [ ] ROADMAP re-sequenced if ordering changed
- [ ] Journal entry: what the merge changed, what it confirmed, what stays contested
- [ ] CHANGELOG entry
- [ ] Commit (merge worksheet + outcome docs together; the *comparison itself* is part
      of the project record per §56)
