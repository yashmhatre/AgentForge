"""The Architect decides the shape of a change it will not make.

It is in none of the shipped Workflows, so the thing worth holding down is that
it is a real Role and not a decorative one: it reaches its Provider through the
same port as the other five, and a definition naming it runs.
"""

import json
from pathlib import Path

from agentforge_framework.agents import RUNNERS
from agentforge_framework.agents.architect import ARCHITECT, build_prompt
from agentforge_framework.agents.architect import Architect as _ArchitectRunner
from agentforge_framework.core.contracts import (
    ContextPack,
    ModelTier,
    Outcome,
    Plan,
    PlanStep,
)
from agentforge_framework.core.plan_format import render_result_block
from agentforge_framework.providers.claude import ClaudeProvider

from .fakes import FakeRunner
from .test_contracts import a_plan


def design_says(summary: str, detail: str = "The parser owns the format.") -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": render_result_block(
                {"outcome": "completed", "summary": summary, "detail": detail}
            ),
        }
    )


def test_the_architect_runs_deep_because_a_boundary_outlives_the_run():
    """An Implementer's mistake is caught by a Tester. A seam in the wrong place
    is caught six months later by whoever has to work across it (ADR-0004)."""
    assert ARCHITECT.tier is ModelTier.DEEP


def test_the_architect_is_registered_as_runnable():
    """CONTEXT.md promises six Roles. This is the sixth."""
    assert ARCHITECT.name in RUNNERS


def test_the_prompt_asks_for_a_seam_and_the_approach_that_was_rejected():
    prompt = build_prompt(a_plan(), ContextPack(), Path("/repo"))

    assert "where the seam goes" in prompt
    assert "rejected" in prompt
    assert "the shape the repository already uses" in prompt


def test_the_prompt_forbids_planning_and_forbids_writing():
    """The Plan is frozen before any Agent runs (ADR-0003). A Role that
    re-planned would be a second Orchestrator with none of the human's context,
    and a designer that writes the code has reviewed its own design."""
    prompt = build_prompt(a_plan(), ContextPack(), Path("/repo"))

    assert "not rewriting it" in prompt
    assert "Write no code and change no files" in prompt
    assert '"outcome": "escalated"' in prompt


def test_the_plans_constraints_and_context_pack_reach_the_design():
    """A design that ignores the constraints it was given is a design somebody
    has to have the argument about twice."""
    plan = Plan(
        summary="Add a retry.",
        steps=(PlanStep("s1", "Wrap the fetch."),),
        constraints=("No new dependencies.",),
    )

    prompt = build_prompt(
        plan,
        ContextPack(files=("src/loader.py",), conventions=("ruff",)),
        Path("/repo"),
    )

    assert "No new dependencies." in prompt
    assert "src/loader.py" in prompt
    assert "ruff" in prompt


def test_the_architect_reaches_its_provider_through_the_shared_port():
    runner = FakeRunner().script("claude", stdout=design_says("Split the parser from the port."))

    result = _ArchitectRunner(ClaudeProvider(runner)).run(
        plan=a_plan(), context=ContextPack(), cwd=Path("/repo")
    )

    assert result.outcome is Outcome.COMPLETED
    assert result.role == "architect"
    assert runner.argument_after("--model", "claude") == "opus", "deep did not reach the CLI"


def test_designing_needs_no_command_execution():
    """Reading is open under ADR-0007, so a Run with commands shut still gets
    its design rather than an escalation."""
    runner = FakeRunner().script("claude", stdout=design_says("Split the parser from the port."))

    result = _ArchitectRunner(ClaudeProvider(runner)).run(
        plan=a_plan(), context=ContextPack(), cwd=Path("/repo")
    )

    assert result.outcome is Outcome.COMPLETED
