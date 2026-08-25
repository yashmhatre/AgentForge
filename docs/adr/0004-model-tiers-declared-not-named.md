# ADR-0004: Roles declare a Model Tier, never a model

Running every Role on the strongest available model is the largest avoidable cost in the system, and ADR-0001 removed the easy fix — a Provider shelling out to three CLIs cannot rely on per-agent model frontmatter, and each CLI accepts different model identifiers. A Role therefore declares an intent-named tier, and each Provider maps tiers onto its own CLI's model flag; users override the mapping in `.agentforge/config.yaml` without touching Role definitions.

Default assignment:

| Role         | Tier     | Reason                                            |
|--------------|----------|---------------------------------------------------|
| Orchestrator | deep     | Pays for all downstream reasoning once (ADR-0003) |
| Architect    | deep     | Design errors are the expensive kind              |
| Security     | deep     | Missed findings are silent                        |
| Implementer  | standard | Executes a plan it did not write                  |
| Tester       | standard | Edge cases need reasoning; scaffolding does not   |
| Reviewer     | cheap    | Reviews and documents against a known diff        |

## Consequences

Three tiers is a coarse instrument: a Provider whose CLI exposes no model flag collapses to one, and AgentForge cannot detect that in advance. The Reviewer at `cheap` is the assignment most likely to be wrong, because it also writes the prose a human reads at Sign-off — if that comes back thin, the Reviewer moves to `standard` and this table gets an update.
