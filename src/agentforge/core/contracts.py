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


class GateVerdict(StrEnum):
    """What a Gate says when it is asked.

    Deliberately not an ``Outcome``. An Outcome is a Role's verdict on its own
    work; a Gate is not an Agent and judges somebody else's. ``ERRORED`` is the
    Gate that could not decide — which is not the same as deciding no, because a
    Gate with nothing to clear cannot be cleared by waiting.
    """

    CLEARED = "cleared"
    BLOCKED = "blocked"
    ERRORED = "errored"


@dataclass(frozen=True)
class GateEntry:
    """One Gate's verdict, as the Run Log carries it. See ADR-0008.

    `step` is the 1-based position of the Step this Gate follows. Unlike a
    result's position — which `current_step` derives — a Gate's is the only thing
    that says which Gate spoke, so it is recorded rather than re-derived.

    `invalidates` names the Role whose output this verdict was drawn from, and is
    empty when the Gate judged nobody's: a Security Gate reads the Security
    Agent's findings, while a human Gate reads a human. A blocked verdict naming
    a Role un-retires that Role's Step, which is what stops the next Run from
    re-reading a verdict the human has already acted on.
    """

    kind: str
    verdict: GateVerdict
    step: int = 0
    invalidates: str = ""
    summary: str = ""

    @property
    def blocked(self) -> bool:
        return self.verdict is GateVerdict.BLOCKED

    @property
    def errored(self) -> bool:
        return self.verdict is GateVerdict.ERRORED

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "verdict": str(self.verdict),
            "step": self.step,
            "invalidates": self.invalidates,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GateEntry:
        return cls(
            kind=str(data["kind"]),
            verdict=GateVerdict(data["verdict"]),
            step=int(data.get("step") or 0),
            invalidates=str(data.get("invalidates") or ""),
            summary=str(data.get("summary") or ""),
        )


class RunStatus(StrEnum):
    """Where a Run stands. Carried on the Issue as a label, per ADR-0002.

    Three of these are ways for a Run to stop, and CONTEXT.md keeps them apart
    because the reader's next move differs in each. ``SUSPENDED`` is a Gate the
    Run can still clear, and nobody need do anything. ``HALTED`` is what an
    Escalation or an errored Gate produces: the completed steps stand and a
    human decides. ``FAILED`` is a Run AgentForge could not finish at all.

    ``HALTED`` was ``ESCALATED`` until the glossary settled that an Escalation
    is the verdict a Role reports and Halted the state it produces. The old
    label is still read; see ``LEGACY_LABELS``.
    """

    PLANNED = "planned"
    RUNNING = "running"
    SUSPENDED = "suspended"
    HALTED = "halted"
    AWAITING_SIGNOFF = "awaiting-signoff"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return f"agentforge:{self.value}"


#: Labels an earlier AgentForge applied, and what they mean now. Read, never
#: written: issues labelled `agentforge:escalated` were open when the rename
#: landed, and a Run that cannot read its own state back is the one thing
#: ADR-0002 does not survive.
LEGACY_LABELS: dict[str, RunStatus] = {"agentforge:escalated": RunStatus.HALTED}

#: Every status label AgentForge may find on an Issue, so a caller can reconcile
#: them without knowing the naming scheme. Retired labels are in here so that a
#: Run clears them rather than leaving an Issue wearing two answers.
RUN_LABELS = tuple(status.label for status in RunStatus) + tuple(LEGACY_LABELS)


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
    """A named specialization with a fixed job, model tier, prompt, and skills.

    A Role is a definition, not a running thing. `instructions` is the standing
    job description; the task-specific prompt is assembled per invocation.
    """

    name: str
    tier: ModelTier
    instructions: str = ""
    skills: tuple[str, ...] = ()

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


def retirement(
    items: Sequence[T], done: Sequence[str], name_of: Callable[[T], str]
) -> tuple[bool, ...]:
    """Which items a completed result has already retired, in order.

    One completed result retires one entry, so a sequence naming the same Role
    twice resumes into the second occurrence rather than skipping both.

    This is the rule; `outstanding` is the view of it the Roster and the Workflow
    ask for. The runtime asks for the flags instead, because it walks every Step
    — a Step behind the Run still has a Gate the Run has to pass through.
    """
    unclaimed = list(done)
    flags = []
    for item in items:
        name = name_of(item)
        retired = name in unclaimed
        if retired:
            unclaimed.remove(name)
        flags.append(retired)
    return tuple(flags)


def outstanding(
    items: Sequence[T], done: Sequence[str], name_of: Callable[[T], str]
) -> tuple[T, ...]:
    """Items not yet retired by a completed result, in order.

    Shared by the Roster and the Workflow because both ask the same question of
    the same Run Log.
    """
    flags = retirement(items, done, name_of)
    return tuple(item for item, retired in zip(items, flags, strict=True) if not retired)


def _drop_last(names: list[str], name: str) -> None:
    """Remove the most recent occurrence, which is the one a Gate just judged."""
    for index in range(len(names) - 1, -1, -1):
        if names[index] == name:
            del names[index]
            return


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
    gates: tuple[GateEntry, ...] = ()
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

        A Gate that blocked on a Role's output un-retires it again: the verdict
        was drawn from work a human has since changed, and a Run that resumed
        past it would re-read the same stale finding forever. The Gate entries
        are counted rather than interleaved with the results, because a Gate's
        verdict always trails the Step it judged — the last matching entry is
        the one it was drawn from, and no cursor is needed to say so.
        """
        done = [result.role for result in self.results if result.ok]
        for entry in self.gates:
            if entry.blocked and entry.invalidates in done:
                _drop_last(done, entry.invalidates)
        return tuple(done)

    @property
    def remaining(self) -> tuple[Role, ...]:
        """Roles that have not yet completed, in Roster order."""
        return outstanding(tuple(self.roster), self.done_roles, lambda role: role.name)

    @property
    def current_step(self) -> int:
        """The 1-based position of the Step the Run is on. Derived, never stored.

        A cursor kept alongside the Run Log would be a second answer to a
        question the Run Log already answers, and the two would disagree the
        first time a human edited the Issue — so the count of retired Steps is
        the only answer there is. A Role that escalated or failed did not retire
        its Step, which is why a halted Run is still standing on the Step that
        halted it, and why re-running resumes there.
        """
        return len(self.done_roles) + 1

    @property
    def escalation(self) -> AgentResult | None:
        """The Escalation that stopped this Run, if one did.

        The last entry rather than the first: the Run Log keeps every attempt,
        and a Role that escalated, had its plan block corrected, and then
        completed did not stop anything.
        """
        last = self.results[-1] if self.results else None
        return last if last is not None and last.escalated else None


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
