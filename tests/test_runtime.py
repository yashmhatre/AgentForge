"""Both commands, end to end, against the fake runner.

Nothing is mocked but the process boundary. Plan serialization, Roster
selection, tier resolution, branch naming, Run Log sequencing, escalation
handling, and every precondition check run as the real thing.

The Run these tests describe is the one M1 exists to prove: a Task typed in one
clone becomes an Issue, and an Issue number alone becomes a draft pull request
in another.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentforge.agents import TESTER
from agentforge.core.contracts import (
    GateEntry,
    GateVerdict,
    ModelTier,
    Outcome,
    RunStatus,
)
from agentforge.core.gates import GATES
from agentforge.core.issues import render_gate_comment
from agentforge.core.plan_format import (
    RESULT_OPEN,
    parse_issue_body,
    render_result_block,
)
from agentforge.core.runtime import Forge, RunFailed

from .fakes import FakeRunner, github_repository
from .test_agents import orchestrator_output, plan_block
from .test_contracts import a_plan

FIXTURES = Path(__file__).parent / "fixtures"
BODY = (FIXTURES / "issue_body_v1.md").read_text(encoding="utf-8")
ROOT = Path("/repo/pipelines")


def agent_says(outcome: str, summary: str, files=("src/loader.py",)) -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": render_result_block(
                {"outcome": outcome, "summary": summary, "files_changed": list(files)}
            ),
        }
    )


def issue_json(body: str = BODY, labels=("agentforge:planned",), comments=()) -> str:
    return json.dumps(
        {
            "number": 12,
            "title": "add a retry to the loader",
            "body": body,
            "url": "https://github.com/acme/pipelines/issues/12",
            "labels": [{"name": name} for name in labels],
            "comments": [{"author": {"login": "bot"}, "body": c} for c in comments],
        }
    )


def a_runner() -> FakeRunner:
    runner = github_repository(FakeRunner(), ROOT)
    runner.script("gh", "issue", "create", stdout="https://github.com/acme/pipelines/issues/12\n")
    runner.script("gh", "pr", "create", stdout="https://github.com/acme/pipelines/pull/13\n")
    runner.script("gh", "issue", "view", stdout=issue_json())
    return runner


def forge(runner: FakeRunner) -> Forge:
    return Forge(cwd=ROOT, provider="claude", runner=runner)


# --- agentforge plan -------------------------------------------------------


def test_planning_files_an_issue_a_human_can_judge_before_any_code_is_written():
    runner = a_runner()
    runner.script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    outcome = forge(runner).plan("add a retry to the loader")

    assert outcome.filed
    assert outcome.issue.number == 12
    body = runner.argument_after("--body", "gh", "issue", "create")
    assert "> add a retry to the loader" in body
    assert "| 1 | implementer | `standard` |" in body


def test_the_filed_body_is_the_body_implement_will_parse_back():
    """The two halves of ADR-0002 and ADR-0003 meeting: what is written once is
    what is read many times."""
    runner = a_runner()
    runner.script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    forge(runner).plan("add a retry to the loader")

    body = runner.argument_after("--body", "gh", "issue", "create")
    assert parse_issue_body(body).roster.names() == ("implementer",)


def test_a_freshly_filed_issue_is_labelled_planned():
    runner = a_runner()
    runner.script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    forge(runner).plan("add a retry")

    create = runner.only("gh", "issue", "create")
    assert create[create.index("--label") + 1] == "agentforge:planned"


def test_an_escalating_orchestrator_files_nothing():
    """An Issue filed from a plan the Orchestrator did not believe in is worse
    than no Issue."""
    runner = a_runner()
    runner.script(
        "claude",
        stdout=json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": render_result_block(
                    {"outcome": "escalated", "summary": "Which loader? There are three."}
                ),
            }
        ),
    )

    outcome = forge(runner).plan("fix the loader")

    assert not outcome.filed
    assert not runner.ran("gh", "issue", "create")


def test_a_tier_override_moves_the_orchestrator():
    runner = a_runner()
    runner.script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    forge(runner).plan("add a retry", tier=ModelTier.CHEAP)

    assert runner.argument_after("--model", "claude") == "haiku"


# --- agentforge implement --------------------------------------------------


def test_an_issue_number_is_the_whole_input():
    """ADR-0002's claim: no session, no working directory, no chat history
    carried from the machine that planned this."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert state.pull_request.endswith("/pull/13")
    assert [r.outcome for r in state.results] == [Outcome.COMPLETED, Outcome.COMPLETED]


def test_the_agent_works_on_a_branch_named_for_the_issue():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    assert runner.ran("git", "checkout", "-b")
    assert runner.only("git", "checkout", "-b")[3] == "agentforge/issue-12"
    assert runner.argument_after("--head", "gh", "pr", "create") == "agentforge/issue-12"


