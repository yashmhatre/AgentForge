"""Command-line entry point for AgentForge."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .core.contracts import ModelTier, Outcome, RunStatus

TIER_HELP = (
    "override the Model Tier: `deep`, `standard`, `cheap`, or `role=tier` "
    "to move one Role. Repeatable."
)

YES_HELP = (
    "file the breakdown without showing it first. Planning cuts a Task into "
    "Slices and files one issue per Slice (ADR-0021); without this the cut is "
    "printed and confirmed, and with nobody at a terminal nothing is filed at all"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentforge",
        description="Coordinate specialized software agents through reusable workflows.",
    )

    # `--version` is answered by the parser and exits, exactly as `--help` does;
    # neither ever reaches `main`'s dispatch. The literal lives in `__version__`
    # alone, so a release bumps one line here and one in `pyproject.toml` --
    # tests/test_docs.py fails if those two ever disagree.
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subcommands = parser.add_subparsers(dest="command", metavar="<command>")

    plan = subcommands.add_parser("plan", help="turn a task into GitHub issues")
    plan.add_argument("task", help="the task, in your own words")
    plan.add_argument("--provider", default=None, help="coding-agent CLI to drive")
    plan.add_argument("--tier", default=None, help="Model Tier for the Orchestrator")
    plan.add_argument("-C", "--directory", default=".", help="repository to plan against")
    plan.add_argument("--yes", action="store_true", help=YES_HELP)

    decompose = subcommands.add_parser(
        "decompose",
        help="turn a plan document you already wrote into GitHub issues",
    )
    decompose.add_argument("path", help="the plan document to read")
    decompose.add_argument("--provider", default=None, help="coding-agent CLI to drive")
    decompose.add_argument("--tier", default=None, help="Model Tier for the Orchestrator")
    decompose.add_argument(
        "-C", "--directory", default=".", help="repository to plan against"
    )
    decompose.add_argument("--yes", action="store_true", help=YES_HELP)

    implement = subcommands.add_parser(
        "implement", help="run an issue's roster and open a draft pull request"
    )
    implement.add_argument("issue", type=int, help="issue number")
    implement.add_argument("--provider", default=None, help="coding-agent CLI to drive")
    implement.add_argument("--tier", action="append", default=[], help=TIER_HELP)
    implement.add_argument("-C", "--directory", default=".", help="repository to work in")
    implement.add_argument(
        "--ignore-blockers",
        action="store_true",
        help=(
            "start the Run even though Issues this one declares as blockers have not "
            "signed off (ADR-0021). The edges are the Orchestrator's reading of what "
            "cannot start yet; this is you saying you know better for this Run"
        ),
    )
    implement.add_argument(
        "--allow-commands",
        action="store_true",
        help=(
            "let Agents run commands, not just edit files (ADR-0007). Off by default, "
            "and granted for this Run only -- never persisted to configuration"
        ),
    )
    implement.add_argument(
        "--no-context-pack",
        action="store_true",
        help=(
            "hand every Role an empty Context Pack, so each reads the repository for "
            "itself. This is the control Run: compare its cost against a packed Run of "
            "the same issue to find out what the pack is worth. Plugin Fragments ride "
            "in the pack, so this drops those too -- use --no-plugins to drop only those"
        ),
    )
    implement.add_argument(
        "--no-plugins",
        action="store_true",
        help=(
            "resolve the Context Pack as usual but activate no Plugins, so no Fragment "
            "reaches a prompt. This is the control for what the Fragments cost, and it "
            "is a separate switch because --no-context-pack removes both at once"
        ),
    )

    run = subcommands.add_parser(
        "run",
        help="run a Plugin's Command: a repeated chore, with no model involved",
    )
    run.add_argument(
        "name",
        nargs="?",
        help="the Command to run. Leave it out to list what this repository has",
    )
    run.add_argument("arguments", nargs="*", help="the Command's positional arguments")
    run.add_argument("-C", "--directory", default=".", help="repository to run in")

    init = subcommands.add_parser(
        "init",
        help="inspect this repository and write .agentforge/config.yaml",
    )
    init.add_argument("--provider", default=None, help="coding-agent CLI to drive")
    init.add_argument("-C", "--directory", default=".", help="repository to configure")
    init.add_argument(
        "--force",
        action="store_true",
        help="replace an existing config. Without it, init reports what differs and writes nothing",
    )

    unslop = subcommands.add_parser("unslop", help="scan prose for machine-writing tells")
    unslop.add_argument("path", help="file to scan")
    unslop.add_argument("--json", action="store_true", help="emit the full report as JSON")

    return parser


def _run_unslop(args: argparse.Namespace, runner=None) -> int:
    from .core.skills import run_unslop

    report = run_unslop(args.path, runner=runner)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        from .core.skills import _describe

        for result in report.results:
            if result.error:
                print(f"  {result.scanner}: could not run — {result.error}")
                continue
            verdict = "clean" if result.clean else f"{result.violations} finding(s)"
            print(f"  {result.scanner}: {verdict}")
            for line in _describe(result):
                print(f"      {line}")
        summary = "clean" if report.clean else f"{report.violations} finding(s)"
        print(f"{report.path.name}: {summary}")

    if report.failed:
        return 2
    return 0 if report.clean else 1


def parse_tier_overrides(values: list[str]) -> tuple[ModelTier | None, dict[str, ModelTier]]:
    """`deep` moves every Role; `implementer=deep` moves one."""
    default: ModelTier | None = None
    per_role: dict[str, ModelTier] = {}

    for value in values or ():
        role, _, tier = value.partition("=")
        if not tier:
            default = _tier(role)
            continue
        per_role[role.strip().lower()] = _tier(tier)

    return default, per_role


def _tier(value: str) -> ModelTier:
    try:
        return ModelTier(value.strip().lower())
    except ValueError as exc:
        known = ", ".join(str(tier) for tier in ModelTier)
        raise SystemExit(f"unknown Model Tier {value.strip()!r}; expected one of: {known}") from exc


def build_interviewer(stdin=None, prompt=input):
    """The human, as a callable — or `None` when nothing interactive is attached.

    The terminal lives here and nowhere else: `core` and `agents` take a
    callable, so the interview is exercised in tests by passing a list of
    answers rather than by faking a console.

    An empty line ends the interview, and so does end-of-input. A Task that was
    already clear should not cost a conversation, and a human who has said
    everything they have to say should not have to answer three more questions
    to get their Issue.
    """
    stream = stdin if stdin is not None else sys.stdin
    if not (hasattr(stream, "isatty") and stream.isatty()):
        return None

    asked = False

    def ask(question: str) -> str | None:
        nonlocal asked
        if not asked:
            print("\nThe Orchestrator has questions before it writes anything down.")
            print("Answer them, or press Enter on an empty line to plan with what it has.")
            asked = True
        print(f"\n  {question}")
        try:
            answer = prompt("  > ").strip()
        except EOFError:
            print()
            return None
        return answer or None

    return ask


def build_approver(assume_yes: bool = False, stdin=None, prompt=input):
    """The human, as a callable, answering once about the breakdown.

    `--yes` is a human who has already answered, so it is a callable that says
    yes rather than an absent one. Nobody at a terminal and no `--yes` returns
    `None`, and the runtime files nothing: a decomposition nobody has read is
    the one outcome worth refusing by default, because undoing it is fifteen
    issues to close by hand.
    """
    if assume_yes:
        return lambda slices: True

    stream = stdin if stdin is not None else sys.stdin
    if not (hasattr(stream, "isatty") and stream.isatty()):
        return None

    def approve(slices) -> bool:
        from .agents.decomposer import render_breakdown

        print(f"\nThis cuts into {len(slices)} Slice(s), each filed as its own issue:\n")
        for line in render_breakdown(slices):
            print(f"  {line}")
        print("\nBlockers are filed first, and a Slice waits for the ones it names.")
        try:
            answer = prompt("\nFile these? [y/N] ").strip().lower()
        except EOFError:
            print()
            return False
        return answer in {"y", "yes"}

    return approve


def _plan_source(args: argparse.Namespace) -> tuple[str, str] | None:
    """What to plan, and where it came from, for whichever command was typed."""
    if args.command == "plan":
        return args.task, "Typed at the command line just now, in their own words."

    from pathlib import Path

    path = Path(args.path)
    try:
        document = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"agentforge: cannot read {path}: {exc}", file=sys.stderr)
        return None

    if not document.strip():
        print(f"agentforge: {path} is empty; there is nothing to decompose.", file=sys.stderr)
        return None

    return document, f"A plan document they wrote, at `{path}` in this repository."


def _run_plan(args: argparse.Namespace, runner=None) -> int:
    """`agentforge plan` and `agentforge decompose`: one pipeline, two sources."""
    from .core.issues import render_run_cost
    from .core.runtime import Forge, RunFailed
    from .providers import DEFAULT_PROVIDER

    source = _plan_source(args)
    if source is None:
        return 2
    document, provenance = source

    forge = Forge(cwd=args.directory, provider=args.provider or DEFAULT_PROVIDER, runner=runner)

    try:
        outcome = forge.decompose(
            document,
            source=provenance,
            tier=_tier(args.tier) if args.tier else None,
            interviewer=build_interviewer(),
            approver=build_approver(args.yes),
        )
    except RunFailed as exc:
        print(f"agentforge: {exc}", file=sys.stderr)
        return 2

    if outcome.interview:
        print(f"\nInterview: {len(outcome.interview)} question(s) answered")

    code = _report_filed(outcome)
    _report_touched(outcome)
    if outcome.results:
        print(f"\n  Cost: {render_run_cost(outcome.results)}")
    return code


def _report_filed(outcome) -> int:
    """What was filed, what was not, and what to type next."""
    if outcome.declined:
        if not outcome.slices:
            print("agentforge: nothing to file.", file=sys.stderr)
            return 2
        print(
            f"\nNothing filed. The cut stands at {len(outcome.slices)} Slice(s); "
            "re-run to cut it again, or re-run with --yes to file this one.",
            file=sys.stderr,
        )
        return 1

    for one in outcome.filed:
        print(f"\nFiled issue #{one.issue.number}: {one.issue.url}")
        print(f"  {one.slice.title}")
        print(f"  Roster: {', '.join(f'{r.name} ({r.tier})' for r in one.document.roster)}")
        if one.blocked_by:
            print(f"  Blocked by: {', '.join(f'#{n}' for n in one.blocked_by)}")
        for note in one.document.notes:
            print(f"  Note: {note}")

    if outcome.unwritten_edges:
        print(
            "\nThis tracker would not record these edges natively, so they live in "
            "the issue bodies alone:"
        )
        for number, blocker in outcome.unwritten_edges:
            print(f"  - #{number} blocked by #{blocker}")

    if outcome.failure is not None:
        stage = "a Slice could not be planned" if outcome.filed else "planning stopped"
        print(f"\nagentforge: {stage}.", file=sys.stderr)
        print(f"  {outcome.failure.summary}", file=sys.stderr)
        if outcome.filed:
            print(
                f"  The {len(outcome.filed)} issue(s) above are complete and stand on "
                "their own; the Slices after this one were not filed.",
                file=sys.stderr,
            )
        return 1 if outcome.failure.escalated else 2

    if not outcome.filed:
        print("agentforge: nothing was filed.", file=sys.stderr)
        return 2

    first = outcome.filed[0].issue.number
    print(f"\nStart with:  agentforge implement {first}")
    if len(outcome.filed) > 1:
        print("Each later issue refuses to start until the ones it names have signed off.")
    return 0


def _report_touched(outcome) -> None:
    if not outcome.touched:
        return
    print("\nThe interview left changes in your working tree:")
    for path in outcome.touched:
        print(f"  - {path}")
    print("Review and commit them: a Run refuses to start on a dirty tree.")


def _run_implement(args: argparse.Namespace, runner=None) -> int:
    from .core.issues import IssueError, render_run_cost
    from .core.runtime import Forge, RunFailed
    from .providers import DEFAULT_PROVIDER

    default_tier, per_role = parse_tier_overrides(args.tier)
    forge = Forge(cwd=args.directory, provider=args.provider or DEFAULT_PROVIDER, runner=runner)

    try:
        state = forge.implement(
            args.issue,
            tier_overrides=per_role or None,
            tier=default_tier,
            allow_commands=args.allow_commands,
            resolve_context=not args.no_context_pack,
            use_plugins=not args.no_plugins,
            ignore_blockers=args.ignore_blockers,
        )
    except (RunFailed, IssueError) as exc:
        print(f"agentforge: {exc}", file=sys.stderr)
        return 2

    for result in state.results:
        marker = {
            Outcome.COMPLETED: "ok",
            Outcome.ESCALATED: "escalated",
            Outcome.FAILED: "failed",
        }[result.outcome]
        print(f"  [{marker}] {result.role} ({result.tier}) — {result.summary}")

    if state.results:
        print(f"\n  Cost: {render_run_cost(state.results)}")

    if state.status is RunStatus.AWAITING_SIGNOFF:
        print(f"\nDraft pull request: {state.pull_request}")
        print("AgentForge stops at Sign-off. A human merges.")
        return 0

    if state.status is RunStatus.HALTED:
        print(
            f"\nRun halted at step {state.current_step}. Issue #{state.issue} is labelled "
            f"`{RunStatus.HALTED.label}`; correct the plan block and re-run.",
            file=sys.stderr,
        )
        return 1

    if state.status is RunStatus.SUSPENDED:
        blocking = next((entry for entry in reversed(state.gates) if entry.blocked), None)
        gate = f"the `{blocking.kind}` Gate" if blocking else "a Gate"
        print(
            f"\nRun suspended at step {state.current_step}, waiting on {gate}. Issue "
            f"#{state.issue} is labelled `{RunStatus.SUSPENDED.label}`; re-run once it clears.",
            file=sys.stderr,
        )
        if blocking and blocking.summary:
            print(f"  {blocking.summary}", file=sys.stderr)
        return 1

    if not state.results:
        print(f"Issue #{state.issue} has nothing left to run.")
        return 0

    print(f"\nRun failed. See issue #{state.issue}.", file=sys.stderr)
    return 2


def _run_command(args: argparse.Namespace, runner=None) -> int:
    """`agentforge run`: a chore, run directly, with no Issue and no Run.

    Activation outside a Run has no blast radius to read, so what answers is
    what the repository is — its root markers. A dbt project has dbt chores
    whatever anybody is editing today (ADR-0019).

    The human typing this is ADR-0007's grant, so a Command that starts a
    process may start it here. That is the difference between this and the same
    Command reached from inside an unattended Run.
    """
    from pathlib import Path

    from .core.commands import run_command
    from .core.contracts import Plan
    from .core.process import SubprocessRunner
    from .core.registry import activate, commands_for

    root = Path(args.directory).resolve()
    table = commands_for(activate(Plan(summary=""), root))

    if not args.name:
        if not table:
            print(f"No Plugin answers for {root}, so there are no Commands to run.")
            print("A Command is contributed by a Plugin: see `agentforge --help`.")
            return 0
        print("Commands this repository's Plugins contribute:\n")
        for name, command in sorted(table.items()):
            named = " ".join(f"<{argument}>" for argument in command.arguments)
            print(f"  {name} {named}".rstrip())
            if command.summary:
                print(f"      {command.summary}")
        return 0

    command = table.get(args.name.strip().lower())
    if command is None:
        available = ", ".join(sorted(table)) or "none in this repository"
        print(f"agentforge: no Command named {args.name!r}; available: {available}", file=sys.stderr)
        return 2

    outcome = run_command(
        command,
        args.arguments,
        root=root,
        runner=runner if runner is not None else SubprocessRunner(),
        allow_commands=True,
    )

    for path in outcome.written:
        print(f"  wrote {path}")

    if outcome.error:
        print(f"agentforge: {outcome.error}", file=sys.stderr)
        return 2

    if outcome.result is not None and not outcome.result.ok:
        rendered = " ".join(outcome.result.argv)
        print(f"agentforge: `{rendered}` exited {outcome.result.returncode}", file=sys.stderr)
        detail = (outcome.result.stderr or outcome.result.stdout).strip()
        if detail:
            print(f"  {detail[:800]}", file=sys.stderr)
        return 1

    if outcome.written:
        print("\nReview them as a diff and commit them yourself: a Command commits nothing.")
    return 0


def _run_init(args: argparse.Namespace, runner=None) -> int:
    """`agentforge init`: look at the repository, say so, and write the config.

    The precondition comes first and refuses loudly. A repository with no
    GitHub remote cannot host a Run (ADR-0002), and finding that out at setup is
    better than finding it out when the first Run halts — so nothing is created
    until `open_repository` has answered.

    What init detects and cannot yet persist it prints. `load_config` reads two
    keys, and writing the rest would be writing keys nothing consults (ADR-0020).
    """
    from .core.config import load_config
    from .core.contracts import Plan
    from .core.process import SubprocessRunner
    from .core.project import config_path, detect, differences, render_config
    from .core.registry import activate
    from .core.repo import PreconditionFailed, open_repository
    from .providers import DEFAULT_PROVIDER, PROVIDERS

    provider = (args.provider or DEFAULT_PROVIDER).strip().lower()
    if provider not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        print(f"agentforge: unknown provider {provider!r}; available: {known}", file=sys.stderr)
        return 2

    runner = runner if runner is not None else SubprocessRunner()
    try:
        repo = open_repository(runner, args.directory)
    except PreconditionFailed as exc:
        print(f"agentforge: {exc}", file=sys.stderr)
        return 2

    active = activate(Plan(summary=""), repo.root)
    context = detect(
        repo.root,
        provider,
        tracked=repo.tracked_files(),
        plugins=tuple(plugin.name for plugin in active.plugins),
    )

    print(f"Repository: {repo.root}")
    print(f"  Languages: {', '.join(context.languages) or 'none recognised'}")
    print(f"  Provider:  {context.provider} ({context.capability_tier} capability tier)")
    suite = " ".join(context.test_suite)
    where = context.suite_detected or "not detected, so this is the documented default"
    print(f"  Suite:     `{suite}` — {where}")
    print(f"  Plugins:   {', '.join(context.plugins) or 'none by root marker'}")
    print(
        "             printed, not written: which Plugins answer is decided per Run\n"
        "             from the frozen plan's blast radius, not from this file."
    )

    path = config_path(repo.root)
    if path.is_file() and not args.force:
        found = differences(context, path.read_text(encoding="utf-8"))
        print(f"\n{path} already exists.")
        if not found:
            print("It matches what init would write. Nothing to do.")
            return 0
        for line in found:
            print(f"  - {line}")
        print("Nothing was written. Re-run with --force to replace it.")
        return 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_config(context), encoding="utf-8")
    load_config(repo.root)  # it reads back, or this command has not succeeded

    print(f"\nWrote {path}")
    print("Review it and commit it: AgentForge reads it and never edits it again.")
    return 0


def main(argv: list[str] | None = None, runner=None) -> int:
    """`runner` is the Command Runner seam: leave it unset and the real one is
    built. Tests pass a fake and the whole CLI runs offline."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 2

    if args.command == "unslop":
        return _run_unslop(args, runner)
    if args.command in {"plan", "decompose"}:
        return _run_plan(args, runner)
    if args.command == "implement":
        return _run_implement(args, runner)
    if args.command == "run":
        return _run_command(args, runner)
    if args.command == "init":
        return _run_init(args, runner)

    raise SystemExit(f"agentforge {args.command} is not implemented yet.")


if __name__ == "__main__":
    sys.exit(main())
