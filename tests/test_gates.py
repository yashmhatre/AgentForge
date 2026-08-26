"""Gates: the predicates, and the registry that finds them.

A Gate is a predicate over Run State and the Run Log returning cleared, blocked,
or errored. Everything here is that predicate called directly — the runtime's
side of it (suspend, halt, resume across two invocations) is in
`tests/test_runtime.py`, because that is where a Run exists.
"""

from __future__ import annotations

from pathlib import Path

from agentforge.core.contracts import (
    GateEntry,
    GateVerdict,
    ModelTier,
    Plan,
    Role,
    Roster,
    RunState,
)
from agentforge.core.gates import GATES, GateContext, evaluate_gate
from agentforge.core.process import MissingBinary

from .fakes import FakeRunner

IMPLEMENTER = Role("implementer", ModelTier.STANDARD)


def a_state(*gates: GateEntry) -> RunState:
    return RunState(
        issue=12,
        plan=Plan(summary="Add a retry."),
        roster=Roster((IMPLEMENTER,)),
        gates=gates,
    )


def a_context(
    kind: str,
    state: RunState | None = None,
    step: int = 1,
    runner: FakeRunner | None = None,
    root: Path | str = "/repo/pipelines",
) -> GateContext:
    return GateContext(
        state=state if state is not None else a_state(),
        kind=kind,
        role="implementer",
        step=step,
        runner=runner if runner is not None else FakeRunner(),
        root=Path(root),
    )


def a_suite(stdout: str = "", returncode: int = 0, stderr: str = "") -> FakeRunner:
    """A machine with pytest on it, answering with one scripted status."""
    runner = FakeRunner().install("pytest")
    runner.script("pytest", stdout=stdout, stderr=stderr, returncode=returncode)
    return runner


# --- the registry ------------------------------------------------------------


def test_the_three_kinds_m3_names_are_all_registered():
    """Registered rather than hardcoded: a Workflow naming one of these is
    accepted because the registry answers for it, not because the runtime does."""
    assert set(GATES) == {"tests", "security", "human"}


def test_a_kind_nobody_registered_errors_rather_than_being_ignored():
    """A Gate the runtime cannot find is a Gate that cannot clear. Silently
    passing one would let a Workflow declare a check that never runs."""
    entry = evaluate_gate("vibes", a_context("vibes"))

    assert entry.verdict is GateVerdict.ERRORED
    assert "vibes" in entry.summary


def test_a_kind_that_is_not_built_yet_errors_rather_than_clearing_silently():
    """`security` is #11. Until it lands a Workflow declaring one stops the Run
    and says so, which is the safe direction to be wrong in."""
    entry = evaluate_gate("security", a_context("security"))

    assert entry.verdict is GateVerdict.ERRORED
    assert "security" in entry.summary


def test_a_verdict_is_stamped_with_the_kind_and_the_step_that_produced_it():
    """The predicate answers; the registry records which Gate was asking, so the
    Run Log entry identifies itself without the predicate having to."""
    entry = evaluate_gate("human", a_context("human", step=3))

    assert entry.kind == "human"
    assert entry.step == 3


def test_registering_a_kind_is_the_whole_cost_of_adding_one(monkeypatch):
    monkeypatch.setitem(
        GATES,
        "moonphase",
        lambda context: GateEntry("", GateVerdict.CLEARED, summary="waxing"),
    )

    assert evaluate_gate("moonphase", a_context("moonphase")).verdict is GateVerdict.CLEARED


# --- the human Gate ----------------------------------------------------------


def test_a_human_gate_blocks_the_first_time_it_is_asked():
    entry = evaluate_gate("human", a_context("human"))

    assert entry.verdict is GateVerdict.BLOCKED
    assert entry.summary


def test_a_human_gate_judges_nobodys_output_so_it_invalidates_no_step():
    """A human Gate blocks on a human, not on the Role in front of it. Marking
    that Step for re-run would re-run work nobody questioned — and the Gate would
    block again, forever."""
    entry = evaluate_gate("human", a_context("human"))

    assert entry.invalidates == ""


def test_a_human_gate_clears_once_the_run_log_shows_it_has_already_blocked():
    """The human's acknowledgement is re-running `agentforge implement`: they
    were told the Run stopped, they looked, and they came back. Read off the Run
    Log rather than a flag, so it survives the laptop the first Run was on."""
    blocked = GateEntry("human", GateVerdict.BLOCKED, step=1)

    entry = evaluate_gate("human", a_context("human", a_state(blocked)))

    assert entry.verdict is GateVerdict.CLEARED


def test_a_human_gate_blocked_at_another_step_is_a_different_gate():
    blocked_elsewhere = GateEntry("human", GateVerdict.BLOCKED, step=1)

    entry = evaluate_gate("human", a_context("human", a_state(blocked_elsewhere), step=2))

    assert entry.verdict is GateVerdict.BLOCKED


def test_another_kinds_block_at_this_step_does_not_clear_the_human_gate():
    other = GateEntry("security", GateVerdict.BLOCKED, step=1, invalidates="security")

    entry = evaluate_gate("human", a_context("human", a_state(other)))

    assert entry.verdict is GateVerdict.BLOCKED


