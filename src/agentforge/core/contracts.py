"""The shared vocabulary of AgentForge, as data.

Every other module imports from here and nothing here imports from them. The
dataclasses carry no behavior beyond serialization, because their serialized
shape is a compatibility surface: ADR-0003 makes the Plan an interface that
every Role parses out of an Issue body someone may have filed a week ago.

Terms are defined in `CONTEXT.md`. This file is where they acquire a shape.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")

#: Bumped when a field is removed or its meaning changes. Added fields with
#: defaults do not require a bump, because an older Issue still parses.
PLAN_FORMAT_VERSION = 1

#: The Workflow an Issue runs when its plan block names none. Every Issue filed
#: before Workflows existed reads as `feature`, which is what those Runs did.
DEFAULT_WORKFLOW = "feature"


class ModelTier(StrEnum):
    """The class of model a Role runs on, named by intent. See ADR-0004.

    Nothing outside a Provider adapter maps these onto a model identifier.
    """

    DEEP = "deep"
    STANDARD = "standard"
    CHEAP = "cheap"


class Outcome(StrEnum):
    """How an Agent finished.

    ``ESCALATED`` is a result, not an exception: ADR-0003 requires a Role that
    finds the plan wrong to stop rather than improvise, and the runtime needs to
    tell that apart from a Role that simply crashed.
    """

    COMPLETED = "completed"
    ESCALATED = "escalated"
    FAILED = "failed"


class RunStatus(StrEnum):
    """Where a Run stands. Carried on the Issue as a label, per ADR-0002."""

    PLANNED = "planned"
    RUNNING = "running"
    ESCALATED = "escalated"
    AWAITING_SIGNOFF = "awaiting-signoff"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return f"agentforge:{self.value}"


#: Every label AgentForge may apply to an Issue, so a caller can reconcile them
#: without knowing the naming scheme.
RUN_LABELS = tuple(status.label for status in RunStatus)


@dataclass(frozen=True)
class Task:
    """A unit of software work stated by a human, in a human's words.

    A Task reaches the Orchestrator and stops there. ADR-0003 keeps the original
    phrasing away from downstream Roles, which execute the Plan instead.
    """

    statement: str


@dataclass(frozen=True)
class PlanStep:
    """One unit of the Plan, written to be executed without re-interpretation."""

    id: str
    intent: str
    files: tuple[str, ...] = ()
    acceptance: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "intent": self.intent,
            "files": list(self.files),
            "acceptance": self.acceptance,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlanStep:
        return cls(
            id=str(data["id"]),
            intent=str(data["intent"]),
            files=tuple(data.get("files") or ()),
            acceptance=str(data.get("acceptance") or ""),
        )


@dataclass(frozen=True)
class Plan:
    """What the Orchestrator decided, frozen once written. See ADR-0003.

    The fields are deliberately dull. A Plan that needs interpreting is a Plan
    that gets interpreted differently by each Role that reads it.
    """

    summary: str
    steps: tuple[PlanStep, ...] = ()
    constraints: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
            "constraints": list(self.constraints),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Plan:
        return cls(
            summary=str(data["summary"]),
            steps=tuple(PlanStep.from_dict(step) for step in data.get("steps") or ()),
            constraints=tuple(data.get("constraints") or ()),
        )


@dataclass(frozen=True)
class Role:
    """A named specialization with a fixed job, a model tier, and a prompt.

    A Role is a definition, not a running thing. `instructions` is the standing
    job description; the task-specific prompt is assembled per invocation.
    """

    name: str
    tier: ModelTier
    instructions: str = ""

    def at_tier(self, tier: ModelTier) -> Role:
        """The same Role with its tier overridden, per user request or config."""
        return replace(self, tier=tier)


@dataclass(frozen=True)
class Roster:
    """The ordered list of Roles an Issue requires.

    Serialization carries names and tiers only. Instructions are code, not
    contract, so they do not travel in an Issue body where they would be noise
    to the human reading it and stale to the Agent parsing it.
    """

    roles: tuple[Role, ...] = ()

    def __iter__(self):
        return iter(self.roles)

    def __len__(self) -> int:
        return len(self.roles)

    def names(self) -> tuple[str, ...]:
        return tuple(role.name for role in self.roles)

    def to_dict(self) -> list[dict]:
        return [{"role": role.name, "tier": str(role.tier)} for role in self.roles]

    @classmethod
    def from_dict(cls, data: list[dict], resolve) -> Roster:
        """Rebuild a Roster, resolving each name through `resolve(name) -> Role`."""
        roles = []
        for entry in data or ():
            role = resolve(str(entry["role"]))
            tier = entry.get("tier")
            roles.append(role.at_tier(ModelTier(tier)) if tier else role)
        return cls(tuple(roles))


@dataclass(frozen=True)
class ContextPack:
    """The bounded set of files, symbols, and conventions handed to an Agent.

    M1 passes a minimal pack so the Provider contract is exercised. Assembling a
    pack that actually saves tokens is M3.
    """

    files: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    conventions: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "files": list(self.files),
            "symbols": list(self.symbols),
            "conventions": list(self.conventions),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> ContextPack:
        data = data or {}
        return cls(
            files=tuple(data.get("files") or ()),
            symbols=tuple(data.get("symbols") or ()),
            conventions=tuple(data.get("conventions") or ()),
        )


@dataclass(frozen=True)
class AgentResult:
    """What one Agent invocation produced.

    `summary` is the line a human reads in the Run Log. For an escalation it is
    the reason the Plan could not be executed.
    """

    role: str
    tier: ModelTier
    outcome: Outcome
    summary: str
    detail: str = ""
    files_changed: tuple[str, ...] = ()

    #: The adapter's full text output. Transport only — it carries the
    #: Orchestrator's plan block out of a Provider invocation and gives a
    #: failure something to show. Deliberately absent from `to_dict`, because
    #: the Run Log is read by humans and re-parsed by later Runs, and a full
    #: transcript in every comment would ruin both.
    raw: str = field(default="", compare=False, repr=False)

    @property
    def escalated(self) -> bool:
        return self.outcome is Outcome.ESCALATED

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.COMPLETED

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "tier": str(self.tier),
            "outcome": str(self.outcome),
            "summary": self.summary,
            "detail": self.detail,
            "files_changed": list(self.files_changed),
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentResult:
        return cls(
            role=str(data["role"]),
            tier=ModelTier(data["tier"]),
            outcome=Outcome(data["outcome"]),
            summary=str(data.get("summary") or ""),
            detail=str(data.get("detail") or ""),
            files_changed=tuple(data.get("files_changed") or ()),
        )


def outstanding(
    items: Sequence[T], done: Sequence[str], name_of: Callable[[T], str]
) -> tuple[T, ...]:
    """Items not yet retired by a completed result, in order.

    Shared by the Roster and the Workflow because both ask the same question of
    the same Run Log. One completed result retires one entry, so a sequence
    naming the same Role twice resumes into the second occurrence rather than
    skipping both.
    """
    unclaimed = list(done)
    pending = []
    for item in items:
        name = name_of(item)
        if name in unclaimed:
            unclaimed.remove(name)
            continue
        pending.append(item)
    return tuple(pending)


@dataclass(frozen=True)
class RunState:
    """One execution of one Roster against one Issue.

    Every field is recoverable from the Issue alone — body for the Plan and the
    Roster, comments for the results, labels for the status. That is ADR-0002's
    claim, and `core.issues.run_state` is where it gets cashed.
    """

    issue: int
    plan: Plan
    roster: Roster
    context: ContextPack = ContextPack()
    results: tuple[AgentResult, ...] = ()
    status: RunStatus = RunStatus.PLANNED
    branch: str = ""
    pull_request: str = ""
    workflow: str = DEFAULT_WORKFLOW

    @property
    def done_roles(self) -> tuple[str, ...]:
        """Roles that finished the job, in Run Log order.

        An escalation is not done. A human corrects the plan block and runs
        `agentforge implement` again, and the Role that escalated is the one
        that has to run — so only completed results retire a Roster entry.
        """
        return tuple(result.role for result in self.results if result.ok)

    @property
    def remaining(self) -> tuple[Role, ...]:
        """Roles that have not yet completed, in Roster order."""
        return outstanding(tuple(self.roster), self.done_roles, lambda role: role.name)

    @property
    def escalation(self) -> AgentResult | None:
        return next((result for result in self.results if result.escalated), None)


@dataclass(frozen=True)
class PlanDocument:
    """The machine-readable half of an Issue body: everything an Agent needs.

    Kept separate from `RunState` because this is what gets written once and
    frozen, while a Run's results accumulate around it.
    """

    plan: Plan
    roster: Roster
    context: ContextPack = ContextPack()
    version: int = PLAN_FORMAT_VERSION
    notes: tuple[str, ...] = field(default=())
    workflow: str = DEFAULT_WORKFLOW

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "plan": self.plan.to_dict(),
            "roster": self.roster.to_dict(),
            "context": self.context.to_dict(),
            "notes": list(self.notes),
            "workflow": self.workflow,
        }

    @classmethod
    def from_dict(cls, data: dict, resolve) -> PlanDocument:
        return cls(
            plan=Plan.from_dict(data["plan"]),
            roster=Roster.from_dict(data.get("roster") or [], resolve),
            context=ContextPack.from_dict(data.get("context")),
            version=int(data.get("version", PLAN_FORMAT_VERSION)),
            notes=tuple(data.get("notes") or ()),
            workflow=str(data.get("workflow") or DEFAULT_WORKFLOW),
        )
