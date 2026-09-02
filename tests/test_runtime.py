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

from agentforge_framework.agents import TESTER
from agentforge_framework.core import runtime as runtime_module
from agentforge_framework.core.contracts import (
    GateEntry,
    GateVerdict,
    ModelTier,
    Outcome,
    RunStatus,
)
from agentforge_framework.core.gates import GATES
from agentforge_framework.core.issues import render_gate_comment
from agentforge_framework.core.plan_format import (
    RESULT_OPEN,
    parse_issue_body,
    render_result_block,
)
from agentforge_framework.core.runtime import Forge, RunFailed

from .fakes import FakeRunner, github_repository
from .test_agents import (
    no_more_questions,
    orchestrator_output,
    pipeline,
    plan_block,
)
from .test_contracts import a_plan

FIXTURES = Path(__file__).parent / "fixtures"
BODY = (FIXTURES / "issue_body_v1.md").read_text(encoding="utf-8")
ROOT = Path("/repo/pipelines")


def agent_says(outcome: str, summary: str, files=("src/loader.py",), findings=()) -> str:
    payload = {"outcome": outcome, "summary": summary, "files_changed": list(files)}
    if findings:
        payload["findings"] = list(findings)
    return json.dumps(
        {"type": "result", "is_error": False, "result": render_result_block(payload)}
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


def _yes(slices) -> bool:
    """The human approving the breakdown. `--yes` is this, and so is a terminal
    where somebody typed y; nothing is filed without one (ADR-0021)."""
    return True


def recorded_claude_run() -> str:
    """A real `claude` envelope, so a Run is priced by what a CLI actually said."""
    return (FIXTURES / "claude_completed.json").read_text(encoding="utf-8")


# --- agentforge plan -------------------------------------------------------


def test_planning_files_an_issue_a_human_can_judge_before_any_code_is_written():
    runner = a_runner()
    runner.script("claude", stdout=pipeline())

    outcome = forge(runner).plan("add a retry to the loader", approver=_yes)

    assert len(outcome.filed) == 1
    assert outcome.filed[0].issue.number == 12
    body = runner.argument_after("--body", "gh", "issue", "create")
    assert "> Add a retry to the loader, end to end." in body
    assert "| 1 | implementer | `standard` |" in body


def test_the_filed_body_is_the_body_implement_will_parse_back():
    """The two halves of ADR-0002 and ADR-0003 meeting: what is written once is
    what is read many times."""
    runner = a_runner()
    runner.script("claude", stdout=pipeline())

    forge(runner).plan("add a retry to the loader", approver=_yes)

    body = runner.argument_after("--body", "gh", "issue", "create")
    document = parse_issue_body(body)
    assert document.roster.names() == ("implementer", "tester", "security", "reviewer")
    assert document.workflow == "feature"


def test_every_issue_is_filed_by_agentforge_and_never_by_the_skill():
    """The vendored `to-spec` and `to-tickets` skills both end by publishing to a
    tracker and applying a `ready-for-agent` label. AgentForge does the filing
    and the labelling itself, once, at the end — and ADR-0007's default-deny is
    what makes that structural: a planning pass cannot reach `gh` to file
    anything even if the skill text tells it to."""
    runner = a_runner()
    runner.script("claude", stdout=pipeline())

    forge(runner).plan("add a retry", approver=_yes)

    created = runner.matching("gh", "issue", "create")
    assert len(created) == 1
    assert "agentforge:planned" in created[0]
    assert "ready-for-agent" in created[0]
    assert all("--dangerously-skip-permissions" not in call for call in runner.matching("claude"))


def test_the_workflow_the_orchestrator_chose_survives_to_the_run():
    """#16's whole point: the Issue names the Workflow, and `implement` runs
    that one rather than the default."""
    runner = a_runner()
    runner.script("claude", stdout=pipeline(workflow="review"))

    forge(runner).plan("review the incoming branch", approver=_yes)

    body = runner.argument_after("--body", "gh", "issue", "create")
    assert "Running the `review` Workflow" in body
    assert parse_issue_body(body).workflow == "review"


def test_a_freshly_filed_issue_is_labelled_planned():
    runner = a_runner()
    runner.script("claude", stdout=pipeline())

    forge(runner).plan("add a retry", approver=_yes)

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

    outcome = forge(runner).plan("fix the loader", approver=_yes)

    assert not outcome.filed
    assert not runner.ran("gh", "issue", "create")


def test_a_tier_override_moves_the_orchestrator():
    runner = a_runner()
    runner.script("claude", stdout=pipeline())

    forge(runner).plan("add a retry", tier=ModelTier.CHEAP, approver=_yes)

    # Every stage of the pass, not just the one that writes the plan block: a
    # tier the human named moves the whole planning pass or none of it.
    invocations = runner.matching("claude")
    assert invocations
    assert all(call[call.index("--model") + 1] == "haiku" for call in invocations)


def test_planning_interviews_the_human_and_files_one_issue():
    """The interview is rounds of invocations before the plan; the Issue is
    still one Issue."""
    runner = a_runner()
    runner.script(
        "claude",
        stdout=[
            json.dumps(
                {
                    "type": "result",
                    "is_error": False,
                    "result": render_result_block(
                        {
                            "outcome": "completed",
                            "summary": "unclear",
                            "questions": ["Which loader?"],
                        }
                    ),
                }
            ),
            no_more_questions(),
            *pipeline(),
        ],
    )

    outcome = forge(runner).plan(
        "add a retry", interviewer=lambda q: "The orders loader.", approver=_yes
    )

    assert len(outcome.filed) == 1
    assert len(runner.matching("gh", "issue", "create")) == 1
    assert [e.answer for e in outcome.interview] == ["The orders loader."]


def test_what_the_interview_left_in_the_working_tree_is_reported():
    """An interview records a settled term in the project's glossary. A human
    who is not told has an unexplained diff and a Run that then refuses to
    start on it."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M CONTEXT.md\n"])
    runner.script("claude", stdout=pipeline())

    outcome = forge(runner).plan("add a retry", interviewer=lambda q: "yes", approver=_yes)

    assert outcome.touched == ("CONTEXT.md",)


def test_a_file_the_human_had_already_changed_is_not_blamed_on_the_interview():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=" M src/loader.py\n")
    runner.script("claude", stdout=pipeline())

    outcome = forge(runner).plan("add a retry", interviewer=lambda q: "yes", approver=_yes)

    assert outcome.touched == ()


def test_a_plan_with_nobody_to_interview_asks_git_nothing():
    """The single-shot path is unchanged, down to the calls it makes."""
    runner = a_runner()
    runner.script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    forge(runner).plan("add a retry")

    assert not runner.ran("git", "status")


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
    assert [r.outcome for r in state.results] == [Outcome.COMPLETED] * 4


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
    assert "### security — completed" in comments[2]
    # Each entry names what it cost: two standard Steps and one deep audit.
    assert all("**Model Tier:**" in comment for comment in comments)
    assert "**Model Tier:** `deep`" in comments[2]

    first_agent, second_agent = [
        index for index, call in enumerate(runner.calls) if call[0] == "claude"
    ][:2]
    # The Context Pack is posted before the first Agent, and every result after
    # the Agent that produced it, so the first result comment is the second one.
    first_result = [
        index
        for index, call in enumerate(runner.calls)
        if call[:3] == ("gh", "issue", "comment")
    ][1]
    assert first_agent < first_result < second_agent


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

    prompts = runner.prompts_to("claude")

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert len(prompts) == 3, "the completed implementer Step was run a second time"
    assert "You are the Tester" in prompts[0]
    assert "You are the Security Role" in prompts[1]
    assert "You are the Reviewer" in prompts[2]


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
    audit = render_result_block(
        {"role": "security", "tier": "deep", "outcome": "completed", "summary": "clean"}
    )
    reviewed = render_result_block(
        {"role": "reviewer", "tier": "cheap", "outcome": "completed", "summary": "matches"}
    )
    runner = a_runner()
    runner.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(
            labels=("agentforge:awaiting-signoff",),
            comments=(implementer, tester, audit, reviewed),
        ),
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
    assert "### implementer — escalated (step 1 of 4)" in entries[0]


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
    assert "### implementer — completed (step 1 of 4)" in entries[0]
    assert "### tester — escalated (step 2 of 4)" in entries[1]
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

    assert "src/nowhere/loader_v2.py" in runner.prompt_to("claude")
    assert state.status is RunStatus.HALTED
    assert labels_applied(runner)[-1] == "agentforge:halted"

    entry = [c for c in comments_on(runner) if RESULT_OPEN in c][-1]
    assert "### implementer — escalated (step 1 of 4)" in entry
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
    assert [result.role for result in state.results] == [
        "implementer",
        "tester",
        "tester",
        "security",
        "reviewer",
    ]
    assert state.done_roles == ("implementer", "tester", "security", "reviewer")
    assert state.results[0].summary == "Implemented the change."
    prompts = second.prompts_to("claude")
    assert len(prompts) == 3, "the completed Step was run a second time"
    assert "You are the Tester" in prompts[0]
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
    audit = render_result_block(
        {"role": "security", "tier": "deep", "outcome": "completed", "summary": "clean"}
    )
    reviewed = render_result_block(
        {"role": "reviewer", "tier": "cheap", "outcome": "completed", "summary": "matches"}
    )
    runner = a_runner()
    runner.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(
            labels=("agentforge:awaiting-signoff",),
            comments=(implementer, tester, audit, reviewed),
        ),
    )

    state = forge(runner).implement(12)

    assert not runner.ran("claude")
    assert state.remaining == ()


def test_an_agent_that_claims_success_but_changed_nothing_fails_loudly():
    """Otherwise the Run opens an empty pull request and reports it worked.

    The empty tree is half of it; the other half is a branch that carries
    nothing the base does not, which is what makes this an empty pull request
    rather than a Run that wrote its work earlier."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout="")
    runner.script("git", "rev-list", "--count", stdout="0\n")
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


def test_an_issue_naming_a_role_that_cannot_run_says_which_one():
    """A hand-edited plan block, or one filed by a newer AgentForge than this."""
    body = plan_block([{"role": "dramaturge"}], plan=a_plan())
    runner = a_runner()
    runner.script("gh", "issue", "view", stdout=issue_json(body=body))

    with pytest.raises(RunFailed, match="dramaturge"):
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


def test_a_workflow_with_no_steps_is_refused_before_any_agent_is_invoked(
    tmp_path, monkeypatch
):
    """Every shipped definition declares Steps now, so this is a definition a
    project wrote. Running it is a no-op rather than a Run."""
    (tmp_path / "feature.yaml").write_text("name: feature\nsteps: []\n", encoding="utf-8")
    monkeypatch.setattr("agentforge_framework.core.workflow.WORKFLOWS_ROOT", tmp_path)
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])

    with pytest.raises(RunFailed, match="feature"):
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
    monkeypatch.setattr("agentforge_framework.core.workflow.WORKFLOWS_ROOT", tmp_path)

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
    monkeypatch.setattr("agentforge_framework.core.workflow.WORKFLOWS_ROOT", tmp_path)

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
    monkeypatch.setattr("agentforge_framework.core.workflow.WORKFLOWS_ROOT", tmp_path)
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
    assert TESTER.tier is ModelTier.CHEAP


