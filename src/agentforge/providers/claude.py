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

from ..core.contracts import ModelTier
from ..core.process import CommandResult
from .base import CliProvider, ProviderOutput


class ClaudeProvider(CliProvider):
    name: ClassVar[str] = "claude"
    binary: ClassVar[str] = "claude"

    models: ClassVar[dict[ModelTier, str]] = {
        ModelTier.DEEP: "opus",
        ModelTier.STANDARD: "sonnet",
        ModelTier.CHEAP: "haiku",
    }

    #: An Agent that has to ask before every edit cannot run unattended. The
    #: blast radius is bounded by the branch the runtime creates first.
    permission_mode: ClassVar[str] = "acceptEdits"

    def build_argv(self, prompt: str, model: str) -> Sequence[str]:
        return (
            self.binary,
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "json",
            "--permission-mode",
            self.permission_mode,
        )

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

        if record.get("is_error") or not result.ok:
            reason = text.strip() or result.stderr.strip() or f"exit status {result.returncode}"
            return ProviderOutput(text=text, error=f"the claude CLI reported an error: {reason}")

        return ProviderOutput(text=text)


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
