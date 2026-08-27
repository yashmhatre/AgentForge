"""The Issue body has to satisfy a human and a parser at the same time.

The characterization test at the bottom is the important one. A recorded body
sits in `fixtures/`, and any change to the format makes it fail — which is the
only warning anyone gets that Issues already filed will no longer parse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentbastion.agents import resolve_role
from agentbastion.agents.implementer import IMPLEMENTER
from agentbastion.core.contracts import (
    ContextPack,
    ModelTier,
    PlanDocument,
    Roster,
    Task,
)
from agentbastion.core.plan_format import (
    PLAN_CLOSE,
    PLAN_OPEN,
    PlanFormatError,
    extract_gate_block,
    extract_result_block,
    parse_issue_body,
    render_gate_block,
    render_issue_body,
    render_issue_title,
    render_result_block,
)

from .test_contracts import a_plan

FIXTURES = Path(__file__).parent / "fixtures"


def a_document() -> PlanDocument:
    return PlanDocument(
        plan=a_plan(),
        roster=Roster((IMPLEMENTER,)),
        context=ContextPack(
            files=("src/loader.py", "tests/test_loader.py"),
            symbols=("load",),
            conventions=("ruff, line length 100",),
        ),
        notes=("The `tester` Role was requested but is not implemented yet (M2).",),
    )


def test_a_body_this_module_wrote_is_a_body_this_module_can_read():
    document = a_document()

    restored = parse_issue_body(render_issue_body(Task("add a retry"), document), resolve_role)

    assert restored == document


def test_the_prose_is_readable_without_the_json():
    body = render_issue_body(Task("add a retry to the loader"), a_document())

    assert "> add a retry to the loader" in body
    assert "Add a retry to the loader." in body
    assert "**s1** — Wrap the fetch in a bounded retry" in body
    assert "| 1 | implementer | `standard` |" in body
    assert "Do not change the public signature of `load`." in body


def test_the_task_statement_is_prose_only_and_never_reaches_a_role():
    """ADR-0003: downstream Roles execute the plan and are not given the
    human's original phrasing."""
    body = render_issue_body(Task("add a retry to the loader"), a_document())

    block = body.split(PLAN_OPEN)[1].split(PLAN_CLOSE)[0]

    assert "add a retry to the loader" not in block


def test_a_body_with_no_plan_block_is_refused_by_name():
    with pytest.raises(PlanFormatError, match="no AgentBastion plan block"):
        parse_issue_body("Someone filed this by hand.", resolve_role)


def test_a_corrupted_block_says_so_rather_than_half_parsing():
    body = f"{PLAN_OPEN}\n```json\n{{not json\n```\n{PLAN_CLOSE}"

    with pytest.raises(PlanFormatError, match="not valid JSON"):
        parse_issue_body(body, resolve_role)


def test_an_empty_roster_is_refused_because_there_is_nothing_to_run():
    payload = {"version": 1, "plan": a_plan().to_dict(), "roster": []}
    body = f"{PLAN_OPEN}\n```json\n{json.dumps(payload)}\n```\n{PLAN_CLOSE}"

    with pytest.raises(PlanFormatError, match="empty Roster"):
        parse_issue_body(body, resolve_role)


def test_a_newer_format_tells_the_user_to_upgrade_rather_than_misreading_it():
    payload = {"version": 99, "plan": a_plan().to_dict(), "roster": [{"role": "implementer"}]}
    body = f"{PLAN_OPEN}\n```json\n{json.dumps(payload)}\n```\n{PLAN_CLOSE}"

    with pytest.raises(PlanFormatError, match="Upgrade AgentBastion"):
        parse_issue_body(body, resolve_role)


def test_the_last_block_wins_when_an_agent_quotes_its_own_instructions():
    """A model told to emit a block often echoes the example first."""
    example = render_result_block({"outcome": "completed", "summary": "EXAMPLE"})
    actual = render_result_block({"outcome": "escalated", "summary": "REAL"})

    payload = extract_result_block(f"Here is the shape I was given:\n{example}\n\n{actual}")

    assert payload["summary"] == "REAL"


