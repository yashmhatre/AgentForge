# ADR-0007: A Role runs no commands unless a human opens the gate

An Agent that may edit files but not run them cannot satisfy acceptance criteria written as commands, and the M1 acceptance run produced exactly that: an Implementer that traced seven tests by hand, reported `completed`, and disclosed the substitution only because it happened to be scrupulous. Every Provider therefore runs default-deny, and execution is opened for one Run by an explicit flag on `agentforge implement` that refuses unless the working tree is clean and a branch exists; a Role denied a command it needs reports that denial in its Agent Result rather than substituting inspection. The posture is set in one place per adapter — `permission_mode` in `claude.py`, its equivalent in every other — and never in prompt text, because a permission expressed as an instruction is a permission the model can talk itself out of.

## Considered Options

Forbidding the Orchestrator from writing command-shaped acceptance criteria was rejected: it makes every plan worse to work around a permissions problem. A per-Role or per-project allowlist is the better long-run answer and is deferred to M5, where Project Context can supply what a project's test and lint commands actually are — the set is bounded there, which is what makes persisting it appropriate when a standing grant is not.

## Consequences

A per-Run flag rather than a configuration key means no standing grant persists in a repository, and the cost is that an unattended Run cannot execute anything until the M5 allowlist arrives. Default-deny also does load-bearing work elsewhere: a vendored skill that would otherwise file its own issue cannot reach `gh`, so the single-Issue guarantee holds by construction rather than by asking the skill nicely.
