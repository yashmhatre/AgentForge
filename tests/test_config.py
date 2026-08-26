"""Project configuration is read once through one shared loader."""

import pytest

from agentforge.core.config import DEFAULT_TEST_SUITE, CapabilityTier, load_config


def test_missing_config_uses_the_documented_provider_capability_defaults(tmp_path):
    config = load_config(tmp_path)

    assert config.capability_for("claude") is CapabilityTier.NATIVE
    assert config.capability_for("codex") is CapabilityTier.FRAGMENT
    assert not (tmp_path / ".agentforge").exists(), "the loader is read-only"


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


def test_the_test_suite_gate_defaults_to_pytest_when_a_project_declares_none():
    """Every repository AgentForge ships plugins for is a Python one, so the
    default is a documented default rather than a pass over the tree."""
    assert load_config("/repo/pipelines").test_suite == DEFAULT_TEST_SUITE


def test_a_project_declares_the_suite_its_test_gate_runs(tmp_path):
    config_dir = tmp_path / ".agentforge"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "gates:\n  tests:\n    suite: npm test --silent\n", encoding="utf-8"
    )

    assert load_config(tmp_path).test_suite == ("npm", "test", "--silent")


def test_a_suite_with_a_space_in_a_path_is_declared_as_a_list(tmp_path):
    """The string form is split the way a shell would split it, which is wrong
    for exactly one case. The list form is the answer to it."""
    config_dir = tmp_path / ".agentforge"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        'gates:\n  tests:\n    suite: ["C:/Program Files/py.exe", "-m", "pytest"]\n',
        encoding="utf-8",
    )

    assert load_config(tmp_path).test_suite == ("C:/Program Files/py.exe", "-m", "pytest")


def test_a_declared_suite_that_names_nothing_is_refused_rather_than_defaulted(tmp_path):
    """Falling back to pytest here would run a suite the project did not ask for
    and report the result as theirs."""
    config_dir = tmp_path / ".agentforge"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        'gates:\n  tests:\n    suite: ""\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="gates.tests.suite"):
        load_config(tmp_path)
