"""The Orchestrator interviews the human before it writes anything down.

Rounds of one-shot invocations, not a conversation: ADR-0001 gives the Provider
port no session, so each round is handed the transcript so far. That is also
why this file needs no console — the human is a callable, and a test hands it a
list of answers.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentforge_framework.agents.orchestrator import (
    MAX_ROUNDS,
    ORCHESTRATOR,
    Exchange,
    Orchestrator,
    render_transcript,
)
from agentforge_framework.core.contracts import ModelTier, Task
from agentforge_framework.core.plan_format import render_result_block
from agentforge_framework.providers.claude import ClaudeProvider

from .fakes import FakeRunner
from .test_agents import orchestrator_output

CWD = Path("/repo/pipelines")


def asks(*questions: str) -> str:
    """One interview round: what the Orchestrator still wants to know."""
    return json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": render_result_block(
                {
                    "outcome": "completed",
                    "summary": "what is still unclear",
                    "questions": list(questions),
                }
            ),
        }
    )


READY = asks()


class Human:
    """A human, as the Orchestrator reaches one: a callable and a memory."""

    def __init__(self, *answers: str | None) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def __call__(self, question: str) -> str | None:
        self.asked.append(question)
        if not self.answers:
            return None
        return self.answers.pop(0)


def an_orchestrator(*replies: str) -> tuple[Orchestrator, FakeRunner]:
    runner = FakeRunner().script("claude", stdout=list(replies))
    return Orchestrator(ClaudeProvider(runner)), runner


def prompts(runner: FakeRunner) -> list[str]:
    return runner.prompts_to("claude")


# --- the interview -----------------------------------------------------------


def test_questions_are_put_to_the_human_before_anything_is_written():
    """ADR-0003 freezes the plan the moment it is filed, so this is the last
    cheap moment to ask."""
    orchestrator, _ = an_orchestrator(
        asks("Which loader?", "Retry on 5xx only?"),
        READY,
        orchestrator_output([{"role": "implementer"}]),
    )
    human = Human("The orders loader.", "5xx and timeouts.")

    planned = orchestrator.plan(Task("add a retry"), CWD, human)

    assert human.asked == ["Which loader?", "Retry on 5xx only?"]
    assert planned.document is not None
    assert [e.answer for e in planned.interview] == ["The orders loader.", "5xx and timeouts."]


def test_each_round_is_handed_what_has_already_been_asked():
    """The port has no memory. A round that could not see the last answer would
    ask the same question again."""
    orchestrator, runner = an_orchestrator(
        asks("Which loader?"),
        asks("Retry on 5xx only?"),
        READY,
        orchestrator_output([{"role": "implementer"}]),
    )

    orchestrator.plan(Task("add a retry"), CWD, Human("The orders loader.", "5xx only."))

    second_round = prompts(runner)[1]
    assert "Which loader?" in second_round
    assert "The orders loader." in second_round


def test_an_interview_that_has_nothing_to_ask_ends_immediately():
    """A one-line fix should not cost a conversation."""
    orchestrator, runner = an_orchestrator(READY, orchestrator_output([{"role": "implementer"}]))
    human = Human()

    planned = orchestrator.plan(Task("fix the typo in the docstring"), CWD, human)

    assert human.asked == []
    assert planned.interview == ()
    assert planned.document is not None
    assert len(prompts(runner)) == 2, "one round, then the plan"


def test_the_human_can_end_the_interview_and_still_get_a_plan():
    """Enter on an empty line, and what they have already answered still counts."""
    orchestrator, _ = an_orchestrator(
        asks("Which loader?", "Retry on 5xx only?", "What timeout?"),
        orchestrator_output([{"role": "implementer"}]),
    )
    human = Human("The orders loader.", None)

    planned = orchestrator.plan(Task("add a retry"), CWD, human)

    assert [e.question for e in planned.interview] == ["Which loader?"]
    assert planned.document is not None, "ending the interview must not cost the plan"


def test_the_interview_stops_asking_after_the_round_cap():
    """A model that always has one more question does not get to charge for it."""
    orchestrator, _ = an_orchestrator(
        *([asks("And another thing?")] * MAX_ROUNDS),
        orchestrator_output([{"role": "implementer"}]),
    )
    human = Human(*["yes"] * 20)

    planned = orchestrator.plan(Task("add a retry"), CWD, human)

    assert len(human.asked) == MAX_ROUNDS
    assert planned.document is not None


def test_a_round_that_answers_in_the_wrong_shape_ends_the_interview():
    """The planning pass is what has to work. An unparsable interview is a
    reason to stop asking, not a reason to stop."""
    orchestrator, _ = an_orchestrator(
        json.dumps({"type": "result", "is_error": False, "result": "I have some questions!"}),
        orchestrator_output([{"role": "implementer"}]),
    )
    human = Human("never asked")

    planned = orchestrator.plan(Task("add a retry"), CWD, human)

    assert human.asked == []
    assert planned.document is not None


# --- what the answers are for ------------------------------------------------


def test_the_answers_reach_the_planning_pass():
    """Otherwise the interview is theatre: the plan is what gets executed."""
    orchestrator, runner = an_orchestrator(
        asks("Which loader?"), READY, orchestrator_output([{"role": "implementer"}])
    )

    orchestrator.plan(Task("add a retry"), CWD, Human("The orders loader."))

    planning = prompts(runner)[-1]
    assert "The orders loader." in planning
    assert "These answers are the Task now" in planning


def test_a_single_shot_plan_carries_no_interview_section():
    orchestrator, runner = an_orchestrator(orchestrator_output([{"role": "implementer"}]))

    planned = orchestrator.plan(Task("add a retry"), CWD)

    assert len(prompts(runner)) == 1, "a plan with nobody to ask asked anyway"
    assert "What the human told you" not in prompts(runner)[0]
    assert planned.interview == ()


# --- the glossary ------------------------------------------------------------


def test_terms_are_resolved_against_the_projects_own_glossary(tmp_path):
    """A term settled in the interview is recorded where the next Task will
    find it, rather than being settled again next week."""
    (tmp_path / "CONTEXT.md").write_text("# Widgets\n\n## Language\n", encoding="utf-8")
    orchestrator, runner = an_orchestrator(
        asks("What is a late-arriving fact here?"),
        READY,
        orchestrator_output([{"role": "implementer"}]),
    )

    orchestrator.plan(Task("handle late-arriving facts"), tmp_path, Human("Older than the batch."))

    interview = prompts(runner)[0]
    assert "CONTEXT.md" in interview
    assert "record the decision there" in interview
    assert "Change nothing else in the repository" in interview


def test_a_project_with_no_glossary_is_not_given_one_mid_interview(tmp_path):
    orchestrator, runner = an_orchestrator(READY, orchestrator_output([{"role": "implementer"}]))

    orchestrator.plan(Task("add a retry"), tmp_path, Human())

    assert "do not start a glossary" in prompts(runner)[0]


# --- skills ------------------------------------------------------------------


def test_the_interview_skill_reaches_the_agent_by_the_capability_tier_path():
    """`grill-with-docs` is the interview and the writing-down as one job. It
    travels the same route as every other skill (ADR-0005), so a Provider that
    cannot take it natively gets it — and the two it composes — as Fragments,
    without this Role knowing the difference."""
    orchestrator, runner = an_orchestrator(
        asks("Which loader?"), READY, orchestrator_output([{"role": "implementer"}])
    )

    orchestrator.plan(Task("add a retry"), CWD, Human("The orders loader."))

    interview = prompts(runner)[0]
    assert "/agentforge:grill-with-docs" in interview


def test_the_interview_skill_is_not_delivered_to_a_pass_with_nobody_in_the_room():
    """An interview skill has nothing to say to a single-shot plan, and a Role
    told to conduct one anyway is a Role told to wait for an empty room."""
    orchestrator, runner = an_orchestrator(orchestrator_output([{"role": "implementer"}]))

    orchestrator.plan(Task("add a retry"), CWD)

    assert "/agentforge:grill-with-docs" not in prompts(runner)[0]
    assert "/agentforge:domain-modeling" in prompts(runner)[0]


def test_the_orchestrator_declares_its_planning_equipment():
    """What a planning pass works with. The interview swaps in its own."""
    assert ORCHESTRATOR.skills == ("domain-modeling", "to-spec", "to-tickets")
    assert ORCHESTRATOR.tier is ModelTier.DEEP


# --- the transcript ----------------------------------------------------------


def test_an_empty_transcript_says_so_rather_than_rendering_nothing():
    assert "first round" in render_transcript(())


def test_a_transcript_pairs_each_question_with_its_answer():
    rendered = render_transcript((Exchange("Which loader?", "The orders loader."),))

    assert "Which loader?" in rendered
    assert "The orders loader." in rendered
