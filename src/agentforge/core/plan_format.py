"""The Issue body: prose for the human, one JSON block for the Agent.

ADR-0002 puts the handoff contract in a GitHub issue and ADR-0003 freezes it
once written. Those two together mean the body has to satisfy two readers at
once. A human judges the plan before any code is written, so the body is
Markdown. `agentforge implement` recovers the Plan a week later on a different
machine, so the body also carries a delimited JSON block and the parser reads
that and only that. No heuristics, no scraping of the prose.

The prose is generated from the same `PlanDocument` as the block, so the two
cannot disagree at filing time. If a human edits one half afterwards, the block
is the authority — it is what every Role parses.
"""

from __future__ import annotations

import json

from .contracts import (
    PLAN_FORMAT_VERSION,
    ContextPack,
    Plan,
    PlanDocument,
    Roster,
    Task,
)

PLAN_OPEN = "<!-- agentforge:plan -->"
PLAN_CLOSE = "<!-- /agentforge:plan -->"

RESULT_OPEN = "<!-- agentforge:result -->"
RESULT_CLOSE = "<!-- /agentforge:result -->"

#: A Gate's verdict travels in a marker of its own rather than in a result
#: block, because a Gate is not an Agent and `parse_run_log` must keep returning
#: Agent Results only. See ADR-0008.
GATE_OPEN = "<!-- agentforge:gate -->"
GATE_CLOSE = "<!-- /agentforge:gate -->"


class PlanFormatError(ValueError):
    """An Issue body does not carry a plan AgentForge can execute."""


def render_issue_title(task: Task) -> str:
    """A title a human recognizes in a list of thirty issues."""
    statement = " ".join(task.statement.split())
    if len(statement) <= 72:
        return statement
    return statement[:69].rstrip(" ,.;:-") + "..."


def render_issue_body(task: Task, document: PlanDocument) -> str:
    """The full Issue body: readable plan, then the frozen block."""
    parts = [
        "## Task",
        "",
        f"> {task.statement.strip()}",
        "",
        "## Plan",
        "",
        document.plan.summary.strip(),
        "",
    ]

    if document.plan.steps:
        parts += ["### Steps", ""]
        for index, step in enumerate(document.plan.steps, start=1):
            parts.append(f"{index}. **{step.id}** — {step.intent}")
            if step.files:
                parts.append(f"   - Files: {', '.join(f'`{f}`' for f in step.files)}")
            if step.acceptance:
                parts.append(f"   - Done when: {step.acceptance}")
        parts.append("")

    if document.plan.constraints:
        parts += ["### Constraints", ""]
        parts += [f"- {constraint}" for constraint in document.plan.constraints]
        parts.append("")

    parts += ["## Roster", "", "| Order | Role | Model Tier |", "| --- | --- | --- |"]
    for index, role in enumerate(document.roster, start=1):
        parts.append(f"| {index} | {role.name} | `{role.tier}` |")
    parts.append("")

    if document.context.files or document.context.conventions or document.context.symbols:
        parts += ["## Context Pack", ""]
        for label, values in (
            ("Files", document.context.files),
            ("Symbols", document.context.symbols),
            ("Conventions", document.context.conventions),
        ):
            if values:
                parts.append(f"- {label}: " + ", ".join(values))
        parts.append("")

    if document.notes:
        parts += ["## Notes", ""]
        parts += [f"- {note}" for note in document.notes]
        parts.append("")

    parts += [
        "---",
        "",
        PLAN_OPEN,
        "```json",
        json.dumps(document.to_dict(), indent=2, sort_keys=True),
        "```",
        PLAN_CLOSE,
        "",
        (
            "*Filed by AgentForge. The block above is the frozen execution contract "
            "(ADR-0003). Every Role parses it; edit it rather than the prose.*"
        ),
    ]
    return "\n".join(parts) + "\n"


def extract_plan_payload(text: str) -> dict:
    """The raw plan block, before Roles are resolved.

    The Orchestrator needs this: the model it just ran may have named a Role
    that does not exist yet, and dropping those is Roster selection's job rather
    than the parser's.
    """
    payload = _extract_block(text, PLAN_OPEN, PLAN_CLOSE)
    if payload is None:
        raise PlanFormatError(
            "no AgentForge plan block found; "
            "it was not written by `agentforge plan`, or the block was deleted"
        )

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PlanFormatError(f"plan block is not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or "plan" not in data:
        raise PlanFormatError("plan block carries no `plan`")
    return data


def parse_issue_body(body: str, resolve=None) -> PlanDocument:
    """Recover the frozen `PlanDocument` from an Issue body."""
    data = extract_plan_payload(body)

    version = int(data.get("version", PLAN_FORMAT_VERSION))
    if version > PLAN_FORMAT_VERSION:
        raise PlanFormatError(
            f"issue carries plan format v{version}; this AgentForge understands "
            f"v{PLAN_FORMAT_VERSION}. Upgrade AgentForge."
        )

    if resolve is None:
        from ..agents import resolve_role as resolve

    try:
        document = PlanDocument.from_dict(data, resolve)
    except KeyError as exc:
        raise PlanFormatError(f"plan block is missing {exc}") from exc

    if not document.roster:
        raise PlanFormatError("plan block carries an empty Roster; there is nothing to run")
    return document


def render_result_block(payload: dict) -> str:
    """The block a Role is asked to end its output with. Shared by every Provider."""
    return "\n".join(
        [RESULT_OPEN, "```json", json.dumps(payload, indent=2), "```", RESULT_CLOSE]
    )


def extract_result_block(text: str) -> dict | None:
    """Pull an Agent's structured verdict out of its free text, if it wrote one."""
    payload = _extract_block(text, RESULT_OPEN, RESULT_CLOSE)
    if payload is None:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def render_gate_block(payload: dict) -> str:
    """The block a Gate ends its Run Log entry with. Written by AgentForge only.

    A Role is asked for a result block; nobody is asked for one of these. The
    Gate that evaluated writes it, so the next Run can read back which Gate
    spoke and what it said.
    """
    return "\n".join(
        [GATE_OPEN, "```json", json.dumps(payload, indent=2), "```", GATE_CLOSE]
    )


def extract_gate_block(text: str) -> dict | None:
    """Pull a Gate's verdict out of a Run Log comment, if it carries one."""
    payload = _extract_block(text, GATE_OPEN, GATE_CLOSE)
    if payload is None:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _extract_block(text: str, open_marker: str, close_marker: str) -> str | None:
    """The JSON inside a delimited, fenced block. Last one wins.

    Last rather than first because an Agent that quotes the instructions it was
    given writes the example before it writes its answer.
    """
    start = text.rfind(open_marker)
    if start == -1:
        return None
    end = text.find(close_marker, start)
    inner = text[start + len(open_marker) : end if end != -1 else len(text)]

    fence = inner.find("```")
    if fence == -1:
        return inner.strip() or None
    inner = inner[fence + 3 :]
    inner = inner.removeprefix("json")
    closing = inner.find("```")
    if closing != -1:
        inner = inner[:closing]
    return inner.strip() or None


__all__ = [
    "GATE_CLOSE",
    "GATE_OPEN",
    "PLAN_CLOSE",
    "PLAN_OPEN",
    "RESULT_CLOSE",
    "RESULT_OPEN",
    "ContextPack",
    "Plan",
    "PlanDocument",
    "PlanFormatError",
    "Roster",
    "extract_gate_block",
    "extract_plan_payload",
    "extract_result_block",
    "parse_issue_body",
    "render_gate_block",
    "render_issue_body",
    "render_issue_title",
    "render_result_block",
]