# --- the test-suite Gate -----------------------------------------------------


def test_the_suite_runs_through_the_command_runner_in_the_repository_it_judges():
    """Nothing in AgentForge imports `subprocess`, this Gate included — and a
    suite run somewhere other than the Run's tree judges somebody else's code."""
    runner = a_suite(stdout="24 passed")

    evaluate_gate("tests", a_context("tests", runner=runner, root="/repo/pipelines"))

    assert runner.only("pytest") == ("pytest",)
    assert runner.cwds[-1] == str(Path("/repo/pipelines"))


def test_a_passing_suite_clears_the_gate_and_the_run_goes_on():
    entry = evaluate_gate("tests", a_context("tests", runner=a_suite(stdout="24 passed")))

    assert entry.verdict is GateVerdict.CLEARED


def test_a_failing_suite_blocks_rather_than_errors_because_a_commit_can_clear_it():
    """Blocked is Suspended: the suite ran, it reported on the code, and the
    next commit may well fix it. Nothing is wrong with the plan."""
    entry = evaluate_gate(
        "tests",
        a_context("tests", runner=a_suite(stdout="1 failed, 23 passed", returncode=1)),
    )

    assert entry.verdict is GateVerdict.BLOCKED


def test_a_failing_suite_puts_its_own_output_in_the_verdict():
    """The reason the Run stopped belongs on the Issue. A failure that lives only
    in the terminal of whoever started the Run is a failure nobody kept."""
    entry = evaluate_gate(
        "tests",
        a_context(
            "tests",
            runner=a_suite(stdout="E   assert 3 == 4\n1 failed, 23 passed", returncode=1),
        ),
    )

    assert "E   assert 3 == 4" in entry.summary
    assert "1 failed, 23 passed" in entry.summary


def test_a_suite_that_could_not_be_run_errors_rather_than_blocking():
    """pytest spends 4 on a usage error: nothing ran, so there is no verdict on
    the code and nothing a later Run could clear by waiting."""
    entry = evaluate_gate(
        "tests",
        a_context("tests", runner=a_suite(stderr="ERROR: file not found", returncode=4)),
    )

    assert entry.verdict is GateVerdict.ERRORED
    assert "ERROR: file not found" in entry.summary


def test_a_suite_binary_that_is_not_installed_errors_before_anything_runs():
    """The clearest case of a suite that cannot be run at all. Named as the
    binary a human has to install rather than as a failing test."""
    runner = FakeRunner().uninstall("pytest")

    entry = evaluate_gate("tests", a_context("tests", runner=runner))

    assert entry.verdict is GateVerdict.ERRORED
    assert "pytest" in entry.summary
    assert not runner.calls, "the Gate ran something on a machine that has nothing"


def test_a_suite_that_will_not_start_errors_rather_than_crashing_the_run():
    """`has_binary` can say yes to something that then refuses to start — a
    Windows `npm.cmd` resolves on PATH and not to CreateProcess. A Gate that let
    that out as an exception would crash the Run rather than ending it, and the
    Issue would carry no reason at all."""

    class WillNotStart(FakeRunner):
        def run(self, argv, **kwargs):
            raise MissingBinary(str(argv[0]))

    entry = evaluate_gate("tests", a_context("tests", runner=WillNotStart().install("pytest")))

    assert entry.verdict is GateVerdict.ERRORED
    assert "pytest" in entry.summary


def test_the_test_suite_gate_judges_no_roles_output_so_it_invalidates_no_step():
    """The load-bearing one (ADR-0008). This Gate re-runs the suite rather than
    reading the Tester's account of it, so it un-retires nobody — naming the
    Tester here would re-run the Tester Step forever."""
    entry = evaluate_gate(
        "tests", a_context("tests", runner=a_suite(returncode=1, stdout="1 failed"))
    )

    assert entry.invalidates == ""


def test_the_suite_is_re_run_rather_than_read_back_off_the_run_log():
    """Unlike the human Gate, an earlier block clears nothing: what makes this
    Gate pass is a suite that passes now."""
    blocked = GateEntry("tests", GateVerdict.BLOCKED, step=1)
    runner = a_suite(stdout="24 passed")

    entry = evaluate_gate("tests", a_context("tests", a_state(blocked), runner=runner))

    assert entry.verdict is GateVerdict.CLEARED
    assert runner.ran("pytest"), "the Gate answered from the Run Log without running"


def test_the_suite_a_project_declares_is_the_one_that_runs(tmp_path):
    """A repository that does not run pytest says so in its Project Context. The
    Gate reads it rather than guessing from the shape of the tree."""
    config_dir = tmp_path / ".agentforge"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "gates:\n  tests:\n    suite: npm test --silent\n", encoding="utf-8"
    )
    runner = FakeRunner().install("npm")

    entry = evaluate_gate("tests", a_context("tests", runner=runner, root=tmp_path))

    assert runner.only("npm") == ("npm", "test", "--silent")
    assert entry.verdict is GateVerdict.CLEARED
