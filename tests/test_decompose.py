"""Planning cuts a Task into Slices and files one Issue per Slice (ADR-0021).

The behaviour worth holding down is the ordering and what it costs. A Slice is
filed after every Slice it names, the edges it declares carry real Issue
numbers, and a Run refuses to start on a Slice whose blockers have not signed
off — because an edge nothing enforces is a comment.

Everything here runs offline through the one Command Runner fake, which is what
lets a four-stage pipeline be tested without a `claude` binary or a tracker.
"""

from __future__ import annotations

import json

import pytest

from agentforge_framework.agents.decomposer import (
    MAX_SLICES,
    Decomposer,
    render_breakdown,
    slice_task,
)
from agentforge_framework.core.contracts import Slice
from agentforge_framework.core.plan_format import (
    PlanFormatError,
    extract_slices,
    order_slices,
    parse_issue_body,
    render_result_block,
)
from agentforge_framework.core.runtime import RunFailed
from agentforge_framework.providers.claude import ClaudeProvider

from .fakes import FakeRunner, github_repository
from .test_agents import (
    _envelope,
    no_more_questions,
    orchestrator_output,
    pipeline,
    slices_output,
    spec_output,
)
from .test_runtime import ROOT, _yes, forge, issue_json

# A cut with a real shape to it: two Slices that can start, one that waits for
# both. Filing order is what the edges say, not what the model listed.
THREE = (
    {"id": "reader", "title": "Read the manifest"},
    {"id": "writer", "title": "Write the manifest"},
    {"id": "roundtrip", "title": "Round-trip a manifest", "blocked_by": ["reader", "writer"]},
)


def a_runner(issues=(12,)) -> FakeRunner:
    """A repository that hands out the Issue numbers a test asks for, in order."""
    runner = github_repository(FakeRunner(), ROOT)
    runner.script(
        "gh",
        "issue",
        "create",
        stdout=[f"https://github.com/acme/pipelines/issues/{n}\n" for n in issues],
    )
    runner.script("gh", "pr", "create", stdout="https://github.com/acme/pipelines/pull/99\n")
    runner.script("gh", "issue", "view", stdout=issue_json())
    # The two halves of a native blocking edge, told apart by their flags.
    runner.script("gh", "api", contains=("--jq",), stdout="900000\n")
    runner.script("gh", "api", contains=("--method",), stdout="")
    return runner


def _prompts(runner: FakeRunner) -> list[str]:
    """What each stage was actually asked, in order. `claude -p <prompt>`."""
    return [call[call.index("-p") + 1] for call in runner.matching("claude")]


def bodies(runner: FakeRunner) -> list[str]:
    return [call[call.index("--body") + 1] for call in runner.matching("gh", "issue", "create")]


def titles(runner: FakeRunner) -> list[str]:
    return [call[call.index("--title") + 1] for call in runner.matching("gh", "issue", "create")]


# --- the cut ---------------------------------------------------------------


def test_a_task_with_three_slices_in_it_files_three_issues():
    runner = a_runner(issues=(12, 13, 14))
    runner.script("claude", stdout=pipeline(slices=THREE))

    outcome = forge(runner).plan("build the manifest round-trip", approver=_yes)

    assert [one.issue.number for one in outcome.filed] == [12, 13, 14]
    assert titles(runner) == [
        "Read the manifest",
        "Write the manifest",
        "Round-trip a manifest",
    ]


def test_a_slice_is_filed_after_every_slice_it_names():
    """The ordering is what makes an edge writable at all: an Issue cannot
    declare it is blocked by one that does not have a number yet."""
    cut = (
        {"id": "last", "title": "Depends on both", "blocked_by": ["first", "second"]},
        {"id": "second", "title": "Second", "blocked_by": ["first"]},
        {"id": "first", "title": "First"},
    )
    runner = a_runner(issues=(12, 13, 14))
    runner.script("claude", stdout=pipeline(slices=cut))

    outcome = forge(runner).plan("do it in order", approver=_yes)

    assert titles(runner) == ["First", "Second", "Depends on both"]
    assert [one.blocked_by for one in outcome.filed] == [(), (12,), (12, 13)]


def test_the_edges_survive_into_the_frozen_plan_block():
    """`implement` reads them back from the body a week later, on a machine that
    saw none of this."""
    runner = a_runner(issues=(12, 13, 14))
    runner.script("claude", stdout=pipeline(slices=THREE))

    forge(runner).plan("build the round-trip", approver=_yes)

    assert parse_issue_body(bodies(runner)[-1]).blocked_by == (12, 13)
    assert "- #12" in bodies(runner)[-1]
    assert parse_issue_body(bodies(runner)[0]).blocked_by == ()


