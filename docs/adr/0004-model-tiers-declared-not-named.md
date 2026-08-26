# ADR-0004: Roles declare a Model Tier, never a model

Running every Role on the strongest available model is the largest avoidable cost in the system, and ADR-0001 removed the easy fix — a Provider shelling out to three CLIs cannot rely on per-agent model frontmatter, and each CLI accepts different model identifiers. A Role therefore declares an intent-named tier, and each Provider maps tiers onto its own CLI's model flag; users override the mapping in `.agentforge/config.yaml` without touching Role definitions.

Default assignment:

| Role         | Tier     | Reason                                              |
|--------------|----------|-----------------------------------------------------|
| Orchestrator | deep     | Pays for all downstream reasoning once (ADR-0003)   |
| Architect    | deep     | Design errors are the expensive kind                |
| Security     | deep     | Missed findings are silent                          |
| Reviewer     | deep     | Speaks last; nothing downstream catches a thin one  |
| Implementer  | standard | Executes a plan it did not write                    |
| Tester       | cheap    | Reports what the suite said rather than deciding it |

One exception to "a Role declares a tier": the Reviewer's rewrites run at `cheap` regardless of the tier the Reviewer is on. Two different jobs share that Step — judging a diff against a frozen Plan, and applying scanner findings that already name the phrase, the line, and a replacement. The declared tier is chosen for the first. This is the only Role that does it, and it is not a general licence: a second tier inside a Step is worth it only where the two jobs are that far apart.

## Consequences

Three tiers is a coarse instrument: a Provider whose CLI exposes no model flag collapses to one, and AgentForge cannot detect that in advance.

**Amended, 2026-08-26.** This ADR predicted that the Reviewer at `cheap` was the assignment most likely to be wrong, and that it would move to `standard`. It moved further, to `deep`. The reason is the one the original gave — it writes the prose a human reads at Sign-off — plus one the original missed: it is the last Role to speak, so nothing downstream catches a review that is wrong or thin. The next thing after it is a person deciding whether to merge.

The Tester moved the other way in the same amendment, from `standard` to `cheap`, and that one contradicts its recorded reason rather than fulfilling it. "Edge cases need reasoning" is still true, and reasoning about an edge case nobody wrote a test for is the capability being traded away here. What is bought is that the expensive tiers sit where a mistake is unrecoverable: the Orchestrator's plan, the Security audit, and the Reviewer's report. The Tester's own claims are checked by something that does not reason at all — the suite either passed or it did not, and #10's Gate re-runs it rather than believing the report. If the Tester starts missing flaws a human then finds at Sign-off, this is the row to move back.
