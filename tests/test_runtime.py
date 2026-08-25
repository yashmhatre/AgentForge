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

from agentforge.core.contracts import ModelTier, Outcome, RunStatus
from agentforge.core.plan_format import parse_issue_body, render_result_block
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

    state = forge(runner).implement(12)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert state.pull_request.endswith("/pull/13")
    assert [r.outcome for r in state.results] == [Outcome.COMPLETED]


def test_the_agent_works_on_a_branch_named_for_the_issue():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12)

    assert runner.ran("git", "checkout", "-b")
    assert runner.only("git", "checkout", "-b")[3] == "agentforge/issue-12"
    assert runner.argument_after("--head", "gh", "pr", "create") == "agentforge/issue-12"


def test_each_result_is_posted_to_the_issue_before_the_run_moves_on():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12)

    comments = [c[c.index("--body") + 1] for c in runner.matching("gh", "issue", "comment")]
    assert any("### implementer — completed" in c for c in comments)
    assert any("Added a bounded retry." in c for c in comments)


def test_the_run_ends_in_a_draft_pull_request_and_never_a_merge():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12)

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


# --- escalation ------------------------------------------------------------


def test_an_escalation_halts_the_run_before_a_pull_request_exists():
    runner = a_runner()
    runner.script("claude", stdout=agent_says("escalated", "Step s1 names a file that is gone.", ()))

    state = forge(runner).implement(12)

    assert state.status is RunStatus.ESCALATED
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
    assert "agentforge:escalated" in labels
    comments = [c[c.index("--body") + 1] for c in runner.matching("gh", "issue", "comment")]
    assert any("Step s1 names a file that is gone." in c for c in comments)


def test_an_escalated_run_re_runs_the_role_that_escalated():
    """The human corrects the plan block and runs the same command again."""
    escalated = render_result_block(
        {"role": "implementer", "tier": "standard", "outcome": "escalated", "summary": "wrong file"}
    )
    runner = a_runner()
    runner.script(
        "gh", "issue", "view", stdout=issue_json(labels=("agentforge:escalated",), comments=(escalated,))
    )
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Fixed now."))

    state = forge(runner).implement(12)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert runner.ran("claude")


def test_a_finished_run_does_not_run_again():
    completed = render_result_block(
        {"role": "implementer", "tier": "standard", "outcome": "completed", "summary": "done"}
    )
    runner = a_runner()
    runner.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(labels=("agentforge:awaiting-signoff",), comments=(completed,)),
    )

    state = forge(runner).implement(12)

    assert not runner.ran("claude")
    assert state.remaining == ()


def test_an_agent_that_claims_success_but_changed_nothing_fails_loudly():
    """Otherwise the Run opens an empty pull request and reports it worked."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout="")
    runner.script("claude", stdout=agent_says("completed", "All good!", ()))

    state = forge(runner).implement(12)

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
    body = plan_block([{"role": "tester"}], plan=a_plan())
    runner = a_runner()
    runner.script("gh", "issue", "view", stdout=issue_json(body=body))

    with pytest.raises(RunFailed, match="tester"):
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

    forge(runner).implement(12)

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
