"""The Tester Role verifies the frozen Plan without repairing its findings."""

import json
from pathlib import Path

from agentforge_framework.agents.tester import TESTER, build_prompt
from agentforge_framework.agents.tester import Tester as _TesterRunner
from agentforge_framework.core.contracts import ContextPack, ModelTier, Outcome
from agentforge_framework.core.plan_format import render_result_block
from agentforge_framework.providers.claude import ClaudeProvider

from .fakes import FakeRunner
from .test_contracts import a_plan


def test_the_tester_is_a_cheap_tier_role_that_reports_findings():
    """`cheap` per ADR-0004: the suite is the authority on pass or fail, and the
    Tester reports what it saw rather than deciding it. The trade is recorded in
    that ADR's amendment — this is the row to move back if flaws start reaching
    Sign-off."""
    prompt = build_prompt(a_plan(), ContextPack(), Path("/repo"))

    assert TESTER.tier is ModelTier.CHEAP
    assert "frozen Plan" in prompt
    assert "edge cases" in prompt
    assert "findings" in prompt
    assert '"outcome": "completed"' in prompt


def test_a_denied_tester_reports_the_denial_instead_of_claiming_completion():
    runner = FakeRunner()

    result = _TesterRunner(ClaudeProvider(runner)).run(
        plan=a_plan(),
        context=ContextPack(),
        cwd=Path("/repo"),
    )

    assert result.outcome is Outcome.ESCALATED
    assert "command" in result.summary.lower() and "denied" in result.summary.lower()
    assert not runner.ran("claude"), "a denied Tester cannot run the suite"


def test_an_open_tester_reaches_the_shared_provider_to_run_the_suite():
    runner = FakeRunner().script(
        "claude",
        stdout=json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": render_result_block(
                    {"outcome": "completed", "summary": "pytest: 24 passed"}
                ),
            }
        ),
    )

    result = _TesterRunner(ClaudeProvider(runner, allow_commands=True)).run(
        plan=a_plan(),
        context=ContextPack(),
        cwd=Path("/repo"),
    )

    assert result.outcome is Outcome.COMPLETED
    assert runner.ran("claude")
    assert "run the repository's test suite" in runner.prompt_to("claude")
