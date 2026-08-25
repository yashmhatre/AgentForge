"""Built-in AgentForge Roles.

A Role is a definition — a name, a default Model Tier, and standing instructions
(CONTEXT.md). The tiers here are ADR-0004's default table; a user overrides them
per Run on the command line, and per project in configuration once
`agentforge init` exists.

M1 implements two of the six. The rest are named so that a Roster mentioning
them is recognized as premature rather than as nonsense.
"""

from __future__ import annotations

from ..core.contracts import ModelTier, Role
from .implementer import IMPLEMENTER
from .orchestrator import ORCHESTRATOR

#: Roles that can actually run today.
ROLES: dict[str, Role] = {
    ORCHESTRATOR.name: ORCHESTRATOR,
    IMPLEMENTER.name: IMPLEMENTER,
}

#: The full cast from CONTEXT.md, with ADR-0004's default tiers. Roles absent
#: from `ROLES` are M2 work; naming them here lets the Orchestrator recognize a
#: reasonable-but-unavailable choice and say so.
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
            f"M1 runs only: {', '.join(sorted(ROLES))}"
        )
    raise UnknownRole(f"no Role named {name!r}; available: {', '.join(sorted(ROLES))}")


__all__ = ["IMPLEMENTER", "KNOWN_TIERS", "ORCHESTRATOR", "ROLES", "UnknownRole", "resolve_role"]
