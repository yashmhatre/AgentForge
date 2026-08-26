"""Gates: the predicates, and the registry that finds them.

A Gate is a predicate over Run State and the Run Log returning cleared, blocked,
or errored. Everything here is that predicate called directly — the runtime's
side of it (suspend, halt, resume across two invocations) is in
`tests/test_runtime.py`, because that is where a Run exists.
"""

from __future__ import annotations

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

IMPLEMENTER = Role("implementer", ModelTier.STANDARD)


def a_state(*gates: GateEntry) -> RunState:
    return RunState(
        issue=12,
        plan=Plan(summary="Add a retry."),
        roster=Roster((IMPLEMENTER,)),
        gates=gates,
    )


def a_context(kind: str, state: RunState | None = None, step: int = 1) -> GateContext:
    return GateContext(
        state=state if state is not None else a_state(),
        kind=kind,
        role="implementer",
        step=step,
    )


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
    """`tests` is #10 and `security` is #11. Until then a Workflow declaring one
    stops the Run and says so, which is the safe direction to be wrong in."""
    for kind in ("tests", "security"):
        entry = evaluate_gate(kind, a_context(kind))

        assert entry.verdict is GateVerdict.ERRORED, kind
        assert kind in entry.summary


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
