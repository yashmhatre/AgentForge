"""The tracker boundary, tested by what it sends to `gh` and what it builds back.

ADR-0002's strongest claim is that a Run's entire state is recoverable from an
Issue. The last group of tests is where that gets cashed: an Issue is
constructed, and a `RunState` comes out with nothing local consulted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentforge.agents.implementer import IMPLEMENTER
from agentforge.core.contracts import AgentResult, ModelTier, Outcome, RunStatus
from agentforge.core.issues import (
    Comment,
    GitHub,
    Issue,
    IssueError,
    parse_run_log,
    render_run_log_comment,
    run_state,
)
from agentforge.core.plan_format import PlanFormatError

from .fakes import FakeRunner

FIXTURES = Path(__file__).parent / "fixtures"
BODY = (FIXTURES / "issue_body_v1.md").read_text(encoding="utf-8")


def github(runner: FakeRunner) -> GitHub:
    return GitHub(runner, Path("/repo"))


def issue_json(**overrides) -> str:
    payload = {
        "number": 12,
        "title": "add a retry to the loader",
        "body": BODY,
        "url": "https://github.com/acme/pipelines/issues/12",
        "labels": [{"name": "agentforge:planned"}],
        "comments": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


# --- argument construction -------------------------------------------------


def test_reading_an_issue_asks_gh_for_everything_a_run_needs():
    runner = FakeRunner().script("gh", "issue", "view", stdout=issue_json())

    issue = github(runner).read_issue(12)

    call = runner.only("gh", "issue", "view")
    assert call[3] == "12"
    requested = set(runner.argument_after("--json", "gh", "issue", "view").split(","))
    assert {"body", "labels", "comments"} <= requested
    assert issue.number == 12
    assert issue.labels == ("agentforge:planned",)


def test_filing_an_issue_sends_the_body_verbatim():
    runner = FakeRunner().script(
        "gh", "issue", "create", stdout="https://github.com/acme/pipelines/issues/41\n"
    )

    issue = github(runner).create_issue("add a retry", BODY, labels=("agentforge:planned",))

    assert runner.argument_after("--body", "gh", "issue", "create") == BODY
    assert issue.number == 41
    assert issue.url.endswith("/41")


def test_a_label_is_created_before_it_is_applied():
    """A repository that has never run AgentForge has none of our labels, and
    `gh issue edit --add-label` fails on a label that does not exist."""
    runner = FakeRunner()

    github(runner).set_label(12, "agentforge:running")

    assert runner.ran("gh", "label", "create")
    assert runner.argument_after("--add-label", "gh", "issue", "edit") == "agentforge:running"


def test_one_status_label_at_a_time_so_a_run_is_never_ambiguous():
    runner = FakeRunner()
    issue = Issue(number=12, title="t", body=BODY, labels=("agentforge:planned",))

    github(runner).set_status(issue, RunStatus.RUNNING)

    edits = runner.matching("gh", "issue", "edit")
    removed = [c[c.index("--remove-label") + 1] for c in edits if "--remove-label" in c]
    added = [c[c.index("--add-label") + 1] for c in edits if "--add-label" in c]

    assert removed == ["agentforge:planned"]
    assert added == ["agentforge:running"]


def test_a_pull_request_is_opened_as_a_draft_against_the_run_branch():
    runner = FakeRunner().script(
        "gh", "pr", "create", stdout="https://github.com/acme/pipelines/pull/42\n"
    )

    url = github(runner).open_draft_pr(
        title="add a retry", body="Closes #12.", head="agentforge/issue-12", base="main"
    )

    call = runner.only("gh", "pr", "create")
    assert "--draft" in call
    assert runner.argument_after("--head", "gh", "pr", "create") == "agentforge/issue-12"
    assert url.endswith("/pull/42")


def test_a_missing_gh_is_named_rather_than_reported_as_a_file_not_found():
    runner = FakeRunner().uninstall("gh")

    with pytest.raises(IssueError, match="gh"):
        github(runner).preflight()


def test_a_failed_gh_call_carries_its_stderr():
    runner = FakeRunner().script(
        "gh", "issue", "view", stdout="", stderr="could not resolve to an Issue", returncode=1
    )

    with pytest.raises(IssueError, match="could not resolve"):
        github(runner).read_issue(999)


def test_unparsable_gh_output_is_an_error_and_not_an_empty_issue():
    runner = FakeRunner().script("gh", "issue", "view", stdout="<html>rate limited</html>")

    with pytest.raises(IssueError, match="could not parse"):
        github(runner).read_issue(12)


# --- the Run Log -----------------------------------------------------------


def test_a_run_log_comment_reads_as_prose_and_parses_as_data():
    result = AgentResult(
        role="implementer",
        tier=ModelTier.STANDARD,
        outcome=Outcome.COMPLETED,
        summary="Added the retry.",
        detail="Capped at three attempts.",
        files_changed=("src/loader.py",),
    )

    body = render_run_log_comment(result)

    assert "### implementer — completed" in body
    assert "`standard`" in body
    assert "`src/loader.py`" in body
    assert parse_run_log(Issue(12, "t", BODY, comments=(Comment("bot", body),))) == (result,)


def test_an_escalation_comment_tells_the_human_what_to_do_next():
    result = AgentResult(
        role="implementer",
        tier=ModelTier.STANDARD,
        outcome=Outcome.ESCALATED,
        summary="Step s1 names a file that is not here.",
    )

    body = render_run_log_comment(result)

    assert "escalated" in body
    assert "plan block" in body and "agentforge implement" in body


def test_human_comments_are_ignored_by_the_run_log():
    issue = Issue(
        12,
        "t",
        BODY,
        comments=(
            Comment("a-human", "Looks reasonable to me, go ahead."),
            Comment("a-human", "```json\n{\"outcome\": \"completed\"}\n```"),
        ),
    )

    assert parse_run_log(issue) == ()


# --- ADR-0002: state comes from the Issue and nowhere else -----------------


def test_a_run_state_is_rebuilt_from_an_issue_alone():
    issue = Issue(12, "add a retry", BODY, labels=("agentforge:planned",))

    state = run_state(issue)

    assert state.issue == 12
    assert state.plan.summary == "Add a retry to the loader."
    assert state.roster.names() == ("implementer",)
    assert state.context.files == ("src/loader.py", "tests/test_loader.py")
    assert state.status is RunStatus.PLANNED
    assert state.remaining == (IMPLEMENTER,)


def test_a_half_finished_run_resumes_where_the_run_log_stopped():
    done = render_run_log_comment(
        AgentResult("implementer", ModelTier.STANDARD, Outcome.COMPLETED, "Added the retry.")
    )
    issue = Issue(
        12, "add a retry", BODY, labels=("agentforge:running",), comments=(Comment("bot", done),)
    )

    state = run_state(issue)

    assert state.done_roles == ("implementer",)
    assert state.remaining == ()


def test_status_falls_back_to_the_run_log_when_a_label_was_removed_by_hand():
    escalated = render_run_log_comment(
        AgentResult("implementer", ModelTier.STANDARD, Outcome.ESCALATED, "wrong file")
    )
    issue = Issue(12, "add a retry", BODY, labels=(), comments=(Comment("bot", escalated),))

    assert run_state(issue).status is RunStatus.ESCALATED


def test_an_issue_nobody_planned_is_refused_with_a_reason():
    issue = Issue(7, "please make the pipeline faster", "It's slow. Thanks!")

    with pytest.raises(PlanFormatError):
        run_state(issue)
