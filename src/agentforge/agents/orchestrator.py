"""The Orchestrator: the only Role that reasons.

It turns a Task into an Issue. It resolves what it needs about the project,
chooses a Roster, and writes a Plan detailed enough to execute without further
interpretation. Everything downstream executes; nothing downstream re-plans
(ADR-0003), which is why this Role runs at the `deep` tier and why its output
quality is the ceiling on the system's.

Two of its behaviors are load-bearing and both are tested:

- **Roster selection** clamps whatever the model asked for down to the Roles
  that exist, and records what it dropped where a human will see it.
- **Ambiguity escalates.** A Task the Orchestrator cannot plan confidently stops
  here, while the human who typed it is still at the keyboard. ADR-0003 freezes
  the plan the moment it is filed, so this is the last cheap moment to ask.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    PlanFormatError,
    extract_plan_payload,
)

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

#: The Orchestrator runs at `deep`: it pays for all downstream reasoning once.
ORCHESTRATOR = Role(name="orchestrator", tier=ModelTier.DEEP, instructions=INSTRUCTIONS)


@dataclass(frozen=True)
class Planned:
    """What a planning pass produced: a document, or a reason there is none."""

    result: AgentResult
    document: PlanDocument | None = None

    @property
    def escalated(self) -> bool:
        return self.document is None


class Orchestrator:
    """Runs one planning pass and hands back something fileable."""

    def __init__(self, provider, tier: ModelTier | None = None) -> None:
        self.provider = provider
        self.tier = tier or ORCHESTRATOR.tier

    def build_prompt(self, task: Task, cwd: Path) -> str:
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

        from ..core.plan_format import RESULT_CLOSE, RESULT_OPEN

        return PROMPT.format(
            instructions=ORCHESTRATOR.instructions,
            task=task.statement.strip(),
            cwd=cwd,
            roles=available,
            plan_open=PLAN_OPEN,
            plan_close=PLAN_CLOSE,
            result_open=RESULT_OPEN,
            result_close=RESULT_CLOSE,
        )

    def plan(self, task: Task, cwd: Path) -> Planned:
        role = ORCHESTRATOR.at_tier(self.tier)
        result = self.provider.invoke(
            role=role,
            prompt=self.build_prompt(task, cwd),
            context=ContextPack(),
            tier=self.tier,
            cwd=cwd,
        )

        if result.outcome is not Outcome.COMPLETED:
            return Planned(result=result)

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
                )
            )

        return Planned(result=result, document=document)


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