def test_each_result_is_posted_to_the_issue_before_the_run_moves_on():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    # Agent Results only: the Run's terminal comment is an ending, not a Step.
    comments = [c for c in comments_on(runner) if RESULT_OPEN in c]
    assert "### implementer — completed" in comments[0]
    assert "### tester — completed" in comments[1]
    assert all("**Model Tier:** `standard`" in comment for comment in comments)

    first_agent, second_agent = [
        index for index, call in enumerate(runner.calls) if call[0] == "claude"
    ]
    first_comment = next(
        index
        for index, call in enumerate(runner.calls)
        if call[:3] == ("gh", "issue", "comment")
    )
    assert first_agent < first_comment < second_agent


def test_a_denied_feature_run_records_the_tester_denial_and_halts():
    runner = a_runner()
    runner.script("claude", stdout=agent_says("completed", "Implemented the change."))

    state = forge(runner).implement(12)

    assert [result.role for result in state.results] == ["implementer", "tester"]
    assert state.results[-1].outcome is Outcome.ESCALATED
    assert "denied" in state.results[-1].summary
    assert len(runner.matching("claude")) == 1
    assert not runner.ran("gh", "pr", "create")


def test_opening_a_denied_run_resumes_at_the_tester():
    implemented = render_result_block(
        {
            "role": "implementer",
            "tier": "standard",
            "outcome": "completed",
            "summary": "implemented",
        }
    )
    denied = render_result_block(
        {
            "role": "tester",
            "tier": "standard",
            "outcome": "escalated",
            "summary": "command execution denied",
        }
    )
    runner = a_runner()
    runner.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(
            # The label an older AgentForge applied: still read, no longer written.
            labels=("agentforge:escalated",),
            comments=(implemented, denied),
        ),
    )
    runner.script("git", "status", "--porcelain", stdout=["", " M tests/test_loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "pytest: 24 passed"))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert len(runner.matching("claude")) == 1
    assert "You are the Tester" in runner.argument_after("-p", "claude")


def test_the_run_ends_in_a_draft_pull_request_and_never_a_merge():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    assert "--draft" in runner.only("gh", "pr", "create")
    assert not runner.ran("gh", "pr", "merge")
    body = runner.argument_after("--body", "gh", "pr", "create")
    assert "Closes #12." in body


def test_the_agent_runs_in_the_repository_root_rather_than_the_invocation_directory():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "done"))

    Forge(cwd=ROOT / "src" / "deep", provider="claude", runner=runner).implement(12)

    index = next(i for i, call in enumerate(runner.calls) if call[0] == "claude")
    assert runner.cwds[index] == str(ROOT)


def test_a_run_log_entry_names_the_tier_it_cost():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "done"))

    forge(runner).implement(12)

    comments = [c[c.index("--body") + 1] for c in runner.matching("gh", "issue", "comment")]
    assert any("**Model Tier:** `standard`" in c for c in comments)


def test_a_role_can_be_moved_up_a_tier_for_a_task_the_user_knows_is_hard():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "done"))

    forge(runner).implement(12, tier_overrides={"implementer": ModelTier.DEEP})

    assert runner.argument_after("--model", "claude") == "opus"


# --- every Run ends by saying how it ended ---------------------------------


def comments_on(runner: FakeRunner) -> list[str]:
    return [c[c.index("--body") + 1] for c in runner.matching("gh", "issue", "comment")]


def test_a_run_that_reaches_sign_off_ends_with_a_terminal_comment():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    last = comments_on(runner)[-1]
    assert "### Run complete" in last
    assert "**Escalated:** no" in last
    assert "/pull/13" in last


def labels_applied(runner: FakeRunner) -> list[str]:
    return [
        c[c.index("--add-label") + 1]
        for c in runner.matching("gh", "issue", "edit")
        if "--add-label" in c
    ]


def test_a_run_moves_from_planned_through_running_to_how_it_ended():
    """The transitions, read where a person reads them: the Issue's labels."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    assert labels_applied(runner) == ["agentforge:running", "agentforge:awaiting-signoff"]
    removed = [
        c[c.index("--remove-label") + 1]
        for c in runner.matching("gh", "issue", "edit")
        if "--remove-label" in c
    ]
    assert removed == ["agentforge:planned", "agentforge:running"], "one status label at a time"


def test_a_run_that_halts_ends_labelled_halted_rather_than_running():
    runner = a_runner()
    runner.script("claude", stdout=agent_says("escalated", "Step s1 names a file that is gone.", ()))

    forge(runner).implement(12, allow_commands=True)

    assert labels_applied(runner) == ["agentforge:running", "agentforge:halted"]


def test_a_halted_run_ends_with_a_terminal_comment_naming_the_step_that_escalated():
    """#7's reason for existing: escalation frequency has to be countable from
    the tracker, which needs the Run to say so on its way out."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script(
        "claude",
        stdout=[
            agent_says("completed", "Implemented the change."),
            agent_says("escalated", "There is no suite in this repository.", ()),
        ],
    )

    forge(runner).implement(12, allow_commands=True)

    last = comments_on(runner)[-1]
    assert "### Run halted" in last
    assert "**Escalated:** yes, at step 2 (tester)" in last


