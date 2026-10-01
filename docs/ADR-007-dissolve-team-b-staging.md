# ADR-007: Dissolving the Team-B staging (no second reviewer exists)

- **Status:** Accepted (owner's word, 2026-09-30)
- **Date:** 2026-09-30
- **Deciders:** Owner (direct statement: "There is no team b")
- **Supersedes:** the staging provisions of ADR-001/002/003/004/005/006 and the
  Team-B clause of the ROADMAP sequencing rule

## Context

The project was sequenced around a two-team review protocol: Team-A (the
authoring agent, Buffy) builds and documents; Team-B, an independent second
reviewer, would attack the result before the "merge" - the ROADMAP's first
rule, six ADR status lines, four "staged for the Team-B review" question
lists, and a merge worksheet (`docs/merge/team-merge-worksheet.md`) all
assumed that review would happen.

On 2026-09-30 the owner stated plainly: there is no Team-B. No second
reviewer exists and none is coming. The staging state was never a live
dependency in practice - the invariant core, the runner loop, and the
model-selection gate all shipped anyway. What remained was paperwork
describing a reviewer who does not exist, which is exactly the kind of
dishonest record this repo refuses elsewhere (provenance comments on
reverted experiments, GGUF drift manifests, parks instead of guesses).

## Decision

The Team-B staging is dissolved. The worksheet's own rule already covered
the limit case: where Team-B is silent, mark "no input" - silence is not
agreement. The terminal form of silence is that the reviewer does not
exist. Every staged question is therefore recorded as **decided by the
owner alone**, with each ADR's open-questions section kept verbatim as the
record of what was decided and on what evidence.

## What this changes

- ADR-002: the remaining "(draft — pre-merge)" qualifier is struck — it was
  already Accepted, but its staging language died with the merge.
- ADR-003/004/005/006: status Proposed -> Accepted (decided as written,
  with their implementations already shipped; no second review was ever
  pending).
- ADR-001: the "reopen if the Team-B review overturns a listed trigger"
  clause is struck; the revisit triggers themselves stand unchanged.
- ROADMAP: the sequencing rule loses the Team-B clause. The sequenced-
  caution discipline remains; there is simply no external gate to wait for.
- The merge worksheet is closed with "no input - reviewer does not exist"
  in the Team-B column and remains as a historical artifact.

## What this does NOT change

- Any technical decision. Nothing was adopted or rejected by this ADR;
  every ADR's content, open questions, and alternatives-rejected section
  stands exactly as written.
- The owner's authority to reopen any of it, the same way as before - with
  evidence, through a new ADR.
- The practice of writing decisions down before acting on them. That was
  never dependent on a second team.

## Consequences

- The decision record is honest about who decided: one owner, with measured
  evidence, no fictional second opinion.
- Future ADRs carry no staging language; a decision is Accepted or
  Superseded.
- Revisit trigger: if a genuine independent reviewer ever appears, this ADR
  is superseded by a new decision and the worksheet discipline revives.
