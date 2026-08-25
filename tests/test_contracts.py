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
    assert RunStatus.ESCALATED.label == "agentforge:escalated"
    assert all(label.startswith("agentforge:") for label in (s.label for s in RunStatus))


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
