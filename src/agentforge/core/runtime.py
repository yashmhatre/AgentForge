"""The two commands, as one object.

`plan` turns a Task into an Issue. `implement` turns an Issue number into a
draft pull request. Between them they exercise all four ADRs: agents are CLI
subprocesses (0001), the Issue carries the handoff and the Run Log (0002), the
plan freezes when it is filed (0003), and every invocation names a tier rather
than a model (0004).

The Workflow runtime, Gates, and multi-step execution are M2. What is here is a
Roster run as a plain sequence, which is all one Role needs and all M1 claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..agents import RUNNERS, resolve_role
from ..agents.orchestrator import Orchestrator, Planned
from ..providers import DEFAULT_PROVIDER, get_provider
from .config import load_config
from .contracts import (
    AgentResult,
    ContextPack,
    ModelTier,
    Outcome,
    PlanDocument,
    Role,
    RunState,
    RunStatus,
    Task,
)
from .issues import (
    GitHub,
    Issue,
    IssueError,
    render_run_log_comment,
    render_terminal_comment,
    run_state,
)
from .plan_format import PlanFormatError, render_issue_body, render_issue_title
from .process import CommandRunner, SubprocessRunner
from .repo import PreconditionFailed, Repository, branch_for_issue, open_repository
from .workflow import WorkflowError, load_workflow


class RunFailed(RuntimeError):
    """A Run could not proceed. The message is written for the person reading it."""


@dataclass(frozen=True)
class PlanOutcome:
    """What `agentforge plan` produced."""

    result: AgentResult
    issue: Issue | None = None
    document: PlanDocument | None = None

    @property
    def filed(self) -> bool:
        return self.issue is not None


class Forge:
    """One working directory, one Provider, one `gh`."""

    def __init__(
        self,
        cwd: Path | str = ".",
        provider: str = DEFAULT_PROVIDER,
        runner: CommandRunner | None = None,
    ) -> None:
        self.runner: CommandRunner = runner or SubprocessRunner()
        self.cwd = Path(cwd)
        self.provider_name = provider

    # --- preconditions -----------------------------------------------------

    def _prepare(self, allow_commands: bool = False) -> tuple[Repository, GitHub, object]:
        """Every check that can fail for free, before anything is spent.

        Absent git, absent remote, absent `gh`, absent coding-agent CLI. A Run
        that is going to fail on a missing binary should fail in the second it
        started, not after a deep-tier planning pass.
        """
        try:
            repo = open_repository(self.runner, self.cwd)
        except PreconditionFailed as exc:
            raise RunFailed(str(exc)) from exc

        github = GitHub(self.runner, repo.root)
        provider = get_provider(
            self.provider_name,
            self.runner,
            allow_commands=allow_commands,
            config=load_config(repo.root),
        )
        try:
            github.preflight()
            provider.preflight()
        except (IssueError, RuntimeError) as exc:
            raise RunFailed(str(exc)) from exc

        return repo, github, provider

    # --- agentforge plan ---------------------------------------------------

    def plan(self, statement: str, tier: ModelTier | None = None) -> PlanOutcome:
        repo, github, provider = self._prepare()
        task = Task(statement=statement)

        planned: Planned = Orchestrator(provider, tier=tier).plan(task, repo.root)
        if planned.document is None:
            return PlanOutcome(result=planned.result)

        body = render_issue_body(task, planned.document)
        issue = github.create_issue(
            title=render_issue_title(task),
            body=body,
            labels=(RunStatus.PLANNED.label,),
        )
        return PlanOutcome(result=planned.result, issue=issue, document=planned.document)

    # --- agentforge implement ----------------------------------------------

    def implement(
        self,
        number: int,
        tier_overrides: dict[str, ModelTier] | None = None,
        tier: ModelTier | None = None,
        allow_commands: bool = False,
    ) -> RunState:
        """Run the Issue's Workflow. `tier` moves every Role; `tier_overrides` moves one.

        `allow_commands` is ADR-0007's gate. It is per-Run rather than
        configuration on purpose: a config key would persist a standing grant
        across every future Run in the repository.
        """
        repo, github, provider = self._prepare(allow_commands=allow_commands)

        if repo.is_dirty():
            raise RunFailed(
                f"{repo.root} has uncommitted changes. AgentForge commits whatever an Agent "
                "leaves in the working tree, so it will not start a Run on top of your work. "
                "Commit or stash first."
            )

        issue = github.read_issue(number)
        try:
            state = run_state(issue)
        except PlanFormatError as exc:
            raise RunFailed(f"issue #{number} cannot be implemented: {exc}") from exc
        except LookupError as exc:
            raise RunFailed(f"issue #{number} names a Role that cannot run: {exc}") from exc

        try:
            workflow = load_workflow(state.workflow)
        except WorkflowError as exc:
            raise RunFailed(f"issue #{number} cannot be implemented: {exc}") from exc

        if not workflow.steps:
            raise RunFailed(
                f"the {workflow.name!r} Workflow declares no steps, so there is nothing "
                "to run. Name a Workflow that does, or fill this one in."
            )

        remaining = workflow.remaining(state.done_roles)
        if not remaining:
            # Nothing to run is not a Run: no branch, no status change, and no
            # terminal comment, which would otherwise post a second ending every
            # time someone re-read a finished Issue.
            return state

        branch = branch_for_issue(number)
        repo.create_branch(branch)
        github.set_status(issue, RunStatus.RUNNING)

        results = list(state.results)
        overrides = tier_overrides or {}
        last_role = remaining[-1].role

        for step in remaining:
            role = resolve_role(step.role)
            at = overrides.get(role.name, tier or step.tier or role.tier)
            result = _run_step(role.at_tier(at), provider, state, repo.root)
            github.post_comment(number, render_run_log_comment(result))
            results.append(result)

            if result.outcome is not Outcome.COMPLETED:
                status = RunStatus.HALTED if result.escalated else RunStatus.FAILED
                return _end(
                    github, issue, _with(state, results=results, status=status, branch=branch)
                )

        changed = repo.changed_files()
        if not repo.commit_all(f"{issue.title}\n\nImplements #{number} via AgentForge."):
            failure = AgentResult(
                role=results[-1].role if results else last_role,
                tier=results[-1].tier if results else resolve_role(last_role).tier,
                outcome=Outcome.FAILED,
                summary=(
                    "the Roster reported success but left no changes in the working tree, "
                    "so there is nothing to open a pull request for"
                ),
            )
            github.post_comment(number, render_run_log_comment(failure))
            results.append(failure)
            return _end(
                github,
                issue,
                _with(state, results=results, status=RunStatus.FAILED, branch=branch),
            )

        repo.push(branch)
        url = github.open_draft_pr(
            title=issue.title,
            body=_pr_body(number, state, results, changed),
            head=branch,
            base=github.default_branch(),
        )

        return _end(
            github,
            issue,
            _with(
                state,
                results=results,
                status=RunStatus.AWAITING_SIGNOFF,
                branch=branch,
                pull_request=url,
            ),
        )


# --- role dispatch ---------------------------------------------------------


def _run_step(role: Role, provider, state: RunState, cwd: Path) -> AgentResult:
    """Invoke whatever runner is registered for this Role.

    The lookup is the whole point: the runtime names no Role, so a Workflow
    naming a seventh one needs an entry in `RUNNERS` and nothing here.
    """
    runner = RUNNERS.get(role.name)
    if runner is None:
        # Unreachable through a validated Workflow — `parse_workflow` refuses
        # unrunnable names at load time — but a Workflow built in code can land here.
        raise RunFailed(
            f"the {role.name!r} Role has no runner in this version; "
            f"available: {', '.join(sorted(RUNNERS))}"
        )

    return runner(provider).run(
        plan=state.plan,
        context=state.context or ContextPack(),
        cwd=cwd,
        role=role,
        tier=role.tier,
    )


def _end(github: GitHub, issue: Issue, state: RunState) -> RunState:
    """Every way out of a Run, in one place.

    The comment first, then the label: a reader who sees the label knows the
    reason is already on the Issue, and a Run that dies between the two leaves
    the ending recorded rather than only asserted.
    """
    github.post_comment(state.issue, render_terminal_comment(state))
    github.set_status(issue, state.status)
    return state


def _pr_body(number: int, state: RunState, results, changed) -> str:
    lines = [
        f"Closes #{number}.",
        "",
        "## Plan",
        "",
        state.plan.summary.strip(),
        "",
        "## Run Log",
        "",
    ]
    for result in results:
        lines.append(f"- **{result.role}** (`{result.tier}`) — {result.summary}")
    if changed:
        lines += ["", "## Files changed", ""]
        lines += [f"- `{path}`" for path in changed]
    lines += [
        "",
        "---",
        "",
        "Opened as a draft by AgentForge. A human merges; no Workflow does.",
    ]
    return "\n".join(lines) + "\n"


def _with(state: RunState, **changes) -> RunState:
    results = changes.pop("results", state.results)
    return RunState(
        issue=state.issue,
        plan=state.plan,
        roster=state.roster,
        context=state.context,
        results=tuple(results),
        status=changes.pop("status", state.status),
        branch=changes.pop("branch", state.branch),
        pull_request=changes.pop("pull_request", state.pull_request),
        workflow=changes.pop("workflow", state.workflow),
    )


__all__ = ["Forge", "PlanOutcome", "RunFailed"]
