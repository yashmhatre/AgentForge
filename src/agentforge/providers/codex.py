"""A second adapter, to keep ADR-0001's portability claim honest.

This exists to prove the Provider port is not merely Claude-shaped. `codex exec`
differs from `claude -p` in the two places that matter: it prints a transcript
rather than a JSON envelope, so success has to be read from the exit status, and
its model identifiers are its own.

It is not a shipping adapter. The tier mapping below is a placeholder — it names
models by intent-adjacent size rather than from verified CLI documentation — and
it should be checked against a real `codex` install before anyone relies on it.
ADR-0004 makes that a configuration change rather than a code change.

Per the note on Issue #1: the useful version of this file is one written by
somebody who has not read `claude.py`. This one was not, so treat its shape as a
weaker signal than a genuinely independent adapter would be.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from ..core.contracts import ModelTier
from ..core.process import CommandResult
from .base import CliProvider, ProviderOutput


class CodexProvider(CliProvider):
    name: ClassVar[str] = "codex"
    binary: ClassVar[str] = "codex"

    models: ClassVar[dict[ModelTier, str]] = {
        ModelTier.DEEP: "gpt-5-codex-high",
        ModelTier.STANDARD: "gpt-5-codex",
        ModelTier.CHEAP: "gpt-5-codex-mini",
    }

    def build_argv(self, prompt: str, model: str) -> Sequence[str]:
        """ADR-0007's two postures, mapped onto this CLI.

        `--full-auto` is the most permissive thing codex offers, and passing it
        unconditionally is what made this adapter contradict both ADR-0007 and
        the `claude` adapter beside it. The denied posture omits it rather than
        naming a stricter flag: removing a permissive switch is safe against a
        CLI this suite cannot exercise, whereas guessing at the name of a
        sandbox flag is exactly the version-bump breakage ADR-0001 warns about.
        Verify against a real `codex` before relying on the denied path.
        """
        posture = ("--full-auto",) if self.allow_commands else ()
        return (self.binary, "exec", "--model", model, *posture, prompt)

    def parse_output(self, result: CommandResult) -> ProviderOutput:
        """No envelope. The transcript is the output and the exit code is the verdict."""
        if result.ok:
            return ProviderOutput(text=result.stdout)

        detail = (result.stderr or result.stdout or "").strip()
        return ProviderOutput(
            text=result.stdout,
            error=f"the codex CLI exited {result.returncode}: {detail[:400]}",
        )


__all__ = ["CodexProvider"]
