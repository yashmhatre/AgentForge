"""The Claude Code adapter.

`claude -p` runs headlessly: it takes a prompt, edits files in the working
directory, and exits. `--output-format json` wraps the run in an envelope
carrying an `is_error` flag and the model's final text under `result`, which is
the only reason this adapter can tell a CLI failure from an Agent failure.

Model identifiers appear here and nowhere else in AgentForge (ADR-0004). The
aliases are used rather than pinned versions so that a vendor release does not
require an AgentForge release; a team that wants a specific version overrides
the mapping in configuration once `agentforge init` exists (M5).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import ClassVar

from ..core.contracts import ModelTier, Usage
from ..core.process import CommandResult
from ..core.skills import SKILLS_ROOT
from .base import CliProvider, ProviderOutput


class ClaudeProvider(CliProvider):
    name: ClassVar[str] = "claude"
    binary: ClassVar[str] = "claude"

    models: ClassVar[dict[ModelTier, str]] = {
        ModelTier.DEEP: "opus",
        ModelTier.STANDARD: "sonnet",
        ModelTier.CHEAP: "haiku",
    }

    #: ADR-0007's two postures, mapped onto this CLI's permission modes.
    #: `acceptEdits` lets an Agent write files and nothing else; commands still
    #: need a confirmation that headless mode cannot give, which is the whole
    #: bug in #18. `bypassPermissions` is the open gate. An Agent that has to
    #: ask before every edit cannot run unattended at all, so there is no
    #: third, stricter posture — the blast radius is bounded by the branch the
    #: runtime creates before any Agent is invoked.
    DENIED: ClassVar[str] = "acceptEdits"
    PERMITTED: ClassVar[str] = "bypassPermissions"

    @property
    def permission_mode(self) -> str:
        return self.PERMITTED if self.allow_commands else self.DENIED

    def build_argv(self, model: str, native_skills: tuple[str, ...] = ()) -> Sequence[str]:
        """`-p` carries no prompt argument: with none, the CLI reads stdin.

        Verified against the installed CLI rather than the help text, which
        documents `-p` as "print response and exit" and says nothing about where
        the prompt comes from.
        """
        argv = (
            self.binary,
            "-p",
            "--model",
            model,
            "--output-format",
            "json",
            "--permission-mode",
            self.permission_mode,
        )
        if native_skills:
            argv += ("--plugin-dir", str(SKILLS_ROOT.parent))
        return argv

    def parse_output(self, result: CommandResult) -> ProviderOutput:
        """Unwrap the JSON envelope.

        Failure modes, in the order they actually happen: the CLI is missing or
        crashed before printing anything; it printed something that is not JSON;
        it printed a well-formed envelope reporting its own error.
        """
        stdout = result.stdout.strip()
        if not stdout:
            detail = result.stderr.strip() or f"exit status {result.returncode}"
            return ProviderOutput(text="", error=f"the claude CLI produced no output: {detail}")

        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            # Older CLIs and `--output-format text` print bare text. The result
            # block still travels in it, so this degrades rather than fails.
            if result.ok:
                return ProviderOutput(text=stdout)
            return ProviderOutput(
                text=stdout,
                error=f"the claude CLI exited {result.returncode} without a JSON envelope",
            )

        record = _final_record(envelope)
        text = str(record.get("result") or record.get("text") or "")
        usage = _usage(record)

        if record.get("is_error") or not result.ok:
            reason = text.strip() or result.stderr.strip() or f"exit status {result.returncode}"
            return ProviderOutput(
                text=text,
                error=f"the claude CLI reported an error: {reason}",
                usage=usage,
            )

        return ProviderOutput(text=text, usage=usage)


#: What the envelope calls the tokens that went in. Cached ones are counted
#: with the rest: they are cheaper, not free, and the price the CLI charged for
#: them is already inside `total_cost_usd` — a token figure that omitted them
#: would disagree with the dollar figure beside it.
_INPUT_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _usage(record: dict) -> Usage | None:
    """What this envelope says the invocation consumed.

    This CLI is the generous one: dollars and a token split, both in the record
    AgentForge was already parsing for the result text. `None` when the envelope
    carries neither, so that a Run Log line can say the CLI reported nothing
    rather than printing a zero nobody measured.
    """
    counts = record.get("usage")
    counts = counts if isinstance(counts, dict) else {}

    inputs = [_number(counts.get(field), int) for field in _INPUT_FIELDS]
    counted = [value for value in inputs if value is not None]

    usage = Usage(
        provider=ClaudeProvider.name,
        input_tokens=sum(counted) if counted else None,
        output_tokens=_number(counts.get("output_tokens"), int),
        cost_usd=_number(record.get("total_cost_usd"), float),
    )
    return usage if usage.reported else None


def _number(value: object, cast):
    """One figure out of the envelope, or `None` where it had none to give."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return cast(value)



def _final_record(envelope: object) -> dict:
    """The terminal record, whether the CLI emitted one object or a stream."""
    if isinstance(envelope, dict):
        return envelope
    if isinstance(envelope, list):
        for item in reversed(envelope):
            if isinstance(item, dict) and item.get("type") == "result":
                return item
        for item in reversed(envelope):
            if isinstance(item, dict):
                return item
    return {}


__all__ = ["ClaudeProvider"]
