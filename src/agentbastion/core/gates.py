"""Gates: what holds a Run between two Steps, as predicates in a registry.

A Gate is a predicate over Run State and the Run Log returning cleared, blocked,
or errored. Blocked suspends the Run — nothing is wrong, and the Gate can still
clear. Errored halts it: a Gate that cannot evaluate has nothing to clear, so
suspending one would invite a resume that suspends again forever.

The registry is the point. `GATES` maps a kind onto its predicate, the Workflow
parser validates against its keys, and the runtime looks a kind up rather than
knowing any. Adding a kind is an entry here and nothing in `runtime.py`, exactly
as a seventh Role is an entry in `RUNNERS`.

Every Gate is handed the same context, the Command Runner and the working tree
included, so that one whose verdict comes from executing something has what it
needs without the runtime knowing which one that is. Most predicates ignore both.

A Gate that draws its verdict from a Role's output names that Role in
`invalidates`, which un-retires the Step that produced it (ADR-0008). A Gate
that reads something else — a human, a suite it re-executes — names nobody, and
every Step behind it stays behind it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from ..agents.security import SECURITY
from .config import load_config
from .contracts import GateEntry, GateVerdict, RunState
from .process import CommandResult, CommandRunner, MissingBinary


@dataclass(frozen=True)
class GateContext:
    """What a Gate is asked about: the Run, and the Step it stands behind.

    `role` is the Role of that Step, not the Role the Gate judges — a human Gate
    stands behind a Step whose work it has no opinion of. A predicate that does
    judge a Role's output names it in the verdict it returns.

    `runner` and `root` are what a Gate acts through when its verdict comes from
    running something rather than from reading the Run Log. They are on every
    context rather than on the ones that need them, so that registering a Gate
    stays the whole cost of adding one.
    """

    state: RunState
    kind: str
    role: str
    step: int
    runner: CommandRunner
    root: Path

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
    acknowledgement is running `agentbastion implement` again — they were told the
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
            "Run's branch, then re-run `agentbastion implement` to carry on."
        ),
    )


#: The exit status a test runner spends on "tests ran and some failed", which is
#: the one non-zero status that is a report on the code rather than on the run.
#: pytest spends 2 through 5 on interruption, internal error, bad usage, and
#: nothing collected; none of those say anything about the repository.
SUITE_FAILED = 1

#: How much of a failing suite reaches the Run Log. The end of it: a test runner
#: puts its summary last, and a comment nobody scrolls to the bottom of is a
#: comment nobody reads.
TAIL_LINES = 40
TAIL_CHARS = 2000


def tests(context: GateContext) -> GateEntry:
    """The test-suite Gate: run the suite, and read the exit status.

    It re-executes rather than reading what the Tester said about the suite. An
    Agent Result is a Role's account of its own work, and a Gate that took one at
    its word would be checking the report rather than the repository. So this
    Gate names nobody in `invalidates`: it judged no Step's output, every Step
    behind it stays behind it, and the Run that resumes runs the suite again
    rather than reading this verdict back (ADR-0008). Naming the Tester here
    would un-retire the Tester Step and deadlock the Run.

    Cleared, blocked, and errored are three different things that happen when you
    run a suite. It passed. It ran and reported failures — nothing is wrong with
    the plan, and the next commit may well clear it, which is Suspended exactly.
    Or it never reached a verdict, and a Gate with nothing to clear halts the Run
    rather than inviting a resume that suspends again.

    ADR-0007's default-deny governs what a Role may run, not what AgentBastion
    runs. A Gate is not an Agent: the suite is the one the project declared, its
    exit status is read rather than interpreted, and no model chose either.
    """
    suite = load_config(context.root).test_suite
    rendered = " ".join(suite)

    if not context.runner.has_binary(suite[0]):
        return _cannot_run(rendered, f"{suite[0]!r} is not installed or not on PATH")

    try:
        result = context.runner.run(suite, cwd=context.root)
    except MissingBinary as exc:
        # The tool resolved on PATH and then would not start: a Windows `npm.cmd`
        # is the everyday way to arrive here. Ending the Run is the Gate's job
        # either way — raising would crash it instead, and nothing a human could
        # act on would reach the Issue.
        return _cannot_run(rendered, str(exc))

    if result.ok:
        return GateEntry(
            kind="",
            verdict=GateVerdict.CLEARED,
            summary=f"`{rendered}` passed.",
        )

    if result.returncode == SUITE_FAILED:
        return GateEntry(
            kind="",
            verdict=GateVerdict.BLOCKED,
            summary=(
                f"`{rendered}` failed. The Run stops here rather than carrying a red "
                f"suite to Sign-off.\n\n{_tail(result)}"
            ),
        )

    return GateEntry(
        kind="",
        verdict=GateVerdict.ERRORED,
        summary=(
            f"`{rendered}` exited {result.returncode}, which is not a report on the "
            "code: the suite did not run to a verdict, so there is nothing here for a "
            f"later Run to clear.\n\n{_tail(result)}"
        ),
    )


def _cannot_run(rendered: str, reason: str) -> GateEntry:
    """The suite never started, which is not a report on the code.

    Errored rather than blocked: waiting clears nothing, and the thing to fix is
    the machine or the declaration rather than the repository.
    """
    return GateEntry(
        kind="",
        verdict=GateVerdict.ERRORED,
        summary=(
            f"the test-suite Gate cannot run `{rendered}`: {reason}. Name the suite this "
            "repository runs under `gates.tests.suite` in `.agentbastion/config.yaml`."
        ),
    )


def _tail(result: CommandResult) -> str:
    """The end of what the suite printed, fenced for the Issue.

    Four backticks rather than three: a failing test in a repository like this
    one prints fenced blocks of its own, and a fence closed early takes the rest
    of the Run Log entry with it.
    """
    text = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    text = "\n".join(text.splitlines()[-TAIL_LINES:])[-TAIL_CHARS:].strip()
    if not text:
        return "It printed nothing."
    return f"````text\n{text}\n````"


def security(context: GateContext) -> GateEntry:
    """The clean-pass Gate: the Security Agent's Findings, read off the Run Log.

    The mirror image of the test-suite Gate above. This verdict is drawn from a
    Role's own output, so it names that Role in `invalidates` (ADR-0008): a human
    who fixes a finding needs the audit run again, and a Run that resumed past
    this entry would re-read a finding about code that no longer exists — which
    is the deadlock the ADR was written about, from the other side.

    An audit that reported nothing clears it. That is why the Security Role is
    told to escalate rather than report an empty list when it could not look:
    "audited and clean" and "did not audit" are the same shape here, and only
    the Role knows which one happened.

    Security not having run at all errors rather than blocks. A Gate waits for
    something that can still arrive, and a Step that is not in front of this Run
    never will.
    """
    audits = [
        result
        for result in context.state.results
        if result.role == SECURITY.name and result.ok
    ]
    if not audits:
        return GateEntry(
            kind="",
            verdict=GateVerdict.ERRORED,
            summary=(
                f"a {SECURITY.name} Gate stands behind the {context.role} Step, and the "
                f"{SECURITY.name} Role has not completed in this Run. There is no audit "
                "to read, so there is nothing here to clear: put a `security` Step in "
                "front of this Gate."
            ),
        )

    findings = audits[-1].findings
    if not findings:
        return GateEntry(
            kind="",
            verdict=GateVerdict.CLEARED,
            summary=f"the {SECURITY.name} Agent audited the change and reported no findings",
        )

    listed = "\n".join(
        f"- `{finding.location or 'no location reported'}` — "
        f"{finding.risk.strip() or 'no risk described'}"
        for finding in findings
    )
    return GateEntry(
        kind="",
        verdict=GateVerdict.BLOCKED,
        invalidates=SECURITY.name,
        # No closing instruction here: the comment this verdict travels in
        # already tells the reader that the Security Step will run again, as
        # every verdict naming a Role in `invalidates` does.
        summary=(
            f"the {SECURITY.name} Agent reported {len(findings)} "
            f"finding{'' if len(findings) == 1 else 's'}, so the Run holds here rather "
            f"than carrying them to Sign-off.\n\n{listed}"
        ),
    )


#: Gate kind to predicate. The Workflow parser validates names against these
#: keys, so a definition naming `vibes` is refused at load time.
GATES: dict[str, GateCheck] = {
    "human": human,
    "tests": tests,
    "security": security,
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


__all__ = ["GATES", "GateCheck", "GateContext", "evaluate_gate", "human", "security", "tests"]