def test_a_definition_naming_an_unrunnable_role_costs_no_provider_call(tmp_path, monkeypatch):
    """#3 puts the bar at "before any Provider is invoked" — `gh` has necessarily run,
    since the Workflow name comes from the Issue."""
    (tmp_path / "feature.yaml").write_text(
        "name: feature\nsteps:\n  - role: dramaturge\n", encoding="utf-8"
    )
    monkeypatch.setattr("agentforge_framework.core.workflow.WORKFLOWS_ROOT", tmp_path)

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
    monkeypatch.setattr("agentforge_framework.core.workflow.WORKFLOWS_ROOT", tmp_path)


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
    assert "You are the Tester" in second.prompt_to("claude")
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
    prompts = second.prompts_to("claude")
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


def test_a_gate_with_nothing_to_read_halts_rather_than_passing_quietly(
    tmp_path, monkeypatch
):
    """A clean-pass Gate behind a Workflow with no Security Step has no audit to
    read, and no later invocation produces one. A declared check that silently
    never runs is the worst of the options."""
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


# --- the clean-pass Gate, from inside a Run ---------------------------------


SECURITY_GATED = (
    "name: feature\nsteps:\n  - role: implementer\n  - role: security\n    gate: security\n"
)

INTERPOLATED_SQL = {
    "location": "src/loader.py:42",
    "risk": "The order id is interpolated into the SQL string.",
    "rationale": "The loader runs against production Unity Catalog.",
}


