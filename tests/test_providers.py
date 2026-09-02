"""Provider adapters, tested at the two places they touch the outside world.

An adapter's whole observable behavior is the argument vector it builds and the
`AgentResult` it recovers from what the CLI printed. Both are pinned here
against fixtures recorded from real CLI output, so a version bump breaks one
adapter's test rather than surfacing later as a confusing Run.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import ClassVar

import pytest

from agentforge_framework.agents.implementer import IMPLEMENTER
from agentforge_framework.core.config import CapabilityTier, Config, load_config
from agentforge_framework.core.contracts import ContextPack, ModelTier, Outcome, Role
from agentforge_framework.core.process import CommandResult
from agentforge_framework.core.skills import fragment_only, read_skill
from agentforge_framework.providers import PROVIDERS, get_provider
from agentforge_framework.providers.base import Provider, ProviderError
from agentforge_framework.providers.claude import ClaudeProvider
from agentforge_framework.providers.codex import CodexProvider

from .fakes import FakeRunner

FIXTURES = Path(__file__).parent / "fixtures"


def recorded(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def invoke(provider, tier=ModelTier.STANDARD, cwd=Path("/repo")):
    return provider.invoke(
        role=IMPLEMENTER,
        prompt="do the thing",
        context=ContextPack(),
        tier=tier,
        cwd=cwd,
    )


# --- argument construction -------------------------------------------------


def test_claude_runs_headlessly_with_the_prompt_it_was_given():
    runner = FakeRunner().script("claude", stdout=recorded("claude_completed.json"))

    invoke(ClaudeProvider(runner))

    call = runner.only("claude")
    assert call[1] == "-p"
    assert runner.prompt_to("claude") == "do the thing"
    assert "--output-format" in call and call[call.index("--output-format") + 1] == "json"


def test_claude_delivers_a_declared_skill_natively_without_inlining_it():
    runner = FakeRunner().script("claude", stdout=recorded("claude_completed.json"))
    provider = ClaudeProvider(
        runner,
        config=Config(provider_capabilities={"claude": CapabilityTier.NATIVE}),
    )
    role = Role("implementer", ModelTier.STANDARD, skills=("grilling",))

    provider.invoke(
        role=role,
        prompt="do the thing",
        context=ContextPack(),
        tier=role.tier,
        cwd=Path("/repo"),
    )

    call = runner.only("claude")
    prompt = runner.prompt_to("claude")
    assert "--plugin-dir" in call
    assert "/agentforge:grilling" in prompt
    assert "Grill the user" not in prompt


def test_a_non_native_provider_appends_the_same_skill_as_a_fragment():
    runner = FakeRunner().script("codex", stdout=recorded("codex_completed.txt"))
    provider = CodexProvider(
        runner,
        config=Config(provider_capabilities={"codex": CapabilityTier.FRAGMENT}),
    )
    role = Role("implementer", ModelTier.STANDARD, skills=("grilling",))

    provider.invoke(
        role=role,
        prompt="do the thing",
        context=ContextPack(),
        tier=role.tier,
        cwd=Path("/repo"),
    )

    prompt = runner.prompt_to("codex")
    assert "## Skill: grilling" in prompt
    assert read_skill("grilling").rstrip() in prompt
    assert "--plugin-dir" not in runner.only("codex")


def test_a_composite_skill_expands_into_its_parts_for_a_fragment_provider():
    """A composite says "run these two". A Provider with no Skill mechanism
    cannot, so the parts travel with it — otherwise the Role is handed an
    instruction pointing at nothing and the method never arrives."""
    runner = FakeRunner().script("codex", stdout=recorded("codex_completed.txt"))
    provider = CodexProvider(
        runner,
        config=Config(provider_capabilities={"codex": CapabilityTier.FRAGMENT}),
    )
    role = Role("orchestrator", ModelTier.DEEP, skills=("grill-with-docs",))

    provider.invoke(
        role=role,
        prompt="do the thing",
        context=ContextPack(),
        tier=role.tier,
        cwd=Path("/repo"),
    )

    prompt = runner.prompt_to("codex")
    assert "## Skill: grill-with-docs" in prompt
    assert "## Skill: grilling" in prompt
    assert "## Skill: domain-modeling" in prompt
    assert prompt.index("grill-with-docs") < prompt.index("## Skill: grilling"), (
        "the job the parts are doing together comes before the parts"
    )


def test_a_composite_is_named_once_to_a_native_provider():
    """Natively the CLI's own Skill mechanism fans it out, which is what that
    mechanism is for. Naming the parts as well would deliver them twice."""
    runner = FakeRunner().script("claude", stdout=recorded("claude_completed.json"))
    provider = ClaudeProvider(
        runner,
        config=Config(provider_capabilities={"claude": CapabilityTier.NATIVE}),
    )
    role = Role("orchestrator", ModelTier.DEEP, skills=("grill-with-docs",))

    provider.invoke(
        role=role,
        prompt="do the thing",
        context=ContextPack(),
        tier=role.tier,
        cwd=Path("/repo"),
    )

    prompt = runner.prompt_to("claude")
    assert "/agentforge:grill-with-docs" in prompt
    assert "/agentforge:grilling" not in prompt


def native_claude(runner):
    return ClaudeProvider(
        runner,
        config=Config(provider_capabilities={"claude": CapabilityTier.NATIVE}),
    )


@pytest.mark.parametrize("name", sorted(fragment_only()))
def test_a_skill_that_forbids_model_invocation_is_a_fragment_at_the_native_tier(name):
    """The Skill tool refuses these, correctly — so declaring one natively
    escalates the Role instead of running it, and the higher the Capability Tier
    the more certainly the pipeline fails.

    Parametrized from the bundle rather than from a list written here: a refresh
    that marks a third skill has to break this test, not a live Run.
    """
    runner = FakeRunner().script("claude", stdout=recorded("claude_completed.json"))
    role = Role("orchestrator", ModelTier.DEEP, skills=(name,))

    native_claude(runner).invoke(
        role=role,
        prompt="do the thing",
        context=ContextPack(),
        tier=role.tier,
        cwd=Path("/repo"),
    )

    prompt = runner.prompt_to("claude")
    assert f"/agentforge:{name}" not in prompt
    assert f"## Skill: {name}" in prompt
    assert read_skill(name).rstrip() in prompt
    assert "--plugin-dir" not in runner.only("claude"), (
        "nothing is offered natively, so the plugin directory has nothing to serve"
    )


def test_the_bundle_carries_at_least_one_skill_that_forbids_model_invocation():
    """Guards the parametrization above: an empty set would make it vacuous."""
    assert fragment_only()


def test_a_role_declaring_both_kinds_gets_both_deliveries():
    """The two are not exclusive. `to-spec` has no native form to fall back to
    and `grilling` gains nothing from being inlined, so each travels its own way
    in one prompt."""
    runner = FakeRunner().script("claude", stdout=recorded("claude_completed.json"))
    role = Role("orchestrator", ModelTier.DEEP, skills=("grilling", "to-spec"))

    native_claude(runner).invoke(
        role=role,
        prompt="do the thing",
        context=ContextPack(),
        tier=role.tier,
        cwd=Path("/repo"),
    )

    prompt = runner.prompt_to("claude")
    assert "/agentforge:grilling" in prompt
    assert "/agentforge:to-spec" not in prompt
    assert "## Skill: to-spec" in prompt
    assert "## Skill: grilling" not in prompt
    assert "--plugin-dir" in runner.only("claude")


# --- the prompt travels on stdin (#100) ------------------------------------

#: What `CreateProcess` allows a whole Windows command line to be. Linux allows
#: roughly 2MB, which is why every `ubuntu-latest` job passed while `decompose`
#: died on the author's own machine.
WINDOWS_COMMAND_LINE_CAP = 32767

RECORDED = {"claude": "claude_completed.json", "codex": "codex_completed.txt"}


def a_provider_of(name, runner):
    return get_provider(name, runner)


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_the_prompt_reaches_the_cli_on_stdin_and_never_in_argv(name):
    runner = FakeRunner().script(name, stdout=recorded(RECORDED[name]))
    role = Role("implementer", ModelTier.STANDARD)

    a_provider_of(name, runner).invoke(
        role=role,
        prompt="a distinctive instruction",
        context=ContextPack(),
        tier=role.tier,
        cwd=Path("/repo"),
    )

    assert runner.prompt_to(name) == "a distinctive instruction"
    assert not any("a distinctive instruction" in part for part in runner.only(name))


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_a_prompt_past_the_windows_cap_still_leaves_a_short_command_line(name):
    """The regression. A Spec of any real size pushed the `SLICES` prompt over
    32,767 characters and `decompose` died as `[WinError 206]`; the prompt is
    now the one thing in an invocation that cannot grow the command line."""
    huge = "x" * (WINDOWS_COMMAND_LINE_CAP * 2)
    runner = FakeRunner().script(name, stdout=recorded(RECORDED[name]))
    role = Role("implementer", ModelTier.STANDARD)

    a_provider_of(name, runner).invoke(
        role=role,
        prompt=huge,
        context=ContextPack(),
        tier=role.tier,
        cwd=Path("/repo"),
    )

    argv = runner.only(name)
    command_line = sum(len(part) + 1 for part in argv)
    assert command_line < WINDOWS_COMMAND_LINE_CAP, (
        f"{name} builds a {command_line}-character command line, which Windows refuses"
    )
    assert runner.prompt_to(name) == huge


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_an_adapter_cannot_be_handed_the_prompt_to_put_in_argv(name):
    """`build_argv` does not take one. A later change that wants the prompt back
    in the argument vector has to alter the port to get it, rather than quietly
    appending it to a tuple."""
    provider = a_provider_of(name, FakeRunner())

    assert "prompt" not in inspect.signature(provider.build_argv).parameters


def test_an_unknown_declared_skill_fails_before_the_provider_is_invoked():
    runner = FakeRunner().script("claude", stdout=recorded("claude_completed.json"))
    role = Role("implementer", ModelTier.STANDARD, skills=("no-such-skill",))

    with pytest.raises(LookupError, match="available"):
        ClaudeProvider(runner).invoke(
            role=role,
            prompt="do the thing",
            context=ContextPack(),
            tier=role.tier,
            cwd=Path("/repo"),
        )

    assert not runner.ran("claude")


def test_provider_selection_uses_the_capability_tier_from_the_shared_loader(tmp_path):
    config_dir = tmp_path / ".agentforge"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "providers:\n  claude:\n    capability_tier: fragment\n",
        encoding="utf-8",
    )
    runner = FakeRunner().script("claude", stdout=recorded("claude_completed.json"))
    provider = get_provider("claude", runner, config=load_config(tmp_path))
    role = Role("implementer", ModelTier.STANDARD, skills=("grilling",))

    provider.invoke(
        role=role,
        prompt="do the thing",
        context=ContextPack(),
        tier=role.tier,
        cwd=tmp_path,
    )

    prompt = runner.prompt_to("claude")
    assert read_skill("grilling").rstrip() in prompt
    assert "--plugin-dir" not in runner.only("claude")


@pytest.mark.parametrize(
    "tier,model",
    [(ModelTier.DEEP, "opus"), (ModelTier.STANDARD, "sonnet"), (ModelTier.CHEAP, "haiku")],
)
def test_a_tier_becomes_a_model_only_inside_the_adapter(tier, model):
    """ADR-0004: nothing above this line knows a model identifier."""
    runner = FakeRunner().script("claude", stdout=recorded("claude_completed.json"))

    invoke(ClaudeProvider(runner), tier=tier)

    assert runner.argument_after("--model", "claude") == model


def test_the_agent_runs_in_the_repository_it_is_editing():
    runner = FakeRunner().script("claude", stdout=recorded("claude_completed.json"))

    invoke(ClaudeProvider(runner), cwd=Path("/repo/pipelines"))

    assert runner.cwds[-1] == str(Path("/repo/pipelines"))


# --- result parsing --------------------------------------------------------


def test_a_completed_run_becomes_a_result_the_runtime_can_act_on():
    runner = FakeRunner().script("claude", stdout=recorded("claude_completed.json"))

    result = invoke(ClaudeProvider(runner))

    assert result.outcome is Outcome.COMPLETED
    assert result.role == "implementer"
    assert result.tier is ModelTier.STANDARD
    assert result.files_changed == ("src/loader.py", "tests/test_loader.py")
    assert "bounded retry" in result.summary


def test_an_escalation_is_a_result_and_not_an_exception():
    """ADR-0003 needs the runtime to tell a refusal apart from a crash."""
    runner = FakeRunner().script("claude", stdout=recorded("claude_escalated.json"))

    result = invoke(ClaudeProvider(runner))

    assert result.escalated
    assert "src/loader.py" in result.summary
    assert result.files_changed == ()


def test_a_cli_that_reports_its_own_error_is_a_failure_not_a_silent_pass():
    runner = FakeRunner().script("claude", stdout=recorded("claude_cli_error.json"))

    result = invoke(ClaudeProvider(runner))

    assert result.outcome is Outcome.FAILED
    assert "Credit balance" in result.summary


def test_a_run_that_reports_nothing_fails_rather_than_claiming_success():
    """Otherwise a Run opens a pull request containing no changes and says it
    worked."""
    runner = FakeRunner().script(
        "claude", stdout='{"type": "result", "is_error": false, "result": "Sure, I had a look."}'
    )

    result = invoke(ClaudeProvider(runner))

    assert result.outcome is Outcome.FAILED
    assert "result block" in result.summary


def test_an_empty_stdout_names_the_exit_status():
    runner = FakeRunner().script("claude", stdout="", stderr="killed", returncode=137)

    result = invoke(ClaudeProvider(runner))

    assert result.outcome is Outcome.FAILED
    assert "killed" in result.summary


def test_bare_text_output_still_yields_a_result():
    """`--output-format text`, or an older CLI. The result block travels in the
    text either way, so this degrades rather than failing."""
    runner = FakeRunner().script("claude", stdout=recorded("codex_completed.txt"))

    result = invoke(ClaudeProvider(runner))

    assert result.outcome is Outcome.COMPLETED


def test_a_streamed_envelope_is_read_from_its_terminal_record():
    runner = FakeRunner().script(
        "claude",
        stdout='[{"type": "assistant", "text": "working"}, '
        + recorded("claude_completed.json").replace("\n", " ")
        + "]",
    )

    assert invoke(ClaudeProvider(runner)).outcome is Outcome.COMPLETED


# --- the port is not Claude-shaped ----------------------------------------


def test_a_second_adapter_satisfies_the_same_port():
    """ADR-0001 claims Providers are interchangeable. This is the check."""
    runner = FakeRunner().script("codex", stdout=recorded("codex_completed.txt"))

    result = invoke(CodexProvider(runner))

    assert result.outcome is Outcome.COMPLETED
    assert result.files_changed == ("src/loader.py",)
    # `exec` rather than argv[1]: this CLI takes its options before the
    # subcommand, and where in the vector it lands is pinned by
    # test_execution_posture.test_codex_options_precede_the_subcommand.
    assert "exec" in runner.only("codex")


def test_the_second_adapter_reads_failure_from_an_exit_code_rather_than_an_envelope():
    runner = FakeRunner().script("codex", stdout="", stderr="model not found", returncode=1)

    result = invoke(CodexProvider(runner))

    assert result.outcome is Outcome.FAILED
    assert "model not found" in result.summary


def test_every_adapter_maps_all_three_tiers():
    for provider in PROVIDERS.values():
        assert set(provider.models) == set(ModelTier), provider.name


def test_every_adapter_implements_the_port():
    for provider in PROVIDERS.values():
        assert issubclass(provider, Provider)


# --- preconditions ---------------------------------------------------------


def test_a_missing_cli_is_named_so_a_user_can_install_it():
    runner = FakeRunner().uninstall("claude")

    with pytest.raises(ProviderError, match="claude"):
        ClaudeProvider(runner).preflight()


def test_an_unknown_provider_lists_the_ones_that_exist():
    with pytest.raises(ProviderError, match="claude"):
        get_provider("aider", FakeRunner())


def test_an_adapter_with_no_model_for_a_tier_says_so():
    class OneTrick(ClaudeProvider):
        models: ClassVar[dict[ModelTier, str]] = {ModelTier.STANDARD: "sonnet"}

    with pytest.raises(ProviderError, match="deep"):
        OneTrick(FakeRunner()).model_for(ModelTier.DEEP)


def test_a_timeout_is_reported_rather_than_hanging_the_run():
    result = CommandResult(argv=("claude",), returncode=124, stderr="timed out after 1800.0s")

    output = ClaudeProvider(FakeRunner()).parse_output(result)

    assert output.error and "timed out" in output.error


# --- what an invocation consumed -------------------------------------------


def test_the_claude_adapter_recovers_dollars_and_a_token_split():
    """The envelope this adapter already parses for the result text carries both.
    Throwing them away is what made "token efficient" an adjective."""
    runner = FakeRunner().script("claude", stdout=recorded("claude_completed.json"))

    result = invoke(ClaudeProvider(runner))

    assert result.usage.provider == "claude"
    assert result.usage.cost_usd == pytest.approx(0.4312)
    assert result.usage.input_tokens == 18422
    assert result.usage.output_tokens == 2317


def test_the_codex_adapter_recovers_tokens_and_claims_no_price():
    """The asymmetry ADR-0009 exists for: this CLI counts tokens and sets no
    price, and inventing one from a rate card would be a number nobody charged."""
    runner = FakeRunner().script("codex", stdout=recorded("codex_completed.txt"))

    result = CodexProvider(runner).invoke(
        role=IMPLEMENTER,
        prompt="do the thing",
        context=ContextPack(),
        tier=ModelTier.STANDARD,
        cwd=Path("/repo"),
    )

    assert result.usage.provider == "codex"
    assert result.usage.total_tokens == 21044
    assert result.usage.cost_usd is None


def test_a_provider_that_reports_nothing_leaves_the_usage_absent_rather_than_zero():
    """A zero is a claim that the invocation was free. Absent is the truth, and
    it is what lets a Run's total say how much of itself is missing."""
    runner = FakeRunner().script("claude", stdout=recorded("claude_no_usage.json"))

    assert invoke(ClaudeProvider(runner)).usage is None


def test_a_failed_invocation_still_reports_what_it_spent():
    """A Run that spent real money discovering its CLI was misconfigured spent
    it. This envelope reports a true zero, which is not the same as silence."""
    runner = FakeRunner().script("claude", stdout=recorded("claude_cli_error.json"))

    result = invoke(ClaudeProvider(runner))

    assert result.outcome is Outcome.FAILED
    assert result.usage.cost_usd == 0.0
    assert result.usage.reported


def test_a_transcript_with_no_token_line_reports_nothing():
    runner = FakeRunner().script("codex", stdout="Done, and no counts were printed.")

    result = CodexProvider(runner).invoke(
        role=IMPLEMENTER,
        prompt="do the thing",
        context=ContextPack(),
        tier=ModelTier.STANDARD,
        cwd=Path("/repo"),
    )

    assert result.usage is None
