"""The Plugin seam: what activates, what it contributes, and where it lands.

Two levels, on purpose. The unit tests below hold the registry to its three
promises — deterministic, bounded, survivable — against Plugins written here.
The Run-level tests hold the whole path honest by reading the prompt back out of
the argument vector the Provider was handed, because a Fragment that reaches a
`ContextPack` and not a prompt has contributed nothing.

Offline like everything else: the `python` Plugin declares no root markers, so
nothing here touches a filesystem that is not a `tmp_path`.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentforge_framework.context.prompt import render_context_block
from agentforge_framework.core.contracts import (
    ContextPack,
    Fragment,
    Plan,
    PlanStep,
    Plugin,
)
from agentforge_framework.core.issues import render_context_comment
from agentforge_framework.core.registry import (
    MAX_FRAGMENT_CHARS,
    MAX_FRAGMENTS_PER_ROLE,
    Activation,
    activate,
    contributions,
    fragments_for,
)
from agentforge_framework.core.runtime import Forge, RunStatus
from agentforge_framework.plugins import BUILT_IN
from agentforge_framework.plugins.python import PYTHON

from .fakes import FakeRunner
from .test_runtime import ROOT, a_runner, agent_says, comments_on, forge

# --- the contract ----------------------------------------------------------


def plan_touching(*files: str) -> Plan:
    return Plan(summary="x", steps=(PlanStep(id="s1", intent="do it", files=files),))


def test_a_plugin_carrying_only_fragments_is_legal():
    """Every contribution field optional is the whole point of the seam: #57 and
    #58 add Extractors and validators without every Plugin growing them."""
    plugin = Plugin(name="minimal", fragments=(Fragment(text="be careful"),))

    assert plugin.suffixes == ()
    assert plugin.root_markers == ()
    assert plugin.fragments[0].roles == ()


def test_a_plugin_is_frozen():
    with pytest.raises(FrozenInstanceError):
        PYTHON.name = "something else"


# --- activation ------------------------------------------------------------


def test_a_plugin_answers_for_a_suffix_in_the_blast_radius(tmp_path):
    active = activate(plan_touching("src/loader.py"), tmp_path)

    assert [p.name for p in active.plugins] == ["python"]


def test_a_plugin_answers_for_a_root_marker_whatever_the_plan_touches(tmp_path):
    """The repository is a fact about the work even when one Plan does not
    happen to touch it — a `databricks.yml` says Databricks regardless."""
    (tmp_path / "databricks.yml").write_text("bundle: {}", encoding="utf-8")
    marked = Plugin(name="databricks", root_markers=("databricks.yml",))

    active = activate(plan_touching("notes.md"), tmp_path, plugins=(marked,))

    assert [p.name for p in active.plugins] == ["databricks"]


def test_the_python_plugin_is_not_active_where_python_is_not_in_the_blast_radius(tmp_path):
    """It declares no root markers on purpose. A repository with a
    `pyproject.toml` and a SQL-only Plan is not doing Python work."""
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")

    active = activate(plan_touching("models/orders.sql"), tmp_path)

    assert active.plugins == ()
    assert not active


def test_activation_is_deterministic_and_in_registration_order(tmp_path):
    first = Plugin(name="a", suffixes=(".py",))
    second = Plugin(name="b", suffixes=(".py",))
    plan = plan_touching("src/loader.py")

    for _ in range(5):
        active = activate(plan, tmp_path, plugins=(first, second))
        assert [p.name for p in active.plugins] == ["a", "b"]


def test_a_plugin_that_raises_is_skipped_and_named_and_the_run_carries_on(tmp_path):
    """Domain knowledge is a nice-to-have. A Run that died because one
    convention list was malformed would be worse than a Run without it."""

    class Exploding:
        """Duck-typed rather than a `Plugin` subclass: the shipped one is frozen,
        and a Plugin that raises in the field is a third-party one that need not
        have been built out of this dataclass at all."""

        name = "broken"
        root_markers = ()
        fragments = ()

        @property
        def suffixes(self):
            raise ValueError("bad suffix table")

    active = activate(plan_touching("src/loader.py"), tmp_path, plugins=(Exploding(), PYTHON))

    assert [p.name for p in active.plugins] == ["python"]
    assert len(active.skipped) == 1
    assert "broken" in active.skipped[0] and "bad suffix table" in active.skipped[0]


# --- what reaches which Role ----------------------------------------------


def test_a_fragment_reaches_the_roles_it_names_and_no_others():
    active = Activation(plugins=(PYTHON,))

    assert fragments_for(active, "implementer")
    assert fragments_for(active, "tester")
    assert fragments_for(active, "reviewer")
    assert fragments_for(active, "security") == ()
    assert fragments_for(active, "architect") == ()


def test_a_fragment_naming_no_role_reaches_every_role():
    active = Activation(plugins=(Plugin(name="all", fragments=(Fragment(text="rule"),)),))

    assert fragments_for(active, "security")
    assert fragments_for(active, "implementer")


def test_one_plugin_cannot_dominate_a_prompt_the_context_pack_exists_to_shrink():
    huge = Plugin(name="huge", fragments=(Fragment(text="x" * (MAX_FRAGMENT_CHARS * 5)),))

    (delivered,) = fragments_for(Activation(plugins=(huge,)), "implementer")

    assert len(delivered) <= MAX_FRAGMENT_CHARS + len("**huge**\n")