def test_a_blocking_edge_is_recorded_as_a_native_dependency():
    """A line of prose in a body is not something the tracker's own board can
    filter on. The dependencies endpoint is."""
    runner = a_runner(issues=(12, 13, 14))
    runner.script("claude", stdout=pipeline(slices=THREE))

    forge(runner).plan("build the round-trip", approver=_yes)

    posted = [call for call in runner.matching("gh", "api") if "--method" in call]
    assert len(posted) == 2, "one per edge into the last Slice"
    assert all("dependencies/blocked_by" in " ".join(call) for call in posted)
    assert all("issue_id=900000" in call for call in posted)


def test_a_tracker_without_dependencies_degrades_rather_than_failing():
    """The plan block carries the same edges, so a repository whose GitHub does
    not offer the endpoint still gets a correct decomposition -- it just does not
    get the board view, and is told so once."""
    runner = a_runner(issues=(12, 13, 14))
    runner.script("claude", stdout=pipeline(slices=THREE))
    runner.script("gh", "api", contains=("--method",), returncode=1, stderr="Not Found")

    outcome = forge(runner).plan("build the round-trip", approver=_yes)

    assert len(outcome.filed) == 3
    assert outcome.unwritten_edges == ((14, 12), (14, 13))
    assert parse_issue_body(bodies(runner)[-1]).blocked_by == (12, 13)


def test_every_filed_issue_wears_the_status_label_and_the_triage_label():
    runner = a_runner(issues=(12, 13, 14))
    runner.script("claude", stdout=pipeline(slices=THREE))

    forge(runner).plan("build the round-trip", approver=_yes)

    for call in runner.matching("gh", "issue", "create"):
        assert "agentforge:planned" in call
        assert "ready-for-agent" in call


def test_a_one_sentence_task_still_files_one_issue():
    """The pipeline did not stop being usable for small work. A Task with one
    Slice in it cuts to one Slice, and nothing special-cases that."""
    runner = a_runner()
    runner.script("claude", stdout=pipeline())

    outcome = forge(runner).plan("add a retry to the loader", approver=_yes)

    assert len(outcome.filed) == 1
    assert len(runner.matching("gh", "issue", "create")) == 1


# --- what stops the pass ---------------------------------------------------


def test_nothing_is_filed_until_the_human_has_seen_the_cut():
    runner = a_runner(issues=(12, 13, 14))
    runner.script("claude", stdout=pipeline(slices=THREE))

    outcome = forge(runner).plan("build the round-trip", approver=lambda slices: False)

    assert outcome.declined
    assert not outcome.filed
    assert not runner.ran("gh", "issue", "create")
    assert len(outcome.slices) == 3, "the cut is still reported, so it can be judged"


def test_an_unattended_pass_files_nothing_at_all():
    """No approver is nobody in the room. Committing somebody to three Issues
    they have not read is not a default worth having."""
    runner = a_runner(issues=(12, 13, 14))
    runner.script("claude", stdout=pipeline(slices=THREE))

    outcome = forge(runner).plan("build the round-trip")

    assert outcome.declined
    assert not runner.ran("gh", "issue", "create")


def test_a_spec_that_never_arrived_stops_before_anything_is_cut():
    """The failure that would not look like one: a breakdown cut from a spec
    that was never written is a breakdown of whatever the model remembered."""
    runner = a_runner()
    runner.script(
        "claude",
        stdout=_envelope(
            "I had some thoughts about the loader.\n\n"
            + render_result_block({"outcome": "completed", "summary": "wrote the spec"})
        ),
    )

    outcome = forge(runner).plan("add a retry", approver=_yes)

    assert outcome.failure is not None
    assert "no usable spec" in outcome.failure.summary
    assert len(runner.matching("claude")) == 1, "the cut never ran"
    assert not runner.ran("gh", "issue", "create")


def test_a_slice_that_will_not_plan_leaves_the_ones_before_it_filed():
    """Those Issues are complete and executable on their own, which is the
    property the cut was for. Discarding them tidies up by throwing away work."""
    runner = a_runner(issues=(12, 13, 14))
    runner.script(
        "claude",
        stdout=[
            spec_output(),
            slices_output(*THREE),
            orchestrator_output([{"role": "implementer"}]),
            orchestrator_output([{"role": "implementer"}]),
            _envelope("no plan block here"),
        ],
    )

    outcome = forge(runner).plan("build the round-trip", approver=_yes)

    assert [one.issue.number for one in outcome.filed] == [12, 13]
    assert outcome.failure is not None


