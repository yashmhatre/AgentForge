"""The Orchestrator: the only Role that reasons.

It turns a Task into an Issue. It resolves what it needs about the project,
chooses a Roster, and writes a Plan detailed enough to execute without further
interpretation. Everything downstream executes; nothing downstream re-plans
(ADR-0003), which is why this Role runs at the `deep` tier and why its output
quality is the ceiling on the system's.

Three of its behaviors are load-bearing and all three are tested:

- **The interview.** A half-formed Task is pushed on while the human is still
  at the keyboard, because ADR-0003 freezes the plan the moment it is filed and
  this is the last cheap moment to ask anything.
- **Roster selection** clamps whatever the model asked for down to the Roles
  that exist, and records what it dropped where a human will see it.
- **Ambiguity escalates.** A Task the Orchestrator cannot plan confidently stops
  here rather than being guessed at.

The interview is rounds of one-shot invocations rather than a conversation.
ADR-0001 gives the Provider port no session and no history, so each round is
handed the transcript so far and answers with the questions it still has. That
is also what makes the interview testable without a CLI installed: it is the
same port, invoked more than once.

Nothing interactive attached means no interview. A Run in CI has nobody to ask,
and a planner that blocked on input that will never arrive would hang rather
than degrade.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..core.contracts import (
    AgentResult,
    ContextPack,
    ModelTier,
    Outcome,
    Plan,
    PlanDocument,
    Role,
    Roster,
    Task,
)
from ..core.plan_format import (
    PLAN_CLOSE,
    PLAN_OPEN,
    RESULT_CLOSE,
    RESULT_OPEN,
    PlanFormatError,
    extract_plan_payload,
    extract_result_block,
)

#: How the Orchestrator reaches the human: one question in, one answer out.
#: `None` back ends the interview, and the Orchestrator plans with what it has —
#: a Task that was already clear should not cost a conversation.
Interviewer = Callable[[str], str | None]

#: Rounds of questions before the Orchestrator plans with what it has. The human
#: can stop sooner and the Orchestrator can declare itself ready sooner; this is
#: only the backstop against a model that always has one more question, and each
#: round it does not ask is a `deep` invocation nobody pays for.
MAX_ROUNDS = 3

#: Skills the interview adds for its own invocations. `grilling` conducts an
#: interview and has nothing to say to a planning pass with nobody in the room,
#: so it is declared per invocation rather than on the Role. Both travel the
#: Capability Tier path like every other skill (ADR-0005).
INTERVIEW_SKILLS = ("grilling", "domain-modeling")

INSTRUCTIONS = """\
You are the Orchestrator in AgentForge. You reason once, and everything after \
you executes without re-planning.

Your output is a frozen execution contract. The Roles that read it will not see \
the human's original wording, will not re-scope the work, and cannot ask you a \
question. A step that needs interpreting will be interpreted differently by \
each Role that reads it.

Write a plan that names files, states what changes in each, and says how each \
step is known to be done.\
"""

PROMPT = """\
{instructions}

## The Task

{task}
{interview}
## The repository

You are running in {cwd}. Read whatever you need in order to plan accurately -- \
existing structure, conventions, tests, and any CONTEXT.md, AGENTS.md, or \
docs/adr/ the project keeps. Do not change any files. This is a planning pass.

## Roles you may put in the Roster

{roles}

Order matters: the Roster runs in the order you write it.

## Required output

End your reply with these two blocks, in this order, and nothing after them.

Write acceptance criteria a Role can check by reading the repository. Agents \
run no commands unless a human opens that gate for a Run (ADR-0007), so a \
criterion phrased as "run the suite and paste the output" is one the executing \
Role will have to report it could not verify. If a step genuinely cannot be \
judged without running something, say so in the criterion itself rather than \
assuming it will be run.

First the plan:

{plan_open}
```json
{{
  "version": 1,
  "plan": {{
    "summary": "one paragraph a human can judge the work by",
    "steps": [
      {{
        "id": "s1",
        "intent": "what changes and why",
        "files": ["path/one.py"],
        "acceptance": "how this step is known to be done, checkable by reading the repository"
      }}
    ],
    "constraints": ["anything the executing Role must not do"]
  }},
  "roster": [{{"role": "implementer", "tier": "standard"}}],
  "context": {{
    "files": ["files a Role must read"],
    "symbols": ["functions or classes the work touches"],
    "conventions": ["project conventions the work must follow"]
  }}
}}
```
{plan_close}

Then your own verdict:

{result_open}
```json
{{"outcome": "completed", "summary": "one line describing the plan you wrote"}}
```
{result_close}

If the Task is too ambiguous to plan without guessing, write the verdict block \
with `"outcome": "escalated"` and a summary naming exactly what you need the \
human to decide. Omit the plan block in that case. The human is still at the \
keyboard right now; that will not be true when this plan is executed.\
"""

INTERVIEW = """\
You are the Orchestrator in AgentForge, interviewing the human who typed this \
Task before you write anything down.