def test_a_failed_run_ends_with_a_terminal_comment_too():
    runner = a_runner()
    runner.script("claude", stdout="", stderr="rate limited", returncode=1)

    forge(runner).implement(12)

    last = comments_on(runner)[-1]
    assert "### Run failed" in last
    assert "**Escalated:** no" in last


def test_a_run_with_nothing_left_to_do_says_nothing():
    """The terminal comment ends a Run. An `implement` that finds every Step
    behind it never started one, and must not post a second ending."""
    implementer = render_result_block(
        {"role": "implementer", "tier": "standard", "outcome": "completed", "summary": "done"}
    )
    tester = render_result_block(
        {"role": "tester", "tier": "standard", "outcome": "completed", "summary": "passed"}
    )
    runner = a_runner()
    runner.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(labels=("agentforge:awaiting-signoff",), comments=(implementer, tester)),
    )

    forge(runner).implement(12)

    assert comments_on(runner) == []


# --- escalation ------------------------------------------------------------


def test_an_escalation_halts_the_run_before_a_pull_request_exists():
    runner = a_runner()
    runner.script("claude", stdout=agent_says("escalated", "Step s1 names a file that is gone.", ()))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.HALTED
    assert not runner.ran("gh", "pr", "create")
    assert not runner.ran("git", "push")


def test_an_escalation_labels_the_issue_and_states_the_reason():
    runner = a_runner()
    runner.script("claude", stdout=agent_says("escalated", "Step s1 names a file that is gone.", ()))

    forge(runner).implement(12)

    labels = [
        c[c.index("--add-label") + 1]
        for c in runner.matching("gh", "issue", "edit")
        if "--add-label" in c
    ]
    assert "agentforge:halted" in labels
    comments = comments_on(runner)
    assert any("Step s1 names a file that is gone." in c for c in comments)


def test_an_escalation_stops_the_run_before_the_next_step_is_invoked():
    """Halting is worth nothing if the rest of the Roster runs anyway: the
    Steps after an Escalation were planned against a plan now known to be wrong."""
    runner = a_runner()
    runner.script("claude", stdout=agent_says("escalated", "Step s1 names a file that is gone.", ()))

    forge(runner).implement(12, allow_commands=True)

    assert len(runner.matching("claude")) == 1, "the tester ran on a plan known to be wrong"
    entries = [c for c in comments_on(runner) if RESULT_OPEN in c]
    assert len(entries) == 1
    assert "### implementer — escalated (step 1 of 2)" in entries[0]


