"""The Implementer: executes a Plan it did not write.

It is handed the Plan and the Context Pack and never the human's original
phrasing (ADR-0003). What it is not handed, it does not go looking for — that
repeated rediscovery is the cost the frozen plan exists to remove.

Its second job is to refuse. A plan written on Monday can be wrong by Thursday,
and an Agent that improvises a correction produces confident wrong work that
looks like success all the way to the pull request. So the prompt gives it an
escalation path and the runtime treats escalation as a result rather than an
error.
"""

from __future__ import annotations

from pathlib import Path

from ..core.contracts import AgentResult, ContextPack, ModelTier, Plan, Role
from ..core.plan_format import RESULT_CLOSE, RESULT_OPEN

INSTRUCTIONS = """\
You are the Implementer in AgentForge. Another Role planned this work and is \
not available to answer questions.

Execute the plan below and nothing else. Do not re-scope it, do not add \
improvements it did not ask for, and do not widen it because something nearby \
looks wrong.

If the plan does not match the repository -- a file it names is gone, a step \
assumes something untrue, two steps contradict each other -- stop and escalate. \
Do not guess at what was meant. A wrong plan caught now costs a comment; a \
wrong plan improvised around costs a review.\
"""

PROMPT = """\
{instructions}

## Plan

{summary}

### Steps

{steps}
{constraints}{context}
## Working directory

{cwd}

You are on a branch created for this work. Commit nothing; AgentForge commits \
what you leave in the working tree.
{execution}
## Required output

End your reply with this block and nothing after it:

{result_open}
```json
{{
  "outcome": "completed",
  "summary": "one line stating what you changed",
  "detail": "anything a reviewer needs to know",
  "files_changed": ["path/one.py"]
}}
```
{result_close}

Use `"outcome": "escalated"` instead if the plan cannot be executed as written. \
Put the specific mismatch in `summary` -- which step, and what the repository \
actually contains. Change no files when you escalate.\
"""

#: The Implementer runs at `standard`: it executes a plan it did not write.
IMPLEMENTER = Role(name="implementer", tier=ModelTier.STANDARD, instructions=INSTRUCTIONS)


def render_steps(plan: Plan) -> str:
    if not plan.steps:
        return "_The plan carries no steps. Escalate rather than inventing them._"

    lines = []
    for index, step in enumerate(plan.steps, start=1):
        lines.append(f"{index}. **{step.id}** — {step.intent}")
        if step.files:
            lines.append("   - Files: " + ", ".join(f"`{path}`" for path in step.files))
        if step.acceptance:
            lines.append(f"   - Done when: {step.acceptance}")
    return "\n".join(lines)


#: What a Role is told when ADR-0007's gate is shut. The posture itself lives in
#: the argument vector; this is only what to do on hitting it. Without it the
#: Agent does what the M1 acceptance run did -- trace the tests by hand and
#: report `completed`, which is the one failure a Run cannot detect.
DENIED_COMMANDS = """
## Commands

You cannot run commands in this Run. You may read and edit files, and nothing \
else. If a step's acceptance criterion asks you to run something, do not \
substitute reading for running: state in `detail` which criterion you could \
not verify, and escalate if that leaves the step unfinished. Reporting \
`completed` on a criterion you could not check is worse than stopping.
"""


def build_prompt(
    plan: Plan,
    context: ContextPack,
    cwd: Path,
    role: Role = IMPLEMENTER,
    allow_commands: bool = False,
) -> str:
    constraints = ""
    if plan.constraints:
        constraints = "\n### Constraints\n\n" + "\n".join(f"- {c}" for c in plan.constraints) + "\n"

    context_block = ""
    sections = [
        ("Read these files", context.files),
        ("These symbols are involved", context.symbols),
        ("Follow these conventions", context.conventions),
    ]
    rendered = [
        f"**{label}:** " + ", ".join(values) for label, values in sections if values
    ]
    if rendered:
        context_block = "\n## Context Pack\n\n" + "\n\n".join(rendered) + "\n"

    return PROMPT.format(
        instructions=role.instructions,
        summary=plan.summary.strip(),
        steps=render_steps(plan),
        constraints=constraints,
        context=context_block,
        cwd=cwd,
        execution="" if allow_commands else DENIED_COMMANDS,
        result_open=RESULT_OPEN,
        result_close=RESULT_CLOSE,
    )


class Implementer:
    """One Provider invocation against a frozen Plan."""

    def __init__(self, provider) -> None:
        self.provider = provider

    def run(
        self,
        *,
        plan: Plan,
        context: ContextPack,
        cwd: Path,
        role: Role = IMPLEMENTER,
        tier: ModelTier | None = None,
    ) -> AgentResult:
        tier = tier or role.tier
        allow_commands = getattr(self.provider, "allow_commands", False)
        return self.provider.invoke(
            role=role,
            prompt=build_prompt(plan, context, cwd, role, allow_commands=allow_commands),
            context=context,
            tier=tier,
            cwd=cwd,
        )


__all__ = ["IMPLEMENTER", "INSTRUCTIONS", "Implementer", "build_prompt", "render_steps"]
