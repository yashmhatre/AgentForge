"""The Security Role: audits the change, and fixes nothing.

It runs at `deep` per ADR-0004, which is the one tier decision in the project
that is not about cost. A missed finding is silent — nobody reviews the audit
that did not happen — so the Role that looks for what nobody asked about is the
Role that cannot be run cheaply.

Its output is a list of Findings rather than prose, because the Gate downstream
of it has to tell "audited, nothing found" from "did not audit", and a paragraph
cannot be asked that question. What blocks the Run is the presence of a Finding
and never the wording of one.
"""

from __future__ import annotations

from pathlib import Path

from ..context.prompt import render_context_block
from ..core.contracts import AgentResult, ContextPack, ModelTier, Plan, Role
from ..core.plan_format import RESULT_CLOSE, RESULT_OPEN
from .implementer import render_steps

INSTRUCTIONS = """\
You are the Security Role in AgentBastion. Another Role wrote the code you are \
reading and is not available to answer questions.

Audit the change described by the frozen Plan against production standards: \
injected input reaching a query or a shell, credentials and tokens in source or \
in logs, data crossing a boundary it should not, permissions widened, and \
anything a regulated shop would refuse at merge time.

Change nothing. You are not the Role that fixes what you find -- a human decides \
what happens next, and an audit that edits the code it is auditing cannot be \
trusted about either.

Report every finding with the file and line to look at, what could go wrong \
there, and why that matters in this repository. "Potential injection risk" as \
the whole message is not a finding; it is a category, and it sends a human \
looking for something you have already found.\
"""

PROMPT = """\
{instructions}

## The frozen Plan

{summary}

### Steps

{steps}
{context}
## Working directory

{cwd}

You are on the branch the change was made on. Read the files the Plan names and \
the code they reach into. Commit nothing and edit nothing.

## Required output

End your reply with this block and nothing after it:

{result_open}
```json
{{
  "outcome": "completed",
  "summary": "one line: how many findings, and the most serious of them",
  "detail": "anything a human needs that did not belong in a finding",
  "findings": [
    {{
      "location": "src/loader.py:42",
      "risk": "what could go wrong here",
      "rationale": "why that matters in this repository"
    }}
  ]
}}
```
{result_close}

A clean audit is `"outcome": "completed"` with `"findings": []`. Report that \
when you looked and found nothing, and never when you could not look -- a Gate \
downstream reads the difference, and an empty list means the change was audited.

Use `"outcome": "escalated"` only when the Plan does not match the repository, \
or when you could not audit the change at all. Say which in `summary`.\
"""

#: Security runs `deep`: a finding nobody makes is a finding nobody reviews.
SECURITY = Role(name="security", tier=ModelTier.DEEP, instructions=INSTRUCTIONS)


def build_prompt(
    plan: Plan,
    context: ContextPack,
    cwd: Path,
    role: Role = SECURITY,
) -> str:
    return PROMPT.format(
        instructions=role.instructions,
        summary=plan.summary.strip(),
        steps=render_steps(plan),
        context=render_context_block(context),
        cwd=cwd,
        result_open=RESULT_OPEN,
        result_close=RESULT_CLOSE,
    )


class Security:
    """One Security invocation through the shared Provider port.

    No denial path, unlike the Tester. Auditing is reading, and ADR-0007 leaves
    reading open — a Run with commands shut still gets its audit.
    """

    def __init__(self, provider) -> None:
        self.provider = provider

    def run(
        self,
        *,
        plan: Plan,
        context: ContextPack,
        cwd: Path,
        role: Role = SECURITY,
        tier: ModelTier | None = None,
    ) -> AgentResult:
        return self.provider.invoke(
            role=role,
            prompt=build_prompt(plan, context, cwd, role),
            context=context,
            tier=tier or role.tier,
            cwd=cwd,
        )


__all__ = ["INSTRUCTIONS", "SECURITY", "Security", "build_prompt"]