def test_the_escalating_roles_own_comment_names_its_step_and_the_mismatch():
    """The terminal comment counts the escalation; this is the entry that says
    what did not match, and #8 wants both halves findable in one place."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script(
        "claude",
        stdout=[
            agent_says("completed", "Implemented the change."),
            agent_says("escalated", "Step s2 names tests/test_loader.py; it is not here.", ()),
        ],
    )

    forge(runner).implement(12, allow_commands=True)

    entries = [c for c in comments_on(runner) if RESULT_OPEN in c]
    assert "### implementer — completed (step 1 of 2)" in entries[0]
    assert "### tester — escalated (step 2 of 2)" in entries[1]
    assert "Step s2 names tests/test_loader.py; it is not here." in entries[1]


def test_a_hand_edited_plan_naming_a_module_that_is_not_there_halts_rather_than_improvising():
    """#8's last criterion, end to end. A human edits the frozen block to name a
    module that does not exist; the Role is handed exactly that and reports the
    mismatch, and the Run stops with a label instead of a pull request full of
    invented work."""
    hand_edited = BODY.replace("src/loader.py", "src/nowhere/loader_v2.py")
    runner = a_runner()
    runner.script("gh", "issue", "view", stdout=issue_json(hand_edited))
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script(
        "claude",
        stdout=agent_says(
            "escalated", "Step s1 names src/nowhere/loader_v2.py, which is not here.", ()
        ),
    )

    state = forge(runner).implement(12, allow_commands=True)

    assert "src/nowhere/loader_v2.py" in runner.argument_after("-p", "claude")
    assert state.status is RunStatus.HALTED
    assert labels_applied(runner)[-1] == "agentforge:halted"

    entry = [c for c in comments_on(runner) if RESULT_OPEN in c][-1]
    assert "### implementer — escalated (step 1 of 2)" in entry
    assert "src/nowhere/loader_v2.py" in entry

    assert not runner.ran("git", "commit"), "a halted Run improvised work and committed it"
    assert not runner.ran("git", "push")
    assert not runner.ran("gh", "pr", "create")


def test_a_corrected_plan_resumes_rather_than_restarting_the_completed_steps():
    """Halting costs the remaining Steps, not the whole Run. The second Run reads
    the first Run's own Run Log back off the Issue, which is the only reason the
    completed Step is still worth anything."""
    first = a_runner()
    first.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    first.script(
        "claude",
        stdout=[
            agent_says("completed", "Implemented the change."),
            agent_says("escalated", "Step s2 names a suite that is not here.", ()),
        ],
    )

    forge(first).implement(12, allow_commands=True)
    log = comments_on(first)

    second = a_runner()
    second.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(labels=("agentforge:halted",), comments=tuple(log)),
    )
    second.script("git", "status", "--porcelain", stdout=["", " M tests/test_loader.py\n"])
    second.script("claude", stdout=agent_says("completed", "pytest: 24 passed."))

    state = forge(second).implement(12, allow_commands=True)

    # The escalated attempt stays in the log beside the completed one; only
    # completed results retire a Step.
    assert [result.role for result in state.results] == ["implementer", "tester", "tester"]
    assert state.done_roles == ("implementer", "tester")
    assert state.results[0].summary == "Implemented the change."
    assert len(second.matching("claude")) == 1, "the completed Step was run a second time"
    assert "You are the Tester" in second.argument_after("-p", "claude")
    assert state.status is RunStatus.AWAITING_SIGNOFF


def test_an_escalated_run_re_runs_the_role_that_escalated():
    """The human corrects the plan block and runs the same command again."""
    escalated = render_result_block(
        {"role": "implementer", "tier": "standard", "outcome": "escalated", "summary": "wrong file"}
    )
    runner = a_runner()
    runner.script(
        "gh", "issue", "view", stdout=issue_json(labels=("agentforge:halted",), comments=(escalated,))
    )
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Fixed now."))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert runner.ran("claude")


def test_a_finished_run_does_not_run_again():
    implementer = render_result_block(
        {"role": "implementer", "tier": "standard", "outcome": "completed", "summary": "done"}
    )
    tester = render_result_block(
        {"role": "tester", "tier": "standard", "outcome": "completed", "summary": "passed"}
    )
    runner = a_runner()
    runner.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(
            labels=("agentforge:awaiting-signoff",), comments=(implementer, tester)
        ),
    )

    state = forge(runner).implement(12)

    assert not runner.ran("claude")
    assert state.remaining == ()


def test_an_agent_that_claims_success_but_changed_nothing_fails_loudly():
    """Otherwise the Run opens an empty pull request and reports it worked."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout="")
    runner.script("claude", stdout=agent_says("completed", "All good!", ()))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.FAILED
    assert not runner.ran("gh", "pr", "create")
    assert "left no changes" in state.results[-1].summary


def test_a_failing_provider_halts_the_run():
    runner = a_runner()
    runner.script("claude", stdout="", stderr="rate limited", returncode=1)

    state = forge(runner).implement(12)

    assert state.status is RunStatus.FAILED
    assert not runner.ran("gh", "pr", "create")


# --- preconditions ---------------------------------------------------------


def test_a_directory_that_is_not_a_repository_is_refused_immediately():
    runner = FakeRunner().script("git", "rev-parse", "--show-toplevel", returncode=128)

    with pytest.raises(RunFailed, match="not inside a git repository"):
        forge(runner).plan("add a retry")

    assert not runner.ran("claude")


def test_a_repository_with_no_remote_is_refused_before_anything_is_spent():
    runner = github_repository(FakeRunner(), ROOT)
    runner.script("git", "remote", "get-url", stdout="", returncode=1)

    with pytest.raises(RunFailed, match="no `origin` remote"):
        forge(runner).plan("add a retry")

    assert not runner.ran("claude")


def test_a_remote_that_is_not_github_says_so_rather_than_failing_later():
    runner = github_repository(FakeRunner(), ROOT)
    runner.script("git", "remote", "get-url", stdout="git@ssh.dev.azure.com:v3/acme/pipelines\n")

    with pytest.raises(RunFailed, match="not GitHub"):
        forge(runner).plan("add a retry")


def test_a_missing_coding_agent_cli_names_the_binary_to_install():
    runner = a_runner().uninstall("claude")

    with pytest.raises(RunFailed, match="claude"):
        forge(runner).plan("add a retry")


def test_a_missing_gh_names_the_binary_to_install():
    runner = a_runner().uninstall("gh")

    with pytest.raises(RunFailed, match="gh"):
        forge(runner).plan("add a retry")


def test_a_dirty_working_tree_is_refused_rather_than_swept_into_the_commit():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=" M notes.md\n")

    with pytest.raises(RunFailed, match="uncommitted changes"):
        forge(runner).implement(12)

    assert not runner.ran("claude")


def test_an_issue_with_no_plan_block_is_refused_with_a_reason():
    runner = a_runner()
    runner.script("gh", "issue", "view", stdout=issue_json(body="please make this faster"))

    with pytest.raises(RunFailed, match="cannot be implemented"):
        forge(runner).implement(12)