def _audited(*findings) -> list[str]:
    """An Implementer that changed something, then an audit of what it changed."""
    return [
        agent_says("completed", "Added a bounded retry."),
        agent_says("completed", "Audit complete.", (), findings=findings),
    ]


def test_findings_hold_the_run_rather_than_reaching_sign_off_quietly(
    tmp_path, monkeypatch
):
    """The point of the Gate: the audit that would otherwise happen at merge
    time happens before a human is asked to look."""
    _workflow(tmp_path, monkeypatch, SECURITY_GATED)
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=_audited(INTERPOLATED_SQL))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.SUSPENDED
    assert labels_applied(runner) == ["agentforge:running", "agentforge:suspended"]
    assert not runner.ran("gh", "pr", "create")


def test_a_finding_reaches_the_run_log_with_somewhere_to_look(tmp_path, monkeypatch):
    """A location and a rationale, in the Agent's own entry and again in the
    Gate's. "Potential injection risk" as the whole message sends a human
    looking for something the Agent has already found."""
    _workflow(tmp_path, monkeypatch, SECURITY_GATED)
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=_audited(INTERPOLATED_SQL))

    forge(runner).implement(12, allow_commands=True)

    audit = next(c for c in comments_on(runner) if "### security" in c)
    assert "**Findings (1):**" in audit
    assert "`src/loader.py:42`" in audit
    assert "Why it matters: The loader runs against production Unity Catalog." in audit

    gate = next(c for c in comments_on(runner) if "Gate —" in c)
    assert "### security Gate — blocked (after step 2 of 2)" in gate
    assert "src/loader.py:42" in gate
    assert "the **security** Step's own output" in gate, "the entry must say what re-runs"


