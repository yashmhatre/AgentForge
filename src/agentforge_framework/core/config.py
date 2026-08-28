"""Read project configuration without creating or changing it."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml


class CapabilityTier(StrEnum):
    """How a Provider receives a Role's declared Vendored Skills."""

    NATIVE = "native"
    FRAGMENT = "fragment"


DEFAULT_CAPABILITIES = {
    "claude": CapabilityTier.NATIVE,
    "codex": CapabilityTier.FRAGMENT,
}

#: What a `tests` Gate runs when the project declares nothing. `pytest` rather
#: than `python -m pytest`, so that an environment without it is a binary the
#: Command Runner cannot find — which is the difference between a suite that
#: could not be run and one that ran and failed, and the Gate reports them
#: differently.
DEFAULT_TEST_SUITE = ("pytest",)


@dataclass(frozen=True)
class Config:
    """The read-only project configuration the Workflow runtime reads."""

    provider_capabilities: dict[str, CapabilityTier] = field(
        default_factory=lambda: dict(DEFAULT_CAPABILITIES)
    )
    #: The argument vector the test-suite Gate runs. An argument vector, not a
    #: Command in the glossary's sense: nothing infers anything from it.
    test_suite: tuple[str, ...] = DEFAULT_TEST_SUITE

    def capability_for(self, provider: str) -> CapabilityTier:
        return self.provider_capabilities.get(provider, CapabilityTier.FRAGMENT)


def load_config(root: Path | str) -> Config:
    """Read ``.agentforge/config.yaml``, or return documented defaults."""
    path = Path(root) / ".agentforge" / "config.yaml"
    if not path.is_file():
        return Config()

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    capabilities = dict(DEFAULT_CAPABILITIES)
    for name, provider in (data.get("providers") or {}).items():
        capabilities[str(name)] = CapabilityTier(provider["capability_tier"])
    return Config(provider_capabilities=capabilities, test_suite=_test_suite(data))


def _test_suite(data: dict) -> tuple[str, ...]:
    """`gates.tests.suite`: what the test-suite Gate runs in this repository.

    A string is split the way a shell would; a list is taken as written, which
    is how a path with a space in it gets named. Nothing here consults the tree
    — a project that runs `npm test` says so, rather than being guessed at.

    ADR-0007 defers a general execution allowlist to M5. This is the bounded
    case that ADR names as the appropriate thing to persist: one suite, declared
    by the project, run by AgentForge itself rather than by a Role.
    """
    value = ((data.get("gates") or {}).get("tests") or {}).get("suite")
    if value is None:
        return DEFAULT_TEST_SUITE

    if isinstance(value, str):
        parts = tuple(shlex.split(value))
    elif isinstance(value, list):
        parts = tuple(str(part) for part in value)
    else:
        raise TypeError(
            f"`gates.tests.suite` must be a string or a list, not {type(value).__name__}"
        )

    if not parts:
        raise ValueError("`gates.tests.suite` is empty; name the command that runs the suite")
    return parts


__all__ = ["DEFAULT_TEST_SUITE", "CapabilityTier", "Config", "load_config"]
