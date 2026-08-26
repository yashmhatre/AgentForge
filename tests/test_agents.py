"""The two Roles M1 implements.

The Orchestrator's testable behavior is Roster selection: what it does with a
model that asks for Roles nobody has built yet. The Implementer's is its prompt
— what it is told, and more importantly what it is not told.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentforge.agents.implementer import IMPLEMENTER, Implementer, build_prompt
from agentforge.agents.orchestrator import (
    ORCHESTRATOR,
    Orchestrator,
    build_document,
    select_roster,
)
from agentforge.core.contracts import ContextPack, ModelTier, Outcome, Task
from agentforge.core.plan_format import (
    PLAN_CLOSE,
    PLAN_OPEN,
    render_result_block,
)
from agentforge.providers.claude import ClaudeProvider

from .fakes import FakeRunner
from .test_contracts import a_plan


def plan_block(roster, plan=None, context=None) -> str:
    payload = {
        "version": 1,
        "plan": (plan or a_plan()).to_dict(),
        "roster": roster,
        "context": context or {},
    }
    return f"{PLAN_OPEN}\n```json\n{json.dumps(payload)}\n```\n{PLAN_CLOSE}"


def orchestrator_output(roster, outcome="completed", summary="planned it") -> str:
    envelope = {
        "type": "result",
        "is_error": False,
        "result": plan_block(roster)
        + "\n\n"
        + render_result_block({"outcome": outcome, "summary": summary}),
    }
    return json.dumps(envelope)


# --- Roster selection ------------------------------------------------------


def test_a_roster_the_orchestrator_asked_for_is_kept_in_order():
    roster, notes = select_roster([{"role": "implementer", "tier": "deep"}])

    assert roster.names() == ("implementer",)
    assert roster.roles[0].tier is ModelTier.DEEP
    assert notes == ()


def test_roles_that_do_not_exist_yet_are_dropped_and_the_human_is_told():
    roster, notes = select_roster(
        [{"role": "implementer"}, {"role": "tester"}, {"role": "architect"}]
    )

    assert roster.names() == ("implementer", "tester")
    assert len(notes) == 1
    assert "architect" in notes[0]


def test_an_invented_role_is_dropped_as_unknown_rather_than_as_deferred():
    _, notes = select_roster([{"role": "implementer"}, {"role": "bulldozer"}])

    assert any("Unknown Role" in note for note in notes)


def test_a_roster_with_nothing_runnable_still_gets_a_role():
    """M1 is proving the pipe. An Issue nobody can implement proves nothing."""
    roster, notes = select_roster([{"role": "architect"}])

    assert roster.names() == ("implementer",)
    assert any("No implemented Role survived" in note for note in notes)


def test_the_orchestrator_never_puts_itself_on_the_roster():
    roster, _ = select_roster([{"role": "orchestrator"}, {"role": "implementer"}])

    assert roster.names() == ("implementer",)


def test_a_repeated_role_appears_once():
    roster, _ = select_roster([{"role": "implementer"}, {"role": "implementer"}])

    assert roster.names() == ("implementer",)


def test_a_bare_role_name_is_accepted_as_well_as_an_object():
    roster, _ = select_roster(["implementer"])

    assert roster.names() == ("implementer",)


def test_a_document_carries_the_plan_the_roster_and_the_context_pack():
    document = build_document(
        plan_block(
            [{"role": "implementer"}],
            context={"files": ["src/loader.py"], "conventions": ["ruff"]},
        )
    )

    assert document.plan.summary == "Add a retry to the loader."
    assert document.context.files == ("src/loader.py",)


# --- planning passes -------------------------------------------------------


def test_a_planning_pass_produces_a_fileable_document():
    runner = FakeRunner().script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    planned = Orchestrator(ClaudeProvider(runner)).plan(Task("add a retry"), Path("/repo"))

    assert not planned.escalated
    assert planned.document.roster.names() == ("implementer",)


def test_the_orchestrator_runs_deep_by_default():
    """ADR-0004: it pays for all downstream reasoning once."""
    runner = FakeRunner().script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    Orchestrator(ClaudeProvider(runner)).plan(Task("add a retry"), Path("/repo"))

    assert ORCHESTRATOR.tier is ModelTier.DEEP
    assert runner.argument_after("--model", "claude") == "opus"


def test_an_ambiguous_task_escalates_while_the_human_is_still_at_the_keyboard():
    """ADR-0003 freezes the plan when it is filed. This is the last cheap moment
    to ask a question."""
    envelope = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": render_result_block(
                {"outcome": "escalated", "summary": "Which loader? There are three."}
            ),
        }
    )
    runner = FakeRunner().script("claude", stdout=envelope)

    planned = Orchestrator(ClaudeProvider(runner)).plan(Task("fix the loader"), Path("/repo"))

    assert planned.escalated
    assert planned.document is None
    assert "three" in planned.result.summary


def test_a_confident_orchestrator_that_wrote_no_plan_is_a_failure():
    envelope = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": render_result_block({"outcome": "completed", "summary": "all done!"}),
        }
    )
    runner = FakeRunner().script("claude", stdout=envelope)

    planned = Orchestrator(ClaudeProvider(runner)).plan(Task("add a retry"), Path("/repo"))

    assert planned.escalated
    assert planned.result.outcome is Outcome.FAILED
    assert "no usable plan" in planned.result.summary


def test_the_planning_prompt_names_only_roles_that_can_run():
    prompt = Orchestrator(ClaudeProvider(FakeRunner())).build_prompt(
        Task("add a retry"), Path("/repo")
    )

    assert "`implementer`" in prompt
    assert "do not put them in the Roster" in prompt
    assert "tester" in prompt


# --- the Implementer -------------------------------------------------------


def test_the_implementer_is_handed_the_plan_and_not_the_task():
    """ADR-0003: downstream Roles are not given the human's original phrasing."""
    prompt = build_prompt(a_plan(), ContextPack(files=("src/loader.py",)), Path("/repo"))

    assert "Wrap the fetch in a bounded retry" in prompt
    assert "Do not change the public signature of `load`." in prompt
    assert "src/loader.py" in prompt
    assert "in your own words" not in prompt


def test_the_implementer_is_told_to_stop_rather_than_improvise():
    prompt = build_prompt(a_plan(), ContextPack(), Path("/repo"))

    assert "escalated" in prompt
    assert "Do not guess" in prompt
    assert "Do not re-scope" in prompt


def test_the_implementer_leaves_committing_to_agentforge():
    """The branch and the commit are the runtime's job; an Agent that commits
    makes the diff impossible to attribute."""
    assert "Commit nothing" in build_prompt(a_plan(), ContextPack(), Path("/repo"))


def test_a_plan_with_no_steps_tells_the_implementer_to_escalate():
    from agentforge.core.contracts import Plan

    prompt = build_prompt(Plan(summary="do something"), ContextPack(), Path("/repo"))

    assert "Escalate rather than inventing them" in prompt


def test_the_implementer_runs_at_standard_by_default():
    runner = FakeRunner().script(
        "claude",
        stdout=json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": render_result_block({"outcome": "completed", "summary": "done"}),
            }
        ),
    )

    result = Implementer(ClaudeProvider(runner)).run(
        plan=a_plan(), context=ContextPack(), cwd=Path("/repo")
    )

    assert IMPLEMENTER.tier is ModelTier.STANDARD
    assert runner.argument_after("--model", "claude") == "sonnet"
    assert result.outcome is Outcome.COMPLETED
