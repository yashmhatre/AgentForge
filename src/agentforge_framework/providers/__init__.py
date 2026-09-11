"""Provider interfaces and integrations.

Selecting a Provider is the only place a user names a coding-agent CLI.
Everything downstream of `get_provider` speaks in Model Tiers (ADR-0004).
"""

from __future__ import annotations

import os

from ..core.config import Config
from ..core.process import CommandRunner
from .antigravity import AntigravityProvider
from .base import CliProvider, Provider, ProviderError, ProviderOutput
from .claude import ClaudeProvider
from .codex import CodexProvider

PROVIDERS: dict[str, type[CliProvider]] = {
    ClaudeProvider.name: ClaudeProvider,
    CodexProvider.name: CodexProvider,
    AntigravityProvider.name: AntigravityProvider,
}

DEFAULT_PROVIDER = ClaudeProvider.name


def detect_default_provider(runner: CommandRunner | None = None) -> str:
    """Determine the most appropriate default provider for the active environment.

    If running inside Antigravity or Antigravity IDE, prefer Antigravity.
    Otherwise, check the CommandRunner for available CLIs before falling back to Claude.
    """
    is_fake = runner is not None and type(runner).__name__.startswith("Fake")

    if not is_fake and (
        os.environ.get("ANTIGRAVITY_AGENT") == "1"
        or os.environ.get("AI_AGENT") == "antigravity"
        or bool(os.environ.get("ANTIGRAVITY_AGENTAPI_EXE"))
    ):
        return AntigravityProvider.name

    if runner is not None:
        if runner.has_binary(ClaudeProvider.binary):
            return ClaudeProvider.name
        if runner.has_binary(CodexProvider.binary):
            return CodexProvider.name
        if runner.has_binary(AntigravityProvider.binary):
            return AntigravityProvider.name

    return DEFAULT_PROVIDER


def get_provider(
    name: str,
    runner: CommandRunner,
    allow_commands: bool = False,
    config: Config | None = None,
) -> Provider:
    """Build an adapter. `allow_commands` is ADR-0007's gate, closed by default."""
    try:
        provider = PROVIDERS[name]
    except KeyError as exc:
        known = ", ".join(sorted(PROVIDERS))
        raise ProviderError(f"unknown provider {name!r}; available: {known}") from exc
    return provider(runner, allow_commands=allow_commands, config=config)


__all__ = [
    "DEFAULT_PROVIDER",
    "PROVIDERS",
    "AntigravityProvider",
    "ClaudeProvider",
    "CliProvider",
    "CodexProvider",
    "Provider",
    "ProviderError",
    "ProviderOutput",
    "detect_default_provider",
    "get_provider",
]
