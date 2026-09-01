"""The shared vocabulary of AgentForge, as data.

Every other module imports from here and nothing here imports from them. The
dataclasses carry no behavior beyond serialization, because their serialized
shape is a compatibility surface: ADR-0003 makes the Plan an interface that
every Role parses out of an Issue body someone may have filed a week ago.

Terms are defined in `CONTEXT.md`. This file is where they acquire a shape.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    # Only for the annotations on `Extractor.read` and `Validator.check`.
    # Imported under the guard so that the rule this module's own docstring
    # states stays true at runtime: everything imports from here and nothing
    # here imports from them.
    from ..context.extractors.base import Extraction
    from .gates import GateContext

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

    def tiers(self) -> dict[str, ModelTier]:
        """The tier each named Role runs at, as the Roster table promises it.

        Keyed by name rather than by position, matching how `align_to_workflow`
        collapses a requested Roster onto a Workflow. A Workflow naming one Role
        twice therefore runs both Steps at the one tier the table shows, which
        is what the table says and the only thing a reader could conclude from
        it. See ADR-0014.
        """
        return {role.name: role.tier for role in self.roles}

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
class Fragment:
    """Conventions a Plugin contributes to the prompts of the Roles it names.

    `roles` empty means every Role. A Fragment is text and nothing else: it is
    inlined into the Context Pack handed to a Step, so it reaches an Agent the
    same way whatever Provider is driving. See ADR-0016.
    """

    text: str
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Extractor:
    """A per-language reader a Plugin contributes, and the suffixes it claims.

    `read` has the signature every built-in extractor has — text in, `Extraction`
    out — because a Plugin's reader is not a second kind of thing. It is a pure
    function of one file's contents: it never opens a second file and never sees
    a path, which is what keeps it testable against a recorded fixture and what
    stops it from making the pack depend on the machine resolving it.

    Claiming a suffix a built-in already reads is the point rather than a
    conflict. A `.sql` file in a dbt project has dependencies a generic SQL read
    cannot see, and the Plugin that knows about dbt is the one that should
    answer for it. See ADR-0010 and ADR-0016.
    """

    suffixes: tuple[str, ...]
    read: Callable[[str], Extraction]


@dataclass(frozen=True)
class Validator:
    """A Gate kind a Plugin contributes, and the predicate that evaluates it.

    `check` has the signature every shipped Gate has — a `GateContext` in, a
    `GateEntry` out — because a Plugin's Gate is not a second kind of thing. It
    is handed the Command Runner and the working tree like any other, so a
    validator that shells out to a parser has what it needs, and it returns
    cleared, blocked, or errored with the same meanings: blocked suspends a Run
    that can still clear, errored halts one that cannot.

    A validator that cannot evaluate returns an errored `GateEntry` rather than
    raising. A Plugin degrades a Run and never ends it, which is the bargain
    `core.registry` makes at activation and `context.extractors` makes when a
    reader raises.

    `kind` is the name a Workflow's YAML writes. It cannot be one of the shipped
    kinds: `human`, `tests`, and `security` mean what the shipped Workflows say
    they mean, and a Plugin that could redefine `human` could make a human Gate
    stop stopping. See ADR-0018.
    """

    kind: str
    check: Callable[[GateContext], GateEntry]


@dataclass(frozen=True)
class Plugin:
    """A bundle of domain knowledge for one technology, as data.

    No behaviour: a Plugin declares what it answers for and what it contributes,
    and `core.registry` does the deciding. Every contribution field is optional,
    so a Plugin carrying only Fragments is legal and is what the `python` Plugin
    is, while one carrying no Fragment at all is equally legal and is what `sql`
    is — it reads files and contributes a Gate kind, and says nothing to a Role.

    `suffixes`, `root_markers`, and `imports` are the three ways a Plugin is
    detected. A suffix answers for the blast radius the frozen Plan names; a
    root marker answers for the repository itself — a `pyproject.toml` says
    Python whatever one Plan happens to touch; an import answers for what a file
    in that blast radius actually uses, because `.py` says nothing about whether
    a module is a Spark job. All three are declarations rather than predicates,
    so a Plugin stays data and `agentforge init` can write down what detection
    already computed. See ADR-0017.

    Detection and contribution are separate on purpose. `suffixes` says when
    this Plugin is active; an `Extractor`'s own suffixes say what it reads once
    it is. The `sql` Plugin activates on `.sql` and a `dbt_project.yml`, and
    then reads the schema YAML beside the models — which it would be wrong to
    activate for on its own.
    """

    name: str
    suffixes: tuple[str, ...] = ()
    root_markers: tuple[str, ...] = ()
    #: Top-level module names whose import activates this Plugin — `pyspark`
    #: matches both `import pyspark` and `from pyspark.sql import functions`.
    #: Read out of the Python files the blast radius names, which is the only
    #: place an import means anything.
    imports: tuple[str, ...] = ()
    fragments: tuple[Fragment, ...] = ()
    extractors: tuple[Extractor, ...] = ()
    validators: tuple[Validator, ...] = ()


@dataclass(frozen=True)
class ContextPack:
    """The bounded set of files, symbols, and conventions handed to an Agent.

    Two things fill one in. The Orchestrator declares what it believes the work
    touches, and that travels in the Issue body; `context.resolver` resolves
    that declaration against the frozen Plan and the repository at the start of
    a Run, which is the pack an Agent is actually handed. See ADR-0010.

    A pack is a head start and never a boundary. A Role that needs a file the
    pack does not name reads it, so a resolver mistake costs tokens rather than
    correctness.
    """

    files: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    conventions: tuple[str, ...] = ()
    #: What those files reach for outside themselves — a module's imports, a
    #: query's source tables. Written by the resolver rather than declared by
    #: the Orchestrator: it is read out of the files, and a Role reads it to
    #: find out what its change can break.
    references: tuple[str, ...] = ()
    #: What the active Plugins contribute to this Step's Role, folded in by the
    #: runtime just before the Agent is invoked. Kept apart from `conventions`
    #: because the two have different authors and a reader should be able to
    #: tell them apart: `conventions` is the Orchestrator's judgement about this
    #: Task, and this is what the repository's technology is held to regardless
    #: of Task. Per Role, so it is absent from the Run-level pack the Run Log
    #: records, and absent from `to_dict` because it never travels in an Issue
    #: body. See ADR-0016.
    fragments: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        """Whether the pack carries anything at all.

        The runtime asks this to tell a resolved pack from the empty one a Run
        started with, and every call site spelling out the four fields is the
        same question asked four ways — one of which gets forgotten the next
        time a field is added.
        """
        return bool(
            self.files
            or self.symbols
            or self.conventions
            or self.references
            or self.fragments
        )

    def to_dict(self) -> dict:
        """The pack as it travels in an Issue body.

        `fragments` is deliberately absent. It is resolved per Step from the
        Plugins active for the repository the Run is in, so writing it into the
        Issue would freeze one machine's answer into the stable surface
        (ADR-0011) and hand the next Run conventions it may not be held to.
        """
        return {
            "files": list(self.files),
            "symbols": list(self.symbols),
            "conventions": list(self.conventions),
            "references": list(self.references),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> ContextPack:
        data = data or {}
        return cls(
            files=tuple(data.get("files") or ()),
            symbols=tuple(data.get("symbols") or ()),
            conventions=tuple(data.get("conventions") or ()),
            references=tuple(data.get("references") or ()),
        )


@dataclass(frozen=True)
class Usage:
    """What one Provider invocation consumed, in whatever unit its CLI reports.

    Every figure is optional and none of them defaults to zero, because the
    Providers disagree about what they will tell you: `claude` reports dollars
    and a token split, `codex` prints one token count and no price, and a CLI
    may report nothing at all. A zero would make all three look like a free
    invocation, so absent stays absent and a total can say how much of itself
    is missing. See ADR-0009.

    `provider` names the CLI the figures came from, so a Run Log line can say
    why a dollar figure is missing rather than leaving a blank where one would
    have been.
    """

    provider: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: One figure for the whole invocation, for a CLI that reports no split.
    total_tokens: int | None = None
    cost_usd: float | None = None

    @property
    def tokens(self) -> int | None:
        """Every token this invocation used, however the CLI broke them down."""
        if self.total_tokens is not None:
            return self.total_tokens
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    @property
    def reported(self) -> bool:
        """Whether the Provider said anything at all about what this cost."""
        return self.cost_usd is not None or self.tokens is not None

    @classmethod
    def combine(cls, usages: Iterable[Usage | None]) -> Usage:
        """Add up what a Run spent, keeping absent figures absent.

        The split is dropped: a Run whose Steps report a mix of split and
        unsplit counts has no honest input/output total, and one token figure
        that is true beats two that are assembled.
        """
        cost: float | None = None
        tokens: int | None = None
        providers = set()

        for usage in usages:
            if usage is None:
                continue
            if usage.provider:
                providers.add(usage.provider)
            if usage.cost_usd is not None:
                cost = (cost or 0.0) + usage.cost_usd
            if usage.tokens is not None:
                tokens = (tokens or 0) + usage.tokens

        return cls(
            provider=providers.pop() if len(providers) == 1 else "",
            total_tokens=tokens,
            cost_usd=cost,
        )

    def to_dict(self) -> dict:
        """Only what was reported. An absent key is the absent figure."""
        payload: dict = {}
        if self.provider:
            payload["provider"] = self.provider
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = int(value)
        if self.cost_usd is not None:
            payload["cost_usd"] = float(self.cost_usd)
        return payload

    @classmethod
    def from_dict(cls, data: dict | None) -> Usage | None:
        """A usage record, or `None` where a Run Log entry carries none."""
        if not data:
            return None
        return cls(
            provider=str(data.get("provider") or ""),
            input_tokens=_number(data.get("input_tokens"), int),
            output_tokens=_number(data.get("output_tokens"), int),
            total_tokens=_number(data.get("total_tokens"), int),
            cost_usd=_number(data.get("cost_usd"), float),
        )


def _number(value: object, cast):
    """A figure a Run Log carried, or `None` if it carried nothing usable.

    A human edits Issue bodies, and a cost line that crashed a resumed Run
    would make the measurement more expensive than the thing it measures.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Finding:
    """One thing an Agent found and did not fix.

    Three fields rather than a sentence, because "potential injection risk" as
    the whole message is what this shape exists to prevent: a human needs to
    know where to look, what could go wrong there, and why that matters in this
    repository rather than in general.

    A finding is not an Escalation. The plan was executable and was executed;
    this is something noticed on the way, and what a Gate does about it is the
    Gate's business.
    """

    location: str
    risk: str
    rationale: str = ""

    def to_dict(self) -> dict:
        return {"location": self.location, "risk": self.risk, "rationale": self.rationale}

    @classmethod
    def from_dict(cls, data: dict) -> Finding:
        return cls(
            location=str(data.get("location") or ""),
            risk=str(data.get("risk") or ""),
            rationale=str(data.get("rationale") or ""),
        )

    @classmethod
    def coerce(cls, value: object) -> Finding:
        """A finding as a Role reported it, however it reported it.

        A model asked for three fields sometimes answers with a sentence.
        Dropping those would clear a Gate that should have blocked, so a bare
        string becomes a finding with no location rather than no finding at all.
        """
        if isinstance(value, dict):
            return cls.from_dict(value)
        return cls(location="", risk=str(value).strip())


