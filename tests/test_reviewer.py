"""The Reviewer writes what a human reads, and rewrites it when it scans dirty.

The retry loop is driven the way the Run drives it: by scripting what the
`unslop` scanners say through the Command Runner fake. No scanner actually runs
here, and no model does either.
"""

import json
import sys
from pathlib import Path

from agentforge_framework.agents.reviewer import (
    MAX_REWRITES,
    REVIEWER,
    WRITING_SKILLS,
    build_prompt,
    build_rewrite_prompt,
    prose_of,
)
from agentforge_framework.agents.reviewer import Reviewer as _ReviewerRunner
from agentforge_framework.core.contracts import (
    AgentResult,
    ContextPack,
    ModelTier,
    Outcome,
)
from agentforge_framework.core.plan_format import render_result_block
from agentforge_framework.core.skills import UNSLOP_SCANNERS, run_unslop
from agentforge_framework.providers.claude import ClaudeProvider

from .fakes import FakeRunner
from .test_contracts import a_plan

DIRTY = {
    "total_violations": 1,
    "violations": [
        {
            "line_number": 4,
            "phrase": "delve into",
            "suggestion": "say what you looked at",
        }
    ],
}
CLEAN = {"total_violations": 0, "violations": []}


def review_says(summary: str, detail: str = "The loader retries three times.") -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": render_result_block(
                {"outcome": "completed", "summary": summary, "detail": detail}
            ),
        }
    )


def a_provider(runner: FakeRunner) -> ClaudeProvider:
    return ClaudeProvider(runner, allow_commands=True)


def scanners_say(runner: FakeRunner, *verdicts: bool) -> FakeRunner:
    """Script one scan per verdict: True is a clean pass, False is one finding.

    Every scanner in a pass answers the same way, which is all the retry loop
    reads — a pass is clean or it is not.
    """
    outs = []
    codes = []
    for clean in verdicts:
        for _ in UNSLOP_SCANNERS:
            outs.append(json.dumps(CLEAN if clean else DIRTY))
            codes.append(0 if clean else 1)

    # The fake consumes one scripted answer per call, so exit codes have to be
    # scripted alongside the payloads rather than once for the run.
    answers = iter(zip(outs, codes, strict=True))

    class Scripted(FakeRunner):
        def run(self, argv, *, cwd=None, stdin=None, timeout=None):
            argv = tuple(str(part) for part in argv)
            if argv[0] == sys.executable:
                self.calls.append(argv)
                self.cwds.append(None)
                stdout, code = next(answers)
                from agentforge_framework.core.process import CommandResult

                return CommandResult(argv=argv, returncode=code, stdout=stdout)
            return super().run(argv, cwd=cwd, stdin=stdin, timeout=timeout)

    scripted = Scripted(binaries=runner.binaries, scripts=runner.scripts)
    return scripted


def reviews(claude, *verdicts: bool):
    """Run the Reviewer against scripted prose and scripted scanner verdicts."""
    runner = scanners_say(FakeRunner().script("claude", stdout=claude), *verdicts)
    result = _ReviewerRunner(a_provider(runner)).run(
        plan=a_plan(), context=ContextPack(), cwd=Path("/repo")
    )
    return result, runner


def test_the_reviewer_is_a_deep_tier_role_that_reports_against_the_plan():
    """`deep` per ADR-0004's amendment: it speaks last, so nothing downstream
    catches a review that is thin or wrong. The next thing after it is a person
    deciding whether to merge."""
    prompt = build_prompt(a_plan(), ContextPack(), Path("/repo"))

    assert REVIEWER.tier is ModelTier.DEEP
    assert "frozen Plan" in prompt
    assert "did what it said it would" in prompt
    assert "Sign-off" in prompt


def test_the_review_is_written_deep_and_the_rewrites_are_not():
    """Two jobs share this Step and they are priced separately. Judging a diff
    against a frozen Plan is what `deep` was chosen for; applying a finding that
    already names the phrase, the line, and a replacement is not."""
    _, runner = reviews(review_says("The change matches the Plan."), False, False, True)

    models = [call[call.index("--model") + 1] for call in runner.matching("claude")]

    assert models == ["opus", "haiku", "haiku"]


def prompts_of(runner) -> list[str]:
    """What the claude CLI was actually asked, in order."""
    return [call[call.index("-p") + 1] for call in runner.matching("claude")]


def test_the_first_draft_is_written_with_the_prose_skill_in_front_of_it():
    """A rewrite reaches a phrase. `silhouette_scan` reads the shape of the
    document, and no substitution changes that, so a review that is dirty for a
    structural reason burns all three attempts and posts dirty anyway. The
    doctrine is cheapest before the first draft."""
    _, runner = reviews(review_says("The change matches the Plan."), True)

    assert REVIEWER.skills == WRITING_SKILLS
    assert "/agentforge:write-plainly" in prompts_of(runner)[0]


def test_a_rewrite_is_not_handed_the_doctrine_a_second_time():
    """#12's rule. Every finding a rewrite acts on already names the phrase, the
    line, and a replacement; re-delivering the skill would spend context
    teaching a Role to write while asking it to run find-and-replace."""
    _, runner = reviews(review_says("The change matches the Plan."), False, True)

    first, rewrite = prompts_of(runner)

    assert "/agentforge:write-plainly" in first
    assert "write-plainly" not in rewrite


