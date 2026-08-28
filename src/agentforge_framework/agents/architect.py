"""The Architect: decides the shape of a change it will not make.

It runs `deep` because a design error is the expensive kind. An Implementer's
mistake is caught by a Tester; a boundary drawn in the wrong place is caught
six months later by whoever has to work across it.

It is in none of the three shipped Workflows, and that is deliberate: most
Tasks do not need a design pass, and paying `deep` for one on every Run would
be the most expensive default in the project. The Orchestrator selects it for
Tasks that are design-heavy, and a project may name it in a Workflow of its own.

What it does not do is plan. The Plan is frozen before any Agent runs
(ADR-0003), and a Role that re-planned would be a second Orchestrator with none
of the human's context. The Architect decides how the frozen Plan should be
built, and escalates when it cannot be built as described.

Know this before putting it in a Workflow: its design reaches the Run Log and
not the next Role's prompt. Every Role is handed the frozen Plan and the Context
Pack, and nothing today folds an earlier Step's result into a later Step's
invocation. So an Architect Step informs the human reading the Issue, and an
Implementer only through them. Closing that gap is a change to what a Context
Pack carries rather than a change to this Role.
"""

from __future__ import annotations

from pathlib import Path

from ..context.prompt import render_context_block
from ..core.contracts import AgentResult, ContextPack, ModelTier, Plan, Role
from ..core.plan_format import RESULT_CLOSE, RESULT_OPEN
from .implementer import render_steps

INSTRUCTIONS = """\
You are the Architect in AgentForge. The Plan below is frozen and you are not \
rewriting it. Somebody decided what to build; you decide how it should be \
shaped.

Say where the seam goes: which module owns which decision, what the interface \
between them is, and what must not leak across it. Name the approach you \
rejected and why, because the next person to read this will otherwise rediscover \
it as a good idea.

Prefer the shape the repository already uses. A design that is better in the \
abstract and unlike everything around it costs every reader afterwards.

Write no code and change no files. What you produce is a decision, recorded on \
the Issue for the human who reads it before the work is built.

If the Plan cannot be built as described -- two steps require incompatible \
designs, or a step assumes a structure the repository does not have -- escalate \
rather than designing around it. That mismatch is the Orchestrator's to fix.\
"""

PROMPT = """\
{instructions}

## The frozen Plan

{summary}

### Steps

{steps}
{constraints}{context}
## Working directory

{cwd}

Read the modules the Plan names and the code they reach into, so the design you \
give back fits the repository rather than a description of it.

## Required output

End your reply with this block and nothing after it:

{result_open}
```json
{{
  "outcome": "completed",
  "summary": "one line: the shape you chose",
  "detail": "the design: the seam, the interfaces, what each module owns, and the approach you rejected",
  "files_changed": []
}}
```
{result_close}

Use `"outcome": "escalated"` when the Plan cannot be built as written. Name the \
step and the mismatch in `summary`.\
"""

#: The Architect runs `deep`: a boundary in the wrong place outlives the Run.
ARCHITECT = Role(name="architect", tier=ModelTier.DEEP, instructions=INSTRUCTIONS)


def build_prompt(
    plan: Plan,
    context: ContextPack,
    cwd: Path,
    role: Role = ARCHITECT,
) -> str:
    constraints = ""
    if plan.constraints:
        constraints = "\n### Constraints\n\n" + "\n".join(f"- {c}" for c in plan.constraints) + "\n"

    return PROMPT.format(
        instructions=role.instructions,
        summary=plan.summary.strip(),
        steps=render_steps(plan),
        constraints=constraints,
        context=render_context_block(context),
        cwd=cwd,
        result_open=RESULT_OPEN,
        result_close=RESULT_CLOSE,
    )


class Architect:
    """One Architect invocation through the shared Provider port.

    No denial path: designing is reading, so ADR-0007's shut gate costs it
    nothing.
    """

    def __init__(self, provider) -> None:
        self.provider = provider

    def run(
        self,
        *,
        plan: Plan,
        context: ContextPack,
        cwd: Path,
        role: Role = ARCHITECT,
        tier: ModelTier | None = None,
    ) -> AgentResult:
        return self.provider.invoke(
            role=role,
            prompt=build_prompt(plan, context, cwd, role),
            context=context,
            tier=tier or role.tier,
            cwd=cwd,
        )


__all__ = ["ARCHITECT", "INSTRUCTIONS", "Architect", "build_prompt"]
