"""Provider interfaces and integrations.

Selecting a Provider is the only place a user names a coding-agent CLI.
Everything downstream of `get_provider` speaks in Model Tiers (ADR-0004).
"""

from __future__ import annotations

from ..core.process import CommandRunner
from .base import CliProvider, Provider, ProviderError, ProviderOutput
from .claude import ClaudeProvider
from .codex import CodexProvider

PROVIDERS: dict[str, type[CliProvider]] = {
    ClaudeProvider.name: ClaudeProvider,
    CodexProvider.name: CodexProvider,
}

DEFAULT_PROVIDER = ClaudeProvider.name


def get_provider(name: str, runner: CommandRunner, allow_commands: bool = False) -> Provider:
    """Build an adapter. `allow_commands` is ADR-0007's gate, closed by default."""
    try:
        provider = PROVIDERS[name]
    except KeyError as exc:
        known = ", ".join(sorted(PROVIDERS))
        raise ProviderError(f"unknown provider {name!r}; available: {known}") from exc
    return provider(runner, allow_commands=allow_commands)


__all__ = [
    "DEFAULT_PROVIDER",
    "PROVIDERS",
    "ClaudeProvider",
    "CliProvider",
    "CodexProvider",
    "Provider",
    "ProviderError",
    "ProviderOutput",
    "get_provider",
]
