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
from agentforge.core.contracts import (
    AgentResult,
    ModelTier,
    Outcome,
    Plan,
    Roster,
    RunState,
    RunStatus,
)
from agentforge.core.issues import (
    Comment,
    GitHub,
    Issue,
    IssueError,
    parse_run_log,
    render_run_log_comment,
    render_terminal_comment,
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


def test_a_label_that_already_exists_does_not_stop_the_run():
    """Creation is idempotent because it is unconditional: `gh label create`
    fails on the second Run of the repository's life, and that is not an error."""
    runner = FakeRunner().script(
        "gh", "label", "create", stderr="label already exists", returncode=1
    )

    github(runner).set_status(Issue(12, "t", BODY), RunStatus.RUNNING)

    assert runner.ran("gh", "label", "create")
    assert runner.argument_after("--add-label", "gh", "issue", "edit") == "agentforge:running"


def test_the_label_this_project_already_applied_is_cleared_when_the_run_moves_on():
    """Renaming `agentforge:escalated` to `agentforge:halted` leaves the old label
    on issues that are still open. A Run that touches one takes it off."""
    runner = FakeRunner()
    issue = Issue(number=12, title="t", body=BODY, labels=("agentforge:escalated",))

    github(runner).set_status(issue, RunStatus.RUNNING)

    edits = runner.matching("gh", "issue", "edit")
    removed = [c[c.index("--remove-label") + 1] for c in edits if "--remove-label" in c]

    assert removed == ["agentforge:escalated"]


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


def test_an_escalation_comment_names_the_step_the_role_stopped_at():
    """A human correcting the plan needs the position as well as the Role: a
    Workflow may name the same Role twice, and "the tester escalated" does not
    say which tester."""
    result = AgentResult(
        role="tester",
        tier=ModelTier.STANDARD,
        outcome=Outcome.ESCALATED,
        summary="Step s2 names tests/test_loader.py, which is not in this repository.",
    )

    body = render_run_log_comment(result, step=2, of=2)

    assert "### tester — escalated (step 2 of 2)" in body
    assert "Step s2 names tests/test_loader.py, which is not in this repository." in body


def test_a_run_log_comment_still_reads_back_as_data_once_it_carries_a_position():
    """The position is prose. It must not reach the result block, which later
    Runs parse to work out which Steps are behind them."""
    result = AgentResult("implementer", ModelTier.STANDARD, Outcome.COMPLETED, "Added the retry.")

    body = render_run_log_comment(result, step=1, of=2)

    assert parse_run_log(Issue(12, "t", BODY, comments=(Comment("bot", body),))) == (result,)


def test_a_result_that_was_not_a_step_names_no_position():
    """An Agent that reported success and left the tree unchanged fails the Run
    rather than a Step, so there is no position to name."""
    result = AgentResult("implementer", ModelTier.STANDARD, Outcome.FAILED, "left no changes")

    assert render_run_log_comment(result).splitlines()[0] == "### implementer — failed"


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

    assert run_state(issue).status is RunStatus.HALTED


def test_an_issue_labelled_by_an_older_agentforge_still_reads_as_a_run():
    """`agentforge:escalated` was the label before Halted was the state's name."""
    issue = Issue(12, "add a retry", BODY, labels=("agentforge:escalated",))

    assert run_state(issue).status is RunStatus.HALTED


def test_the_step_a_run_reached_is_derived_from_the_issue_and_nothing_else():
    done = render_run_log_comment(
        AgentResult("implementer", ModelTier.STANDARD, Outcome.COMPLETED, "Added the retry.")
    )
    stopped = render_run_log_comment(
        AgentResult("tester", ModelTier.STANDARD, Outcome.ESCALATED, "There is no suite here.")
    )
    issue = Issue(
        12,
        "add a retry",
        BODY,
        labels=("agentforge:halted",),
        comments=(Comment("bot", done), Comment("bot", stopped)),
    )

    state = run_state(issue)

    assert state.done_roles == ("implementer",)
    assert state.current_step == 2
    assert state.escalation is not None and state.escalation.role == "tester"


# --- the terminal comment --------------------------------------------------


def a_state(status: RunStatus, *results: AgentResult, **fields) -> RunState:
    """A Run that got as far as `results` and ended in `status`."""
    return RunState(
        issue=12,
        plan=Plan(summary="Add a retry to the loader."),
        roster=Roster((IMPLEMENTER,)),
        results=results,
        status=status,
        **fields,
    )


IMPLEMENTED = AgentResult("implementer", ModelTier.STANDARD, Outcome.COMPLETED, "Added the retry.")
TESTED = AgentResult("tester", ModelTier.STANDARD, Outcome.COMPLETED, "pytest: 24 passed.")


def test_a_finished_run_ends_by_stating_where_it_got_to():
    state = a_state(
        RunStatus.AWAITING_SIGNOFF,
        IMPLEMENTED,
        TESTED,
        pull_request="https://github.com/acme/pipelines/pull/13",
    )

    body = render_terminal_comment(state)

    assert "### Run complete" in body
    assert "`agentforge:awaiting-signoff`" in body
    assert "**Escalated:** no" in body
    assert "implementer, tester" in body
    assert "/pull/13" in body


def test_a_halted_run_names_the_escalation_and_the_step_it_stopped_on():
    """ADR-0003 makes escalation frequency the signal of Orchestrator quality,
    which is only true if a person can count it off the tracker."""
    escalated = AgentResult(
        "tester", ModelTier.STANDARD, Outcome.ESCALATED, "There is no suite here."
    )

    body = render_terminal_comment(a_state(RunStatus.HALTED, IMPLEMENTED, escalated))

    assert "### Run halted" in body
    assert "`agentforge:halted`" in body
    assert "**Escalated:** yes, at step 2 (tester)" in body
    assert "There is no suite here." in body
    assert "agentforge implement 12" in body


def test_a_failed_run_says_it_failed_rather_than_that_it_escalated():
    failed = AgentResult("tester", ModelTier.STANDARD, Outcome.FAILED, "claude: rate limited")

    body = render_terminal_comment(a_state(RunStatus.FAILED, IMPLEMENTED, failed))

    assert "### Run failed" in body
    assert "`agentforge:failed`" in body
    assert "**Escalated:** no" in body
    assert "claude: rate limited" in body


def test_a_suspended_run_reads_differently_from_a_halted_one():
    """Suspended is a Gate a Run can still clear; halted is a human's move. A
    person reading the Issue has to be able to tell which one they are looking at."""
    suspended = render_terminal_comment(a_state(RunStatus.SUSPENDED, IMPLEMENTED))
    halted = render_terminal_comment(
        a_state(
            RunStatus.HALTED,
            AgentResult("implementer", ModelTier.STANDARD, Outcome.ESCALATED, "wrong file"),
        )
    )

    assert "### Run suspended" in suspended
    assert "`agentforge:suspended`" in suspended
    assert "**Escalated:** no" in suspended
    assert "Gate" in suspended

    assert "### Run halted" in halted
    assert "`agentforge:halted`" in halted
    assert suspended != halted


def test_a_run_that_is_still_going_has_no_ending_to_post():
    with pytest.raises(IssueError, match="has not ended"):
        render_terminal_comment(a_state(RunStatus.RUNNING, IMPLEMENTED))


def test_the_terminal_comment_is_not_read_back_as_an_agent_result():
    """It ends the Run Log; a Run that resumed past it would count it as a Step."""
    body = render_terminal_comment(a_state(RunStatus.HALTED, IMPLEMENTED))

    assert parse_run_log(Issue(12, "t", BODY, comments=(Comment("bot", body),))) == ()


def test_a_run_state_costs_one_gh_call_and_touches_no_local_state(tmp_path):
    """ADR-0002 has no run directory and no database, so the whole traffic of
    reading a Run's state is one `gh issue view` and the whole input is its JSON."""
    stopped = render_run_log_comment(
        AgentResult("implementer", ModelTier.STANDARD, Outcome.ESCALATED, "wrong file")
    )
    runner = FakeRunner().script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(
            labels=[{"name": "agentforge:halted"}],
            comments=[{"author": {"login": "bot"}, "body": stopped}],
        ),
    )

    state = run_state(GitHub(runner, tmp_path).read_issue(12))

    assert [call[:3] for call in runner.calls] == [("gh", "issue", "view")]
    assert list(tmp_path.iterdir()) == [], "a Run that writes state locally does not survive a laptop"
    assert state.status is RunStatus.HALTED
    assert state.current_step == 1


def test_an_issue_nobody_planned_is_refused_with_a_reason():
    issue = Issue(7, "please make the pipeline faster", "It's slow. Thanks!")

    with pytest.raises(PlanFormatError):
        run_state(issue)