@dataclass(frozen=True)
class AgentResult:
    """What one Agent invocation produced.

    `summary` is the line a human reads in the Run Log. For an escalation it is
    the reason the Plan could not be executed.

    `findings` is what the Agent noticed and left for somebody else. Empty means
    it looked and found nothing, which is why a Role that could not look at all
    escalates instead: a Gate reading this cannot tell the two apart otherwise.

    `usage` is what this invocation consumed, and it hangs here rather than on
    the Run because that is the granularity a tiering decision is made at: a
    Run's total says the Run was expensive, and only a per-Role figure says
    which Role to move.
    """

    role: str
    tier: ModelTier
    outcome: Outcome
    summary: str
    detail: str = ""
    files_changed: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    usage: Usage | None = None

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
        payload = {
            "role": self.role,
            "tier": str(self.tier),
            "outcome": str(self.outcome),
            "summary": self.summary,
            "detail": self.detail,
            "files_changed": list(self.files_changed),
        }
        # Written only when there are any: every Implementer result in the Run
        # Log would otherwise carry an empty list saying it found nothing, which
        # is not something the Implementer was asked.
        if self.findings:
            payload["findings"] = [finding.to_dict() for finding in self.findings]
        # Same rule, for the same reason: a Provider that reported nothing
        # writes no key, so a later reader can tell silence from a free Run.
        if self.usage is not None and self.usage.to_dict():
            payload["usage"] = self.usage.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> AgentResult:
        return cls(
            role=str(data["role"]),
            tier=ModelTier(data["tier"]),
            outcome=Outcome(data["outcome"]),
            summary=str(data.get("summary") or ""),
            detail=str(data.get("detail") or ""),
            files_changed=tuple(data.get("files_changed") or ()),
            findings=tuple(Finding.coerce(item) for item in data.get("findings") or ()),
            usage=Usage.from_dict(data.get("usage")),
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
