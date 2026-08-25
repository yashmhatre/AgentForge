"""A second adapter, to keep ADR-0001's portability claim honest.

This exists to prove the Provider port is not merely Claude-shaped. `codex exec`
differs from `claude -p` in the two places that matter: it prints a transcript
rather than a JSON envelope, so success has to be read from the exit status, and
its model identifiers are its own.

The posture flags below are taken from `codex --help` on a real install. The
tier mapping is not: `gpt-5-codex*` does not exist on the account this was
checked against, whose picker offers GPT-5.6 Sol, Terra, and Luna, GPT-5.5,
GPT-5.4, and GPT-5.4 Mini. Those are display names rather than `--model`
strings, so the mapping stays wrong until someone reads the identifiers out of
`~/.codex/config.toml`. ADR-0004 anticipated exactly this — tier names outlive
model names — and makes the fix a configuration change.

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

    #: ADR-0007's two postures, on the two axes `codex --help` documents.
    #:
    #: The sandbox never changes: an Agent writes in the workspace and nowhere
    #: else, in both postures. What the gate moves is the approval policy.
    #: `untrusted` auto-runs only reads (`ls`, `cat`, `sed`) and escalates
    #: anything else, which is this CLI's nearest analogue to the `claude`
    #: adapter's `acceptEdits`. `never` stops asking.
    #:
    #: `danger-full-access` and `--dangerously-bypass-approvals-and-sandbox`
    #: are deliberately unused. ADR-0007 opens a gate; it does not remove the
    #: sandbox, and an unattended Role is the last thing that should be outside
    #: one.
    SANDBOX: ClassVar[str] = "workspace-write"
    DENIED: ClassVar[str] = "untrusted"
    PERMITTED: ClassVar[str] = "never"

    def build_argv(self, prompt: str, model: str) -> Sequence[str]:
        """Options precede the subcommand: `codex [OPTIONS] <COMMAND> [ARGS]`.

        This adapter previously passed `--full-auto` after `exec`, which fails
        twice over: that flag does not exist in the current CLI, and options
        placed after the subcommand are rejected regardless.
        """
        return (
            self.binary,
            "--model",
            model,
            "--sandbox",
            self.SANDBOX,
            "--ask-for-approval",
            self.PERMITTED if self.allow_commands else self.DENIED,
            "exec",
            prompt,
        )

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