def test_a_clean_audit_clears_the_gate_and_the_run_reaches_sign_off(
    tmp_path, monkeypatch
):
    _workflow(tmp_path, monkeypatch, SECURITY_GATED)
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=_audited())

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert "--draft" in runner.only("gh", "pr", "create")
    assert [c for c in comments_on(runner) if "Gate —" in c] == [], (
        "a Gate that cleared posted an entry saying the Run carried on"
    )


def test_a_resumed_run_re_audits_rather_than_reading_the_finding_back(
    tmp_path, monkeypatch
):
    """ADR-0008 from the other side. The human fixes the finding and commits;
    the Security Step is un-retired, so the audit runs again on the code as it
    now is, and the Run goes on when it comes back clean."""
    _workflow(tmp_path, monkeypatch, SECURITY_GATED)

    first = a_runner()
    first.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    first.script("claude", stdout=_audited(INTERPOLATED_SQL))
    suspended = forge(first).implement(12, allow_commands=True)

    second = a_runner()
    second.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(labels=("agentforge:suspended",), comments=tuple(comments_on(first))),
    )
    # The fix is committed already; a Run refuses to start on a dirty tree.
    second.script("git", "status", "--porcelain", stdout="")
    second.script("claude", stdout=agent_says("completed", "Audit complete.", ()))

    resumed = forge(second).implement(12, allow_commands=True)

    assert suspended.done_roles == ("implementer",), "the Gate did not un-retire its Step"
    prompts = second.prompts_to("claude")
    assert len(prompts) == 1, "the implementer Step ran again for a finding about its output"
    assert "You are the Security Role" in prompts[0]
    assert resumed.status is RunStatus.AWAITING_SIGNOFF
    assert "--draft" in second.only("gh", "pr", "create")


def test_an_audit_that_writes_nothing_still_opens_the_pull_request(
    tmp_path, monkeypatch
):
    """A Role that changes no files is not a Run that produced nothing. The
    work was committed by the invocation that suspended, and refusing to open
    the pull request here would strand it."""
    _workflow(tmp_path, monkeypatch, SECURITY_GATED)

    first = a_runner()
    first.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    first.script("claude", stdout=_audited(INTERPOLATED_SQL))
    forge(first).implement(12, allow_commands=True)

    second = a_runner()
    second.script(
        "gh",
        "issue",
        "view",
        stdout=issue_json(labels=("agentforge:suspended",), comments=tuple(comments_on(first))),
    )
    second.script("git", "status", "--porcelain", stdout="")
    second.script("claude", stdout=agent_says("completed", "Audit complete.", ()))

    state = forge(second).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert not second.ran("git", "commit"), "there was nothing to commit"


# --- the three shipped Workflows, end to end -------------------------------


def _shipped_run(name: str, runner=None):
    """One Run of a shipped definition, against the recorded Issue body."""
    runner = runner or a_runner()
    runner.script("gh", "issue", "view", stdout=issue_json(_body_naming_workflow(name)))
    return runner


def test_the_feature_workflow_runs_its_four_roles_in_order():
    runner = _shipped_run("feature")
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Done."))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert state.done_roles == ("implementer", "tester", "security", "reviewer")


def test_the_bugfix_workflow_fixes_verifies_and_reports_without_an_audit():
    """A bug fix that touches auth is a Task the Orchestrator routes to
    `feature`. That is a judgement about the Task, not a Step in this one."""
    runner = _shipped_run("bugfix")
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Fixed."))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert state.done_roles == ("implementer", "tester", "reviewer")
    prompts = runner.prompts_to("claude")
    assert not any("You are the Security Role" in prompt for prompt in prompts)


