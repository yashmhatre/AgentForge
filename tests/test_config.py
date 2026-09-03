"""Project configuration is read once through one shared loader."""

import pytest

from agentforge_framework.core.config import (
    DEFAULT_TEST_SUITE,
    CapabilityTier,
    load_config,
)
from agentforge_framework.core.contracts import Effort, ModelTier


def test_missing_config_uses_the_documented_provider_capability_defaults(tmp_path):
    config = load_config(tmp_path)

    assert config.capability_for("claude") is CapabilityTier.NATIVE
    assert config.capability_for("codex") is CapabilityTier.FRAGMENT
    assert not (tmp_path / ".agentforge").exists(), "the loader is read-only"


def _config_file(tmp_path, text: str):
    config_dir = tmp_path / ".agentforge"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.yaml").write_text(text, encoding="utf-8")
    return load_config(tmp_path)


def test_the_pack_inventory_is_not_published_unless_the_project_says_so():
    """ADR-0024. The safe direction is the default because the disclosure is
    the unrecoverable half: a comment on a public tracker is published."""
    assert load_config("/nowhere").publish_pack_inventory is False


def test_a_project_whose_tracker_matches_its_code_publishes_the_inventory(tmp_path):
    config = _config_file(tmp_path, "context:\n  publish_inventory: true\n")

    assert config.publish_pack_inventory is True


def test_a_project_can_say_no_as_explicitly_as_it_can_say_yes(tmp_path):
    config = _config_file(tmp_path, "context:\n  publish_inventory: false\n")

    assert config.publish_pack_inventory is False


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


# --- ADR-0004's override, finally read (#112) -------------------------------


def test_a_project_pins_the_model_a_tier_means(tmp_path):
    """The sentence ADR-0004 opened with in 2026-08 and nothing read until now:
    users override the mapping without touching Role definitions."""
    config = _config_file(
        tmp_path,
        "providers:\n  claude:\n    models:\n      deep: claude-opus-4-1\n",
    )

    assert config.model_for("claude", ModelTier.DEEP) == "claude-opus-4-1"


def test_an_unnamed_tier_keeps_the_adapters_own_model(tmp_path):
    """A project pins the one tier it disagrees about rather than restating all
    three, so a later adapter bump still reaches it on the other two."""
    config = _config_file(
        tmp_path,
        "providers:\n  claude:\n    models:\n      deep: claude-opus-4-1\n",
    )

    assert config.model_for("claude", ModelTier.CHEAP) is None
    assert config.model_for("codex", ModelTier.DEEP) is None


def test_naming_models_does_not_disturb_the_capability_tier(tmp_path):
    """Two unrelated keys under one provider. The capability default survived
    being read from a block that never mentions it."""
    config = _config_file(
        tmp_path,
        "providers:\n  claude:\n    models:\n      deep: claude-opus-4-1\n",
    )

    assert config.capability_for("claude") is CapabilityTier.NATIVE


def test_a_project_overrides_either_axis_of_a_role(tmp_path):
    config = _config_file(
        tmp_path,
        "roles:\n  security:\n    tier: deep\n    effort: max\n  tester:\n    effort: low\n",
    )

    assert config.role_tiers == {"security": ModelTier.DEEP}
    assert config.role_efforts == {"security": Effort.MAX, "tester": Effort.LOW}


def test_a_role_that_names_a_model_is_refused_rather_than_ignored(tmp_path):
    """The shape everybody reaches for, and the one ADR-0004 exists to stop. A
    model named per Role does not survive a release and does not port across
    Providers — and dropping the key quietly would leave a project believing it
    had pinned something, which reads as working until a Provider changes."""
    with pytest.raises(ValueError) as exc:
        _config_file(tmp_path, "roles:\n  security:\n    model: claude-opus-5\n")

    assert "roles.security.model" in str(exc.value)
    assert "ADR-0004" in str(exc.value), "the refusal names the decision it enforces"
