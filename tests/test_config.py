"""Project configuration is read once through the shared M3 loader."""

from agentforge.core.config import CapabilityTier, load_config


def test_missing_config_uses_the_documented_provider_capability_defaults(tmp_path):
    config = load_config(tmp_path)

    assert config.capability_for("claude") is CapabilityTier.NATIVE
    assert config.capability_for("codex") is CapabilityTier.FRAGMENT
    assert not (tmp_path / ".agentforge").exists(), "the M3 loader is read-only"


def test_capability_tiers_are_read_from_the_shared_config_file(tmp_path):
    config_dir = tmp_path / ".agentforge"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "providers:\n"
        "  claude:\n"
        "    capability_tier: fragment\n"
        "  local-cli:\n"
        "    capability_tier: native\n",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.capability_for("claude") is CapabilityTier.FRAGMENT
    assert config.capability_for("local-cli") is CapabilityTier.NATIVE
