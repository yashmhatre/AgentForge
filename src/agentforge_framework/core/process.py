"""The one place AgentForge touches an external process.

`gh`, the coding-agent CLIs, `git`, and the vendored unslop scanners all route
through this port. Nothing else in the codebase imports `subprocess`.

The payoff is in the test suite: a fake runner scripted by argument-vector
prefix stands in for every external tool at once, so the whole framework above
this line runs as real code with no network, no GitHub account, and no
coding-agent CLI installed.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class MissingBinary(RuntimeError):
    """A required external tool is not on PATH.

    Raised in preference to a bare `FileNotFoundError` so the message names the
    binary a user has to install rather than the syscall that failed.
    """

    def __init__(self, binary: str, hint: str = "") -> None:
        message = f"{binary!r} is not installed or not on PATH"
        if hint:
            message = f"{message}. {hint}"
        super().__init__(message)
        self.binary = binary


@dataclass(frozen=True)
class CommandResult:
    """Exit status, standard output, and standard error. Nothing else."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def check(self) -> CommandResult:
        """Return self, or raise with enough context to debug the failure."""
        if self.ok:
            return self
        rendered = " ".join(self.argv)
        detail = (self.stderr or self.stdout or "").strip()
        raise CommandFailed(f"`{rendered}` exited {self.returncode}: {detail[:800]}", result=self)


class CommandFailed(RuntimeError):
    """An external process ran and returned a non-zero status."""

    def __init__(self, message: str, result: CommandResult) -> None:
        super().__init__(message)
        self.result = result


class CommandRunner(Protocol):
    """The port. An argument vector and a working directory go in; a result
    comes out."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        stdin: str | None = None,
        timeout: float | None = None,
    ) -> CommandResult: ...

    def has_binary(self, binary: str) -> bool:
        """Whether the tool is available, checked before a Run spends anything."""
        ...


class SubprocessRunner:
    """The real implementation. The only `subprocess` call site in AgentForge."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        stdin: str | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        argv = tuple(str(part) for part in argv)
        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd) if cwd else None,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                # A non-zero exit is a result here, not an exception. `gh` uses
                # one to mean "no such issue" and the unslop scanners use one to
                # mean "found something"; callers decide what it means.
                check=False,
            )
        except FileNotFoundError as exc:
            raise MissingBinary(argv[0]) from exc
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                argv=argv,
                returncode=124,
                stdout=_text(exc.stdout),
                stderr=f"timed out after {timeout}s",
            )
        return CommandResult(
            argv=argv,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def has_binary(self, binary: str) -> bool:
        return shutil.which(binary) is not None


def _text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def require(runner: CommandRunner, binary: str, hint: str = "") -> None:
    """Fail before a model is invoked rather than halfway through a Run."""
    if not runner.has_binary(binary):
        raise MissingBinary(binary, hint)
