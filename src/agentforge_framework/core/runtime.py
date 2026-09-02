"""The commands, as one object.

`decompose` turns a Task into a set of Issues in dependency order, and `plan` is
that same pipeline handed a Task typed at a shell rather than read from a file
(ADR-0021). `implement` turns one Issue number into a draft pull request. Between
them they exercise all four founding ADRs: agents are CLI subprocesses (0001),
the Issue carries the handoff and the Run Log (0002), the plan freezes when it is
filed (0003), and every invocation names a tier rather than a model (0004).

`implement` walks the Steps of a Workflow, running the ones the Run Log does not
already account for and passing through the Gate that follows each. It names no
Role and no Gate kind: Roles are looked up in `RUNNERS` and Gate kinds in
`GATES`, so a seventh of either is a registration and nothing here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..agents import RUNNERS, resolve_role
from ..agents.decomposer import Approver, Decomposed, Decomposer, slice_task
from ..agents.orchestrator import Exchange, Interviewer, Orchestrator, Planned
from ..context.resolver import resolve_pack
from ..providers import DEFAULT_PROVIDER, get_provider
from .config import Config, load_config
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
    Slice,
    Task,
    retirement,
)
from .gates import GateContext, evaluate_gate
from .issues import (
    READY_FOR_AGENT,
    GitHub,
    Issue,
    IssueError,
    render_context_comment,
    render_gate_comment,
    render_run_log_comment,
    render_terminal_comment,
    run_state,
    status_of,
)
from .plan_format import PlanFormatError, render_issue_body, render_issue_title
from .process import CommandRunner, SubprocessRunner
from .registry import (
    NO_PLUGINS,
    Activation,
    activate,
    contributions,
    extractors_for,
    fragments_for,
    gates_for,
)
from .repo import (
    PreconditionFailed,
    Repository,
    branch_for_issue,
    open_repository,
    unclaimed,
)
from .workflow import Workflow, WorkflowError, load_workflow


class RunFailed(RuntimeError):
    """A Run could not proceed. The message is written for the person reading it."""


@dataclass(frozen=True)
class FiledSlice:
    """One Slice, once it is an Issue with a number and its edges written."""

    slice: Slice
    issue: Issue
    document: PlanDocument

    @property
    def blocked_by(self) -> tuple[int, ...]:
        return self.document.blocked_by


@dataclass(frozen=True)
class PlanOutcome:
    """What a planning pass produced, from either entry point. See ADR-0021.

    `filed` is a tuple because a Task cuts into as many Slices as it has work in
    it. A one-sentence Task fills it with one, which is what `agentforge plan`
    always did, and nothing here special-cases that.
    """

    #: Every invocation the pass made, in order -- the grill rounds, the Spec,
    #: the cut, and one planning pass per Slice. The caller prices the whole
    #: thing from this and says which stage stopped when one did.
    results: tuple[AgentResult, ...] = ()
    spec: str = ""
    slices: tuple[Slice, ...] = ()
    filed: tuple[FiledSlice, ...] = ()
    #: What the human was asked and answered, empty when nobody was there.
    interview: tuple[Exchange, ...] = ()
    #: Files the planning pass left changed in the working tree. An interview
    #: records settled terms in the project's glossary, and a human who is not
    #: told that has an unexplained diff and a Run that then refuses to start.
    touched: tuple[str, ...] = ()
    #: Set when a stage did not produce what the next one needs. Whatever was
    #: filed before it stands; a Slice already filed is not un-filed because a
    #: later one could not be planned.
    failure: AgentResult | None = None
    #: True when the breakdown was shown and the human said no. Not a failure --
    #: rejecting a cut is the reason it is shown.
    declined: bool = False
    #: Edges the tracker would not record natively. The plan block and the Issue
    #: body carry them regardless, so this degrades the view rather than the
    #: contract, and the caller says so once.
    unwritten_edges: tuple[tuple[int, int], ...] = ()

    @property
    def issues(self) -> tuple[Issue, ...]:
        return tuple(one.issue for one in self.filed)

    @property
    def result(self) -> AgentResult | None:
        """The stage that stopped the pass, or the last one that ran."""
        return self.failure or (self.results[-1] if self.results else None)


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

    def _prepare(
        self, allow_commands: bool = False
    ) -> tuple[Repository, GitHub, object, Config]:
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
        config = load_config(repo.root)
        provider = get_provider(
            self.provider_name,
            self.runner,
            allow_commands=allow_commands,
            config=config,
        )
        try:
            github.preflight()
            provider.preflight()
        except (IssueError, RuntimeError) as exc:
            raise RunFailed(str(exc)) from exc

        return repo, github, provider, config

    # --- agentforge plan ---------------------------------------------------

    def plan(
        self,
        statement: str,
        tier: ModelTier | None = None,
        interviewer: Interviewer | None = None,
        approver: Approver | None = None,
    ) -> PlanOutcome:
        """A Task, as typed. Everything else is `decompose`."""
        return self.decompose(
            statement,
            source="Typed at the command line just now, in their own words.",
            tier=tier,
            interviewer=interviewer,
            approver=approver,
        )

    def decompose(
        self,
        document: str,
        source: str,
        tier: ModelTier | None = None,
        interviewer: Interviewer | None = None,
        approver: Approver | None = None,
    ) -> PlanOutcome:
        """Grill, synthesize, cut, plan each Slice, file in dependency order.

        `interviewer` is the human, as a callable, and `approver` is the same
        human answering once about the breakdown. Passing neither is the
        unattended path: a scheduled Run has nobody to ask, and waiting on an
        answer that will never arrive is worse than planning from what was
        typed. But nothing is filed without an approver -- committing somebody
        to fifteen Issues they have not seen is not a default worth having, and
        the caller supplies an approver that always says yes when the human
        asked for exactly that.
        """
        repo, github, provider, _config = self._prepare()

        # Only when interviewing: every stage is told to change nothing, and two
        # extra `git status` calls on an unattended pass buy nothing.
        before = set(repo.changed_files()) if interviewer else set()

        cut = Decomposer(provider, tier=tier).decompose(
            document, source, repo.root, interviewer
        )
        touched = (
            tuple(path for path in repo.changed_files() if path not in before)
            if interviewer
            else ()
        )
        outcome = PlanOutcome(
            results=cut.results,
            spec=cut.spec,
            slices=cut.slices,
            interview=cut.interview,
            touched=touched,
            failure=cut.failure,
        )
        if not cut.ok:
            return outcome

        if approver is None or not approver(cut.slices):
            return replace(outcome, declined=True)

        return self._file_slices(github, provider, repo, cut, outcome, tier)

    def _file_slices(
        self,
        github: GitHub,
        provider,
        repo: Repository,
        cut: Decomposed,
        outcome: PlanOutcome,
        tier: ModelTier | None,
    ) -> PlanOutcome:
        """One planning pass per Slice, filed as its blockers become numbers.

        Blockers first, always: `order_slices` guarantees it, and the ordering is
        what makes an edge writable at all -- an Issue cannot declare it is
        blocked by one that does not exist yet.

        A Slice that will not plan stops the pass and leaves what came before it
        filed. Those Issues are complete and executable on their own, which is
        the property the cut was for; discarding them would throw away good work
        to tidy up after a bad Slice.
        """
        orchestrator = Orchestrator(provider, tier=tier)
        numbers: dict[str, int] = {}
        by_id = {one.id: one for one in cut.slices}
        filed: list[FiledSlice] = []
        results = list(outcome.results)
        unwritten: list[tuple[int, int]] = []

        for one in cut.slices:
            blockers = tuple(by_id[name] for name in one.blocked_by)
            planned: Planned = orchestrator.plan(
                slice_task(one, cut.spec, blockers), repo.root, interviewer=None
            )
            results.append(planned.result)

            if planned.document is None:
                return replace(
                    outcome,
                    results=tuple(results),
                    filed=tuple(filed),
                    unwritten_edges=tuple(unwritten),
                    failure=planned.result,
                )

            blocked_by = tuple(numbers[name] for name in one.blocked_by)
            document = replace(planned.document, blocked_by=blocked_by)

            # The Issue body says what this Slice delivers, not what the
            # planning pass was handed: that prompt carries the whole Spec, and
            # repeating it in fifteen bodies is fifteen copies to go stale.
            headline = Task(statement=one.delivers or one.title)
            issue = github.create_issue(
                title=render_issue_title(Task(statement=one.title)),
                body=render_issue_body(headline, document),
                labels=(RunStatus.PLANNED.label, READY_FOR_AGENT),
            )
            numbers[one.id] = issue.number

            for blocker in blocked_by:
                if not github.block_on(issue.number, blocker):
                    unwritten.append((issue.number, blocker))

            filed.append(FiledSlice(slice=one, issue=issue, document=document))

        return replace(
            outcome,
            results=tuple(results),
            filed=tuple(filed),
            unwritten_edges=tuple(unwritten),
        )

    def _unmet_blockers(self, github: GitHub, blocked_by: Sequence[int]) -> tuple[tuple[int, str], ...]:
        """The blockers that have not cleared, each with why it has not.

        Two things clear a blocker: its Run reached Sign-off, or somebody closed
        the Issue. The second matters as much as the first -- a human who
        decided a Slice was unnecessary and closed it has cleared the edge as
        surely as a Run that finished it, and a check that only read labels
        would leave the rest of the plan stuck behind a decision already made.

        A blocker that cannot be read at all does not block. The tracker being
        unreachable is not evidence the work is unfinished, and refusing a Run
        over a failed read would make an outage look like a dependency.
        """
        unmet: list[tuple[int, str]] = []
        for number in blocked_by:
            try:
                blocker = github.read_issue(number)
            except IssueError:
                continue
            if blocker.closed:
                continue
            status = status_of(blocker)
            if status is RunStatus.AWAITING_SIGNOFF:
                continue
            unmet.append((number, status.value if status else "not started"))
        return tuple(unmet)

    # --- agentforge implement ----------------------------------------------

    def implement(
        self,
        number: int,
        tier_overrides: dict[str, ModelTier] | None = None,
        tier: ModelTier | None = None,
        allow_commands: bool = False,
        resolve_context: bool = True,
        use_plugins: bool = True,
        ignore_blockers: bool = False,
    ) -> RunState:
        """Run the Issue's Workflow. `tier` moves every Role; `tier_overrides` moves one.

        `ignore_blockers` overrides ADR-0021's ordering. The edges are the
        Orchestrator's reading of what genuinely cannot start yet, and the human
        who wrote the plan may know better -- but they say so per Run rather
        than having the edges quietly not mean anything.

        `allow_commands` is ADR-0007's gate. It is per-Run rather than
        configuration on purpose: a config key would persist a standing grant
        across every future Run in the repository.

        `resolve_context` off is the control Run. A Context Pack is supposed to
        make a Run cheaper, and the only honest way to know is to run the same
        Issue without one and compare the totals the two Run Logs carry.

        `use_plugins` off keeps the pack and drops the Plugins' Fragments. The
        two switches are separate because they measure different things and
        ADR-0016 needs both: Fragments ride in the pack, so `resolve_context`
        off already suppresses them, and a Run with neither cannot say which of
        the two moved the total. See ADR-0016 for the three conditions.
        """
        repo, github, provider, config = self._prepare(allow_commands=allow_commands)

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

        if not ignore_blockers:
            unmet = self._unmet_blockers(github, state.blocked_by)
            if unmet:
                listed = ", ".join(f"#{n} ({why})" for n, why in unmet)
                raise RunFailed(
                    f"issue #{number} is blocked by {listed}. It is one Slice of a "
                    "decomposed plan and the Slices before it have not finished "
                    "(ADR-0021). Run those first, or pass --ignore-blockers if you "
                    "know this Slice does not actually need them."
                )

        # Before the Workflow is loaded, not after. A later ticket lets a Plugin
        # register a Gate kind, and `parse_workflow` refuses an unknown kind at
        # load time — so a Workflow naming a Plugin's Gate would be rejected
        # before its Plugin existed if these two ran the other way round.
        activation = activate(state.plan, repo.root) if use_plugins else NO_PLUGINS
        # The Gate kinds this Run may name: the shipped three, widened by the
        # Plugins just activated. Assembled once and handed to both the parser
        # and the evaluator, so a definition cannot load against one table and
        # be evaluated against another (ADR-0018).
        gate_kinds = gates_for(activation)

        try:
            workflow = load_workflow(state.workflow, gates=gate_kinds)
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

        # Resolved once, from the frozen Plan, before any Role is invoked
        # (ADR-0010). Doing it per Step would let what a Role sees drift between
        # Steps of one Run, which is the thing the frozen Plan exists to stop.
        pack = (
            # The extractor table comes from the activation resolved above, so a
            # Plugin's reader and a Plugin's Fragment are decided by one answer
            # rather than two. A control Run activated nothing and gets the
            # built-in three, which is what makes it a control for the readers
            # as well as for the prompts.
            resolve_pack(
                state.plan, repo.root, state.context, extractors_for(activation)
            )
            if resolve_context
            else ContextPack()
        )
        state = _with(state, context=pack)

        branch = branch_for_issue(number)
        repo.create_branch(branch)
        github.set_status(issue, RunStatus.RUNNING)

        results = list(state.results)
        gates = list(state.gates)
        overrides = tier_overrides or {}
        # ADR-0014: read off the frozen plan block, so a resumed Run resolves
        # tiers the way the invocation that filed the Issue would have.
        chosen = state.roster.tiers()
        invoked = False

        for position, (step, behind) in enumerate(zip(workflow.steps, retired), start=1):
            if not behind:
                # Before the first Agent of this invocation and never again: the
                # pack is what the Agents below were shown, and a Run that only
                # walked a Gate showed nobody anything.
                if not invoked:
                    github.post_comment(
                        number,
                        render_context_comment(
                            state.context,
                            contributions(activation),
                            activation.skipped,
                            publish_inventory=config.publish_pack_inventory,
                        ),
                    )
                role = resolve_role(step.role)
                at = overrides.get(
                    role.name,
                    tier or step.tier or chosen.get(role.name) or role.tier,
                )
                # Derived from the Run Log rather than enumerated, because a
                # resumed Run starts partway through and would otherwise tell a
                # human that a Role escalated at step 1 of a Run whose step 1 is
                # behind it.
                where = _with(state, results=results, gates=gates).current_step
                result = _run_step(
                    role.at_tier(at), provider, state, repo.root, activation
                )
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
                gates=gate_kinds,
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
        declared = _declared_surface(state, results)
        committed = repo.commit_declared(
            f"{issue.title}\n\nImplements #{number} via AgentForge.",
            declared,
        )
        # Left in the working tree on purpose (ADR-0015), and named rather
        # than dropped: the human at Sign-off is the only one who can say
        # whether an undeclared file was an Agent's work or its suite's.
        left = tuple(path for path in changed if path not in committed)
        # The other half of the same disclosure. A tracked file is committed
        # however it changed (ADR-0015), which is right when the only writers
        # are the Agents and their commands, and is how a second agent sharing
        # the checkout gets its work committed and attributed to a Role (#101).
        # Naming these is the difference between a data-loss bug and a line at
        # Sign-off.
        unclaimed_paths = unclaimed(committed, declared)
        base = github.default_branch()
        # An empty working tree is only a failure when the branch has nothing on
        # it either. Plenty of Runs legitimately write nothing here: an audit
        # changes no files, a Step behind a cleared Gate was committed by the
        # invocation that suspended, and a `review` Workflow is pointed at a diff
        # AgentForge did not write. What none of those may do is claim success
        # over a branch identical to the base, which is the empty pull request
        # this check exists to refuse.
        if not committed and invoked and not repo.carries_work_against(base):
            failure = _nothing_to_open(results, workflow, left)
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
            body=_pr_body(number, state, results, committed, left, unclaimed_paths),
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


def _run_step(
    role: Role,
    provider,
    state: RunState,
    cwd: Path,
    activation: Activation = NO_PLUGINS,
) -> AgentResult:
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
        context=_pack_for(role, state.context, activation),
        cwd=cwd,
        role=role,
        tier=role.tier,
    )


def _pack_for(role: Role, pack: ContextPack | None, activation: Activation) -> ContextPack:
    """The Run's pack, plus whatever the active Plugins say to this one Role.

    Folded here rather than inside each Role runner: Fragments are per Role and
    the pack is per Run, and this is the one place that knows both. No runner
    signature changes, and the pack recorded in the Run Log stays the Run-level
    one, so a human comparing two Runs is comparing the same object.

    A Run that resolved no pack gets no Fragments either. ADR-0016 settles that
    `--no-context-pack` is a combined control and `--no-plugins` is the one that
    isolates them.
    """
    pack = pack or ContextPack()
    if not pack:
        return pack

    fragments = fragments_for(activation, role.name)
    return replace(pack, fragments=fragments) if fragments else pack


def _declared_surface(state: RunState, results: Sequence[AgentResult]) -> tuple[str, ...]:
    """Every path this Run said it would touch, from both places it says so.

    The frozen Plan names files per Step before anything runs, and each Agent
    Result names what its Agent reports changing. Neither is trusted for whether
    work happened — `carries_work_against` asks git that — but together they are
    the only account of *which* files were the Run's, and ADR-0015 needs one:
    `--allow-commands` means a suite writes into the working tree alongside the
    Agents, and no property of a file on disk separates the two.

    Duplicates are kept out and order is preserved, so a failure message listing
    this reads in Plan order rather than in whatever order a set happened to hold.
    """
    declared: list[str] = []
    seen: set[str] = set()
    for path in (
        *(path for step in state.plan.steps for path in step.files),
        *(path for result in results for path in result.files_changed),
    ):
        if path and path not in seen:
            seen.add(path)
            declared.append(path)
    return tuple(declared)


def _nothing_to_open(
    results: list[AgentResult], workflow: Workflow, left: Sequence[str] = ()
) -> AgentResult:
    """The Run reported success and committed nothing.

    Recorded against the last Role to speak, because that is the one whose claim
    the empty commit contradicts. Otherwise the Run opens an empty pull request
    and says it worked.

    `left` separates the two ways to get here, because the fix differs. An empty
    working tree means the Agents wrote nothing. A working tree holding only
    undeclared files means they wrote somewhere the Plan and their own results
    never named, and ADR-0015 left it uncommitted — which a human can only act on
    if the Run says which files.
    """
    last = results[-1] if results else None
    if left:
        summary = (
            "the Roster reported success but every file it left is one neither the Plan "
            "nor any Agent Result named, so nothing was committed (ADR-0015): "
            + ", ".join(left)
        )
    else:
        summary = (
            "the Roster reported success but left no changes in the working tree, "
            "so there is nothing to open a pull request for"
        )
    return AgentResult(
        role=last.role if last else workflow.steps[-1].role,
        tier=last.tier if last else resolve_role(workflow.steps[-1].role).tier,
        outcome=Outcome.FAILED,
        summary=summary,
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
    if repo.commit_declared(message, _declared_surface(state, state.results)):
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


def _pr_body(number: int, state: RunState, results, committed, left=(), unclaimed=()) -> str:
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
    if committed:
        lines += ["", "## Files changed", ""]
        lines += [f"- `{path}`" for path in committed]
    if left:
        lines += [
            "",
            "## Left uncommitted",
            "",
            (
                "In the working tree of the machine that ran this, and not in this diff. "
                "Neither the Plan nor any Agent Result named these, so AgentForge did not "
                "commit them (ADR-0015). A build artifact is the usual reason; an Agent "
                "writing outside its Step is the one worth reading."
            ),
            "",
        ]
        lines += [f"- `{path}`" for path in left]
    if unclaimed:
        lines += [
            "",
            "## Committed, but no Agent claimed them",
            "",
            (
                "These are in the diff. Git already tracked them, so AgentForge committed "
                "them however they changed (ADR-0015) — but neither the Plan nor any Agent "
                "Result named them, so nothing in this Run says they are its work. Read "
                "them before you sign off. Another agent or a person editing this checkout "
                "while the Run was going is the reason worth ruling out."
            ),
            "",
        ]
        lines += [f"- `{path}`" for path in unclaimed]
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
