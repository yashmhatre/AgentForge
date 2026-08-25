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
    RUN_LABELS,
    AgentResult,
    ContextPack,
    Outcome,
    RunState,
    RunStatus,
)
from .plan_format import extract_result_block, parse_issue_body, render_result_block
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
        """One status label at a time, so a Run's state stays unambiguous."""
        number = issue.number if isinstance(issue, Issue) else int(issue)
        known = set(issue.labels) if isinstance(issue, Issue) else set()
        if number in self._applied:
            known.add(self._applied[number])

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


def render_run_log_comment(result: AgentResult) -> str:
    """One Run Log entry: prose a human reads, and a block a Run resumes from."""
    heading = {
        Outcome.COMPLETED: "completed",
        Outcome.ESCALATED: "escalated",
        Outcome.FAILED: "failed",
    }[result.outcome]

    lines = [
        f"### {result.role} — {heading}",
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


def run_state(issue: Issue, resolve=None) -> RunState:
    """Derive a Run's entire state from the Issue. This is ADR-0002's claim.

    Body for the Plan and the Roster, comments for the results, labels for the
    status. Nothing local is consulted, which is why `agentforge implement 12`
    works from a clone that has never seen this Run.
    """
    document = parse_issue_body(issue.body, resolve)
    results = parse_run_log(issue)

    status = _status_from_labels(issue.labels)
    if status is None:
        if any(result.escalated for result in results):
            status = RunStatus.ESCALATED
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
        status=status,
        workflow=document.workflow,
    )


def _status_from_labels(labels: tuple[str, ...]) -> RunStatus | None:
    for status in RunStatus:
        if status.label in labels:
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
    "parse_run_log",
    "render_run_log_comment",
    "run_state",
]
