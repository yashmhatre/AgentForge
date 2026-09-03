"""The Security Role audits the change and fixes nothing.

Its output is the interesting part: a list of Findings rather than prose,
because the clean-pass Gate downstream has to tell "audited, nothing found"
from "did not audit", and a paragraph cannot be asked that question.
"""

import json
from pathlib import Path

from agentforge_framework.agents.implementer import IMPLEMENTER
from agentforge_framework.agents.security import SECURITY, build_prompt
from agentforge_framework.agents.security import Security as _SecurityRunner
from agentforge_framework.core.contracts import ContextPack, Effort, ModelTier, Outcome
from agentforge_framework.core.plan_format import render_result_block
from agentforge_framework.providers.claude import ClaudeProvider

from .fakes import FakeRunner
from .test_contracts import a_plan


def audit_says(summary: str, findings=()) -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": render_result_block(
                {"outcome": "completed", "summary": summary, "findings": list(findings)}
            ),
        }
    )


def test_security_thinks_deeply_without_running_deep():
    """The one tier decision in the project that was not about cost, re-read.

    ADR-0004 bought Security `deep` because a missed finding is silent — nobody
    reviews the audit that did not happen. When effort became a Role's own axis
    the argument split in two, and only half of it was ever about model size:
    what a silent failure needs is reasoning, so Security keeps `high` and lets
    the tier fall to `standard`.

    Both halves are asserted together deliberately. Either one alone reads as a
    plain cost cut, and the next person to see `standard` here should find the
    line that says it was paid for elsewhere.
    """
    assert SECURITY.tier is ModelTier.STANDARD
    assert SECURITY.effort is Effort.HIGH


def test_security_out_thinks_the_role_whose_work_it_audits():
    """The comparison that makes the tier move survivable: the audit is not
    permitted to be a shallower pass than the code it is reading."""
    assert list(Effort).index(SECURITY.effort) > list(Effort).index(IMPLEMENTER.effort)


def test_the_prompt_asks_for_a_location_and_a_rationale_rather_than_a_category():
    prompt = build_prompt(a_plan(), ContextPack(), Path("/repo"))

    assert "location" in prompt
    assert "rationale" in prompt
    assert "Potential injection risk" in prompt, "the anti-example is the instruction"
    assert '"findings": []' in prompt


def test_the_prompt_tells_security_to_change_nothing():
    """An audit that edits the code it is auditing cannot be trusted about
    either half."""
    prompt = build_prompt(a_plan(), ContextPack(), Path("/repo"))

    assert "Change nothing" in prompt
    assert "edit nothing" in prompt


def test_an_audit_runs_even_with_command_execution_denied():
    """Unlike the Tester. Auditing is reading, and ADR-0007 leaves reading open,
    so a Run with commands shut still gets its audit rather than an escalation."""
    runner = FakeRunner().script("claude", stdout=audit_says("No findings."))

    result = _SecurityRunner(ClaudeProvider(runner)).run(
        plan=a_plan(), context=ContextPack(), cwd=Path("/repo")
    )

    assert result.outcome is Outcome.COMPLETED
    assert runner.ran("claude")


def test_findings_survive_the_provider_port_as_structure_rather_than_prose():
    runner = FakeRunner().script(
        "claude",
        stdout=audit_says(
            "1 finding: interpolated SQL.",
            [
                {
                    "location": "src/loader.py:42",
                    "risk": "The order id is interpolated into the SQL string.",
                    "rationale": "The loader runs against production Unity Catalog.",
                }
            ],
        ),
    )

    result = _SecurityRunner(ClaudeProvider(runner, allow_commands=True)).run(
        plan=a_plan(), context=ContextPack(), cwd=Path("/repo")
    )

    assert len(result.findings) == 1
    assert result.findings[0].location == "src/loader.py:42"
    assert "Unity Catalog" in result.findings[0].rationale


def test_a_clean_audit_reports_no_findings_rather_than_no_field():
    runner = FakeRunner().script("claude", stdout=audit_says("Nothing found."))

    result = _SecurityRunner(ClaudeProvider(runner, allow_commands=True)).run(
        plan=a_plan(), context=ContextPack(), cwd=Path("/repo")
    )

    assert result.outcome is Outcome.COMPLETED
    assert result.findings == ()


def test_a_finding_reported_as_a_sentence_is_kept_rather_than_dropped():
    """A model asked for three fields sometimes answers with one string.
    Dropping those would clear a Gate that should have blocked, so the sentence
    becomes a finding with no location rather than no finding at all."""
    runner = FakeRunner().script(
        "claude", stdout=audit_says("1 finding.", ["The token is logged at INFO."])
    )

    result = _SecurityRunner(ClaudeProvider(runner, allow_commands=True)).run(
        plan=a_plan(), context=ContextPack(), cwd=Path("/repo")
    )

    assert len(result.findings) == 1
    assert result.findings[0].location == ""
    assert "token is logged" in result.findings[0].risk