def test_the_review_workflow_completes_on_a_diff_no_agent_wrote():
    """#14's substantive half. No Implementer runs, nothing is written to the
    tree, and the Run still reaches Sign-off — the branch already carries the
    human's commits, which is the whole premise of pointing `review` at one.

    This is the case that catches a runtime quietly assuming it produced the
    diff itself."""
    runner = _shipped_run("review")
    # Nothing to commit at any point: the diff was committed by whoever wrote it.
    runner.script("git", "status", "--porcelain", stdout="")
    runner.script("claude", stdout=agent_says("completed", "Reviewed.", ()))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert state.done_roles == ("security", "reviewer")
    assert not runner.ran("git", "commit"), "a review Workflow committed something"
    assert "--draft" in runner.only("gh", "pr", "create")


def test_a_review_run_on_a_branch_with_nothing_on_it_still_refuses():
    """The refusal survives the Workflow that legitimately writes nothing: an
    empty branch is an empty pull request whoever was supposed to fill it."""
    runner = _shipped_run("review")
    runner.script("git", "status", "--porcelain", stdout="")
    runner.script("git", "rev-list", "--count", stdout="0\n")
    runner.script("claude", stdout=agent_says("completed", "Reviewed.", ()))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.FAILED
    assert not runner.ran("gh", "pr", "create")


def test_a_workflow_naming_the_architect_loads_and_runs(tmp_path, monkeypatch):
    """The Architect is in no shipped definition, which is what makes this worth
    asserting: a Role nothing can run is decorative, and CONTEXT.md promised six.

    A project that wants a design pass writes it into a Workflow of its own, and
    the runtime looks the runner up like any other."""
    _workflow(
        tmp_path,
        monkeypatch,
        "name: feature\nsteps:\n  - role: architect\n  - role: implementer\n",
    )
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script(
        "claude",
        stdout=[
            agent_says("completed", "The parser owns the format; the port stays dumb.", ()),
            agent_says("completed", "Built it that way."),
        ],
    )

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert state.done_roles == ("architect", "implementer")
    prompts = runner.prompts_to("claude")
    assert "You are the Architect" in prompts[0]
    assert "the port stays dumb" in next(c for c in comments_on(runner) if "### architect" in c)


def test_the_runtime_names_no_role():
    """#4: adding a seventh Role must not require editing the engine.

    Asserted against the source because that is where the property lives. A
    `RUNNERS` lookup keyed by the Workflow step is the only dispatch there is.

    Located through the module rather than by spelling out the package directory,
    so that renaming the import path cannot turn this into a file-not-found.
    """
    source = Path(runtime_module.__file__).read_text(encoding="utf-8")

    for role in ("implementer", "tester", "reviewer", "security", "architect"):
        assert role not in source.lower(), f"runtime.py names the {role!r} Role"


# --- the Context Pack ------------------------------------------------------


def test_every_role_is_handed_the_pack_resolved_from_the_frozen_plan():
    """Six Agents rediscovering the same files is the cost the frozen Plan
    exists to remove, and until the pack was resolved each of them paid it."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    prompts = runner.prompts_to("claude")
    assert prompts, "no Agent was invoked"
    for prompt in prompts:
        assert "## Context Pack" in prompt
        assert "src/loader.py" in prompt
        assert "tests/test_loader.py" in prompt


def test_the_pack_is_posted_to_the_run_log_before_the_first_agent_runs():
    """A Run that went wrong is diagnosed against what its Agents were shown,
    and the record has to be written before they are shown it."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    assert "### Context Pack" in comments_on(runner)[0]
    first_comment = next(
        index for index, call in enumerate(runner.calls) if call[:3] == ("gh", "issue", "comment")
    )
    first_agent = next(index for index, call in enumerate(runner.calls) if call[0] == "claude")
    assert first_comment < first_agent


def test_the_pack_is_recorded_once_however_many_steps_run():
    """It is resolved once per Run, so a Run Log with one entry per Step would
    be saying the same thing four times."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    assert sum("### Context Pack" in comment for comment in comments_on(runner)) == 1


def test_the_control_run_hands_every_role_nothing_and_says_so():
    """A pack is supposed to make a Run cheaper. The only honest way to know is
    to run the same Issue without one and compare the two totals."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True, resolve_context=False)

    prompts = runner.prompts_to("claude")
    assert all("## Context Pack" not in prompt for prompt in prompts)
    assert "Context Pack — none" in comments_on(runner)[0]