Ask only about what would change the plan. A question whose answer you could \
find by reading the repository is a question you should not be asking, and one \
whose answer would not change a single step is worse.

## The Task

{task}

## The repository

You are running in {cwd}. Read whatever you need.

{glossary}

## What you have asked so far

{transcript}

## Required output

End your reply with this block and nothing after it:

{result_open}
```json
{{
  "outcome": "completed",
  "summary": "one line on what is still unclear, or that nothing is",
  "questions": ["one question per entry, in the order you want them asked"]
}}
```
{result_close}

An empty `questions` list means you have enough to plan. Say that as soon as it \
is true: this is the last cheap moment to ask, and it is also the human's time.\
"""

GLOSSARY_PRESENT = """\
This project keeps a glossary at `{path}`. Resolve the Task's terms against it \
rather than against ordinary usage, and when the human settles a term that is \
not in it, record the decision there in the format the file already uses. \
Somebody asking the same question next week should find the answer instead of \
you. Change nothing else in the repository -- this is not the work.\
"""

GLOSSARY_ABSENT = """\
This project keeps no glossary. Resolve the Task's terms against how the \
repository itself uses them, and do not start a glossary during an interview.\
"""

#: The Orchestrator runs at `deep`: it pays for all downstream reasoning once.
#: `domain-modeling` is standing equipment rather than interview-only: resolving
#: a Task's terms against the project's own vocabulary is what it does whether
#: or not anybody is in the room.
ORCHESTRATOR = Role(
    name="orchestrator",
    tier=ModelTier.DEEP,
    instructions=INSTRUCTIONS,
    skills=("domain-modeling",),
)


@dataclass(frozen=True)
class Exchange:
    """One question the Orchestrator asked and what came back."""

    question: str
    answer: str


def render_transcript(exchanges: Sequence[Exchange]) -> str:
    """The interview so far, as the next round is handed it."""
    if not exchanges:
        return "_Nothing yet. This is the first round._"
    return "\n\n".join(
        f"**You asked:** {e.question.strip()}\n**They answered:** {e.answer.strip()}"
        for e in exchanges
    )


def glossary_section(cwd: Path) -> str:
    """What to tell the interview about the project's vocabulary.

    The path rather than the text: the Agent is already in the repository and
    can read it, and a glossary inlined into every round is paid for in every
    round.
    """
    path = Path(cwd) / "CONTEXT.md"
    if path.is_file():
        return GLOSSARY_PRESENT.format(path="CONTEXT.md")
    return GLOSSARY_ABSENT


@dataclass(frozen=True)
class Planned:
    """What a planning pass produced: a document, or a reason there is none."""

    result: AgentResult
    document: PlanDocument | None = None
    #: The interview behind the plan, empty when there was nobody to interview.
    #: Kept so the caller can say what it cost and what was asked.
    interview: tuple[Exchange, ...] = ()

    @property
    def escalated(self) -> bool:
        return self.document is None