def test_an_issue_naming_an_unbuilt_role_says_which_one():
    body = plan_block([{"role": "security"}], plan=a_plan())
    runner = a_runner()
    runner.script("gh", "issue", "view", stdout=issue_json(body=body))

    with pytest.raises(RunFailed, match="security"):
        forge(runner).implement(12)


# --- the Workflow drives the Run -------------------------------------------


def _body_naming_workflow(name: str) -> str:
    """The recorded body with its plan block's Workflow swapped."""
    return BODY.replace('"workflow": "feature"', f'"workflow": "{name}"')


def test_a_workflow_that_does_not_exist_is_refused_before_any_agent_is_invoked():
    runner = a_runner()
    runner.script("gh", "issue", "view", stdout=issue_json(_body_naming_workflow("nonesuch")))
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])

    with pytest.raises(RunFailed, match="nonesuch"):
        forge(runner).implement(12)

    assert not runner.ran("claude"), "a bad Workflow name must cost nothing"


def test_a_workflow_with_no_steps_is_refused_before_any_agent_is_invoked():
    """`bugfix` and `review` ship empty until #14. Running one is a no-op, not a Run."""
    runner = a_runner()
    runner.script("gh", "issue", "view", stdout=issue_json(_body_naming_workflow("review")))
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])

    with pytest.raises(RunFailed, match="review"):
        forge(runner).implement(12)

    assert not runner.ran("claude")


def test_an_issue_filed_before_workflows_existed_still_runs():
    """The plan block's `workflow` key is additive: absent means `feature`."""
    runner = a_runner()
    older = BODY.replace(',\n  "workflow": "feature"', "")
    runner.script("gh", "issue", "view", stdout=issue_json(older))
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    assert runner.ran("claude")
    assert "--draft" in runner.only("gh", "pr", "create")


def test_a_step_tier_override_moves_the_role_without_a_command_line_flag(tmp_path, monkeypatch):
    """The step's `tier:` is the second of the four fields to become real."""
    (tmp_path / "feature.yaml").write_text(
        "name: feature\nsteps:\n  - role: implementer\n    tier: deep\n", encoding="utf-8"
    )
    monkeypatch.setattr("agentforge.core.workflow.WORKFLOWS_ROOT", tmp_path)

    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "done"))

    forge(runner).implement(12)

    assert runner.argument_after("--model", "claude") == "opus"


def test_a_command_line_tier_still_beats_the_step_override(tmp_path, monkeypatch):
    """Precedence: explicit user request, then the step, then the Role's ADR-0004 default."""
    (tmp_path / "feature.yaml").write_text(
        "name: feature\nsteps:\n  - role: implementer\n    tier: deep\n", encoding="utf-8"
    )
    monkeypatch.setattr("agentforge.core.workflow.WORKFLOWS_ROOT", tmp_path)

    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "done"))

    forge(runner).implement(12, tier_overrides={"implementer": ModelTier.CHEAP})

    assert runner.argument_after("--model", "claude") == "haiku"


def test_a_tester_step_override_leaves_the_roles_default_unchanged(tmp_path, monkeypatch):
    (tmp_path / "feature.yaml").write_text(
        "name: feature\nsteps:\n  - role: implementer\n  - role: tester\n    tier: deep\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("agentforge.core.workflow.WORKFLOWS_ROOT", tmp_path)
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "done"))

    forge(runner).implement(12, allow_commands=True)

    assert [call[call.index("--model") + 1] for call in runner.matching("claude")] == [
        "sonnet",
        "opus",
    ]
    comments = [
        call[call.index("--body") + 1]
        for call in runner.matching("gh", "issue", "comment")
        if "### tester" in call[call.index("--body") + 1]
    ]
    assert "**Model Tier:** `deep`" in comments[0]
    assert TESTER.tier is ModelTier.STANDARD


def test_a_definition_naming_an_unrunnable_role_costs_no_provider_call(tmp_path, monkeypatch):
    """#3 puts the bar at "before any Provider is invoked" — `gh` has necessarily run,
    since the Workflow name comes from the Issue."""
    (tmp_path / "feature.yaml").write_text(
        "name: feature\nsteps:\n  - role: dramaturge\n", encoding="utf-8"
    )
    monkeypatch.setattr("agentforge.core.workflow.WORKFLOWS_ROOT", tmp_path)

    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])

    with pytest.raises(RunFailed, match="dramaturge"):
        forge(runner).implement(12)

    assert not runner.ran("claude")
    assert not runner.ran("git", "checkout", "-b"), "a bad definition must not touch the repo"


# --- Gates hold the Run ----------------------------------------------------


HUMAN_GATED = (
    "name: feature\nsteps:\n  - role: implementer\n    gate: human\n  - role: tester\n"
)


def _workflow(tmp_path, monkeypatch, text: str) -> None:
    """A definition of the Run's own making, since only `feature` ships steps."""
    (tmp_path / "feature.yaml").write_text(text, encoding="utf-8")
    monkeypatch.setattr("agentforge.core.workflow.WORKFLOWS_ROOT", tmp_path)