def test_a_cut_that_blocks_its_own_ordering_is_refused():
    """Whichever Slice went first would carry an edge to an Issue that does not
    exist yet."""
    with pytest.raises(PlanFormatError, match="block their own ordering"):
        order_slices(
            (
                Slice(id="a", title="A", blocked_by=("b",)),
                Slice(id="b", title="B", blocked_by=("a",)),
            )
        )


def test_a_slice_blocked_by_something_nobody_defined_is_refused():
    payload = slices_output(
        {"id": "one", "title": "One", "blocked_by": ["ghost"]},
    )
    with pytest.raises(PlanFormatError, match="which no slice defines"):
        extract_slices(json.loads(payload)["result"])


def test_two_slices_sharing_an_id_are_refused():
    payload = slices_output({"id": "one", "title": "One"}, {"id": "one", "title": "Also one"})
    with pytest.raises(PlanFormatError, match="share the id"):
        extract_slices(json.loads(payload)["result"])


def test_a_cut_past_the_cap_stops_rather_than_filing_forty_issues():
    runner = a_runner()
    many = tuple({"id": f"s{n}", "title": f"Slice {n}"} for n in range(MAX_SLICES + 1))
    runner.script("claude", stdout=[spec_output(), slices_output(*many)])

    outcome = forge(runner).plan("everything, at once", approver=_yes)

    assert outcome.failure is not None
    assert "wants splitting" in outcome.failure.summary
    assert not runner.ran("gh", "issue", "create")


# --- the stages ------------------------------------------------------------


def test_each_stage_is_handed_only_its_own_skill():
    """A pass given both `to-spec` and `to-tickets` has the method for a job it
    is not doing yet, and both skills end by publishing to a tracker."""
    runner = a_runner()
    runner.script("claude", stdout=pipeline())

    forge(runner).plan("add a retry", approver=_yes)

    prompts = _prompts(runner)
    assert "to-spec" in prompts[0] or "synthesizing" in prompts[0]
    assert "tracer bullet" in prompts[1]
    assert "one fresh context window" in prompts[1]


def test_the_grill_runs_before_the_spec_and_its_answers_reach_it():
    """Every question not asked here becomes a guess made later by something
    with less context than the human at the keyboard right now."""
    runner = a_runner()
    runner.script(
        "claude",
        stdout=[
            _envelope(
                render_result_block(
                    {
                        "outcome": "completed",
                        "summary": "unclear",
                        "questions": ["Which manifest format?"],
                    }
                )
            ),
            no_more_questions(),
            *pipeline(),
        ],
    )

    forge(runner).plan("add a manifest", interviewer=lambda q: "Parquet.", approver=_yes)

    spec_prompt = _prompts(runner)[2]
    assert "Which manifest format?" in spec_prompt
    assert "Parquet." in spec_prompt


def test_the_planning_pass_for_a_slice_is_told_what_the_earlier_ones_deliver():
    """So that a Slice which assumes work an earlier one does says so rather
    than planning it a second time."""
    task = slice_task(
        Slice(id="c", title="Round-trip", delivers="Round-trips a manifest.", blocked_by=("a",)),
        spec="## Problem Statement\n\nManifests.",
        blockers=(Slice(id="a", title="Read", delivers="Reads a manifest."),),
    )

    assert "Reads a manifest." in task.statement
    assert "Round-trips a manifest." in task.statement
    assert "Do not plan any of it." in task.statement


def test_the_issue_body_carries_the_slice_and_not_the_whole_spec():
    """The planning prompt carries the spec so the Slice knows where it sits.
    Repeating it in every body is one copy per Issue to go stale."""
    runner = a_runner(issues=(12, 13, 14))
    runner.script(
        "claude",
        stdout=[
            spec_output("## Problem Statement\n\nA spec nobody wants three copies of."),
            slices_output(*THREE),
            orchestrator_output([{"role": "implementer"}]),
        ],
    )

    forge(runner).plan("build the round-trip", approver=_yes)

    for body in bodies(runner):
        assert "three copies of" not in body


