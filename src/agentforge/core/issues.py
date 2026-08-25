"""The sole boundary to the issue tracker.

Every GitHub call in AgentForge is in this file, made by invoking `gh` through
the Command Runner. AgentForge implements no GitHub authentication and speaks to
no REST API, because `gh` already solves both (ADR-0002).

There is deliberately no Tracker interface. When Azure DevOps becomes real, this
is the file that gets rewritten, and the estimate in ADR-0002 is that this is a
day of work rather than a rewrite. Keeping every call site here is what makes
that estimate true.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .contracts import (
    LEGACY_LABELS,
    RUN_LABELS,
    AgentResult,
    ContextPack,
    GateEntry,
    GateVerdict,
    Outcome,
    RunState,
    RunStatus,
)
from .plan_format import (
    extract_gate_block,
    extract_result_block,
    parse_issue_body,
    render_gate_block,
    render_result_block,
)
from .process import CommandRunner, MissingBinary, require

GH_HINT = "Install the GitHub CLI (https://cli.github.com) and run `gh auth login`."


class IssueError(RuntimeError):
    """The tracker could not be read or written."""


@dataclass(frozen=True)
class Comment:
    """One entry in the Run Log."""

    author: str
    body: str


@dataclass(frozen=True)
class Issue:
    """A GitHub issue as AgentForge reads it."""

    number: int
    title: str
    body: str
    url: str = ""
    labels: tuple[str, ...] = ()
    comments: tuple[Comment, ...] = field(default=())


class GitHub:
    """`gh`, wrapped narrowly enough that swapping the tracker is one file."""

    def __init__(self, runner: CommandRunner, cwd: Path | str) -> None:
        self.runner = runner
        self.cwd = Path(cwd)
        #: Status labels this instance has applied, so a second transition in
        #: the same Run clears the first without re-reading the Issue or
        #: blind-firing a removal for every label in the scheme.
        self._applied: dict[int, str] = {}

    def preflight(self) -> None:
        """Confirm `gh` exists before a Run spends anything."""
        try:
            require(self.runner, "gh", GH_HINT)
        except MissingBinary as exc:
            raise IssueError(str(exc)) from exc

    def _gh(self, *args: str, check: bool = True):
        result = self.runner.run(("gh", *args), cwd=self.cwd)
        if check and not result.ok:
            detail = (result.stderr or result.stdout or "").strip()
            raise IssueError(f"`gh {' '.join(args)}` failed: {detail[:600]}")
        return result

    # --- reading -----------------------------------------------------------

    def read_issue(self, number: int) -> Issue:
        result = self._gh(
            "issue",
            "view",
            str(number),
            "--json",
            "number,title,body,url,labels,comments",
        )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise IssueError(f"could not parse `gh issue view {number}` output: {exc}") from exc

        return Issue(
            number=int(data.get("number", number)),
            title=str(data.get("title") or ""),
            body=str(data.get("body") or ""),
            url=str(data.get("url") or ""),
            labels=tuple(label.get("name", "") for label in data.get("labels") or ()),
            comments=tuple(
                Comment(
                    author=str((comment.get("author") or {}).get("login") or ""),
                    body=str(comment.get("body") or ""),
                )
                for comment in data.get("comments") or ()
            ),
        )

    # --- writing -----------------------------------------------------------

    def create_issue(self, title: str, body: str, labels: tuple[str, ...] = ()) -> Issue:
        args = ["issue", "create", "--title", title, "--body", body]
        for label in labels:
            self.ensure_label(label)
            args += ["--label", label]

        url = self._gh(*args).stdout.strip().splitlines()[-1].strip()
        return Issue(number=_number_from_url(url), title=title, body=body, url=url, labels=labels)

    def post_comment(self, number: int, body: str) -> None:
        self._gh("issue", "comment", str(number), "--body", body)

    def ensure_label(self, label: str) -> None:
        """Labels are created on demand; a fresh repository has none of ours."""
        self._gh("label", "create", label, "--description", "AgentForge run status", check=False)

    def set_label(self, number: int, label: str) -> None:
        self.ensure_label(label)
        self._gh("issue", "edit", str(number), "--add-label", label)

    def remove_label(self, number: int, label: str) -> None:
        self._gh("issue", "edit", str(number), "--remove-label", label, check=False)

    def set_status(self, issue: Issue | int, status: RunStatus) -> None:
        """One status label at a time, so a Run's state stays unambiguous.

        The Issue is a snapshot taken when the Run started, so its labels only
        say what was there before. Once this Run has set a status of its own,
        that label is what the Issue is actually wearing and the snapshot is
        stale — which is why the second transition of a Run clears one label
        rather than firing a removal for the first one all over again.
        """
        number = issue.number if isinstance(issue, Issue) else int(issue)
        if number in self._applied:
            known = {self._applied[number]}
        else:
            known = set(issue.labels) if isinstance(issue, Issue) else set()

        for stale in sorted(known & set(RUN_LABELS) - {status.label}):
            self.remove_label(number, stale)

        self.set_label(number, status.label)
        self._applied[number] = status.label

    def open_draft_pr(
        self, *, title: str, body: str, head: str, base: str = "main"
    ) -> str:
        """Open a draft pull request and stop. No Workflow ever merges."""
        result = self._gh(
            "pr",
            "create",
            "--draft",
            "--title",
            title,
            "--body",
            body,
            "--head",
            head,
            "--base",
            base,
        )
        return result.stdout.strip().splitlines()[-1].strip()

    def default_branch(self) -> str:
        result = self._gh("repo", "view", "--json", "defaultBranchRef", check=False)
        if not result.ok:
            return "main"
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return "main"
        return str((data.get("defaultBranchRef") or {}).get("name") or "main")


# --- the Run Log ----------------------------------------------------------


def render_run_log_comment(
    result: AgentResult, *, step: int | None = None, of: int | None = None
) -> str:
    """One Run Log entry: prose a human reads, and a block a Run resumes from.

    `step` is the 1-based position of the Step this result retired or stopped on,
    and `of` how many the Workflow declares. A Role name alone does not locate an
    Escalation — a Workflow may name the same Role twice, and the human who has
    to correct the plan is looking for one Step in it. Both are optional because
    not every result is a Step: a Run that fails after every Step has run is
    failing the Run, and naming a position there would invent one.

    The position stays in the prose. The result block is what later Runs parse to
    work out which Steps are behind them, and a position recorded there would be
    a second answer to a question `current_step` already derives.
    """
    heading = {
        Outcome.COMPLETED: "completed",
        Outcome.ESCALATED: "escalated",
        Outcome.FAILED: "failed",
    }[result.outcome]

    lines = [
        f"### {result.role} — {heading}{_position(step, of)}",
        "",
        f"**Model Tier:** `{result.tier}`",
        "",
        result.summary.strip() or "_no summary reported_",
    ]

    if result.outcome is Outcome.ESCALATED:
        lines += [
            "",
            (
                "The Plan was not executed. Per ADR-0003 a Role that finds the Plan wrong "
                "stops rather than improvising. Correct the plan block in the issue body "
                "and re-run `agentforge implement`."
            ),
        ]

    if result.files_changed:
        lines += ["", "**Files changed:**", ""]
        lines += [f"- `{path}`" for path in result.files_changed]

    if result.detail.strip():
        lines += ["", "<details><summary>Detail</summary>", "", result.detail.strip(), "", "</details>"]

    lines += ["", render_result_block(result.to_dict())]
    return "\n".join(lines) + "\n"


#: What each verdict means for the Run, in the words the Run Log uses.
_GATE_ENDINGS: dict[GateVerdict, str] = {
    GateVerdict.BLOCKED: (
        "The Run is suspended here. Nothing is wrong with the plan — this Gate can "
        "still clear."
    ),
    GateVerdict.ERRORED: (
        "The Run is halted here. A Gate that cannot evaluate has nothing to clear, "
        "so waiting would not help."
    ),
}


def render_gate_comment(entry: GateEntry, *, of: int | None = None) -> str:
    """One Run Log entry for a Gate's verdict. See ADR-0008.

    It carries a Gate block rather than a result block, because `parse_run_log`
    returns Agent Results and a Gate is not an Agent — one counted as a Step
    would retire the Step it had just refused.

    Only a Gate that stopped the Run writes one. A Gate that cleared has nothing
    to tell the human and nothing the next Run needs, and posting one on every
    resume would fill the Issue with entries that say a Run carried on.
    """
    lines = [
        f"### {entry.kind} Gate — {entry.verdict}{_after(entry.step, of)}",
        "",
        entry.summary.strip() or "_no reason reported_",
    ]

    if entry.blocked and entry.invalidates:
        lines += [
            "",
            (
                f"This verdict was drawn from the **{entry.invalidates}** Step's own output, "
                "so that Step is marked for re-run: the next `agentforge implement` runs it "
                "again rather than reading this verdict back."
            ),
        ]

    ending = _GATE_ENDINGS.get(entry.verdict)
    if ending:
        lines += ["", ending]

    lines += ["", render_gate_block(entry.to_dict())]
    return "\n".join(lines) + "\n"


def _after(step: int, of: int | None) -> str:
    """Which Step this Gate stands behind, if the caller knew the total."""
    if not step:
        return ""
    return f" (after step {step} of {of})" if of else f" (after step {step})"


def _position(step: int | None, of: int | None) -> str:
    """Where in the Workflow this entry sits, if the caller knew."""
    if step is None:
        return ""
    return f" (step {step} of {of})" if of else f" (step {step})"


#: How each way of ending a Run reads in the Run Log. A Run that is still moving
#: has no ending, so `PLANNED` and `RUNNING` are deliberately absent.
_ENDINGS: dict[RunStatus, str] = {
    RunStatus.AWAITING_SIGNOFF: "Run complete — awaiting Sign-off",
    RunStatus.SUSPENDED: "Run suspended",
    RunStatus.HALTED: "Run halted",
    RunStatus.FAILED: "Run failed",
}


def render_terminal_comment(state: RunState) -> str:
    """The last entry in a Run Log: how the Run ended and how far it got.

    Every Run that starts posts exactly one of these. That is what makes
    escalation frequency countable off the tracker, which ADR-0003 calls the
    signal of Orchestrator quality — a Run that merely stops posting leaves a
    reader to infer why from the absence of a comment, and nobody counts an
    absence.

    It carries no result block. The Run Log is replayed to work out which Steps
    are behind a Run, and an ending is not a Step.
    """
    ending = _ENDINGS.get(state.status)
    if ending is None:
        raise IssueError(
            f"a Run in {state.status} has not ended, so there is nothing to conclude"
        )

    complete = state.status is RunStatus.AWAITING_SIGNOFF
    lines = [
        f"### {ending}" if complete else f"### {ending}{_stopped_at(state)}",
        "",
        f"- **Final state:** `{state.status.label}`",
        f"- **Escalated:** {_escalated_line(state)}",
        f"- **Steps completed:** {', '.join(state.done_roles) or 'none'}",
        f"- **Workflow:** `{state.workflow}`",
    ]

    waiting = _waiting_on(state)
    if waiting:
        lines.append(f"- **Waiting on:** {waiting}")
    lines.append("")

    last = state.results[-1] if state.results else None
    if last is not None and not last.ok:
        lines += [f"> {last.summary.strip() or 'no summary reported'}", ""]

    if complete and state.pull_request:
        lines += [f"Draft pull request: {state.pull_request}", ""]

    lines += [_what_next(state), ""]
    return "\n".join(lines)


def _waiting_on(state: RunState) -> str:
    """The Gate that stopped this Run, if one did.

    The label says a Run is suspended; it does not say what would clear it, and
    "waiting" without "on what" is what makes a stalled Run look like a crashed
    one. The last entry rather than the first: a Run that cleared one Gate and
    stopped at the next is waiting on the next.
    """
    stopped = [entry for entry in state.gates if entry.blocked or entry.errored]
    if not stopped:
        return ""
    last = stopped[-1]
    return f"the `{last.kind}` Gate after step {last.step}"


def _escalated_line(state: RunState) -> str:
    """Whether this Run escalated, and where — the countable half of the comment."""
    escalation = state.escalation
    if escalation is None:
        return "no"
    return f"yes, at step {state.current_step} ({escalation.role})"


def _stopped_at(state: RunState) -> str:
    """The Step the Run stopped on, as a position and a Role.

    A Role that escalated or failed did not retire its Step, so the Run is still
    standing on it. A Role that completed retired its Step, and a Run that stops
    after one — at a Gate — stopped on the Step behind it.
    """
    if not state.results:
        return ""
    last = state.results[-1]
    return f" at step {state.current_step - (1 if last.ok else 0)} — {last.role}"


def _what_next(state: RunState) -> str:
    """The reader's move. It differs for each way of stopping, which is the
    whole reason suspended, halted, and failed are three states and not one."""
    rerun = f"`agentforge implement {state.issue}`"
    if state.status is RunStatus.AWAITING_SIGNOFF:
        return "AgentForge stops here. Sign-off is a human Gate; no Workflow merges."
    if state.status is RunStatus.SUSPENDED:
        return (
            "The Run is waiting on a Gate it can still clear. Nothing is wrong with the "
            f"plan: re-run {rerun} once the Gate passes."
        )
    if state.status is RunStatus.HALTED:
        return (
            "Halted is not failed — the completed Steps above stand. Per ADR-0003 a Role "
            "that finds the plan wrong stops rather than improvising, so correct the plan "
            f"block in the issue body and re-run {rerun}."
        )
    return (
        "AgentForge could not finish this Run. The Run Log entry above carries the detail; "
        f"re-run {rerun} once the cause is gone."
    )


def parse_run_log(issue: Issue) -> tuple[AgentResult, ...]:
    """Recover every Agent Result the Run Log carries, in order."""
    results = []
    for comment in issue.comments:
        payload = extract_result_block(comment.body)
        if not payload or "role" not in payload or "outcome" not in payload:
            continue
        try:
            results.append(AgentResult.from_dict(payload))
        except (KeyError, ValueError):
            continue
    return tuple(results)


def parse_gate_log(issue: Issue) -> tuple[GateEntry, ...]:
    """Recover every Gate verdict the Run Log carries, in order.

    Kept apart from `parse_run_log` rather than merged into it: the two answer
    different questions, and a single sequence of both would make every caller
    of the Run Log ask what kind of entry it was holding.
    """
    entries = []
    for comment in issue.comments:
        payload = extract_gate_block(comment.body)
        if not payload or "kind" not in payload or "verdict" not in payload:
            continue
        try:
            entries.append(GateEntry.from_dict(payload))
        except (KeyError, ValueError):
            continue
    return tuple(entries)


def run_state(issue: Issue, resolve=None) -> RunState:
    """Derive a Run's entire state from the Issue. This is ADR-0002's claim.

    Body for the Plan and the Roster, comments for the results, labels for the
    status. Nothing local is consulted, which is why `agentforge implement 12`
    works from a clone that has never seen this Run.
    """
    document = parse_issue_body(issue.body, resolve)
    results = parse_run_log(issue)
    gates = parse_gate_log(issue)

    status = _status_from_labels(issue.labels)
    if status is None:
        if results and results[-1].escalated:
            status = RunStatus.HALTED
        elif results:
            status = RunStatus.RUNNING
        else:
            status = RunStatus.PLANNED

    return RunState(
        issue=issue.number,
        plan=document.plan,
        roster=document.roster,
        context=document.context or ContextPack(),
        results=results,
        gates=gates,
        status=status,
        workflow=document.workflow,
    )


def _status_from_labels(labels: tuple[str, ...]) -> RunStatus | None:
    for status in RunStatus:
        if status.label in labels:
            return status
    for label, status in LEGACY_LABELS.items():
        if label in labels:
            return status
    return None


def _number_from_url(url: str) -> int:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    try:
        return int(tail)
    except ValueError as exc:
        raise IssueError(f"could not read an issue number out of {url!r}") from exc


__all__ = [
    "Comment",
    "GitHub",
    "Issue",
    "IssueError",
    "parse_gate_log",
    "parse_run_log",
    "render_gate_comment",
    "render_run_log_comment",
    "render_terminal_comment",
    "run_state",
]