def test_no_role_carries_more_fragments_than_the_cap():
    many = tuple(
        Plugin(name=f"p{i}", fragments=(Fragment(text=f"rule {i}"),))
        for i in range(MAX_FRAGMENTS_PER_ROLE + 3)
    )

    assert len(fragments_for(Activation(plugins=many), "implementer")) == MAX_FRAGMENTS_PER_ROLE


def test_the_prompt_block_keeps_the_orchestrators_conventions_apart_from_a_plugins():
    """Different authors, different headings. A Role reading one run-on list
    cannot tell which half the Plan actually asked for."""
    pack = ContextPack(
        files=("src/loader.py",),
        conventions=("Do not change the public signature of `load`.",),
        fragments=("**python**\nAnnotate new public functions.",),
    )

    block = render_context_block(pack)

    assert "**Follow these conventions:** Do not change the public signature" in block
    assert "This repository's technology is held to these conventions:" in block
    assert block.index("Follow these conventions") < block.index("held to these conventions")


def test_fragments_never_travel_in_an_issue_body():
    """ADR-0011: the Issue body is the stable surface. Plugins are resolved
    against the repository the Run is in, so freezing one machine's answer into
    the Issue would hand the next Run conventions it may not be held to."""
    pack = ContextPack(files=("a.py",), fragments=("**python**\nrule",))

    assert "fragments" not in pack.to_dict()
    assert ContextPack.from_dict(pack.to_dict()).fragments == ()


# --- through a whole Run ---------------------------------------------------


def prompts_from(runner: FakeRunner) -> list[str]:
    """What each Agent was actually handed, read off the argument vector."""
    return [call[call.index("-p") + 1] for call in runner.matching("claude")]


def a_python_run() -> FakeRunner:
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))
    return runner


def test_a_fragment_reaches_the_prompt_of_the_roles_it_is_keyed_to():
    runner = a_python_run()

    forge(runner).implement(12, allow_commands=True)

    implementer, tester, security, reviewer = prompts_from(runner)
    assert "Type-annotate new public functions" in implementer
    assert "Type-annotate new public functions" in tester
    assert "Type-annotate new public functions" in reviewer
    assert "Type-annotate new public functions" not in security


def test_a_repository_matching_no_plugin_produces_the_prompts_it_produces_today():
    with_plugins = a_python_run()
    forge(with_plugins).implement(12, allow_commands=True)

    without = a_python_run()
    forge(without).implement(12, allow_commands=True, use_plugins=False)

    assert prompts_from(with_plugins) != prompts_from(without)
    # The Security Role is keyed to no Fragment, so its prompt is the control
    # for every other difference between the two Runs.
    assert prompts_from(with_plugins)[2] == prompts_from(without)[2]


def test_no_plugins_keeps_the_pack_and_drops_only_the_fragments():
    runner = a_python_run()

    forge(runner).implement(12, allow_commands=True, use_plugins=False)

    implementer = prompts_from(runner)[0]
    assert "## Context Pack" in implementer, "the pack itself is still resolved"
    assert "Type-annotate new public functions" not in implementer


def test_no_context_pack_is_the_combined_control():
    """Fragments ride in the pack, so the pack's control suppresses both. That
    is why #61 needs the separate switch above."""
    runner = a_python_run()

    forge(runner).implement(12, allow_commands=True, resolve_context=False)

    implementer = prompts_from(runner)[0]
    assert "## Context Pack" not in implementer
    assert "Type-annotate new public functions" not in implementer


def test_a_fragment_reaches_both_capability_tiers_the_same_way():
    """ADR-0005 says a skill is inlined where the Provider offers no native
    equivalent. A Plugin's conventions have no native equivalent at any tier —
    there is no `/agentforge:` command that means "Unity Catalog naming" — so
    the existing rule yields one answer, always inline, and ADR-0016 does not
    add a second rule to reach it.

    `codex` is the Fragment-tier Provider and `claude` the native one, and the
    same convention has to land in both.
    """
    runner = a_python_run()
    Forge(cwd=ROOT, provider="codex", runner=runner).implement(12, allow_commands=True)

    prompts = [call[call.index("exec") + 1] for call in runner.matching("codex")]
    assert "Type-annotate new public functions" in prompts[0]


def test_the_context_pack_comment_names_the_active_plugins_and_what_each_gave():
    runner = a_python_run()

    forge(runner).implement(12, allow_commands=True)

    (pack_comment,) = [c for c in comments_on(runner) if "### Context Pack" in c]
    assert "**Plugins active (1):**" in pack_comment
    assert "`python`" in pack_comment
    assert "implementer, tester, reviewer" in pack_comment


def test_the_control_run_says_it_carried_no_fragments_either():
    assert "Plugin Fragments ride in the pack" in render_context_comment(ContextPack())


def test_a_skipped_plugin_is_named_in_the_run_log():
    comment = render_context_comment(
        ContextPack(files=("a.py",)), skipped=("broken (ValueError: bad table)",)
    )

    assert "**Plugins skipped:**" in comment
    assert "broken (ValueError: bad table)" in comment


def test_a_run_with_plugins_still_reaches_sign_off():
    """The seam is additive. Nothing about activating a Plugin changes how a Run
    ends, and this is the test that fails first if that stops being true."""
    runner = a_python_run()

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF


def test_the_shipped_registry_is_ordered_and_holds_the_python_plugin():
    assert isinstance(BUILT_IN, tuple)
    assert PYTHON in BUILT_IN


def test_contributions_reads_as_a_line_a_human_can_act_on():
    assert contributions(Activation(plugins=(PYTHON,))) == (
        ("python", "1 Fragment(s) for implementer, tester, reviewer"),
    )
