"""Gates: what holds a Run between two Steps, as predicates in a registry.

A Gate is a predicate over Run State and the Run Log returning cleared, blocked,
or errored. Blocked suspends the Run — nothing is wrong, and the Gate can still
clear. Errored halts it: a Gate that cannot evaluate has nothing to clear, so
suspending one would invite a resume that suspends again forever.

The registry is the point. `GATES` maps a kind onto its predicate, the Workflow
parser validates against its keys, and the runtime looks a kind up rather than
knowing any. Adding the test-suite Gate (#10) or the Security Gate (#11) is an
entry here and nothing in `runtime.py`, exactly as a seventh Role is an entry in
`RUNNERS`.

A Gate that draws its verdict from a Role's output names that Role in
`invalidates`, which un-retires the Step that produced it (ADR-0008). A Gate
that reads something else — a human, a suite it re-executes — names nobody, and
every Step behind it stays behind it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from .contracts import GateEntry, GateVerdict, RunState


@dataclass(frozen=True)
class GateContext:
    """What a Gate is asked about: the Run, and the Step it stands behind.

    `role` is the Role of that Step, not the Role the Gate judges — a human Gate
    stands behind a Step whose work it has no opinion of. A predicate that does
    judge a Role's output names it in the verdict it returns.
    """

    state: RunState
    kind: str
    role: str
    step: int

    @property
    def verdicts(self) -> tuple[GateEntry, ...]:
        """What this Gate has already said, at this Step, in Run Log order.

        Identity is the kind and the position: one Workflow may declare the same
        kind of Gate twice, and the second one has not spoken because the first
        one did.
        """
        return tuple(
            entry
            for entry in self.state.gates
            if entry.kind == self.kind and entry.step == self.step
        )


GateCheck = Callable[[GateContext], GateEntry]


def human(context: GateContext) -> GateEntry:
    """A human Gate: the Run stops, and a human decides when it goes on.

    It clears once the Run Log shows it has already blocked here. The human's
    acknowledgement is running `agentforge implement` again — they were told the
    Run stopped, they looked at the branch, and they came back. Nothing else
    would be less ceremony: resuming is a command they have to type either way.

    Read off the Run Log rather than held in memory, so the Run that resumes may
    be on a different machine from the Run that suspended (ADR-0002).
    """
    if any(entry.blocked for entry in context.verdicts):
        return GateEntry(
            kind="",
            verdict=GateVerdict.CLEARED,
            summary="a human was asked to look at this Step and has re-run the Run",
        )
    return GateEntry(
        kind="",
        verdict=GateVerdict.BLOCKED,
        summary=(
            f"a human Gate follows the {context.role} Step. Review the work on the "
            "Run's branch, then re-run `agentforge implement` to carry on."
        ),
    )


def _not_built(kind: str, ticket: str) -> GateCheck:
    """A kind a Workflow may name and this version cannot evaluate.

    Erroring rather than clearing is the safe direction: a Security Gate that
    passed because nobody had written it yet is worse than a Run that stops.
    """

    def check(context: GateContext) -> GateEntry:
        return GateEntry(
            kind="",
            verdict=GateVerdict.ERRORED,
            summary=(
                f"the {kind!r} Gate is declared by this Workflow but is not "
                f"implemented in this version of AgentForge ({ticket})"
            ),
        )

    return check


#: Gate kind to predicate. The Workflow parser validates names against these
#: keys, so a definition naming `vibes` is refused at load time.
GATES: dict[str, GateCheck] = {
    "human": human,
    "tests": _not_built("tests", "#10"),
    "security": _not_built("security", "#11"),
}


def evaluate_gate(kind: str, context: GateContext) -> GateEntry:
    """Ask one Gate, and stamp its answer with which Gate was asked.

    Total on purpose: an unregistered kind errors rather than raising, so the
    runtime has one way of ending at a Gate rather than two. A Workflow cannot
    reach here with an unknown kind — the parser refuses those — but a Workflow
    built in code can.
    """
    check = GATES.get(kind)
    if check is None:
        entry = GateEntry(
            kind=kind,
            verdict=GateVerdict.ERRORED,
            summary=(
                f"no Gate of kind {kind!r} is registered in this version; "
                f"kinds are: {', '.join(sorted(GATES))}"
            ),
        )
    else:
        entry = check(context)

    return replace(entry, kind=kind, step=context.step)


__all__ = ["GATES", "GateCheck", "GateContext", "evaluate_gate", "human"]