def test_the_breakdown_a_human_is_shown_names_the_blockers_by_title():
    """Nobody approves a graph of slugs."""
    lines = render_breakdown(
        (
            Slice(id="reader", title="Read the manifest", delivers="Reads it."),
            Slice(id="rt", title="Round-trip", delivers="Round-trips.", blocked_by=("reader",)),
        )
    )
    rendered = "\n".join(lines)

    assert "Read the manifest" in rendered
    assert "Blocked by: Read the manifest" in rendered
    assert "can start immediately" in rendered


# --- the edges are enforced ------------------------------------------------


def blocked_issue(blocked_by, labels=("agentforge:planned",), state="OPEN") -> str:
    """An Issue whose frozen plan says it waits on others."""
    from agentforge_framework.core.plan_format import PLAN_CLOSE, PLAN_OPEN

    from .test_contracts import a_plan

    payload = {
        "version": 1,
        "plan": a_plan().to_dict(),
        "roster": [{"role": "implementer", "tier": "standard"}],
        "context": {},
        "blocked_by": list(blocked_by),
    }
    body = f"{PLAN_OPEN}\n```json\n{json.dumps(payload)}\n```\n{PLAN_CLOSE}"
    return json.dumps(
        {
            "number": 14,
            "title": "Round-trip a manifest",
            "body": body,
            "url": "https://github.com/acme/pipelines/issues/14",
            "labels": [{"name": name} for name in labels],
            "comments": [],
            "state": state,
        }
    )


def test_a_run_refuses_to_start_on_a_slice_whose_blockers_have_not_finished():
    runner = a_runner()
    runner.script("gh", "issue", "view", contains=("14",), stdout=blocked_issue((12,)))
    runner.script("gh", "issue", "view", contains=("12",), stdout=issue_json())

    with pytest.raises(RunFailed, match="blocked by #12"):
        forge(runner).implement(14)


def test_a_blocker_that_reached_signoff_no_longer_blocks():
    runner = a_runner()
    runner.script("gh", "issue", "view", contains=("14",), stdout=blocked_issue((12,)))
    runner.script(
        "gh",
        "issue",
        "view",
        contains=("12",),
        stdout=issue_json(labels=("agentforge:awaiting-signoff",)),
    )
    runner.script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    forge(runner).implement(14)  # does not raise


def test_a_blocker_somebody_closed_by_hand_no_longer_blocks():
    """A human who decided a Slice was unnecessary and closed it has cleared the
    edge as surely as a Run that finished it."""
    runner = a_runner()
    runner.script("gh", "issue", "view", contains=("14",), stdout=blocked_issue((12,)))
    runner.script(
        "gh",
        "issue",
        "view",
        contains=("12",),
        stdout=_closed(issue_json()),
    )
    runner.script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    forge(runner).implement(14)  # does not raise


def _closed(raw: str) -> str:
    data = json.loads(raw)
    data["state"] = "CLOSED"
    return json.dumps(data)


def test_ignore_blockers_is_how_the_human_says_they_know_better():
    runner = a_runner()
    runner.script("gh", "issue", "view", contains=("14",), stdout=blocked_issue((12,)))
    runner.script("gh", "issue", "view", contains=("12",), stdout=issue_json())
    runner.script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    forge(runner).implement(14, ignore_blockers=True)  # does not raise


def test_an_unreadable_blocker_does_not_block():
    """The tracker being unreachable is not evidence the work is unfinished, and
    refusing the Run would make an outage look like a dependency."""
    runner = a_runner()
    runner.script("gh", "issue", "view", contains=("14",), stdout=blocked_issue((12,)))
    runner.script("gh", "issue", "view", contains=("12",), returncode=1, stderr="rate limited")
    runner.script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    forge(runner).implement(14)  # does not raise


def test_an_issue_from_before_the_cut_has_no_blockers_and_runs():
    """Additive: `blocked_by` defaults to empty, so an Issue filed before
    ADR-0021 parses and starts exactly as it did."""
    runner = a_runner()
    runner.script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    forge(runner).implement(12)  # does not raise


# --- the Decomposer on its own ---------------------------------------------


def test_the_decomposer_reports_every_invocation_so_the_pass_can_be_priced():
    runner = github_repository(FakeRunner(), ROOT)
    runner.script("claude", stdout=[spec_output(), slices_output(*THREE)])

    cut = Decomposer(ClaudeProvider(runner)).decompose(
        "a plan document", source="a file", cwd=ROOT
    )

    assert cut.ok
    assert len(cut.results) == 2
    assert len(cut.slices) == 3