def test_every_run_log_entry_carries_what_its_step_cost():
    """Cost attributed to the Role that spent it, in the place a human is
    already reading."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=recorded_claude_run())

    state = forge(runner).implement(12, allow_commands=True)

    entries = [comment for comment in comments_on(runner) if RESULT_OPEN in comment]
    assert entries and all("**Cost:** $0.4312" in entry for entry in entries)
    assert all(result.usage.cost_usd == pytest.approx(0.4312) for result in state.results)


def test_the_run_that_ends_says_what_the_whole_thing_cost():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=recorded_claude_run())

    forge(runner).implement(12, allow_commands=True)

    assert "- **Cost:** $" in comments_on(runner)[-1]


# --- the Roster names the tier that runs (#71, ADR-0014) --------------------

#: The tier every Role in the shipped `feature` Workflow would run at if nobody
#: said otherwise: ADR-0004's defaults, as `claude` spells them.
BY_DEFAULT = ["sonnet", "haiku", "opus", "opus"]


def body_with_roster(roster) -> str:
    """An Issue whose Roster is the thing under test.

    Only the plan block is parsed, so the prose above it is one line rather than
    a second copy of the renderer's output kept in step by hand.
    """
    return "## Task\n\n> add a retry to the loader\n\n" + plan_block(roster, workflow="feature")


def models_used(runner: FakeRunner) -> list[str]:
    return [call[call.index("--model") + 1] for call in runner.matching("claude")]


def a_run_of(roster) -> FakeRunner:
    runner = a_runner()
    runner.script("gh", "issue", "view", stdout=issue_json(body_with_roster(roster)))
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "done"))
    return runner


MOVED = [
    {"role": "implementer", "tier": "standard"},
    {"role": "tester", "tier": "cheap"},
    # The one the Orchestrator moved, and the one the smoke Run for #67 caught:
    # the table said `standard` and the Run Log said `deep`.
    {"role": "security", "tier": "standard"},
    {"role": "reviewer", "tier": "deep"},
]


def test_a_roster_tier_below_the_roles_default_is_the_tier_the_step_runs_at():
    """The Orchestrator's per-Role judgement had never once taken effect: no
    shipped Workflow pins a Step, so every Run fell through to the default."""
    runner = a_run_of(MOVED)

    forge(runner).implement(12, allow_commands=True)

    assert models_used(runner) == ["sonnet", "haiku", "sonnet", "opus"]
    assert models_used(runner) != BY_DEFAULT, "the Roster changed nothing"


def test_the_run_log_names_the_tier_the_roster_promised():
    """The accuracy of the one artifact 0.1 promises to keep: the table and the
    Run Log are read side by side, and they now say the same thing."""
    runner = a_run_of(MOVED)

    forge(runner).implement(12, allow_commands=True)

    audit = next(c for c in comments_on(runner) if "### security" in c)
    assert "**Model Tier:** `standard`" in audit


def test_a_named_tier_override_still_beats_the_roster():
    runner = a_run_of(MOVED)

    forge(runner).implement(12, allow_commands=True, tier_overrides={"security": ModelTier.DEEP})

    assert models_used(runner) == ["sonnet", "haiku", "opus", "opus"]


def test_a_run_wide_tier_still_beats_the_roster():
    """The person at the keyboard is answering with more than the Orchestrator had."""
    runner = a_run_of(MOVED)

    forge(runner).implement(12, allow_commands=True, tier=ModelTier.CHEAP)

    assert models_used(runner) == ["haiku"] * 4


def test_a_step_tier_in_the_workflow_still_beats_the_roster(tmp_path, monkeypatch):
    """`align_to_workflow` writes the Roster in this order, so reading it back in
    another one would make the two disagree the moment anybody pinned a Step."""
    (tmp_path / "feature.yaml").write_text(
        "name: feature\nsteps:\n  - role: security\n    tier: cheap\n", encoding="utf-8"
    )
    monkeypatch.setattr("agentforge_framework.core.workflow.WORKFLOWS_ROOT", tmp_path)
    runner = a_run_of([{"role": "security", "tier": "standard"}])

    forge(runner).implement(12, allow_commands=True)

    assert models_used(runner) == ["haiku"]


def test_a_role_the_roster_does_not_name_falls_through_to_its_default():
    """`issue_body_v1.md` is an Issue an older AgentForge filed: one Role in the
    Roster, four Steps in the Workflow. Those Runs used the defaults and still do."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "done"))

    forge(runner).implement(12, allow_commands=True)

    assert models_used(runner) == BY_DEFAULT


