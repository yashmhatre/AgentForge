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

This CLI carries reasoning effort separately from model choice, and the
per-model defaults disagree: `gpt-5.6-sol` starts at `low` while the rest
start at `medium`. Left alone, `deep` would buy the frontier model and ask it
to think as little as possible. The adapter pins one value across all three
tiers instead, so the Model Tier chooses the model and nothing else shifts
underneath it.

Per the note on Issue #1: the useful version of this file is one written by
somebody who has not read `claude.py`. This one was not, so treat its shape as a
weaker signal than a genuinely independent adapter would be.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import ClassVar

from ..core.contracts import Effort, ModelTier, Usage
from ..core.process import CommandResult
from .base import CliProvider, ProviderOutput


class CodexProvider(CliProvider):
    name: ClassVar[str] = "codex"
    binary: ClassVar[str] = "codex"

    #: Slugs read out of a real install's `~/.codex/models_cache.json` rather
    #: than guessed. This is the same-generation reading of ADR-0004's three
    #: tiers, which the previous mapping named as the obvious alternative to
    #: itself and then declined. It was taken on 2026-09-03: stepping back a
    #: generation per tier bought older models on every axis at once, where the
    #: three 5.6 slugs differ in the one dimension a tier is supposed to name.
    #: Overriding it is a configuration line — now literally, under
    #: `providers.codex.models`.
    models: ClassVar[dict[ModelTier, str]] = {
        ModelTier.DEEP: "gpt-5.6-sol",
        ModelTier.STANDARD: "gpt-5.6-terra",
        ModelTier.CHEAP: "gpt-5.6-luna",
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

    #: This adapter used to pin one effort across every tier, because the
    #: per-model defaults disagree — `gpt-5.6-sol` starts at `low`, the others
    #: at `medium` — and pinning kept a Model Tier meaning one thing.
    #:
    #: The objection was right and the fix was the wrong half. A tier still
    #: means one thing; effort is simply no longer part of it, because it is
    #: now the Role's to declare (ADR-0004, amended 2026-09-03). What survives
    #: is the reason the constant existed: nothing is left to a per-model
    #: default, so an Agent's reasoning depth is what a Role asked for and
    #: never what a slug happened to ship with.
    #:
    #: `ultra` is available on `gpt-5.6-sol` alone and `Effort` does not offer
    #: it; a level one Provider can honor is not an intent-named level.

    def build_argv(
        self,
        model: str,
        effort: Effort,
        native_skills: tuple[str, ...] = (),
    ) -> Sequence[str]:
        """Options precede the subcommand: `codex [OPTIONS] <COMMAND> [ARGS]`.

        The prompt argument is `-`, which `codex exec --help` documents as "read
        instructions from stdin". Omitting it entirely reads stdin too, but the
        sentinel says so out loud, and the same help says a prompt supplied
        alongside piped stdin gets the stdin appended as a `<stdin>` block —
        which is the failure mode the explicit `-` rules out.

        This adapter previously passed `--full-auto` after `exec`, which fails
        twice over: that flag does not exist in the current CLI, and options
        placed after the subcommand are rejected regardless.

        Reasoning effort arrives as an argument rather than being read off a
        class constant. The old docstring noted that a per-tier value could not
        be set here, because the port hands the Model Tier to `model_for` and
        not to this method. That is still true and no longer matters: effort is
        not derived from the tier, so it travels on its own.
        """
        return (
            self.binary,
            "--model",
            model,
            "-c",
            f"model_reasoning_effort={effort}",
            "--sandbox",
            self.SANDBOX,
            "--ask-for-approval",
            self.PERMITTED if self.allow_commands else self.DENIED,
            "exec",
            "-",
        )

    def parse_output(self, result: CommandResult) -> ProviderOutput:
        """No envelope. The transcript is the output and the exit code is the verdict."""
        usage = _usage(result.stdout)

        if result.ok:
            return ProviderOutput(text=result.stdout, usage=usage)

        detail = (result.stderr or result.stdout or "").strip()
        return ProviderOutput(
            text=result.stdout,
            error=f"the codex CLI exited {result.returncode}: {detail[:400]}",
            usage=usage,
        )


#: The last line of a transcript, on a run that got that far. One figure and no
#: split, which is the asymmetry ADR-0009 exists to keep honest: this adapter
#: reports tokens and never dollars, and the Run Log says so rather than leaving
#: a blank where a price would have been.
_TOKENS = re.compile(r"tokens used:\s*([\d,]+)", re.IGNORECASE)


def _usage(transcript: str) -> Usage | None:
    """The token count this CLI prints when it finishes, if it printed one.

    The last match rather than the first: a transcript that reports per-turn
    counts ends with the one for the whole invocation, and an Agent that quoted
    the phrase in its own output would otherwise be believed over the CLI.
    """
    matches = _TOKENS.findall(transcript or "")
    if not matches:
        return None
    return Usage(provider=CodexProvider.name, total_tokens=int(matches[-1].replace(",", "")))


__all__ = ["CodexProvider"]
