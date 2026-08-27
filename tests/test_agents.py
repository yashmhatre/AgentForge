"""The two Roles M1 implements.

The Orchestrator's testable behavior is Roster selection: what it does with a
model that asks for Roles nobody has built yet. The Implementer's is its prompt
— what it is told, and more importantly what it is not told.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentbastion import agents
from agentbastion.agents.implementer import IMPLEMENTER, Implementer, build_prompt
from agentbastion.agents.orchestrator import (
    ORCHESTRATOR,
    Orchestrator,
    build_document,
    select_roster,
)
from agentbastion.core.contracts import ContextPack, ModelTier, Outcome, Task
from agentbastion.core.plan_format import (
    PLAN_CLOSE,
    PLAN_OPEN,
    render_result_block,
)
from agentbastion.providers.claude import ClaudeProvider

from .fakes import FakeRunner
from .test_contracts import a_plan


def plan_block(roster, plan=None, context=None, workflow=None) -> str:
    payload = {
        "version": 1,
        "plan": (plan or a_plan()).to_dict(),
        "roster": roster,
        "context": context or {},
    }
    if workflow is not None:
        payload["workflow"] = workflow
    return f"{PLAN_OPEN}\n```json\n{json.dumps(payload)}\n```\n{PLAN_CLOSE}"


def orchestrator_output(
    roster, outcome="completed", summary="planned it", workflow=None
) -> str:
    envelope = {
        "type": "result",
        "is_error": False,
        "result": plan_block(roster, workflow=workflow)
        + "\n\n"
        + render_result_block({"outcome": outcome, "summary": summary}),
    }
    return json.dumps(envelope)


# --- the Orchestrator picks the Workflow -------------------------------------


def test_the_workflow_the_orchestrator_named_is_the_one_the_issue_runs():
    """The Roster follows from it: a bug fix and a schema migration draw
    different Rosters because they draw different Workflows."""
    document = build_document(
        plan_block([{"role": "implementer"}], workflow="bugfix")
        + render_result_block({"outcome": "completed", "summary": "planned"})
    )

    assert document.workflow == "bugfix"
    assert document.roster.names() == ("implementer", "tester", "reviewer")


def test_a_review_of_somebody_elses_diff_draws_a_roster_with_no_implementer():
    document = build_document(
        plan_block([{"role": "security"}], workflow="review")
        + render_result_block({"outcome": "completed", "summary": "planned"})
    )

    assert document.roster.names() == ("security", "reviewer")


def test_a_plan_block_naming_no_workflow_reads_as_the_default():
    """Additive: an Issue filed before Workflows were selectable still runs."""
    document = build_document(
        plan_block([{"role": "implementer"}])
        + render_result_block({"outcome": "completed", "summary": "planned"})
    )

    assert document.workflow == "feature"


def test_a_workflow_nobody_ships_is_caught_while_the_human_is_still_here():
    """Rather than a week later on somebody else's machine, when the person who
    could have corrected it has gone."""
    document = build_document(
        plan_block([{"role": "implementer"}], workflow="deploy-to-prod")
        + render_result_block({"outcome": "completed", "summary": "planned"})
    )

    assert document.workflow == "feature"
    assert any("deploy-to-prod" in note for note in document.notes)


def test_a_role_the_workflow_does_not_run_is_dropped_and_said_so():
    """The Roster table is what a human reads to find out who is about to touch
    their repository. It cannot list somebody who will not."""
    document = build_document(
        plan_block([{"role": "implementer"}, {"role": "security"}], workflow="bugfix")
        + render_result_block({"outcome": "completed", "summary": "planned"})
    )

    assert "security" not in document.roster.names()
    assert any("security" in note and "bugfix" in note for note in document.notes)


def test_a_tier_the_orchestrator_asked_for_survives_the_workflow():
    """Choosing the Workflow is a judgement about the shape of the Task; moving
    a Role up a tier is a judgement about the difficulty of this one."""
    document = build_document(
        plan_block([{"role": "implementer", "tier": "deep"}], workflow="bugfix")
        + render_result_block({"outcome": "completed", "summary": "planned"})
    )

    tiers = {role.name: role.tier for role in document.roster}
    assert tiers["implementer"] is ModelTier.DEEP
    assert tiers["tester"] is ModelTier.CHEAP, "a Role nobody moved kept its default"


def test_the_planning_prompt_lists_the_workflows_and_their_steps():
    prompt = Orchestrator(ClaudeProvider(FakeRunner())).build_prompt(
        Task("add a retry"), Path("/repo")
    )

    assert "`feature`: implementer, tester, security, reviewer" in prompt
    assert "`bugfix`: implementer, tester, reviewer" in prompt
    assert "`review`: security, reviewer" in prompt


def test_the_planning_prompt_says_the_orchestrator_files_nothing():
    """`to-spec` and `to-tickets` both end by publishing to a tracker and
    labelling what they filed. That is AgentBastion's job, and a second Issue
    filed from inside a planning pass is one nobody is tracking."""
    prompt = Orchestrator(ClaudeProvider(FakeRunner())).build_prompt(
        Task("add a retry"), Path("/repo")
    )

    assert "no issue tracker and no triage labels" in prompt
    assert "do not apply a label" in prompt


# --- Roster selection ------------------------------------------------------


def test_a_roster_the_orchestrator_asked_for_is_kept_in_order():
    roster, notes = select_roster([{"role": "implementer", "tier": "deep"}])

    assert roster.names() == ("implementer",)
    assert roster.roles[0].tier is ModelTier.DEEP
    assert notes == ()


def test_roles_that_do_not_exist_yet_are_dropped_and_the_human_is_told(monkeypatch):
    """Every Role CONTEXT.md names now runs, so the gap this covers is the one a
    seventh Role sits in between being given a tier and being given a runner.
    The Architect was the last name to sit in it."""
    monkeypatch.setitem(agents.KNOWN_TIERS, "cartographer", ModelTier.DEEP)

    roster, notes = select_roster(
        [{"role": "implementer"}, {"role": "tester"}, {"role": "cartographer"}]
    )

    assert roster.names() == ("implementer", "tester")
    assert len(notes) == 1
    assert "cartographer" in notes[0]
    assert "not implemented yet" in notes[0]


def test_an_invented_role_is_dropped_as_unknown_rather_than_as_deferred():
    _, notes = select_roster([{"role": "implementer"}, {"role": "bulldozer"}])

    assert any("Unknown Role" in note for note in notes)


def test_a_roster_with_nothing_runnable_still_gets_a_role():
    """M1 is proving the pipe. An Issue nobody can implement proves nothing."""
    roster, notes = select_roster([{"role": "bulldozer"}])

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
    # The Workflow is what runs, so the Roster is its Roles rather than the
    # shorter list the model asked for.
    assert planned.document.workflow == "feature"
    assert planned.document.roster.names() == ("implementer", "tester", "security", "reviewer")


def test_the_orchestrator_runs_deep_by_default():
    """ADR-0004: it pays for all downstream reasoning once."""
    runner = FakeRunner().script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    Orchestrator(ClaudeProvider(runner)).plan(Task("add a retry"), Path("/repo"))

    assert ORCHESTRATOR.tier is ModelTier.DEEP
    assert runner.argument_after("--model", "claude") == "opus"


def test_the_two_tier_tables_agree():
    """ADR-0004's table is written down twice: once as `KNOWN_TIERS`, and once as
    the tier each Role actually declares. Only the second drives an invocation,
    so the first can go stale without anything failing — and it is what the
    Orchestrator is shown when it picks a Roster."""
    declared = {name: role.tier for name, role in agents.ROLES.items()}
    known = {name: tier for name, tier in agents.KNOWN_TIERS.items() if name in declared}

    assert declared == known


def test_the_tier_table_covers_every_role_context_md_names():
    """A Role missing from `KNOWN_TIERS` reads as invented rather than deferred,
    and the human is told the wrong thing about why it was dropped."""
    assert set(agents.ROLES) <= set(agents.KNOWN_TIERS)


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


def test_the_planning_prompt_names_every_role_that_can_run():
    prompt = Orchestrator(ClaudeProvider(FakeRunner())).build_prompt(
        Task("add a retry"), Path("/repo")
    )

    for name in ("architect", "implementer", "tester", "security", "reviewer"):
        assert f"`{name}`" in prompt, name
    assert "orchestrator" not in prompt.split("## Roles")[-1].split("##")[0], (
        "the Orchestrator offered itself a place on the Roster"
    )
    assert "do not put them in the Roster" not in prompt, (
        "nothing is deferred now, so the prompt must not warn about nothing"
    )


def test_the_planning_prompt_warns_off_a_role_that_has_a_tier_and_no_runner(monkeypatch):
    """The other half: a Role named in the tier table before its runner lands is
    a reasonable thing for a model to reach for, and the prompt says not to."""
    monkeypatch.setitem(agents.KNOWN_TIERS, "cartographer", ModelTier.DEEP)

    prompt = Orchestrator(ClaudeProvider(FakeRunner())).build_prompt(
        Task("add a retry"), Path("/repo")
    )

    assert "do not put them in the Roster: `cartographer`" in prompt


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


def test_the_implementer_leaves_committing_to_agentbastion():
    """The branch and the commit are the runtime's job; an Agent that commits
    makes the diff impossible to attribute."""
    assert "Commit nothing" in build_prompt(a_plan(), ContextPack(), Path("/repo"))


def test_a_plan_with_no_steps_tells_the_implementer_to_escalate():
    from agentbastion.core.contracts import Plan

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
