"""Running a Command: a repeated chore, with no inference anywhere in it.

`agentforge run scaffold-dbt-model orders` writes the files and exits. No Issue
is filed, no Run starts, no Provider is invoked, and nothing here reads the
repository to decide what to write — a Command is a template and an argument
vector, and the whole of what it will do is readable in the Plugin that declares
it. That is what makes its output reviewable as an ordinary diff rather than as
a thing somebody has to check for hallucination.

Three rules this module owes whoever types one:

- **It never overwrites.** A Command that clobbered a file would be a Command
  nobody dares run twice, and the failure would be silent in the one place — a
  working tree — where the tool has already promised the diff is the review.
- **It never writes outside the repository.** The same containment rule the
  Context Pack resolver applies, for the same reason: a template path is data,
  and data that renders to `../../.ssh/authorized_keys` is refused rather than
  clamped.
- **It runs processes through the Command Runner, under ADR-0007.** A Command
  that starts something is subject to the same default-deny as everything else,
  and the human typing `agentforge run` is the grant. Nothing else here starts a
  process, and nothing here imports `subprocess`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template

from ..context.resolver import inside
from .contracts import Command
from .process import CommandResult, CommandRunner, MissingBinary


@dataclass(frozen=True)
class CommandOutcome:
    """What running one Command did, or why it did nothing.

    `written` is what a human is about to read in `git status`, so it is carried
    even when the process that followed the writing failed: files that reached
    the tree are the tree's now, and a report that hid them would send somebody
    looking for a mess they were not told about.
    """

    command: str
    written: tuple[str, ...] = ()
    result: CommandResult | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and (self.result is None or self.result.ok)


def run_command(
    command: Command,
    arguments: list[str] | tuple[str, ...],
    root: Path | str,
    runner: CommandRunner,
    allow_commands: bool = False,
) -> CommandOutcome:
    """Run one Command in one repository, and say what it did.

    `allow_commands` is ADR-0007's posture and matters only to a Command that
    declares an `argv`: writing a file is what an Agent does anyway, and starting
    a process is the thing the gate is about. `agentforge run` passes it because
    a human typed the name — an explicit, attended grant is exactly what that
    ADR asks for — and a Run passes its own flag, so a Plugin cannot become the
    route by which an unattended Agent executes arbitrary code.

    Every failure is a returned outcome rather than an exception. The caller is
    a CLI that has to print something and pick an exit status, and a traceback
    is neither.
    """
    root = Path(root)

    if len(arguments) != len(command.arguments):
        return CommandOutcome(command=command.name, error=_usage(command))

    values = dict(zip(command.arguments, arguments, strict=True))

    # Before anything is written, rather than after. A Command refused halfway
    # leaves files in a tree whose author was told the Command did not run.
    if command.argv and not allow_commands:
        return CommandOutcome(
            command=command.name,
            error=(
                f"{command.name} runs `{' '.join(command.argv)}`, and command execution "
                "is denied here (ADR-0007). Run it yourself, or start the Run with "
                "--allow-commands."
            ),
        )

    try:
        planned = [_render(template, values, root) for template in command.templates]
    except KeyError as exc:
        # A placeholder no argument answers for. A declaration fault rather than
        # a typing one, and it names the placeholder so whoever wrote the Plugin
        # can find it.
        return CommandOutcome(
            command=command.name,
            error=f"{command.name} names a placeholder its arguments do not define: {exc}",
        )
    except ValueError as exc:
        return CommandOutcome(command=command.name, error=str(exc))

    for path, _ in planned:
        if (root / path).exists():
            return CommandOutcome(
                command=command.name,
                error=(
                    f"{path} already exists. {command.name} writes files and never "
                    "replaces one; move it aside, or name something else."
                ),
            )

    written: list[str] = []
    for path, text in planned:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(path)

    if not command.argv:
        return CommandOutcome(command=command.name, written=tuple(written))

    argv = tuple(_substitute(part, values) for part in command.argv)
    try:
        result = runner.run(argv, cwd=root)
    except MissingBinary as exc:
        return CommandOutcome(command=command.name, written=tuple(written), error=str(exc))

    return CommandOutcome(command=command.name, written=tuple(written), result=result)


def _render(template, values: dict[str, str], root: Path) -> tuple[str, str]:
    """One template as the path it writes and the text it writes there."""
    rendered = _substitute(template.path, values)
    path = inside(rendered, root)
    if path is None:
        raise ValueError(
            f"{rendered!r} is outside the repository. A Command writes into the tree "
            "it was run in and nowhere else."
        )
    return path, _substitute(template.text, values)


def _substitute(source: str, values: dict[str, str]) -> str:
    """`$name` and `${name}`, and `$$` for a literal dollar.

    `string.Template` rather than `str.format`, because a dbt model is Jinja and
    a template full of `{{ ref(...) }}` would have to double every brace it
    already carries.
    """
    return Template(source).substitute(values)


def _usage(command: Command) -> str:
    """What to type instead, in the shape the CLI's own help uses."""
    named = " ".join(f"<{name}>" for name in command.arguments)
    takes = (
        f"takes {len(command.arguments)} argument(s)"
        if command.arguments
        else "takes no arguments"
    )
    return f"{command.name} {takes}: agentforge run {command.name} {named}".rstrip()


__all__ = ["CommandOutcome", "run_command"]
