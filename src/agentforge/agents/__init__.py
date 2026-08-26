"""Built-in AgentForge Roles.

A Role is a definition — a name, a default Model Tier, and standing instructions
(CONTEXT.md). The tiers here are ADR-0004's default table; a user overrides them
per Run on the command line, and per project in configuration once
`agentforge init` exists.

All six Roles CONTEXT.md names can run. The Architect is in no shipped Workflow
even so — most Tasks do not need a design pass, and one at `deep` on every Run
would be the most expensive default in the project.
"""

from __future__ import annotations

from ..core.contracts import ModelTier, Role
from .architect import ARCHITECT, Architect
from .implementer import IMPLEMENTER, Implementer
from .orchestrator import ORCHESTRATOR
from .reviewer import REVIEWER, Reviewer
from .security import SECURITY, Security
from .tester import TESTER, Tester

#: Roles that can actually run today.
ROLES: dict[str, Role] = {
    ORCHESTRATOR.name: ORCHESTRATOR,
    ARCHITECT.name: ARCHITECT,
    IMPLEMENTER.name: IMPLEMENTER,
    TESTER.name: TESTER,
    SECURITY.name: SECURITY,
    REVIEWER.name: REVIEWER,
}

#: How to run each Role, keyed the same way. A runner takes a Provider and
#: exposes `run(plan=, context=, cwd=, role=, tier=)`.
#:
#: This registry is why the Workflow runtime names no Role: it looks a runner up
#: by the name the Workflow step declared. Adding a Role is an entry here and a
#: line of YAML, never an edit to the engine. The Orchestrator is absent
#: deliberately — it produces Issues rather than running inside a Workflow.
RUNNERS: dict[str, type] = {
    ARCHITECT.name: Architect,
    IMPLEMENTER.name: Implementer,
    TESTER.name: Tester,
    SECURITY.name: Security,
    REVIEWER.name: Reviewer,
}

#: The full cast from CONTEXT.md, with ADR-0004's default tiers. Every name here
#: now has a runner, so the two sets are equal — the difference between them is
#: what the Orchestrator uses to recognize a reasonable-but-unavailable choice
#: and say so, and it is what makes naming a seventh Role's tier before writing
#: its runner safe. The Architect was the last name to sit in the gap.
KNOWN_TIERS: dict[str, ModelTier] = {
    "orchestrator": ModelTier.DEEP,
    "architect": ModelTier.DEEP,
    "security": ModelTier.DEEP,
    "implementer": ModelTier.STANDARD,
    "tester": ModelTier.STANDARD,
    "reviewer": ModelTier.CHEAP,
}


class UnknownRole(LookupError):
    """A Roster names a Role this version of AgentForge cannot run."""


def resolve_role(name: str) -> Role:
    """Look a Role up by name, for rebuilding a Roster out of an Issue body."""
    key = name.strip().lower()
    if key in ROLES:
        return ROLES[key]
    if key in KNOWN_TIERS:
        raise UnknownRole(
            f"the {key!r} Role is not implemented in this version of AgentForge; "
            f"runnable Roles: {', '.join(sorted(ROLES))}"
        )
    raise UnknownRole(f"no Role named {name!r}; available: {', '.join(sorted(ROLES))}")


__all__ = [
    "ARCHITECT",
    "IMPLEMENTER",
    "KNOWN_TIERS",
    "ORCHESTRATOR",
    "REVIEWER",
    "ROLES",
    "RUNNERS",
    "SECURITY",
    "TESTER",
    "UnknownRole",
    "resolve_role",
]
