"""Read project configuration without creating or changing it."""

from __future__ import annotations

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


@dataclass(frozen=True)
class Config:
    """The read-only project configuration used by M3 call sites."""

    provider_capabilities: dict[str, CapabilityTier] = field(
        default_factory=lambda: dict(DEFAULT_CAPABILITIES)
    )

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
    return Config(provider_capabilities=capabilities)


__all__ = ["CapabilityTier", "Config", "load_config"]