class Orchestrator:
    """Runs one planning pass and hands back something fileable."""

    def __init__(self, provider, tier: ModelTier | None = None) -> None:
        self.provider = provider
        self.tier = tier or ORCHESTRATOR.tier

    def interview(
        self, task: Task, cwd: Path, interviewer: Interviewer
    ) -> tuple[Exchange, ...]:
        """Ask until there is nothing worth asking, the human stops, or the cap.

        Each round is one invocation handed the whole transcript, because the
        port has no memory (ADR-0001). A round that comes back with no questions
        ends the interview: the Orchestrator saying it has enough is the outcome
        this is for, not a fallback.

        A round that fails to answer in the required shape also ends it. The
        planning pass is what has to work, and an interview that cannot be
        parsed is a reason to stop asking rather than a reason to stop.
        """
        role = replace(ORCHESTRATOR.at_tier(self.tier), skills=INTERVIEW_SKILLS)
        exchanges: list[Exchange] = []

        for _ in range(MAX_ROUNDS):
            result = self.provider.invoke(
                role=role,
                prompt=INTERVIEW.format(
                    task=task.statement.strip(),
                    cwd=cwd,
                    glossary=glossary_section(cwd),
                    transcript=render_transcript(exchanges),
                    result_open=RESULT_OPEN,
                    result_close=RESULT_CLOSE,
                ),
                context=ContextPack(),
                tier=self.tier,
                cwd=cwd,
            )

            questions = _questions(result)
            if not questions:
                break

            for question in questions:
                answer = interviewer(question)
                if answer is None:
                    # The human ended it. What they have already answered still
                    # counts; a plan is better for three answers than for none.
                    return tuple(exchanges)
                exchanges.append(Exchange(question=question, answer=answer))

        return tuple(exchanges)

    def build_prompt(
        self, task: Task, cwd: Path, exchanges: Sequence[Exchange] = ()
    ) -> str:
        from . import KNOWN_TIERS, ROLES

        available = "\n".join(
            f"- `{name}` (default tier `{ROLES[name].tier}`)"
            for name in sorted(ROLES)
            if name != ORCHESTRATOR.name
        )
        deferred = sorted(set(KNOWN_TIERS) - set(ROLES))
        if deferred:
            available += (
                "\n\nNot yet implemented, so do not put them in the Roster: "
                + ", ".join(f"`{name}`" for name in deferred)
                + "."
            )

        interview = ""
        if exchanges:
            interview = (
                "\n## What the human told you when you asked\n\n"
                + render_transcript(exchanges)
                + "\n\nThese answers are the Task now. The Roles downstream will not see "
                "them, so anything here that changes a step belongs in the step.\n"
            )

        return PROMPT.format(
            instructions=ORCHESTRATOR.instructions,
            task=task.statement.strip(),
            interview=interview,
            cwd=cwd,
            roles=available,
            plan_open=PLAN_OPEN,
            plan_close=PLAN_CLOSE,
            result_open=RESULT_OPEN,
            result_close=RESULT_CLOSE,
        )

    def plan(
        self, task: Task, cwd: Path, interviewer: Interviewer | None = None
    ) -> Planned:
        """Interview if there is anybody to interview, then plan once.

        No interviewer is the single-shot path, unchanged: a scheduled Run has
        nobody at the keyboard, and blocking on input that will never arrive is
        the one failure mode worse than planning from an underspecified Task.
        """
        exchanges = self.interview(task, cwd, interviewer) if interviewer else ()

        role = ORCHESTRATOR.at_tier(self.tier)
        result = self.provider.invoke(
            role=role,
            prompt=self.build_prompt(task, cwd, exchanges),
            context=ContextPack(),
            tier=self.tier,
            cwd=cwd,
        )

        if result.outcome is not Outcome.COMPLETED:
            return Planned(result=result, interview=exchanges)

        try:
            document = build_document(result.raw)
        except PlanFormatError as exc:
            return Planned(
                result=AgentResult(
                    role=role.name,
                    tier=self.tier,
                    outcome=Outcome.FAILED,
                    summary=f"the Orchestrator reported success but wrote no usable plan: {exc}",
                    detail=result.raw,
                    raw=result.raw,
                ),
                interview=exchanges,
            )

        return Planned(result=result, document=document, interview=exchanges)


def _questions(result: AgentResult) -> tuple[str, ...]:
    """The questions one interview round came back with.

    Read out of the result block rather than out of a marker of its own. An
    interview never reaches an Issue, so this is a prompt convention rather than
    a compatibility surface, and a fourth marker would have to be maintained
    like one.
    """
    if result.outcome is not Outcome.COMPLETED:
        return ()
    payload = extract_result_block(result.raw) or {}
    asked = payload.get("questions")
    if not isinstance(asked, list):
        return ()
    return tuple(str(q).strip() for q in asked if str(q).strip())


def build_document(text: str) -> PlanDocument:
    """Turn an Orchestrator's raw output into the document that gets filed."""
    payload = extract_plan_payload(text)
    plan = Plan.from_dict(payload["plan"])
    roster, notes = select_roster(payload.get("roster") or [])
    context = ContextPack.from_dict(payload.get("context"))
    return PlanDocument(plan=plan, roster=roster, context=context, notes=notes)


def select_roster(requested) -> tuple[Roster, tuple[str, ...]]:
    """Clamp a requested Roster to the Roles that exist.

    A model asked to plan a schema migration will reach for a Tester and a
    Security Role, and it is right to. Dropping them silently would leave a
    human reading the Issue believing work is scheduled that never runs, so
    every drop becomes a note in the Issue body.

    The Implementer is appended when nothing executable survives: the pipe is
    what M1 is proving, and an Issue nobody can implement proves nothing.
    """
    from . import IMPLEMENTER, KNOWN_TIERS, ROLES

    roles: list[Role] = []
    notes: list[str] = []
    seen: set[str] = set()

    for entry in requested:
        name = str(entry.get("role", "")).strip().lower() if isinstance(entry, dict) else str(entry)
        if not name or name == ORCHESTRATOR.name:
            continue

        if name not in ROLES:
            if name in KNOWN_TIERS:
                notes.append(
                    f"The Orchestrator asked for the `{name}` Role, which is not implemented "
                    "yet (M2). It was dropped from the Roster."
                )
            else:
                notes.append(f"Unknown Role `{name}` requested by the Orchestrator; dropped.")
            continue

        if name in seen:
            continue
        seen.add(name)

        role = ROLES[name]
        tier = entry.get("tier") if isinstance(entry, dict) else None
        roles.append(role.at_tier(ModelTier(tier)) if tier else role)

    if not roles:
        notes.append(
            "No implemented Role survived Roster selection, so the Implementer was added. "
            "M1 runs a single Role end to end."
        )
        roles.append(IMPLEMENTER)

    return Roster(tuple(roles)), tuple(notes)


__all__ = ["INSTRUCTIONS", "ORCHESTRATOR", "Orchestrator", "Planned", "build_document", "select_roster"]
