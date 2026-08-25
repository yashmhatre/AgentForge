"""Command-line entry point for AgentForge."""

from __future__ import annotations

import argparse
import json
import sys

from .core.contracts import ModelTier, Outcome, RunStatus

TIER_HELP = (
    "override the Model Tier: `deep`, `standard`, `cheap`, or `role=tier` "
    "to move one Role. Repeatable."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentforge",
        description="Coordinate specialized software agents through reusable workflows.",
    )
    subcommands = parser.add_subparsers(dest="command", metavar="<command>")

    plan = subcommands.add_parser("plan", help="turn a task into a GitHub issue")
    plan.add_argument("task", help="the task, in your own words")
    plan.add_argument("--provider", default=None, help="coding-agent CLI to drive")
    plan.add_argument("--tier", default=None, help="Model Tier for the Orchestrator")
    plan.add_argument("-C", "--directory", default=".", help="repository to plan against")

    implement = subcommands.add_parser(
        "implement", help="run an issue's roster and open a draft pull request"
    )
    implement.add_argument("issue", type=int, help="issue number")
    implement.add_argument("--provider", default=None, help="coding-agent CLI to drive")
    implement.add_argument("--tier", action="append", default=[], help=TIER_HELP)
    implement.add_argument("-C", "--directory", default=".", help="repository to work in")

    init = subcommands.add_parser(
        "init", help="configure AgentForge for the repository in the working directory"
    )
    init.add_argument("--provider", default="claude", help="coding-agent CLI to drive")

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


def _run_plan(args: argparse.Namespace, runner=None) -> int:
    from .core.runtime import Forge, RunFailed
    from .providers import DEFAULT_PROVIDER

    forge = Forge(cwd=args.directory, provider=args.provider or DEFAULT_PROVIDER, runner=runner)

    try:
        outcome = forge.plan(args.task, tier=_tier(args.tier) if args.tier else None)
    except RunFailed as exc:
        print(f"agentforge: {exc}", file=sys.stderr)
        return 2

    result = outcome.result
    if not outcome.filed:
        heading = "needs a decision from you" if result.escalated else "could not plan this task"
        print(f"agentforge: the Orchestrator {heading}.", file=sys.stderr)
        print(f"  {result.summary}", file=sys.stderr)
        return 1 if result.escalated else 2

    issue = outcome.issue
    document = outcome.document
    print(f"Filed issue #{issue.number}: {issue.url}")
    print(f"  Roster: {', '.join(f'{r.name} ({r.tier})' for r in document.roster)}")
    for note in document.notes:
        print(f"  Note: {note}")
    print(f"\nRun it with:  agentforge implement {issue.number}")
    return 0


def _run_implement(args: argparse.Namespace, runner=None) -> int:
    from .core.issues import IssueError
    from .core.runtime import Forge, RunFailed
    from .providers import DEFAULT_PROVIDER

    default_tier, per_role = parse_tier_overrides(args.tier)
    forge = Forge(cwd=args.directory, provider=args.provider or DEFAULT_PROVIDER, runner=runner)

    try:
        state = forge.implement(
            args.issue, tier_overrides=per_role or None, tier=default_tier
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

    if state.status is RunStatus.AWAITING_SIGNOFF:
        print(f"\nDraft pull request: {state.pull_request}")
        print("AgentForge stops at Sign-off. A human merges.")
        return 0

    if state.status is RunStatus.ESCALATED:
        print(
            f"\nRun halted. Issue #{state.issue} is labelled "
            f"`{RunStatus.ESCALATED.label}`; correct the plan block and re-run.",
            file=sys.stderr,
        )
        return 1

    if not state.results:
        print(f"Issue #{state.issue} has nothing left to run.")
        return 0

    print(f"\nRun failed. See issue #{state.issue}.", file=sys.stderr)
    return 2


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
    if args.command == "plan":
        return _run_plan(args, runner)
    if args.command == "implement":
        return _run_implement(args, runner)

    raise SystemExit(f"agentforge {args.command} is not implemented yet.")


if __name__ == "__main__":
    sys.exit(main())
