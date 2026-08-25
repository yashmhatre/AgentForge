"""A second adapter, to keep ADR-0001's portability claim honest.

This exists to prove the Provider port is not merely Claude-shaped. `codex exec`
differs from `claude -p` in the two places that matter: it prints a transcript
rather than a JSON envelope, so success has to be read from the exit status, and
its model identifiers are its own.

The posture flags and model slugs below are read off a real install — `codex
--help` and `~/.codex/models_cache.json` — rather than guessed. The previous
`gpt-5-codex*` identifiers existed on no account we could find, which is
ADR-0004's whole point: tier names outlive model names, and a pinned
identifier goes stale inside a release.

One axis is deliberately unhandled. This CLI carries reasoning effort
separately from model choice (`model_reasoning_effort`, low through ultra),
and `gpt-5.6-sol` defaults to `low` — so `deep` currently buys the frontier
model at its shallowest setting. Fixing that means `build_argv` seeing the
Model Tier, which it does not; see the note in that method.

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

    #: Slugs read out of a real install's `~/.codex/models_cache.json` rather
    #: than guessed. The tiers step back a generation at a time: the current
    #: frontier model for `deep`, then the two preceding releases. The
    #: same-generation alternatives (`gpt-5.6-terra`, `gpt-5.6-luna`) are the
    #: obvious other reading of ADR-0004's three tiers; this mapping is the
    #: maintainer's choice, and overriding it is a configuration line.
    models: ClassVar[dict[ModelTier, str]] = {
        ModelTier.DEEP: "gpt-5.6-sol",
        ModelTier.STANDARD: "gpt-5.5",
        ModelTier.CHEAP: "gpt-5.4",
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

    def build_argv(
        self, prompt: str, model: str, native_skills: tuple[str, ...] = ()
    ) -> Sequence[str]:
        """Options precede the subcommand: `codex [OPTIONS] <COMMAND> [ARGS]`.

        This adapter previously passed `--full-auto` after `exec`, which fails
        twice over: that flag does not exist in the current CLI, and options
        placed after the subcommand are rejected regardless.

        Reasoning effort is missing here and should not be. It is a second axis
        this CLI exposes (`-c model_reasoning_effort=...`), and `gpt-5.6-sol`
        defaults to `low`, so `deep` currently pays for the frontier model and
        asks it to think as little as possible. Setting it needs the Model Tier,
        which the port hands to `model_for` and not to this method. Widening the
        signature is the fix and it touches every adapter, so it wants doing on
        its own rather than folded in here.
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