def entries_on(runner: FakeRunner) -> list[str]:
    """Run Log comments that are an Agent Result, in order."""
    return [c for c in comments_on(runner) if RESULT_OPEN in c]


def _blocks_once(invalidates: str = ""):
    """A Gate that blocks the first time it is asked and clears afterwards.

    Registered by a test rather than shipped: what the runtime does with a
    verdict must not depend on which kinds happen to exist.
    """

    def check(context):
        if any(entry.blocked for entry in context.verdicts):
            return GateEntry("", GateVerdict.CLEARED, summary="cleared")
        return GateEntry(
            "", GateVerdict.BLOCKED, invalidates=invalidates, summary="not yet"
        )

    return check


def test_a_step_followed_by_a_human_gate_suspends_the_run_rather_than_failing_it(
    tmp_path, monkeypatch
):
    _workflow(tmp_path, monkeypatch, HUMAN_GATED)
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.SUSPENDED
    assert len(runner.matching("claude")) == 1, "the Step after the Gate ran anyway"
    assert not runner.ran("gh", "pr", "create")


def test_a_suspended_run_is_labelled_so_a_stalled_run_is_not_a_crashed_one(
    tmp_path, monkeypatch
):
    _workflow(tmp_path, monkeypatch, HUMAN_GATED)
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    assert labels_applied(runner) == ["agentforge:running", "agentforge:suspended"]
    last = comments_on(runner)[-1]
    assert "### Run suspended" in last
    assert "**Waiting on:** the `human` Gate after step 1" in last


def test_the_gate_that_stopped_the_run_says_so_in_the_run_log(tmp_path, monkeypatch):
    _workflow(tmp_path, monkeypatch, HUMAN_GATED)
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    gate = [c for c in comments_on(runner) if "Gate —" in c]
    assert len(gate) == 1
    assert "### human Gate — blocked (after step 1 of 2)" in gate[0]


def test_a_suspended_run_leaves_its_work_committed_on_the_branch(tmp_path, monkeypatch):
    """A Gate the human has to clear is a Gate the human has to see the work for
    — and the next invocation refuses to start on a dirty tree, so a Run that
    suspended without committing could never be resumed."""
    _workflow(tmp_path, monkeypatch, HUMAN_GATED)
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    assert runner.ran("git", "commit")
    assert runner.only("git", "push")[-1] == "agentforge/issue-12"
    assert not runner.ran("gh", "pr", "create"), "a suspended Run is not a finished one"


def test_a_second_invocation_resumes_from_the_suspension_point(tmp_path, monkeypatch):
    """#9's criterion that matters: two separate Runs, with only the Issue
    between them. The second one reads the first one's Run Log off the Issue and
    starts at the Step the Gate stopped in front of."""
    _workflow(tmp_path, monkeypatch, HUMAN_GATED)

    first = a_runner()
    first.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    first.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    suspended = forge(first).implement(12, allow_commands=True)
    log = comments_on(first)

    second = a_runner()
    second.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(labels=("agentforge:suspended",), comments=tuple(log)),
    )
    second.script("git", "status", "--porcelain", stdout=["", " M tests/test_loader.py\n"])
    second.script("claude", stdout=agent_says("completed", "pytest: 24 passed."))

    resumed = forge(second).implement(12, allow_commands=True)

    assert suspended.status is RunStatus.SUSPENDED
    assert resumed.status is RunStatus.AWAITING_SIGNOFF
    assert len(second.matching("claude")) == 1, "the completed Step was run a second time"
    assert "You are the Tester" in second.argument_after("-p", "claude")
    assert resumed.done_roles == ("implementer", "tester")


def test_resuming_past_a_gate_duplicates_no_run_log_comment(tmp_path, monkeypatch):
    """The Run Log is replayed by every later Run, so a Step recorded twice is a
    Step counted twice."""
    _workflow(tmp_path, monkeypatch, HUMAN_GATED)

    first = a_runner()
    first.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    first.script("claude", stdout=agent_says("completed", "Added a bounded retry."))
    forge(first).implement(12, allow_commands=True)

    second = a_runner()
    second.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(labels=("agentforge:suspended",), comments=tuple(comments_on(first))),
    )
    second.script("git", "status", "--porcelain", stdout=["", " M tests/test_loader.py\n"])
    second.script("claude", stdout=agent_says("completed", "pytest: 24 passed."))
    forge(second).implement(12, allow_commands=True)

    steps = [c.splitlines()[0] for c in entries_on(first) + entries_on(second)]
    assert steps == [
        "### implementer — completed (step 1 of 2)",
        "### tester — completed (step 2 of 2)",
    ]
    assert [c for c in comments_on(second) if "Gate —" in c] == [], (
        "a Gate that cleared posted an entry saying the Run carried on"
    )


