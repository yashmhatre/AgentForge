"""The serialized shape of the contracts is a compatibility surface.

An Issue filed today is parsed by an `agentforge implement` running next month,
so a change to any of these shapes has to be a deliberate act. That is what
round-tripping asserts: not that the dataclasses work, but that what goes into
an Issue body comes back out unchanged.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentforge.agents import UnknownRole, resolve_role
from agentforge.agents.implementer import IMPLEMENTER
from agentforge.core.contracts import (
    LEGACY_LABELS,
    RUN_LABELS,
    AgentResult,
    ContextPack,
    ModelTier,
    Outcome,
    Plan,
    PlanDocument,
    PlanStep,
    Role,
    Roster,
    RunState,
    RunStatus,
)


def a_plan() -> Plan:
    return Plan(
        summary="Add a retry to the loader.",
        steps=(
            PlanStep(
                id="s1",
                intent="Wrap the fetch in a bounded retry",
                files=("src/loader.py",),
                acceptance="A transient failure retries three times and then raises.",
            ),
            PlanStep(id="s2", intent="Cover the exhausted-retry path", files=("tests/test_loader.py",)),
        ),
        constraints=("Do not change the public signature of `load`.",),
    )


def test_plan_round_trips_through_its_serialized_form():
    plan = a_plan()
    assert Plan.from_dict(plan.to_dict()) == plan


def test_roster_round_trips_by_name_and_tier():
    roster = Roster((IMPLEMENTER.at_tier(ModelTier.DEEP),))

    restored = Roster.from_dict(roster.to_dict(), resolve_role)

    assert restored.names() == ("implementer",)
    assert restored.roles[0].tier is ModelTier.DEEP


def test_roster_carries_names_and_tiers_but_not_prompts():
    """Instructions are code. Shipping them in an Issue body would make every
    filed Issue carry a stale copy of a prompt that has since changed."""
    payload = Roster((IMPLEMENTER,)).to_dict()

    assert payload == [{"role": "implementer", "tier": "standard"}]


def test_plan_document_round_trips_whole():
    document = PlanDocument(
        plan=a_plan(),
        roster=Roster((IMPLEMENTER,)),
        context=ContextPack(files=("src/loader.py",), conventions=("ruff",)),
        notes=("the tester Role was dropped",),
    )

    assert PlanDocument.from_dict(document.to_dict(), resolve_role) == document


def test_agent_result_round_trips_but_leaves_the_transcript_behind():
    result = AgentResult(
        role="implementer",
        tier=ModelTier.STANDARD,
        outcome=Outcome.COMPLETED,
        summary="Added the retry.",
        files_changed=("src/loader.py",),
        raw="a few thousand tokens of transcript",
    )

    restored = AgentResult.from_dict(result.to_dict())

    assert restored == result
    assert restored.raw == "", "raw output must not travel into the Run Log"


def test_tier_overrides_leave_the_role_definition_alone():
    moved = IMPLEMENTER.at_tier(ModelTier.DEEP)

    assert moved.tier is ModelTier.DEEP
    assert IMPLEMENTER.tier is ModelTier.STANDARD


def test_run_labels_are_namespaced_so_they_do_not_collide_with_a_project_scheme():
    assert RunStatus.HALTED.label == "agentforge:halted"
    assert all(label.startswith("agentforge:") for label in (s.label for s in RunStatus))


def test_suspended_halted_and_failed_are_three_states_and_not_three_words():
    """The distinction the design session settled, pinned where it is defined.

    A Run waiting on a Gate it can still clear is suspended. A Run an Escalation
    or an errored Gate stopped is halted. A Run AgentForge could not finish is
    failed. Collapsing any two of them loses the only question #8 and #9 ask.
    """
    three = (RunStatus.SUSPENDED, RunStatus.HALTED, RunStatus.FAILED)

    assert len(set(three)) == 3
    assert [status.label for status in three] == [
        "agentforge:suspended",
        "agentforge:halted",
        "agentforge:failed",
    ]


def test_the_escalated_label_this_project_already_applied_still_reads():
    """`agentforge:escalated` predates the vocabulary that made Escalation the
    verdict and Halted the state. Issues carrying it are still runnable."""
    assert LEGACY_LABELS["agentforge:escalated"] is RunStatus.HALTED
    assert "agentforge:escalated" in RUN_LABELS, "a stale label AgentForge cannot clear is a leak"
    assert "agentforge:escalated" not in [status.label for status in RunStatus]


def test_the_current_step_is_derived_from_the_run_log_rather_than_stored():
    """ADR-0002: a stored cursor is a second answer to a question the Run Log
    already answers, and the two drift the first time a human edits the Issue."""
    planned = RunState(issue=12, plan=a_plan(), roster=Roster((IMPLEMENTER,)))

    assert planned.current_step == 1
    assert not hasattr(planned, "step") and "current_step" not in vars(planned)


def test_a_completed_step_moves_the_run_on_and_an_escalated_one_does_not():
    def state(*results):
        return RunState(issue=12, plan=a_plan(), roster=Roster((IMPLEMENTER,)), results=results)

    done = AgentResult("implementer", ModelTier.STANDARD, Outcome.COMPLETED, "done")
    stopped = AgentResult("tester", ModelTier.STANDARD, Outcome.ESCALATED, "no suite")

    assert state(done).current_step == 2
    assert state(done, stopped).current_step == 2, "the Role that stopped is still on its Step"


def test_a_completed_role_retires_its_roster_entry():
    state = RunState(
        issue=12,
        plan=a_plan(),
        roster=Roster((IMPLEMENTER,)),
        results=(
            AgentResult("implementer", ModelTier.STANDARD, Outcome.COMPLETED, "done"),
        ),
    )

    assert state.remaining == ()


def test_an_escalated_role_stays_on_the_roster():
    """A human corrects the plan and re-runs; the Role that escalated is exactly
    the one that has to run again."""
    state = RunState(
        issue=12,
        plan=a_plan(),
        roster=Roster((IMPLEMENTER,)),
        results=(
            AgentResult("implementer", ModelTier.STANDARD, Outcome.ESCALATED, "step s1 names a file that does not exist"),
        ),
    )

    assert state.remaining == (IMPLEMENTER,)
    assert state.escalation is not None


def test_an_escalation_a_later_run_worked_past_is_not_the_runs_escalation():
    """The Run Log keeps every attempt, so `escalation` has to mean the live one:
    the human corrected the plan block, the Role ran again, and the Run moved on."""
    state = RunState(
        issue=12,
        plan=a_plan(),
        roster=Roster((IMPLEMENTER,)),
        results=(
            AgentResult("implementer", ModelTier.STANDARD, Outcome.ESCALATED, "wrong file"),
            AgentResult("implementer", ModelTier.STANDARD, Outcome.COMPLETED, "fixed now"),
        ),
    )

    assert state.escalation is None
    assert state.done_roles == ("implementer",)


def test_an_unimplemented_role_is_named_rather_than_guessed_at():
    with pytest.raises(UnknownRole, match="security"):
        resolve_role("security")


def test_an_invented_role_says_what_is_available():
    with pytest.raises(UnknownRole, match="implementer"):
        resolve_role("bulldozer")


def test_roles_are_frozen_definitions():
    with pytest.raises(FrozenInstanceError):
        Role(name="x", tier=ModelTier.CHEAP).name = "y"  # type: ignore[misc]


def test_a_role_declares_the_vendored_skills_its_agent_needs():
    role = Role(
        name="architect",
        tier=ModelTier.DEEP,
        skills=("domain-modeling", "grilling"),
    )

    assert role.skills == ("domain-modeling", "grilling")
