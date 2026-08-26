"""The Tester: turns the frozen Plan's acceptance claims into executed tests."""

from __future__ import annotations

from pathlib import Path

from ..core.contracts import AgentResult, ContextPack, ModelTier, Outcome, Plan, Role
from ..core.plan_format import RESULT_CLOSE, RESULT_OPEN
from .implementer import render_steps

INSTRUCTIONS = """\
You are the Tester in AgentForge. Work from the frozen Plan, not from the human's \
original Task and not from a new interpretation of what the change should be.

Write test cases for the Plan's acceptance criteria, exercise relevant edge cases, \
and run the repository's test suite. Do not repair implementation flaws. Report \
them as findings and escalate so a human can decide what changes next. Never \
report completed unless the suite actually ran and passed.\
"""

PROMPT = """\
{instructions}

## The frozen Plan

{summary}

### Steps

{steps}

## Working directory

{cwd}

You are on the same branch the Implementer used. Commit nothing; AgentForge \
commits what the Roster leaves in the working tree.

## Required output

End your reply with this block and nothing after it:

{result_open}
```json
{{
  "outcome": "completed",
  "summary": "one line stating which suite ran and its result",
  "detail": "findings, or that no flaws were found",
  "files_changed": ["tests/path.py"]
}}
```
{result_close}

Use `"outcome": "escalated"` when the suite exposes an implementation flaw or \
cannot be run. Name each finding and the acceptance criterion it prevents you \
from verifying. Reading tests is not a substitute for running them.\
"""

#: The Tester runs `cheap`: the suite is the authority on pass or fail, and this
#: Role reports what it saw rather than deciding it. The trade is deliberate and
#: recorded in ADR-0004 — reasoning about an edge case nobody wrote a test for is
#: the part that gets worse here, and it is the part a human reads the findings
#: for anyway.
TESTER = Role(name="tester", tier=ModelTier.CHEAP, instructions=INSTRUCTIONS)


def build_prompt(
    plan: Plan,
    context: ContextPack,
    cwd: Path,
    role: Role = TESTER,
) -> str:
    return PROMPT.format(
        instructions=role.instructions,
        summary=plan.summary.strip(),
        steps=render_steps(plan),
        cwd=cwd,
        result_open=RESULT_OPEN,
        result_close=RESULT_CLOSE,
    )


class Tester:
    """One Tester invocation through the shared Provider port."""

    def __init__(self, provider) -> None:
        self.provider = provider

    def run(
        self,
        *,
        plan: Plan,
        context: ContextPack,
        cwd: Path,
        role: Role = TESTER,
        tier: ModelTier | None = None,
    ) -> AgentResult:
        tier = tier or role.tier
        if not getattr(self.provider, "allow_commands", False):
            return AgentResult(
                role=role.name,
                tier=tier,
                outcome=Outcome.ESCALATED,
                summary="command execution is denied, so the Tester cannot run the suite",
                detail=(
                    "The test-suite acceptance criteria were not verified. Re-run "
                    "`agentforge implement` with `--allow-commands`."
                ),
            )
        return self.provider.invoke(
            role=role,
            prompt=build_prompt(plan, context, cwd, role),
            context=context,
            tier=tier,
            cwd=cwd,
        )


__all__ = ["INSTRUCTIONS", "TESTER", "Tester", "build_prompt"]