def test_a_gate_blocking_on_a_roles_output_re_runs_that_step_on_resume(
    tmp_path, monkeypatch
):
    """The amendment's rule, end to end. Without it the human fixes the code, the
    Gate re-reads the verdict drawn from the code before the fix, and the Run
    never moves."""
    monkeypatch.setitem(GATES, "human", _blocks_once(invalidates="implementer"))
    _workflow(tmp_path, monkeypatch, HUMAN_GATED)

    first = a_runner()
    first.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    first.script("claude", stdout=agent_says("completed", "Added a bounded retry."))
    suspended = forge(first).implement(12, allow_commands=True)

    second = a_runner()
    second.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(labels=("agentforge:suspended",), comments=tuple(comments_on(first))),
    )
    second.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    second.script("claude", stdout=agent_says("completed", "Fixed and re-run."))

    resumed = forge(second).implement(12, allow_commands=True)

    assert suspended.done_roles == (), "the Gate did not mark its Step for re-run"
    assert suspended.current_step == 1
    prompts = [call[call.index("-p") + 1] for call in second.matching("claude")]
    assert len(prompts) == 2, "the invalidated Step did not run again"
    assert "You are the Implementer" in prompts[0]
    assert "You are the Tester" in prompts[1]
    assert resumed.status is RunStatus.AWAITING_SIGNOFF


def test_an_errored_gate_halts_the_run_rather_than_suspending_it(tmp_path, monkeypatch):
    """A Gate that cannot evaluate has nothing to clear, so suspending it would
    invite a resume that suspends again forever."""
    monkeypatch.setitem(
        GATES,
        "human",
        lambda context: GateEntry("", GateVerdict.ERRORED, summary="no reviewer configured"),
    )
    _workflow(tmp_path, monkeypatch, HUMAN_GATED)

    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.HALTED
    assert labels_applied(runner) == ["agentforge:running", "agentforge:halted"]
    assert not runner.ran("gh", "pr", "create")
    assert "no reviewer configured" in next(c for c in comments_on(runner) if "Gate —" in c)


def test_a_gate_kind_this_version_cannot_evaluate_halts_rather_than_passing_quietly(
    tmp_path, monkeypatch
):
    """`security` is registered so a Workflow may name it, and #11 has not built
    it. A declared check that silently never runs is the worst of the options."""
    _workflow(
        tmp_path,
        monkeypatch,
        "name: feature\nsteps:\n  - role: implementer\n    gate: security\n",
    )
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.HALTED
    assert not runner.ran("gh", "pr", "create")


def test_a_gated_workflow_still_ends_in_a_draft_pull_request_and_never_a_merge(
    tmp_path, monkeypatch
):
    """Sign-off is terminal and not configurable: no arrangement of Gates makes a
    Workflow merge."""
    _workflow(tmp_path, monkeypatch, HUMAN_GATED)
    runner = a_runner()
    runner.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(
            labels=("agentforge:suspended",),
            comments=(
                render_result_block(
                    {
                        "role": "implementer",
                        "tier": "standard",
                        "outcome": "completed",
                        "summary": "done",
                    }
                ),
                render_gate_comment(GateEntry("human", GateVerdict.BLOCKED, step=1), of=2),
            ),
        ),
    )
    runner.script("git", "status", "--porcelain", stdout=["", " M tests/test_loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "pytest: 24 passed."))

    forge(runner).implement(12, allow_commands=True)

    assert "--draft" in runner.only("gh", "pr", "create")
    assert not runner.ran("gh", "pr", "merge")


def test_a_gate_after_the_last_step_suspends_before_the_pull_request(
    tmp_path, monkeypatch
):
    """A Gate is a Gate wherever it sits. One after the final Step holds the Run
    in front of Sign-off, and the resume opens the pull request."""
    _workflow(
        tmp_path,
        monkeypatch,
        "name: feature\nsteps:\n  - role: implementer\n    gate: human\n",
    )

    first = a_runner()
    first.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    first.script("claude", stdout=agent_says("completed", "Added a bounded retry."))
    suspended = forge(first).implement(12, allow_commands=True)

    second = a_runner()
    second.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(labels=("agentforge:suspended",), comments=tuple(comments_on(first))),
    )
    # The work is committed already: this Run has a Gate to clear and no Step to run.
    second.script("git", "status", "--porcelain", stdout="")
    resumed = forge(second).implement(12, allow_commands=True)

    assert suspended.status is RunStatus.SUSPENDED
    assert not first.ran("gh", "pr", "create")
    assert not second.ran("claude"), "a Run with no outstanding Step invoked a Role"
    assert resumed.status is RunStatus.AWAITING_SIGNOFF
    assert "--draft" in second.only("gh", "pr", "create")


def test_a_suspended_run_whose_gate_is_gone_finishes_rather_than_staying_suspended(
    tmp_path, monkeypatch
):
    """Suspended means a Run that can still go on. Somebody who removes the Gate
    from the definition has cleared it in the bluntest way there is."""
    _workflow(tmp_path, monkeypatch, "name: feature\nsteps:\n  - role: implementer\n")
    runner = a_runner()
    runner.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(
            labels=("agentforge:suspended",),
            comments=(
                render_result_block(
                    {
                        "role": "implementer",
                        "tier": "standard",
                        "outcome": "completed",
                        "summary": "done",
                    }
                ),
                render_gate_comment(GateEntry("human", GateVerdict.BLOCKED, step=1), of=1),
            ),
        ),
    )
    runner.script("git", "status", "--porcelain", stdout="")

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert not runner.ran("claude")


