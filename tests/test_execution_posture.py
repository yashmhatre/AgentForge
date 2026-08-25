"""ADR-0007: a Role runs no commands unless a human opens the gate.

The M1 acceptance run produced a Role that could edit files but not run them,
traced seven tests by hand, and reported `completed`. The code was correct,
which is the danger — nothing required the honesty that disclosed it.

These tests pin the argument vector each posture produces, because the posture
lives in the adapter and a permission expressed anywhere else is one the model
can talk itself out of.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentforge.core.contracts import ModelTier, Role
from agentforge.core.plan_format import render_result_block
from agentforge.core.runtime import Forge, RunFailed
from agentforge.providers import get_provider

from .fakes import FakeRunner, github_repository
from .test_runtime import BODY, ROOT, issue_json

CWD = Path("/repo")


def _invoke(provider_name: str, *, allow_commands: bool) -> list[str]:
    """The argv one Role invocation produces under one posture."""
    runner = FakeRunner()
    runner.script(
        provider_name,
        stdout=json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": render_result_block({"outcome": "completed", "summary": "done"}),
            }
        )
        if provider_name == "claude"
        else "done",
    )
    provider = get_provider(provider_name, runner, allow_commands=allow_commands)
    provider.invoke(
        role=Role(name="implementer", tier=ModelTier.STANDARD),
        prompt="do the thing",
        context=None,
        tier=ModelTier.STANDARD,
        cwd=CWD,
    )
    return list(runner.only(provider_name))


# --- the posture is in the argument vector -----------------------------------


def test_claude_denies_commands_by_default():
    argv = _invoke("claude", allow_commands=False)

    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_claude_opens_the_gate_when_asked():
    argv = _invoke("claude", allow_commands=True)

    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"


def test_codex_denies_commands_by_default():
    """`untrusted` auto-runs only reads and escalates the rest — this CLI's
    nearest analogue to the `claude` adapter's `acceptEdits`."""
    argv = _invoke("codex", allow_commands=False)

    assert argv[argv.index("--ask-for-approval") + 1] == "untrusted"


def test_codex_opens_the_gate_when_asked():
    argv = _invoke("codex", allow_commands=True)

    assert argv[argv.index("--ask-for-approval") + 1] == "never"


def test_codex_stays_sandboxed_in_both_postures():
    """ADR-0007 opens a gate; it does not remove the sandbox."""
    for allow in (True, False):
        argv = _invoke("codex", allow_commands=allow)
        assert argv[argv.index("--sandbox") + 1] == "workspace-write"
        assert "danger-full-access" not in argv
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_codex_options_precede_the_subcommand():
    """`codex [OPTIONS] <COMMAND>`. Options after `exec` are rejected by the CLI,
    which is how the previous `--full-auto` placement failed."""
    argv = _invoke("codex", allow_commands=False)
    exec_at = argv.index("exec")

    for flag in ("--model", "--sandbox", "--ask-for-approval"):
        assert argv.index(flag) < exec_at, f"{flag} must precede `exec`"
    assert argv[-1] != "exec", "the prompt is the subcommand's argument"


def test_codex_no_longer_passes_a_flag_the_cli_does_not_have():
    """`--full-auto` does not exist in the current CLI, in any position."""
    for allow in (True, False):
        assert "--full-auto" not in _invoke("codex", allow_commands=allow)


def test_every_adapter_denies_by_default():
    """The posture is a Provider-level contract, not a per-adapter opinion."""
    for name in ("claude", "codex"):
        assert get_provider(name, FakeRunner(), allow_commands=False).allow_commands is False


def test_the_default_is_deny_when_nothing_is_said():
    assert get_provider("claude", FakeRunner()).allow_commands is False


# --- the flag reaches the adapter through a Run ------------------------------


def _runner() -> FakeRunner:
    runner = github_repository(FakeRunner(), ROOT)
    runner.script("gh", "issue", "view", stdout=issue_json(BODY))
    runner.script("gh", "pr", "create", stdout="https://github.com/acme/pipelines/pull/13\n")
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script(
        "claude",
        stdout=json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": render_result_block(
                    {"outcome": "completed", "summary": "done", "files_changed": ["src/loader.py"]}
                ),
            }
        ),
    )
    return runner


def test_a_run_denies_commands_unless_asked():
    runner = _runner()

    Forge(cwd=ROOT, provider="claude", runner=runner).implement(12)

    assert runner.argument_after("--permission-mode", "claude") == "acceptEdits"


def test_a_run_opens_the_gate_when_the_human_asks():
    runner = _runner()

    Forge(cwd=ROOT, provider="claude", runner=runner).implement(12, allow_commands=True)

    calls = runner.matching("claude")
    assert calls
    assert all(call[call.index("--permission-mode") + 1] == "bypassPermissions" for call in calls)


def test_opening_the_gate_is_refused_on_a_dirty_tree():
    """ADR-0007: refuses unless the working tree is clean and a branch exists.

    A dirty tree already blocks every Run; this pins that opening the gate does
    not become a way around it.
    """
    runner = _runner()
    runner.script("git", "status", "--porcelain", stdout=" M src/loader.py\n")

    with pytest.raises(RunFailed, match="uncommitted"):
        Forge(cwd=ROOT, provider="claude", runner=runner).implement(12, allow_commands=True)

    assert not runner.ran("claude")


def test_the_agent_runs_on_a_branch_before_the_gate_is_open():
    """The blast radius is bounded by the branch the runtime creates first."""
    runner = _runner()

    Forge(cwd=ROOT, provider="claude", runner=runner).implement(12, allow_commands=True)

    branch_at = next(
        i for i, call in enumerate(runner.calls) if tuple(call[:3]) == ("git", "checkout", "-b")
    )
    agent_at = next(i for i, call in enumerate(runner.calls) if call[0] == "claude")
    assert branch_at < agent_at


# --- what a denied Role is told ----------------------------------------------


def test_a_denied_role_is_told_to_report_rather_than_substitute_inspection():
    """The posture is not prompt text; what to do when denied is."""
    runner = _runner()

    Forge(cwd=ROOT, provider="claude", runner=runner).implement(12)

    prompt = runner.only("claude")[2]
    assert "cannot run commands" in prompt.lower()
    assert "escalate" in prompt.lower()


def test_a_permitted_role_is_not_told_it_is_denied():
    runner = _runner()

    Forge(cwd=ROOT, provider="claude", runner=runner).implement(12, allow_commands=True)

    assert all("cannot run commands" not in call[2].lower() for call in runner.matching("claude"))