def test_a_resumed_run_resolves_the_tier_the_first_invocation_would_have():
    """Both invocations read the same frozen block, which is the whole claim."""
    done = [
        render_result_block(
            {"role": role, "tier": tier, "outcome": "completed", "summary": "done"}
        )
        for role, tier in (("implementer", "standard"), ("tester", "cheap"))
    ]
    runner = a_runner()
    runner.script(
        "gh", "issue", "view", stdout=issue_json(body_with_roster(MOVED), comments=tuple(done))
    )
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "done"))

    forge(runner).implement(12, allow_commands=True)

    assert models_used(runner) == ["sonnet", "opus"], "the security Step resumed at the wrong tier"


# --- a Run commits what it declared (#72, ADR-0015) -------------------------

#: What the smoke Run for #67 actually left behind: the edit, and what running
#: the suite under `--allow-commands` wrote next to it in a repository whose
#: `.gitignore` does not cover `__pycache__` — because it has none.
AFTER_PYTEST = (
    " M src/loader.py\n"
    "?? src/__pycache__/loader.cpython-311.pyc\n"
    "?? tests/__pycache__/test_loader.cpython-311-pytest-9.1.1.pyc\n"
)


def staged(runner: FakeRunner) -> list[str]:
    """The paths the Run put in the index, in the order it named them."""
    call = runner.only("git", "add")
    return list(call[call.index("--") + 1 :])


def test_a_file_a_roles_command_wrote_is_not_committed():
    """`--allow-commands` is what makes a Run produce files it did not write.
    Before it, staging the tree and staging the change were the same thing."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", AFTER_PYTEST])
    runner.script("claude", stdout=agent_says("completed", "pytest: 24 passed"))

    forge(runner).implement(12, allow_commands=True)

    assert staged(runner) == ["src/loader.py"]
    assert not any("__pycache__" in path for path in staged(runner))
    assert runner.ran("git", "commit")


def test_the_pull_request_lists_only_what_was_committed():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", AFTER_PYTEST])
    runner.script("claude", stdout=agent_says("completed", "pytest: 24 passed"))

    forge(runner).implement(12, allow_commands=True)

    body = runner.argument_after("--body", "gh", "pr", "create")
    assert "- `src/loader.py`" in body
    assert "loader.cpython-311.pyc" not in body.split("## Left uncommitted")[0]


#: A second agent editing the same checkout while the Run is going. The Run's
#: own file is modified as expected; `src/telemetry.py` is somebody else's
#: half-finished work, and git tracks it, so ADR-0015 commits it either way.
CONCURRENT_EDIT = " M src/loader.py\n M src/telemetry.py\n"


def test_a_committed_file_no_agent_claimed_is_named_in_the_pull_request():
    """The concurrency hazard in #101. Antigravity's agent and a Run share one
    checkout; ADR-0015 commits every change to a tracked file however it
    arrived, so the other agent's work lands in the branch attributed to a Role.
    Nothing about the file on disk says otherwise, so the Run says it instead."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", CONCURRENT_EDIT])
    runner.script("claude", stdout=agent_says("completed", "retry added"))

    forge(runner).implement(12, allow_commands=True)

    assert "src/telemetry.py" in staged(runner), "ADR-0015 still commits it"
    body = runner.argument_after("--body", "gh", "pr", "create")
    unclaimed_section = body.split("## Committed, but no Agent claimed them")[1]
    assert "- `src/telemetry.py`" in unclaimed_section
    assert "- `src/loader.py`" not in unclaimed_section, (
        "the Role declared this one; naming it would bury the one that matters"
    )


def test_a_run_whose_every_file_was_declared_says_nothing_about_unclaimed_ones():
    """The section is a disclosure, not a fixture of the body. An ordinary Run
    that names what it changed reads exactly as it did before."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "retry added"))

    forge(runner).implement(12, allow_commands=True)

    body = runner.argument_after("--body", "gh", "pr", "create")
    assert "no Agent claimed" not in body


def test_a_role_that_spelled_its_path_the_windows_way_still_claimed_the_file():
    """A Role reports `src\\telemetry.py` and git answers `src/telemetry.py`.
    Comparing those literally would accuse an Agent of somebody else's edit on
    every Windows Run, which is the platform this hazard was found on."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", CONCURRENT_EDIT])
    runner.script(
        "claude",
        stdout=agent_says("completed", "retry added", files=("src\\telemetry.py",)),
    )

    forge(runner).implement(12, allow_commands=True)

    body = runner.argument_after("--body", "gh", "pr", "create")
    assert "no Agent claimed" not in body