# --- the test-suite Gate, from inside a Run ---------------------------------


TESTS_GATED = (
    "name: feature\nsteps:\n  - role: implementer\n  - role: tester\n    gate: tests\n"
)

FAILING_SUITE = "E   assert 3 == 4\n1 failed, 23 passed in 0.42s"


def test_a_failing_suite_suspends_the_run_rather_than_opening_a_pull_request(
    tmp_path, monkeypatch
):
    """The point of the Gate: a Run does not reach a human's pull request having
    broken the build. Suspended rather than halted, because the commit that fixes
    the suite clears it."""
    _workflow(tmp_path, monkeypatch, TESTS_GATED)
    runner = a_runner().install("pytest")
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))
    runner.script("pytest", stdout=FAILING_SUITE, returncode=1)

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.SUSPENDED
    assert labels_applied(runner) == ["agentforge:running", "agentforge:suspended"]
    assert not runner.ran("gh", "pr", "create")


def test_the_failing_suites_own_output_reaches_the_run_log(tmp_path, monkeypatch):
    """On the Issue rather than in the terminal of whoever started the Run —
    which is the whole of ADR-0002's claim, applied to a Gate."""
    _workflow(tmp_path, monkeypatch, TESTS_GATED)
    runner = a_runner().install("pytest")
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))
    runner.script("pytest", stdout=FAILING_SUITE, returncode=1)

    forge(runner).implement(12, allow_commands=True)

    gate = next(c for c in comments_on(runner) if "Gate —" in c)
    assert "### tests Gate — blocked (after step 2 of 2)" in gate
    assert "1 failed, 23 passed in 0.42s" in gate
    assert "**Waiting on:** the `tests` Gate after step 2" in comments_on(runner)[-1]


def test_a_suite_that_cannot_be_run_halts_the_run_and_is_not_a_failing_suite(
    tmp_path, monkeypatch
):
    """Two different endings, and the Issue says which. A suite that never ran
    has reported nothing about the code, so there is nothing for a later Run to
    clear by waiting — and the label a human sees is halted, not suspended."""
    _workflow(tmp_path, monkeypatch, TESTS_GATED)
    runner = a_runner().uninstall("pytest")
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.HALTED
    assert labels_applied(runner) == ["agentforge:running", "agentforge:halted"]
    assert "pytest" in next(c for c in comments_on(runner) if "Gate —" in c)


def test_a_resumed_run_re_runs_the_suite_and_re_runs_no_step(tmp_path, monkeypatch):
    """Two invocations with only the Issue between them. The Gate judged nobody's
    output, so every Step stays behind the Run and what has to happen again is
    the suite — which is now green, so the Run goes on to Sign-off."""
    _workflow(tmp_path, monkeypatch, TESTS_GATED)

    first = a_runner().install("pytest")
    first.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    first.script("claude", stdout=agent_says("completed", "Added a bounded retry."))
    first.script("pytest", stdout=FAILING_SUITE, returncode=1)
    suspended = forge(first).implement(12, allow_commands=True)

    second = a_runner().install("pytest")
    second.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(labels=("agentforge:suspended",), comments=tuple(comments_on(first))),
    )
    # The human committed the fix before re-running; a Run refuses a dirty tree.
    second.script("git", "status", "--porcelain", stdout="")
    second.script("pytest", stdout="24 passed in 0.44s")

    resumed = forge(second).implement(12, allow_commands=True)

    assert suspended.done_roles == ("implementer", "tester"), (
        "the test-suite Gate un-retired a Step, which is the ADR-0008 deadlock"
    )
    assert not second.ran("claude"), "a Gate that judged no Role re-ran one anyway"
    assert second.ran("pytest"), "the Run read the old verdict back instead of re-running"
    assert resumed.status is RunStatus.AWAITING_SIGNOFF
    assert "--draft" in second.only("gh", "pr", "create")


def test_the_runtime_names_no_role():
    """#4: adding a seventh Role must not require editing the engine.

    Asserted against the source because that is where the property lives. A
    `RUNNERS` lookup keyed by the Workflow step is the only dispatch there is.
    """
    source = (
        Path(__file__).parent.parent / "src" / "agentforge" / "core" / "runtime.py"
    ).read_text(encoding="utf-8")

    for role in ("implementer", "tester", "reviewer", "security", "architect"):
        assert role not in source.lower(), f"runtime.py names the {role!r} Role"
