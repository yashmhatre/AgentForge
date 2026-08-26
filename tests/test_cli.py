"""The commands as a data engineer types them.

These cover the surface `agentforge --help` promises: exit codes, the tier
override syntax, and what gets printed when a Run stops rather than finishes.
`main` takes the Command Runner as an argument, so the whole CLI runs offline
without a single patch.
"""

from __future__ import annotations

import json

import pytest

from agentforge import cli
from agentforge.core.contracts import ModelTier
from agentforge.core.plan_format import render_result_block

from .fakes import FakeRunner
from .test_agents import orchestrator_output
from .test_runtime import HUMAN_GATED, ROOT, _workflow, a_runner, agent_says


@pytest.fixture
def runner() -> FakeRunner:
    return a_runner()


def run(argv, runner) -> int:
    return cli.main([*argv, "-C", str(ROOT)] if argv[0] in {"plan", "implement"} else argv, runner)


def test_no_command_prints_help_rather_than_failing_obscurely(capsys):
    assert cli.main([]) == 2
    assert "usage: agentforge" in capsys.readouterr().out


def test_plan_reports_the_issue_and_how_to_run_it(runner, capsys):
    runner.script("claude", stdout=orchestrator_output([{"role": "implementer"}]))

    assert run(["plan", "add a retry to the loader"], runner) == 0

    out = capsys.readouterr().out
    assert "Filed issue #12" in out
    assert "Roster: implementer (standard)" in out
    assert "agentforge implement 12" in out


def test_plan_surfaces_dropped_roles_where_the_user_will_read_them(runner, capsys):
    runner.script(
        "claude", stdout=orchestrator_output([{"role": "implementer"}, {"role": "architect"}])
    )

    run(["plan", "add a retry"], runner)

    assert "Note: The Orchestrator asked for the `architect` Role" in capsys.readouterr().out


def test_an_ambiguous_task_exits_one_and_says_what_is_needed(runner, capsys):
    runner.script(
        "claude",
        stdout=json.dumps(
            {
                "type": "result",
                "is_error": False,
                "result": render_result_block(
                    {"outcome": "escalated", "summary": "Which loader? There are three."}
                ),
            }
        ),
    )

    assert run(["plan", "fix the loader"], runner) == 1
    assert "There are three." in capsys.readouterr().err


def test_a_precondition_failure_exits_two(capsys):
    runner = FakeRunner().script("git", "rev-parse", "--show-toplevel", returncode=128)

    assert run(["plan", "add a retry"], runner) == 2
    assert "not inside a git repository" in capsys.readouterr().err


def test_implement_prints_the_run_log_and_the_pull_request(runner, capsys):
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    assert run(["implement", "12", "--allow-commands"], runner) == 0

    out = capsys.readouterr().out
    assert "[ok] implementer (standard) — Added a bounded retry." in out
    assert "/pull/13" in out
    assert "A human merges." in out


def test_a_halted_run_exits_one_and_points_at_the_label(runner, capsys):
    runner.script("claude", stdout=agent_says("escalated", "Step s1 names a file that is gone.", ()))

    assert run(["implement", "12"], runner) == 1

    captured = capsys.readouterr()
    assert "[escalated] implementer" in captured.out
    assert "agentforge:halted" in captured.err


def test_a_suspended_run_exits_one_and_names_the_gate_it_is_waiting_on(
    runner, capsys, tmp_path, monkeypatch
):
    """Exit 1 rather than 0: a suspended Run has not done what was asked yet,
    and a script that carried on would carry on past the Gate."""
    _workflow(tmp_path, monkeypatch, HUMAN_GATED)
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    assert run(["implement", "12", "--allow-commands"], runner) == 1

    captured = capsys.readouterr()
    assert "[ok] implementer" in captured.out
    assert "waiting on the `human` Gate" in captured.err
    assert "agentforge:suspended" in captured.err


def test_a_tier_override_reaches_the_provider(runner):
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "done"))

    run(["implement", "12", "--tier", "deep"], runner)

    assert runner.argument_after("--model", "claude") == "opus"


def test_a_bare_tier_moves_every_role():
    assert cli.parse_tier_overrides(["deep"]) == (ModelTier.DEEP, {})


def test_a_qualified_tier_moves_one_role():
    assert cli.parse_tier_overrides(["implementer=deep"]) == (None, {"implementer": ModelTier.DEEP})


def test_both_forms_compose():
    default, per_role = cli.parse_tier_overrides(["cheap", "implementer=deep"])

    assert default is ModelTier.CHEAP
    assert per_role == {"implementer": ModelTier.DEEP}


def test_an_unknown_tier_lists_the_real_ones():
    with pytest.raises(SystemExit, match="standard"):
        cli.parse_tier_overrides(["thorough"])


def test_unslop_still_runs_after_the_command_runner_refactor(tmp_path, capsys):
    """The vendored Command was moved onto the same process boundary as
    everything else; this is the check that it still works."""
    target = tmp_path / "clean.md"
    target.write_text(
        "The loader retries three times before giving up. Each retry waits twice as\n"
        "long as the last.\n",
        encoding="utf-8",
    )

    assert cli.main(["unslop", str(target)]) == 0
    assert "clean" in capsys.readouterr().out


def test_init_is_still_honestly_unimplemented():
    with pytest.raises(SystemExit, match="not implemented"):
        cli.main(["init"])


def test_the_help_says_which_command_is_not_built(capsys):
    """A release whose `--help` advertises a command that exits 1 is a release
    that answers "does this do what I need" wrongly."""
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    assert "not built yet" in capsys.readouterr().out
