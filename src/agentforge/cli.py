"""Command-line entry point for AgentForge."""

from __future__ import annotations

import argparse
import json
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentforge",
        description="Coordinate specialized software agents through reusable workflows.",
    )
    subcommands = parser.add_subparsers(dest="command", metavar="<command>")

    plan = subcommands.add_parser("plan", help="turn a task into a GitHub issue")
    plan.add_argument("task", help="the task, in your own words")

    implement = subcommands.add_parser(
        "implement", help="run an issue's roster and open a draft pull request"
    )
    implement.add_argument("issue", type=int, help="issue number")

    init = subcommands.add_parser(
        "init", help="configure AgentForge for the repository in the working directory"
    )
    init.add_argument("--provider", default="claude", help="coding-agent CLI to drive")

    unslop = subcommands.add_parser("unslop", help="scan prose for machine-writing tells")
    unslop.add_argument("path", help="file to scan")
    unslop.add_argument("--json", action="store_true", help="emit the full report as JSON")

    return parser


def _run_unslop(args: argparse.Namespace) -> int:
    from .core.skills import run_unslop

    report = run_unslop(args.path)

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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 2

    if args.command == "unslop":
        return _run_unslop(args)

    raise SystemExit(f"agentforge {args.command} is not implemented yet.")


if __name__ == "__main__":
    sys.exit(main())