def test_what_was_left_behind_is_named_rather_than_dropped():
    """A build artifact is the usual reason and an Agent writing outside its
    Step is the one worth reading, and only a human can tell them apart."""
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", AFTER_PYTEST])
    runner.script("claude", stdout=agent_says("completed", "pytest: 24 passed"))

    forge(runner).implement(12, allow_commands=True)

    body = runner.argument_after("--body", "gh", "pr", "create")
    left = body.split("## Left uncommitted")[1]
    assert "- `src/__pycache__/loader.cpython-311.pyc`" in left
    assert "- `tests/__pycache__/test_loader.cpython-311-pytest-9.1.1.pyc`" in left


def test_an_untracked_file_the_plan_named_is_committed():
    """The Plan's second Step is `tests/test_loader.py`, which does not exist yet."""
    runner = a_runner()
    runner.script(
        "git",
        "status",
        "--porcelain",
        stdout=["", " M src/loader.py\n?? tests/test_loader.py\n?? .coverage\n"],
    )
    runner.script("claude", stdout=agent_says("completed", "covered the retry path"))

    forge(runner).implement(12, allow_commands=True)

    assert staged(runner) == ["src/loader.py", "tests/test_loader.py"]


def test_an_untracked_file_an_agent_reported_is_committed():
    """`files_changed` stopped being decoration: it is half of the declared surface."""
    runner = a_runner()
    runner.script(
        "git", "status", "--porcelain", stdout=["", " M src/loader.py\n?? src/backoff.py\n"]
    )
    runner.script(
        "claude",
        stdout=agent_says(
            "completed", "split the backoff out", files=("src/loader.py", "src/backoff.py")
        ),
    )

    forge(runner).implement(12, allow_commands=True)

    assert staged(runner) == ["src/loader.py", "src/backoff.py"]


def test_an_out_of_plan_edit_to_a_tracked_file_still_reaches_the_commit():
    """ADR-0015's asymmetry, deliberately. Git already knows about the file, so
    the edit is a change to the project however it got there — and declining it
    because the Plan forgot the file would drop an Agent's work silently."""
    runner = a_runner()
    runner.script(
        "git", "status", "--porcelain", stdout=["", " M src/loader.py\n M docs/loading.md\n"]
    )
    runner.script("claude", stdout=agent_says("completed", "done"))

    forge(runner).implement(12, allow_commands=True)

    assert staged(runner) == ["src/loader.py", "docs/loading.md"]


def test_a_run_that_left_only_undeclared_files_fails_and_says_which():
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", "?? .pytest_cache/CACHEDIR.TAG\n"])
    runner.script("git", "rev-list", "--count", stdout="0\n")
    runner.script("claude", stdout=agent_says("completed", "ran the suite", files=()))

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.FAILED
    assert not runner.ran("git", "commit")
    assert not runner.ran("gh", "pr", "create")
    assert ".pytest_cache/CACHEDIR.TAG" in state.results[-1].summary


def test_a_suspended_run_commits_the_same_surface(tmp_path, monkeypatch):
    """One rule about what belongs in a commit. A Gate stopping the Run is not
    a reason to relax it."""
    _workflow(tmp_path, monkeypatch, HUMAN_GATED)
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", AFTER_PYTEST])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    forge(runner).implement(12, allow_commands=True)

    assert staged(runner) == ["src/loader.py"]
    assert runner.only("git", "push")[-1] == "agentforge/issue-12"


def test_a_new_package_the_plan_named_is_committed_file_by_file():
    """`git status` collapses a wholly new directory to `src/newpkg/` unless
    asked not to, and a collapsed entry matches no declared path — so the fix
    for a stray `__pycache__` would have dropped a package the Plan asked for."""
    runner = a_runner()
    runner.script(
        "git",
        "status",
        "--porcelain",
        stdout=[
            "",
            (
                "?? src/backoff/__init__.py\n"
                "?? src/backoff/policy.py\n"
                "?? src/backoff/__pycache__/policy.cpython-311.pyc\n"
            ),
        ],
    )
    runner.script(
        "claude",
        stdout=agent_says(
            "completed",
            "split the backoff out",
            files=("src/backoff/__init__.py", "src/backoff/policy.py"),
        ),
    )

    forge(runner).implement(12, allow_commands=True)

    assert staged(runner) == ["src/backoff/__init__.py", "src/backoff/policy.py"]
    assert "--untracked-files=all" in runner.matching("git", "status")[-1]
