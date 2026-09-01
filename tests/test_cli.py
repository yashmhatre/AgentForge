"""The commands as a data engineer types them.

These cover the surface `agentforge --help` promises: exit codes, the tier
override syntax, and what gets printed when a Run stops rather than finishes.
`main` takes the Command Runner as an argument, so the whole CLI runs offline
without a single patch.
"""

from __future__ import annotations

import json

import pytest

from agentforge_framework import __version__, cli
from agentforge_framework.core.config import CapabilityTier, load_config
from agentforge_framework.core.contracts import ModelTier
from agentforge_framework.core.plan_format import render_result_block

from .fakes import FakeRunner
from .test_agents import orchestrator_output
from .test_runtime import (
    HUMAN_GATED,
    ROOT,
    _workflow,
    a_runner,
    agent_says,
    recorded_claude_run,
)


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
        "claude", stdout=orchestrator_output([{"role": "implementer"}, {"role": "bulldozer"}])
    )

    run(["plan", "add a retry"], runner)

    assert "Note: Unknown Role `bulldozer`" in capsys.readouterr().out


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


# --- the interview reaches the human through the terminal, and only there ----


class _Stream:
    """A stdin that is, or is not, a terminal."""

    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_nothing_interactive_attached_means_no_interviewer():
    """A scheduled Run has nobody to ask. The Orchestrator falls back to the
    single-shot path rather than blocking on input that will never arrive."""
    assert cli.build_interviewer(stdin=_Stream(tty=False)) is None


def test_a_terminal_gets_an_interviewer_that_reads_answers(capsys):
    answers = iter(["The orders loader."])
    ask = cli.build_interviewer(stdin=_Stream(tty=True), prompt=lambda _: next(answers))

    assert ask is not None
    assert ask("Which loader?") == "The orders loader."
    out = capsys.readouterr().out
    assert "Which loader?" in out
    assert "press Enter on an empty line" in out, "the way out has to be discoverable"


def test_an_empty_line_ends_the_interview():
    ask = cli.build_interviewer(stdin=_Stream(tty=True), prompt=lambda _: "   ")

    assert ask("Which loader?") is None


def test_end_of_input_ends_the_interview():
    """A pipe that closes mid-question is a human who has left."""

    def closed(_):
        raise EOFError

    ask = cli.build_interviewer(stdin=_Stream(tty=True), prompt=closed)

    assert ask("Which loader?") is None


def test_the_banner_is_printed_once_however_many_questions_there_are(capsys):
    ask = cli.build_interviewer(stdin=_Stream(tty=True), prompt=lambda _: "yes")

    ask("First?")
    ask("Second?")

    assert capsys.readouterr().out.count("press Enter on an empty line") == 1


def test_every_command_the_help_advertises_can_be_run(capsys):
    """A release whose `--help` advertises a command that exits 1 is a release
    that answers "does this do what I need" wrongly. `init` was that command
    until #76, and this is the test that noticed."""
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    out = capsys.readouterr().out
    assert "not built yet" not in out
    assert "init" in out and "run" in out


def test_version_prints_the_version_and_exits_zero(capsys):
    """`--version` is answered by the parser and exits, so it never returns
    through `main` the way a command does. Zero is the half that matters: a
    release script asks the installed CLI what it is and checks the status."""
    with pytest.raises(SystemExit) as exit_:
        cli.main(["--version"])

    assert exit_.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_implement_prints_what_the_run_cost(runner, capsys):
    """The figure a human asks for first, in the terminal they are already
    looking at rather than only on the Issue."""
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=recorded_claude_run())

    assert run(["implement", "12", "--allow-commands"], runner) == 0

    assert "Cost: $" in capsys.readouterr().out


def test_the_control_run_is_one_flag(runner, capsys):
    """Measuring what a pack saved needs a Run without one, and a data engineer
    should not have to edit an Issue body to get it."""
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))

    assert run(["implement", "12", "--allow-commands", "--no-context-pack"], runner) == 0

    prompts = [call[call.index("-p") + 1] for call in runner.matching("claude")]
    assert prompts and all("## Context Pack" not in prompt for prompt in prompts)


