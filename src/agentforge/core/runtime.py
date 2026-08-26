"""The two commands, as one object.

`plan` turns a Task into an Issue. `implement` turns an Issue number into a
draft pull request. Between them they exercise all four ADRs: agents are CLI
subprocesses (0001), the Issue carries the handoff and the Run Log (0002), the
plan freezes when it is filed (0003), and every invocation names a tier rather
than a model (0004).

`implement` walks the Steps of a Workflow, running the ones the Run Log does not
already account for and passing through the Gate that follows each. It names no
Role and no Gate kind: Roles are looked up in `RUNNERS` and Gate kinds in
`GATES`, so a seventh of either is a registration and nothing here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ..agents import RUNNERS, resolve_role
from ..agents.orchestrator import Orchestrator, Planned
from ..providers import DEFAULT_PROVIDER, get_provider
from .config import load_config
from .contracts import (
    AgentResult,
    ContextPack,
    GateEntry,
    GateVerdict,
    ModelTier,
    Outcome,
    PlanDocument,
    Role,
    RunState,
    RunStatus,
    Task,
    retirement,
)
from .gates import GateContext, evaluate_gate
from .issues import (
    GitHub,
    Issue,
    IssueError,
    render_gate_comment,
    render_run_log_comment,
    render_terminal_comment,
    run_state,
)
from .plan_format import PlanFormatError, render_issue_body, render_issue_title
from .process import CommandRunner, SubprocessRunner
from .repo import PreconditionFailed, Repository, branch_for_issue, open_repository
from .workflow import Workflow, WorkflowError, load_workflow


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

        # One flag per Step rather than the outstanding ones alone: a Step behind
        # the Run still has a Gate in front of the next one, and a resumed Run
        # has to pass through it.
        retired = retirement(workflow.steps, state.done_roles, lambda step: step.role)
        if not _has_work(workflow, state, retired):
            # Nothing to run is not a Run: no branch, no status change, and no
            # terminal comment, which would otherwise post a second ending every
            # time someone re-read a finished Issue.
            return state

        branch = branch_for_issue(number)
        repo.create_branch(branch)
        github.set_status(issue, RunStatus.RUNNING)

        results = list(state.results)
        gates = list(state.gates)
        overrides = tier_overrides or {}
        invoked = False

        for position, (step, behind) in enumerate(zip(workflow.steps, retired), start=1):
            if not behind:
                role = resolve_role(step.role)
                at = overrides.get(role.name, tier or step.tier or role.tier)
                # Derived from the Run Log rather than enumerated, because a
                # resumed Run starts partway through and would otherwise tell a
                # human that a Role escalated at step 1 of a Run whose step 1 is
                # behind it.
                where = _with(state, results=results, gates=gates).current_step
                result = _run_step(role.at_tier(at), provider, state, repo.root)
                github.post_comment(
                    number,
                    render_run_log_comment(result, step=where, of=len(workflow.steps)),
                )
                results.append(result)
                invoked = True

                if result.outcome is not Outcome.COMPLETED:
                    status = RunStatus.HALTED if result.escalated else RunStatus.FAILED
                    return _end(
                        github,
                        issue,
                        _with(
                            state,
                            results=results,
                            gates=gates,
                            status=status,
                            branch=branch,
                        ),
                    )

            if step.gate is None:
                continue

            entry = evaluate_gate(
                step.gate,
                GateContext(
                    state=_with(state, results=results, gates=gates),
                    kind=step.gate,
                    role=step.role,
                    step=position,
                    runner=self.runner,
                    root=repo.root,
                ),
            )
            if entry.verdict is GateVerdict.CLEARED:
                continue

            # Only a Gate that stopped the Run writes to the Run Log. One that
            # cleared has told the reader nothing and the next Run nothing, and
            # would post an entry on every resume saying the Run carried on.
            github.post_comment(number, render_gate_comment(entry, of=len(workflow.steps)))
            gates.append(entry)
            return _stop_at(
                github,
                issue,
                repo,
                _with(state, results=results, gates=gates, branch=branch),
                entry,
            )

        changed = repo.changed_files()
        committed = repo.commit_all(f"{issue.title}\n\nImplements #{number} via AgentForge.")
        base = github.default_branch()
        # An empty working tree is only a failure when the branch has nothing on
        # it either. Plenty of Runs legitimately write nothing here: an audit
        # changes no files, a Step behind a cleared Gate was committed by the
        # invocation that suspended, and a `review` Workflow is pointed at a diff
        # AgentForge did not write. What none of those may do is claim success
        # over a branch identical to the base, which is the empty pull request
        # this check exists to refuse.
        if not committed and invoked and not repo.carries_work_against(base):
            failure = _nothing_to_open(results, workflow)
            github.post_comment(number, render_run_log_comment(failure))
            results.append(failure)
            return _end(
                github,
                issue,
                _with(
                    state,
                    results=results,
                    gates=gates,
                    status=RunStatus.FAILED,
                    branch=branch,
                ),
            )

        # A Run that invoked nobody cleared a Gate and found every Step behind
        # it: the work was committed by the Run that suspended, so an empty
        # working tree here is the expected shape rather than a failure.
        repo.push(branch)
        url = github.open_draft_pr(
            title=issue.title,
            body=_pr_body(number, state, results, changed),
            head=branch,
            base=base,
        )

        return _end(
            github,
            issue,
            _with(
                state,
                results=results,
                gates=gates,
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


def _nothing_to_open(results: list[AgentResult], workflow: Workflow) -> AgentResult:
    """The Run reported success and left the working tree alone.

    Recorded against the last Role to speak, because that is the one whose claim
    the empty tree contradicts. Otherwise the Run opens an empty pull request and
    says it worked.
    """
    last = results[-1] if results else None
    return AgentResult(
        role=last.role if last else workflow.steps[-1].role,
        tier=last.tier if last else resolve_role(workflow.steps[-1].role).tier,
        outcome=Outcome.FAILED,
        summary=(
            "the Roster reported success but left no changes in the working tree, "
            "so there is nothing to open a pull request for"
        ),
    )


def _has_work(workflow: Workflow, state: RunState, retired: tuple[bool, ...]) -> bool:
    """Whether this invocation has anything to do at all.

    A Step still to run, or a Gate the Run has yet to pass through. A Run that
    already reached Sign-off has neither: its Gates were cleared by the Run that
    opened the pull request, and walking them again would open a second one.

    A suspended Run always has work, even when the definition it suspended
    against no longer declares the Gate that stopped it. Suspended means a Run
    that can still go on, and one that answered "nothing to do" forever would be
    halted under another name.
    """
    if not all(retired):
        return True
    if state.status is RunStatus.AWAITING_SIGNOFF:
        return False
    return state.status is RunStatus.SUSPENDED or any(step.gate for step in workflow.steps)


def _stop_at(
    github: GitHub, issue: Issue, repo: Repository, state: RunState, gate: GateEntry
) -> RunState:
    """End a Run at a Gate. Blocked is suspended; errored is halted.

    Errored halts because a Gate that could not evaluate has nothing to clear,
    and suspending it would invite a resume that suspends again forever.

    A suspended Run commits and pushes what it has. The human who is being asked
    to clear the Gate has to be able to see the work, and the next invocation
    refuses to start on a dirty working tree — so a Run that suspended without
    committing could never be resumed, which is most of what #9 is for.
    """
    if not gate.blocked:
        return _end(github, issue, _with(state, status=RunStatus.HALTED))

    message = (
        f"{issue.title}\n\nPartial work for #{state.issue}; the Run is suspended at a "
        f"{gate.kind} Gate."
    )
    if repo.commit_all(message):
        repo.push(state.branch)
    return _end(github, issue, _with(state, status=RunStatus.SUSPENDED))


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
    """The same Run, moved on.

    `dataclasses.replace` rather than a field-by-field copy: the hand-rolled one
    silently dropped every field it did not name, which is a bug that only shows
    up the next time somebody adds a field to `RunState`.
    """
    for accumulating in ("results", "gates"):
        if accumulating in changes:
            changes[accumulating] = tuple(changes[accumulating])
    return replace(state, **changes)


__all__ = ["Forge", "PlanOutcome", "RunFailed"]
