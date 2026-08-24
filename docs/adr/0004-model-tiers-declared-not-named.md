# ADR-0004: Roles declare a model tier, never a model

- Status: Accepted
- Date: 2026-08-24

## Context

Running every Role on the strongest available model is the largest avoidable cost in the system. Scaffolding a pytest fixture and auditing a Delta MERGE for injection risk do not need the same reasoning depth, and paying for the deeper one twice is waste that a newcomer will never notice on their bill.

ADR-0001 removed the easy path. Claude Code agent frontmatter can name a model per agent; a Provider shelling out to three different CLIs cannot rely on that, and each CLI accepts different model identifiers.

## Decision

A Role declares an intent-named tier: `deep`, `standard`, or `cheap`. It never names a model.

Each Provider maps tiers onto its own CLI's model flag. Users override the mapping in `.agentforge/config.yaml` without touching Role definitions.

Default assignment:

| Role         | Tier     | Reason                                          |
|--------------|----------|-------------------------------------------------|
| Orchestrator | deep     | Pays for all downstream reasoning once (ADR-0003) |
| Architect    | deep     | Design errors are the expensive kind             |
| Security     | deep     | Missed findings are silent                       |
| Implementer  | standard | Executes a plan it did not write                 |
| Tester       | standard | Edge cases need reasoning; scaffolding does not  |
| Reviewer     | cheap    | Reviews and documents against a known diff       |

## Consequences

Cost per Run becomes a configuration line rather than a code change, and a team can move a Role up a tier when it underperforms without editing prompts.

Three tiers is a coarse instrument. A Provider whose CLI exposes no model flag can only offer one tier, and AgentForge cannot detect that in advance — the mapping simply collapses.

Tier names outlive model names. `deep` survives a vendor's next release; `claude-opus-5` does not.

The Reviewer at `cheap` is the assignment most likely to be wrong. Review that only checks a diff against a plan is cheap work, but the Reviewer also writes the documentation a human reads at sign-off. If that prose comes back thin, the Reviewer moves to `standard` and this table gets an update.