# --- agentforge run --------------------------------------------------------


def a_dbt_project(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("name: warehouse\n", encoding="utf-8")
    return tmp_path


def test_run_with_no_command_lists_what_this_repository_has(tmp_path, capsys):
    assert cli.main(["run", "-C", str(a_dbt_project(tmp_path))], FakeRunner()) == 0

    out = capsys.readouterr().out
    assert "scaffold-dbt-model <name>" in out
    assert "Write a dbt model" in out


def test_run_in_a_repository_no_plugin_answers_for_says_so_rather_than_listing_nothing(
    tmp_path, capsys
):
    assert cli.main(["run", "-C", str(tmp_path)], FakeRunner()) == 0

    assert "no Commands to run" in capsys.readouterr().out


def test_run_writes_the_files_and_leaves_committing_to_a_human(tmp_path, capsys):
    root = a_dbt_project(tmp_path)

    assert cli.main(["run", "scaffold-dbt-model", "orders", "-C", str(root)], FakeRunner()) == 0

    out = capsys.readouterr().out
    assert "wrote models/orders.sql" in out
    assert "a Command commits nothing" in out
    assert (root / "models" / "orders.sql").is_file()


def test_run_files_no_issue_and_starts_no_run(tmp_path):
    """The reason it is a separate command: a chore should not cost an Issue, a
    branch, and six Role invocations. Nothing reaches the process boundary at
    all, because this Command writes files and starts nothing."""
    runner = FakeRunner()

    cli.main(["run", "scaffold-dbt-model", "orders", "-C", str(a_dbt_project(tmp_path))], runner)

    assert not runner.calls


def test_an_unknown_command_exits_two_and_names_what_there_is(tmp_path, capsys):
    root = a_dbt_project(tmp_path)

    assert cli.main(["run", "scaffold-everything", "-C", str(root)], FakeRunner()) == 2

    err = capsys.readouterr().err
    assert "scaffold-everything" in err
    assert "scaffold-dbt-model" in err


def test_a_command_run_twice_refuses_rather_than_overwriting(tmp_path, capsys):
    root = a_dbt_project(tmp_path)
    cli.main(["run", "scaffold-dbt-model", "orders", "-C", str(root)], FakeRunner())
    (root / "models" / "orders.sql").write_text("the real model", encoding="utf-8")

    assert cli.main(["run", "scaffold-dbt-model", "orders", "-C", str(root)], FakeRunner()) == 2

    assert "already exists" in capsys.readouterr().err
    assert (root / "models" / "orders.sql").read_text(encoding="utf-8") == "the real model"


def test_the_wrong_arguments_exit_two_and_say_what_to_type(tmp_path, capsys):
    assert cli.main(["run", "scaffold-dbt-model", "-C", str(a_dbt_project(tmp_path))], FakeRunner()) == 2

    assert "agentforge run scaffold-dbt-model <name>" in capsys.readouterr().err


# --- agentforge init -------------------------------------------------------


def a_repository(tmp_path, tracked: str = "src/app.py\ntests/test_app.py\n") -> FakeRunner:
    """A git repository with a GitHub remote, as git would report it."""
    fake = FakeRunner()
    fake.script("git", "rev-parse", "--show-toplevel", stdout=f"{tmp_path}\n")
    fake.script("git", "remote", "get-url", stdout="https://github.com/acme/pipelines.git\n")
    fake.script("git", "ls-files", stdout=tracked)
    return fake


def init(tmp_path, *flags, fake=None) -> int:
    return cli.main(["init", "-C", str(tmp_path), *flags], fake or a_repository(tmp_path))


def test_init_writes_a_config_the_loader_reads_back(tmp_path, capsys):
    assert init(tmp_path) == 0

    config = load_config(tmp_path)
    assert config.test_suite == ("pytest",)
    assert config.capability_for("claude") is CapabilityTier.NATIVE
    assert "Wrote" in capsys.readouterr().out


def test_init_creates_the_directory_when_it_is_absent(tmp_path):
    assert not (tmp_path / ".agentforge").exists()

    init(tmp_path)

    assert (tmp_path / ".agentforge" / "config.yaml").is_file()


def test_init_outside_a_git_repository_creates_nothing(tmp_path, capsys):
    fake = FakeRunner()
    fake.script("git", "rev-parse", "--show-toplevel", returncode=1)

    assert init(tmp_path, fake=fake) == 2
    assert not (tmp_path / ".agentforge").exists()
    assert "not inside a git repository" in capsys.readouterr().err


def test_init_without_an_origin_refuses_with_the_precondition_it_is_about(tmp_path, capsys):
    """ADR-0002 makes a GitHub remote a precondition for every Run, and setup is
    a better place to find that out than the first Run's halt."""
    fake = a_repository(tmp_path)
    fake.script("git", "remote", "get-url", stdout="")

    assert init(tmp_path, fake=fake) == 2
    assert not (tmp_path / ".agentforge").exists()
    assert "ADR-0002" in capsys.readouterr().err


def test_init_against_a_remote_that_is_not_github_refuses(tmp_path, capsys):
    fake = a_repository(tmp_path)
    fake.script("git", "remote", "get-url", stdout="git@gitlab.com:acme/pipelines.git\n")

    assert init(tmp_path, fake=fake) == 2
    assert not (tmp_path / ".agentforge").exists()
    assert "not GitHub" in capsys.readouterr().err


def test_an_unknown_provider_is_refused_by_name_rather_than_written(tmp_path, capsys):
    assert init(tmp_path, "--provider", "cursor") == 2

    assert not (tmp_path / ".agentforge").exists()
    err = capsys.readouterr().err
    assert "cursor" in err and "claude, codex" in err


def test_the_provider_named_is_the_provider_written(tmp_path):
    init(tmp_path, "--provider", "codex")

    assert load_config(tmp_path).capability_for("codex") is CapabilityTier.FRAGMENT


def test_init_prints_the_plugins_and_says_they_are_not_written(tmp_path, capsys):
    """Activation is per Run from the frozen plan's blast radius. Printing it
    keeps what init knows visible without writing a key nothing reads."""
    (tmp_path / "dbt_project.yml").write_text("name: warehouse\n", encoding="utf-8")

    init(tmp_path)

    out = capsys.readouterr().out
    assert "sql" in out
    assert "printed, not written" in out
    written = (tmp_path / ".agentforge" / "config.yaml").read_text(encoding="utf-8")
    assert "sql" not in written, "a key nothing reads is a lie told to whoever edits it"


def test_init_reports_the_languages_git_knows_about(tmp_path, capsys):
    init(tmp_path, fake=a_repository(tmp_path, "a.py\nb.py\nmodels/orders.sql\n"))

    assert "Languages: Python, SQL" in capsys.readouterr().out


def test_re_running_reports_the_difference_and_writes_nothing(tmp_path, capsys):
    init(tmp_path)
    edited = (tmp_path / ".agentforge" / "config.yaml")
    edited.write_text(
        "providers: {claude: {capability_tier: native}}\ngates: {tests: {suite: [make, test]}}\n",
        encoding="utf-8",
    )

    assert init(tmp_path) == 1

    assert "make test" in capsys.readouterr().out
    assert load_config(tmp_path).test_suite == ("make", "test"), "it overwrote an edited config"


def test_re_running_against_a_config_that_matches_says_there_is_nothing_to_do(tmp_path, capsys):
    init(tmp_path)

    assert init(tmp_path) == 0
    assert "Nothing to do" in capsys.readouterr().out


def test_force_replaces_a_config_somebody_edited(tmp_path):
    init(tmp_path)
    (tmp_path / ".agentforge" / "config.yaml").write_text(
        "gates: {tests: {suite: [make, test]}}\n", encoding="utf-8"
    )

    assert init(tmp_path, "--force") == 0
    assert load_config(tmp_path).test_suite == ("pytest",)


def test_a_repository_with_no_suite_to_find_is_told_what_it_got(tmp_path, capsys):
    init(tmp_path, fake=a_repository(tmp_path, "README.md\n"))

    assert "not detected" in capsys.readouterr().out
    assert load_config(tmp_path).test_suite == ("pytest",)
