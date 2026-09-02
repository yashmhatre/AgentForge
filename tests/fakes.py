"""The one test double in the suite.

Everything AgentForge does that a test can observe crosses the Command Runner:
the argument vectors it sends to `git`, `gh`, and a coding-agent CLI, and the
artifacts it builds out of what comes back. Faking that single port lets plan
serialization, Roster ordering, tier resolution, Run Log sequencing, escalation
handling, and precondition checks all run as real code.

Scripting is by argument-vector prefix, so a test says "when something runs
`gh issue view`, answer with this" and stays indifferent to the flags the
implementation chooses.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agentforge_framework.core.process import CommandResult


@dataclass
class _Script:
    prefix: tuple[str, ...]
    contains: tuple[str, ...]
    results: list[tuple[int, str, str]]

    def matches(self, argv: tuple[str, ...]) -> bool:
        if argv[: len(self.prefix)] != self.prefix:
            return False
        return all(token in argv for token in self.contains)

    def take(self) -> tuple[int, str, str]:
        """Consume one response; the last one repeats forever."""
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


@dataclass
class FakeRunner:
    """A Command Runner that answers from a script and records every call."""

    binaries: set[str] = field(default_factory=lambda: {"git", "gh", "claude", "codex"})
    calls: list[tuple[str, ...]] = field(default_factory=list)
    cwds: list[str | None] = field(default_factory=list)
    #: What each call was fed on stdin, positionally aligned with `calls`. A
    #: coding-agent CLI is handed its prompt this way and never in argv (#100),
    #: so a test asking what a Role was told asks `prompts_to`.
    stdins: list[str | None] = field(default_factory=list)
    scripts: list[_Script] = field(default_factory=list)
    default: tuple[int, str, str] = (0, "", "")

    # --- scripting ---------------------------------------------------------

    def script(
        self,
        *prefix: str,
        stdout: str | Sequence[str] = "",
        stderr: str = "",
        returncode: int = 0,
        contains: Sequence[str] = (),
    ) -> FakeRunner:
        """Answer any call starting with `prefix`. A sequence of stdouts is
        consumed in order, so a test can make `git status` change mid-Run."""
        outs = [stdout] if isinstance(stdout, str) else list(stdout)
        # Later scripts win over earlier ones, so a test can override a fixture.
        self.scripts.insert(
            0,
            _Script(
                prefix=tuple(prefix),
                contains=tuple(contains),
                results=[(returncode, out, stderr) for out in outs] or [(returncode, "", stderr)],
            ),
        )
        return self

    def install(self, *binaries: str) -> FakeRunner:
        """Simulate a machine where a tool a Run may reach for is present.

        The default set is what every Run needs; a Workflow that declares a Gate
        of its own reaches for more, and says so here rather than by the absence
        of a check.
        """
        self.binaries.update(binaries)
        return self

    def uninstall(self, *binaries: str) -> FakeRunner:
        """Simulate a machine where a tool is not installed."""
        for binary in binaries:
            self.binaries.discard(binary)
        return self

    # --- the port ----------------------------------------------------------

    def _record(self, argv: tuple[str, ...], cwd, stdin: str | None) -> None:
        """The three ledgers stay positionally aligned, so a subclass that
        answers some calls itself records through here rather than appending to
        one of them and quietly desyncing the others."""
        self.calls.append(argv)
        self.cwds.append(str(cwd) if cwd is not None else None)
        self.stdins.append(stdin)

    def run(self, argv, *, cwd=None, stdin=None, timeout=None) -> CommandResult:
        argv = tuple(str(part) for part in argv)
        self._record(argv, cwd, stdin)

        for script in self.scripts:
            if script.matches(argv):
                returncode, stdout, stderr = script.take()
                return CommandResult(argv=argv, returncode=returncode, stdout=stdout, stderr=stderr)

        returncode, stdout, stderr = self.default
        return CommandResult(argv=argv, returncode=returncode, stdout=stdout, stderr=stderr)

    def has_binary(self, binary: str) -> bool:
        return binary in self.binaries

    # --- assertions --------------------------------------------------------

    def _indices(self, *prefix: str) -> list[int]:
        return [i for i, call in enumerate(self.calls) if call[: len(prefix)] == tuple(prefix)]

    def matching(self, *prefix: str) -> list[tuple[str, ...]]:
        return [self.calls[i] for i in self._indices(*prefix)]

    def prompts_to(self, *prefix: str) -> list[str]:
        """What each matching call was told, in call order.

        The transport is this helper's business and not the caller's: a test
        about what a Role was asked should not have to know that the prompt
        arrives on stdin rather than after a flag.
        """
        return [self.stdins[i] or "" for i in self._indices(*prefix)]

    def prompt_to(self, *prefix: str) -> str:
        """What the single matching call was told; fails loudly if there is not
        exactly one."""
        found = self.prompts_to(*prefix)
        assert len(found) == 1, f"expected exactly one {' '.join(prefix)} call, got {len(found)}"
        return found[0]

    def only(self, *prefix: str) -> tuple[str, ...]:
        """The single call starting with `prefix`; fails loudly if there is not
        exactly one."""
        found = self.matching(*prefix)
        assert len(found) == 1, f"expected exactly one {' '.join(prefix)} call, got {found}"
        return found[0]

    def argument_after(self, flag: str, *prefix: str) -> str:
        call = self.only(*prefix)
        return call[call.index(flag) + 1]

    def ran(self, *prefix: str) -> bool:
        return bool(self.matching(*prefix))


def github_repository(runner: FakeRunner, root: Path) -> FakeRunner:
    """Script the calls every Run makes before it does anything interesting."""
    runner.script("git", "rev-parse", "--show-toplevel", stdout=f"{root}\n")
    runner.script("git", "remote", "get-url", stdout="https://github.com/acme/pipelines.git\n")
    runner.script("git", "rev-parse", "--abbrev-ref", stdout="main\n")
    runner.script("git", "status", "--porcelain", stdout="")
    runner.script("git", "rev-parse", "--verify", returncode=1)
    # A branch with something on it. The Run asks git this rather than asking a
    # Role what it changed, so a test about an empty branch scripts a 0 here.
    runner.script("git", "rev-list", "--count", stdout="2\n")
    runner.script("gh", "repo", "view", stdout='{"defaultBranchRef": {"name": "main"}}')
    return runner
