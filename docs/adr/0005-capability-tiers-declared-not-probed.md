# ADR-0005: Skill delivery follows a declared Capability Tier

ADR-0001 makes Providers interchangeable but not equally capable: `claude` loads a skill natively and takes it as a slash command in a headless prompt, while `codex` and `aider` have no equivalent and never will on our schedule. Each Provider therefore declares a Capability Tier, and a skill reaches an Agent natively where the tier allows it and as a Fragment — its `SKILL.md` inlined into the prompt — where it does not. The tier is configuration, never inferred by probing what is installed, because a probe that guesses wrong degrades in the middle of a Run and looks like a bad model rather than a missing feature.

## Consequences

Every skill AgentBastion ships needs a Fragment form, so a skill that only works as a native slash command cannot be adopted. A Fragment costs prompt tokens on every invocation that native delivery does not, and the degraded path is the one most likely to go untested, since the primary Provider never exercises it.
