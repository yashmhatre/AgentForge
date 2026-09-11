"""The Antigravity (agy) adapter.

`agy -p -` runs headlessly: it takes a prompt on stdin, edits files in the
working directory, and exits. `--output-format json` wraps the run in an envelope
carrying a `status` flag, the model's final response under `response`, and token
usage counts.

Model identifiers map onto Google DeepMind's Gemini models available via Antigravity.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import ClassVar

from ..core.contracts import Effort, ModelTier, Usage
from ..core.process import CommandResult, MissingBinary, require
from .base import CliProvider, ProviderError, ProviderOutput

_EFFORT_MAP = {
    Effort.LOW: "low",
    Effort.MEDIUM: "medium",
    Effort.HIGH: "high",
    Effort.XHIGH: "high",
    Effort.MAX: "high",
}


class AntigravityProvider(CliProvider):
    name: ClassVar[str] = "antigravity"
    binary: ClassVar[str] = "agy"

    models: ClassVar[dict[ModelTier, str]] = {
        ModelTier.DEEP: "gemini-3.1-pro-high",
        ModelTier.STANDARD: "gemini-3.8-flash-high",
        ModelTier.CHEAP: "gemini-3.8-flash-low",
    }

    def __init__(
        self,
        runner,
        timeout: float | None = 1800.0,
        allow_commands: bool = False,
        config=None,
    ) -> None:
        super().__init__(
            runner,
            timeout=timeout,
            allow_commands=allow_commands,
            config=config,
        )
        env_binary = os.environ.get("ANTIGRAVITY_AGENTAPI_EXE")
        if env_binary and runner.has_binary(env_binary):
            self.binary = env_binary
        else:
            self.binary = self.__class__.binary

    def preflight(self) -> None:
        try:
            require(
                self.runner,
                self.binary,
                f"AgentForge drives the {self.name} CLI (ADR-0001); install it or "
                f"select another provider with --provider.",
            )
        except MissingBinary as exc:
            raise ProviderError(str(exc)) from exc

    def build_argv(
        self,
        model: str,
        effort: Effort,
        native_skills: tuple[str, ...] = (),
    ) -> Sequence[str]:
        argv = [
            self.binary,
            "--output-format",
            "json",
            "-p",
            "-",
            "--model",
            model,
            "--effort",
            _EFFORT_MAP.get(effort, "medium"),
        ]
        if self.allow_commands:
            argv.append("--dangerously-skip-permissions")
        else:
            argv.extend(["--mode", "accept-edits"])
        return tuple(argv)

    def parse_output(self, result: CommandResult) -> ProviderOutput:
        if not result.stdout.strip():
            reason = result.stderr.strip() or f"exit status {result.returncode}"
            return ProviderOutput(
                text="",
                error=f"the antigravity CLI reported an error: {reason}",
            )

        try:
            record = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return ProviderOutput(
                text=result.stdout,
                error=f"the antigravity CLI emitted unparseable JSON: {exc}",
            )

        if not isinstance(record, dict):
            return ProviderOutput(
                text=result.stdout,
                error="the antigravity CLI emitted a non-object JSON payload",
            )

        text = str(record.get("response") or record.get("result") or record.get("text") or "")
        usage = _usage(record)

        status = record.get("status")
        if (status and str(status).upper() != "SUCCESS") or not result.ok:
            reason = (
                text.strip()
                or result.stderr.strip()
                or f"status {status}"
                or f"exit status {result.returncode}"
            )
            return ProviderOutput(
                text=text,
                error=f"the antigravity CLI reported an error: {reason}",
                usage=usage,
            )

        return ProviderOutput(text=text, usage=usage)


def _usage(record: dict) -> Usage | None:
    """What the Antigravity envelope says the invocation consumed."""
    counts = record.get("usage")
    if not isinstance(counts, dict):
        return None

    usage = Usage(
        provider=AntigravityProvider.name,
        input_tokens=_number(counts.get("input_tokens"), int),
        output_tokens=_number(counts.get("output_tokens"), int),
        total_tokens=_number(counts.get("total_tokens"), int),
    )
    return usage if usage.reported else None


def _number(value: object, cast):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return cast(value)


__all__ = ["AntigravityProvider"]