def test_a_rewritten_review_is_still_reported_at_the_tier_it_was_written_at():
    """A result carries whatever tier produced it, so the last rewrite would
    otherwise make a `deep` review read as `cheap` in the Run Log — for the only
    reason that a phrase was fixed."""
    result, _ = reviews(review_says("The change matches the Plan."), False, True)

    assert result.tier is ModelTier.DEEP
    assert "rewritten at `cheap`" in result.detail


def test_a_review_that_needed_no_rewrite_says_nothing_about_rewrite_tiers():
    result, _ = reviews(review_says("The change matches the Plan."), True)

    assert result.tier is ModelTier.DEEP
    assert "rewritten at" not in result.detail


def test_clean_prose_is_posted_without_a_rewrite():
    result, runner = reviews(review_says("The change matches the Plan."), True)

    assert result.outcome is Outcome.COMPLETED
    assert len(runner.matching("claude")) == 1, "clean prose was rewritten anyway"
    assert "clean on attempt 1 of 3" in result.detail


def test_a_finding_triggers_one_rewrite_and_the_second_attempt_is_posted():
    result, runner = reviews(review_says("The change matches the Plan."), False, True)

    assert len(runner.matching("claude")) == 2
    assert "clean on attempt 2 of 3" in result.detail


def test_clean_on_the_third_attempt_stops_retrying():
    result, runner = reviews(review_says("The change matches the Plan."), False, False, True)

    assert len(runner.matching("claude")) == 3
    assert "clean on attempt 3 of 3" in result.detail


def test_prose_that_never_scans_clean_reaches_sign_off_anyway():
    """The scan is a Command and not a Gate. Holding a finished Run on a
    cosmetic check trades a real cost for a stylistic one."""
    result, runner = reviews(review_says("The change matches the Plan."), False, False, False)

    assert result.outcome is Outcome.COMPLETED, "the scan blocked the Run"
    assert len(runner.matching("claude")) == MAX_REWRITES + 1
    assert "Posted anyway" in result.detail


def test_the_report_reaches_the_run_log_on_the_failing_path_too():
    """Thin prose nobody can diagnose is how a check like this quietly stops
    meaning anything."""
    result, _ = reviews(review_says("The change matches the Plan."), False, False, False)

    assert "banned_phrase_scan.py: 1 finding(s)" in result.detail
    assert "'delve into'" in result.detail
    assert "say what you looked at" in result.detail


def test_the_rewrite_is_handed_the_findings_rather_than_the_skill():
    """Each finding carries the phrase, the line, and a suggestion. Inlining the
    skill's doctrine on top would be AgentForge teaching a Role to write."""
    runner = scanners_say(
        FakeRunner().script("claude", stdout=review_says("The change matches the Plan.")),
        False,
        True,
    )

    _ReviewerRunner(a_provider(runner)).run(
        plan=a_plan(), context=ContextPack(), cwd=Path("/repo")
    )

    rewrite = [call[call.index("-p") + 1] for call in runner.matching("claude")][1]
    assert "delve into" in rewrite
    assert "say what you looked at" in rewrite
    assert "Keep every claim you made" in rewrite
    assert "unslop" not in rewrite, "the skill's own instructions reached the prompt"


def test_the_rewrite_prompt_carries_the_prose_it_is_rewriting():
    report = run_unslop(
        Path(__file__),
        scanners=(),
        runner=FakeRunner(),
    )
    prompt = build_rewrite_prompt("The loader delves into the retry path.", report)

    assert "The loader delves into the retry path." in prompt


def test_a_reviewer_that_escalated_is_not_scanned_or_rewritten():
    """An escalation is the Run's business rather than the scanner's."""
    escalated = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": render_result_block(
                {"outcome": "escalated", "summary": "Nothing was changed."}
            ),
        }
    )
    runner = FakeRunner().script("claude", stdout=escalated)

    result = _ReviewerRunner(a_provider(runner)).run(
        plan=a_plan(), context=ContextPack(), cwd=Path("/repo")
    )

    assert result.outcome is Outcome.ESCALATED
    assert not runner.ran(sys.executable), "an escalation was scanned"
    assert len(runner.matching("claude")) == 1


def test_the_prose_is_scanned_outside_the_working_tree():
    """AgentForge commits whatever a Role leaves behind, so a scratch file in
    the repository would be committed and read by nobody."""
    _, runner = reviews(review_says("The change matches the Plan."), True)

    scanned = [call for call in runner.calls if call[0] == sys.executable]
    assert scanned, "nothing was scanned"
    for call in scanned:
        assert "/repo" not in call[-1].replace("\\", "/"), "the scratch file was in the tree"


def test_the_summary_is_scanned_and_not_only_the_detail():
    """It is the line most people read and the least likely to have been
    thought about twice."""
    result = AgentResult(
        role="reviewer",
        tier=ModelTier.CHEAP,
        outcome=Outcome.COMPLETED,
        summary="Let us delve into the change.",
        detail="The loader retries three times.",
    )

    assert "delve" in prose_of(result)
    assert "retries three times" in prose_of(result)


def test_a_rewrite_is_added_to_what_the_review_cost():
    """A Role that spends three invocations and reports the last one's price
    understates itself forever, and the tiering decision reads the understated
    number."""
    priced = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": render_result_block(
                {"outcome": "completed", "summary": "Reviewed.", "detail": "It matches."}
            ),
            "total_cost_usd": 0.25,
            "usage": {"input_tokens": 1000, "output_tokens": 200},
        }
    )

    result, _ = reviews(priced, False, True)

    # The review and the one rewrite it took, at a quarter each.
    assert result.usage.cost_usd == 0.5
    assert result.usage.tokens == 2400