def test_a_reply_with_no_result_block_reads_as_absent_not_as_success():
    assert extract_result_block("I had a go at it and it seems fine.") is None


# --- a Gate's verdict is its own block, not an Agent Result ------------------


def test_a_gate_block_round_trips():
    payload = {"kind": "human", "verdict": "blocked", "step": 1, "invalidates": ""}

    assert extract_gate_block(render_gate_block(payload)) == payload


def test_a_block_survives_a_payload_that_carries_fences_of_its_own():
    """The test-suite Gate quotes what the suite printed, and a suite in a
    repository like this one prints fenced blocks. A closing fence found inside
    the JSON truncates it, and the entry is dropped from the Run Log by a Run
    that was counting on reading it back."""
    payload = {
        "kind": "tests",
        "verdict": "blocked",
        "step": 2,
        "invalidates": "",
        "summary": "`pytest` failed.\n\n````text\nE   assert render() == \"```json\"\n````",
    }

    assert extract_gate_block(render_gate_block(payload)) == payload


def test_a_gate_block_is_not_read_as_an_agent_result():
    """ADR-0008: a Gate is not an Agent, so `parse_run_log` must not see one.
    A Gate verdict counted as a Step would retire the Step it was judging."""
    block = render_gate_block({"kind": "human", "verdict": "blocked", "step": 1})

    assert extract_result_block(block) is None


def test_an_agent_result_is_not_read_as_a_gate_verdict():
    block = render_result_block({"outcome": "completed", "summary": "done"})

    assert extract_gate_block(block) is None


def test_a_long_task_becomes_a_title_a_human_can_scan():
    statement = "add a late-arriving-facts handler to the orders pipeline " * 4

    title = render_issue_title(Task(statement))

    assert len(title) <= 72
    assert title.endswith("...")


def test_a_short_task_is_its_own_title():
    assert render_issue_title(Task("add a retry to the loader")) == "add a retry to the loader"


# --- characterization ------------------------------------------------------


def test_a_previously_filed_issue_still_parses():
    """The format is an interface. This fixture is an Issue as an older
    AgentBastion filed it — before the body named its Workflow in prose — and if
    it stops parsing, so does every Issue already in someone's tracker."""
    body = (FIXTURES / "issue_body_v1.md").read_text(encoding="utf-8")

    document = parse_issue_body(body, resolve_role)

    assert document.version == 1
    assert document.plan.summary == "Add a retry to the loader."
    assert [step.id for step in document.plan.steps] == ["s1", "s2"]
    assert document.plan.steps[0].files == ("src/loader.py",)
    assert document.plan.constraints == ("Do not change the public signature of `load`.",)
    assert document.roster.names() == ("implementer",)
    assert document.roster.roles[0].tier is ModelTier.STANDARD
    assert document.context.files == ("src/loader.py", "tests/test_loader.py")
    assert document.notes


def test_the_rendered_body_still_matches_the_recorded_one():
    """Rendering drift is caught here rather than by a confused human comparing
    two issues filed a month apart.

    A separate recording from the one above on purpose: that fixture is an
    Issue already in a tracker and has to keep parsing, while this one is what
    the renderer produces today. Changing the format means re-recording this and
    leaving that alone."""
    recorded = (FIXTURES / "issue_body_current.md").read_text(encoding="utf-8")

    assert render_issue_body(Task("add a retry to the loader"), a_document()) == recorded


def test_the_body_says_which_workflow_will_run():
    """A human reading the Issue finds out which Roles are about to touch their
    repository, and in what order, without opening the JSON."""
    body = render_issue_body(Task("add a retry"), a_document())

    assert "Running the `feature` Workflow" in body
    assert parse_issue_body(body, resolve_role).workflow == "feature"
