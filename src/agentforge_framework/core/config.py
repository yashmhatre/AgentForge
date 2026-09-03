"""Read project configuration without creating or changing it."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml

from .contracts import Effort, ModelTier


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

    #: Whether the Context Pack comment publishes the symbols and import graph
    #: it resolved, or only their counts. Off by default: the Issue already
    #: carries the pack's file paths in the frozen Plan, and nothing else on it
    #: carries private symbol names. A tracker whose audience matches the
    #: code's turns this on. See ADR-0024.
    publish_pack_inventory: bool = False

    #: Per-Provider overrides of the adapter's tier-to-model table, keyed by
    #: provider name and then by tier. ADR-0004 promised this in its first
    #: paragraph and nothing read it until 2026-09-03. A tier absent here keeps
    #: the adapter's default, so a project pins the one tier it disagrees about
    #: rather than restating all three.
    provider_models: dict[str, dict[ModelTier, str]] = field(default_factory=dict)

    #: Per-Role overrides of the two declared axes. The Role still never names a
    #: model — it names a tier, and the Provider maps that. `roles.x.model` is
    #: deliberately not a key: a model named per Role does not survive a release
    #: and does not port across Providers, which is the whole of ADR-0004.
    role_tiers: dict[str, ModelTier] = field(default_factory=dict)
    role_efforts: dict[str, Effort] = field(default_factory=dict)

    def capability_for(self, provider: str) -> CapabilityTier:
        return self.provider_capabilities.get(provider, CapabilityTier.FRAGMENT)

    def model_for(self, provider: str, tier: ModelTier) -> str | None:
        """The configured model for a tier, or None to keep the adapter's own."""
        return self.provider_models.get(provider, {}).get(tier)


def load_config(root: Path | str) -> Config:
    """Read ``.agentforge/config.yaml``, or return documented defaults."""
    path = Path(root) / ".agentforge" / "config.yaml"
    if not path.is_file():
        return Config()

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    capabilities = dict(DEFAULT_CAPABILITIES)
    models: dict[str, dict[ModelTier, str]] = {}
    for name, provider in (data.get("providers") or {}).items():
        if "capability_tier" in provider:
            capabilities[str(name)] = CapabilityTier(provider["capability_tier"])
        if provider.get("models"):
            models[str(name)] = {
                ModelTier(tier): str(slug) for tier, slug in provider["models"].items()
            }

    tiers, efforts = _role_overrides(data)
    return Config(
        provider_capabilities=capabilities,
        test_suite=_test_suite(data),
        publish_pack_inventory=bool(
            (data.get("context") or {}).get("publish_inventory", False)
        ),
        provider_models=models,
        role_tiers=tiers,
        role_efforts=efforts,
    )


def _role_overrides(data: dict) -> tuple[dict[str, ModelTier], dict[str, Effort]]:
    """`roles.<name>.tier` and `roles.<name>.effort`, the two declared axes.

    A `model` key here is refused rather than ignored. Silently dropping it
    would leave a project believing it had pinned a model per Role, which reads
    as working right up until a Provider changes — and the whole reason
    ADR-0004 gives a Role a tier is that the failure is otherwise invisible.
    """
    tiers: dict[str, ModelTier] = {}
    efforts: dict[str, Effort] = {}
    for name, role in (data.get("roles") or {}).items():
        if "model" in role:
            raise ValueError(
                f"`roles.{name}.model` names a model per Role, which ADR-0004 "
                f"does not allow; set `roles.{name}.tier` and override "
                f"`providers.<name>.models.<tier>` if the mapping is wrong"
            )
        if "tier" in role:
            tiers[str(name)] = ModelTier(role["tier"])
        if "effort" in role:
            efforts[str(name)] = Effort(role["effort"])
    return tiers, efforts


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
