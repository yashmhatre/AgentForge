"""What `agentforge init` learns, and what it writes down.

Detection is a pure function of the repository path and the files git reports,
so everything here is called directly with a `tmp_path` and a tuple of paths —
no process, no repository, no network. Verification is the one part that asks
the machine anything, and it asks through the Command Runner, so it is a fake
runner here rather than a process. The CLI half, including the ADR-0002
precondition and the refusal to clobber, is in `tests/test_cli.py`, because that
is where a command exists.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agentforge_framework.core.config import (
    DEFAULT_TEST_SUITE,
    CapabilityTier,
    load_config,
)
from agentforge_framework.core.project import (
    ProjectContext,
    config_path,
    detect,
    differences,
    render_config,
    verified,
)

from .fakes import FakeRunner

FIXTURES = Path(__file__).parent / "fixtures"


def a_context(**kwargs) -> ProjectContext:
    return ProjectContext(
        root=Path("/repo/pipelines"),
        provider=kwargs.pop("provider", "claude"),
        capability_tier=kwargs.pop("capability_tier", CapabilityTier.NATIVE),
        **kwargs,
    )


# --- detection -------------------------------------------------------------


def test_a_declared_pytest_section_is_the_most_specific_evidence(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")

    context = detect(tmp_path, "claude", tracked=("pyproject.toml",))

    assert context.test_suite == ("pytest",)
    assert "pyproject.toml" in context.suite_detected


def test_a_tests_directory_is_evidence_enough(tmp_path):
    context = detect(tmp_path, "claude", tracked=("src/app.py", "tests/test_app.py"))

    assert context.test_suite == ("pytest",)
    assert "`tests/`" in context.suite_detected


def test_a_node_project_runs_npm_test(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest"}}', encoding="utf-8")

    context = detect(tmp_path, "claude", tracked=("package.json",))

    assert context.test_suite == ("npm", "test")


def test_a_repository_that_shows_nothing_is_told_it_got_the_default(tmp_path):
    """Guessing silently is how a Gate runs the wrong command for a month. The
    default is documented, and the file says it was not detected."""
    context = detect(tmp_path, "claude", tracked=("README.md",))

    assert context.test_suite == DEFAULT_TEST_SUITE
    assert context.suite_detected == ""
    assert "not detected" in render_config(context)


def test_the_language_census_reads_what_git_tracks_commonest_first():
    tracked = ("a.py", "b.py", "c.py", "one.sql", "two.sql", "notes.md", "vendor.exe")

    context = detect("/repo", "claude", tracked=tracked)

    assert context.languages == ("Python", "SQL", "Markdown")


def test_the_census_is_stable_between_two_runs_against_one_repository():
    """Two languages with the same number of files do not swap places, because
    a report that reorders itself is one nobody can diff."""
    tracked = ("a.py", "one.sql", "notes.md")

    first = detect("/repo", "claude", tracked=tracked).languages
    second = detect("/repo", "claude", tracked=tracked).languages

    assert first == second == ("Markdown", "Python", "SQL")


def test_the_provider_decides_the_capability_tier_that_is_written():
    """ADR-0005: declared rather than probed. `codex` is the Fragment-tier
    Provider and `claude` the native one."""
    assert detect("/repo", "codex").capability_tier is CapabilityTier.FRAGMENT
    assert detect("/repo", "claude").capability_tier is CapabilityTier.NATIVE


def a_virtualenv(root: Path, where: str = "", interpreter: str = "bin/python") -> str:
    """A virtualenv on disk, as detection finds it: by the interpreter in it.

    Untracked deliberately. A venv is the thing git does not know about, which is
    why detection stats for this one rather than reading it off `tracked`.
    """
    relative = "/".join(part for part in (where, ".venv", interpreter) if part)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return relative


def test_a_virtualenv_beside_the_tests_is_what_the_suite_runs_under(tmp_path):
    """Bare `pytest` is whichever one PATH answers with, and in a project with a
    venv that is the interpreter the project does not use: the suite fails on
    imports that are installed and reads as a broken repository (#120)."""
    a_virtualenv(tmp_path)

    context = detect(tmp_path, "claude", tracked=("src/app.py", "tests/test_app.py"))

    assert context.test_suite == (".venv/bin/python", "-m", "pytest")
    assert ".venv/bin/python" in context.suite_note


def test_the_interpreter_is_named_relative_to_the_repository(tmp_path):
    """The file this goes into gets committed. An absolute path is correct on
    the machine init ran on and wrong on every clone of it."""
    a_virtualenv(tmp_path, interpreter="Scripts/python.exe")

    context = detect(tmp_path, "claude", tracked=("tests/test_app.py",))

    assert not Path(context.test_suite[0]).is_absolute()
    assert str(tmp_path) not in context.test_suite[0]


def test_a_project_in_a_subdirectory_is_found_and_named(tmp_path):
    """Root-marker detection reported a repository whose whole suite lives one
    directory down as having no suite at all."""
    a_virtualenv(tmp_path, where="subproject", interpreter="Scripts/python.exe")

    context = detect(tmp_path, "claude", tracked=("subproject/tests/test_app.py",))

    assert context.test_suite == (
        "subproject/.venv/Scripts/python.exe",
        "-m",
        "pytest",
        "subproject",
    )
    assert "`subproject/`" in context.suite_detected


def test_a_subdirectory_runs_under_its_own_virtualenv_before_the_repositorys(tmp_path):
    a_virtualenv(tmp_path)
    a_virtualenv(tmp_path, where="subproject")

    context = detect(tmp_path, "claude", tracked=("subproject/tests/test_app.py",))

    assert context.test_suite[0] == "subproject/.venv/bin/python"


def test_the_repositorys_own_evidence_wins_over_a_subdirectorys(tmp_path):
    context = detect(
        tmp_path, "claude", tracked=("tests/test_app.py", "sub/tests/test_other.py")
    )

    assert context.test_suite == ("pytest",)
    assert "at the repository root" in context.suite_detected


def test_a_project_buried_deeper_than_two_directories_is_not_guessed_at(tmp_path):
    """A repository that buries a project that deep is telling us it has more
    than one, and picking between them would be guessing rather than detecting."""
    context = detect(tmp_path, "claude", tracked=("services/orders/api/tests/test_app.py",))

    assert context.test_suite == DEFAULT_TEST_SUITE
    assert context.suite_detected == ""


def test_a_setup_cfg_pytest_section_is_evidence(tmp_path):
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\naddopts = -q\n", encoding="utf-8")

    context = detect(tmp_path, "claude", tracked=("setup.cfg",))

    assert context.test_suite == ("pytest",)
    assert "setup.cfg" in context.suite_detected


def test_a_dot_directory_is_not_a_project(tmp_path):
    """Detection reads tracked paths rather than walking the tree, and a
    `.github/` full of workflow files is not a Python project."""
    context = detect(tmp_path, "claude", tracked=(".github/workflows/ci.yml",))

    assert context.test_suite == DEFAULT_TEST_SUITE


def test_a_suite_with_nothing_to_pin_it_to_says_so(tmp_path):
    """The bare vector is deliberate — `python -m pytest` without pytest exits
    the way a failing suite does — but a human reading the file should know
    which `pytest` this is."""
    context = detect(tmp_path, "claude", tracked=("tests/test_app.py",))

    assert context.test_suite == ("pytest",)
    assert "on PATH" in context.suite_note
    assert "on PATH" in render_config(context)


# --- verification ----------------------------------------------------------


def test_an_interpreter_that_answers_keeps_the_suite_it_was_pinned_to(tmp_path):
    a_virtualenv(tmp_path)
    context = detect(tmp_path, "claude", tracked=("tests/test_app.py",))
    runner = FakeRunner()

    checked = verified(context, runner)

    assert checked.test_suite == context.test_suite
    assert runner.calls[-1] == (str(tmp_path / ".venv/bin/python"), "-m", "pytest", "--version")


def test_the_probe_names_the_interpreter_absolutely_rather_than_leaving_it_to_the_os(tmp_path):
    """A relative program name resolves against the child's directory on POSIX
    and the parent's on Windows. A probe that ran on one of those would be
    proving something about the machine it was written on."""
    a_virtualenv(tmp_path)
    runner = FakeRunner()

    verified(detect(tmp_path, "claude", tracked=("tests/test_app.py",)), runner)

    assert Path(runner.calls[-1][0]).is_absolute()


def test_a_virtualenv_without_pytest_falls_back_rather_than_writing_a_suite_that_exits_one(
    tmp_path,
):
    """The venv exists and the runner is not in it. Left pinned, the Gate would
    run `python -m pytest`, take the 1 it exits for a failing suite, and tell a
    human their tests are red."""
    a_virtualenv(tmp_path)
    runner = FakeRunner().install("pytest")
    runner.script(str(tmp_path / ".venv/bin/python"), returncode=1, stderr="No module named pytest")

    checked = verified(detect(tmp_path, "claude", tracked=("tests/test_app.py",)), runner)

    assert checked.test_suite == ("pytest",)
    assert "falls back" in checked.suite_note


def test_the_fallback_keeps_the_directory_the_suite_was_pointed_at(tmp_path):
    a_virtualenv(tmp_path, where="subproject")
    runner = FakeRunner().install("pytest")
    runner.script(str(tmp_path / "subproject/.venv/bin/python"), returncode=1)

    checked = verified(detect(tmp_path, "claude", tracked=("subproject/tests/t.py",)), runner)

    assert checked.test_suite == ("pytest", "subproject")


def test_a_default_suite_this_machine_cannot_run_is_written_with_the_doubt_on_it(tmp_path):
    """Verifying the default is what the fallback is worth: init already knows
    whether the command it just wrote exists, and saying nothing leaves that to
    be discovered by a Gate halting a Run."""
    checked = verified(detect(tmp_path, "claude", tracked=("tests/t.py",)), FakeRunner())

    assert checked.test_suite == ("pytest",)
    assert "not on PATH" in checked.suite_note


def test_verification_corrects_the_file_and_never_refuses_to_write_one(tmp_path):
    """Whoever runs init knows this repository better than detection does."""
    checked = verified(detect(tmp_path, "claude", tracked=("tests/t.py",)), FakeRunner())

    assert "not on PATH" in render_config(checked)
    assert yaml.safe_load(render_config(checked))["gates"]["tests"]["suite"] == ["pytest"]


# --- what gets written -----------------------------------------------------


def test_the_written_file_round_trips_through_load_config(tmp_path):
    """The claim the whole command rests on: what init reports is what
    `load_config` reads back, field for field."""
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest"}}', encoding="utf-8")
    context = detect(tmp_path, "codex", tracked=("package.json",))

    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(render_config(context), encoding="utf-8")

    config = load_config(tmp_path)
    assert config.test_suite == context.test_suite
    assert config.capability_for("codex") is context.capability_tier
    assert config.publish_pack_inventory is False


def test_an_argument_with_a_dash_in_it_survives_the_round_trip(tmp_path):
    """`suite: [pytest, -q]` is a YAML list with a flag in it, and an unquoted
    `-q` is not a string. Quoted here so the file reads back as typed."""
    context = a_context(test_suite=("pytest", "-q", "--maxfail=1"))
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(render_config(context), encoding="utf-8")

    assert load_config(tmp_path).test_suite == ("pytest", "-q", "--maxfail=1")


def test_the_file_says_which_values_were_detected_and_which_were_defaulted():
    detected = render_config(a_context(suite_detected="a `tests/` directory"))

    assert "detected: a `tests/` directory" in detected
    assert "not detected" in render_config(a_context())


def test_the_file_carries_no_key_nothing_reads():
    """`docs/PLAN.md` promised a config owning plugin activation, and the file
    holds only what `load_config` reads. A key that has no effect is a lie told
    to whoever edits it, so activation is printed and the file says why
    (ADR-0020). Every key here arrived with its reader."""
    written = render_config(a_context(plugins=("sql", "databricks")))

    assert set(yaml.safe_load(written)) == {"providers", "gates", "context"}
    assert "databricks" not in written
    # The absence is explained where somebody looking for the key will look.
    assert "no `plugins:` key here to edit" in written


# --- re-running against a file that is already there -----------------------


def test_a_config_that_matches_reports_no_differences():
    context = a_context()

    assert differences(context, render_config(context)) == ()


def test_a_file_somebody_reformatted_is_not_a_difference():
    """Compared as the values `load_config` reads rather than as text: the
    question is whether somebody's edits are still there, and a diff that fired
    on whitespace or a comment could not answer it."""
    context = a_context()

    found = differences(
        context,
        "# my own note\nproviders: {claude: {capability_tier: native}}\n"
        "gates:\n  tests:\n    suite: pytest\n",
    )

    assert found == ()


def test_an_edited_suite_is_reported_with_both_sides():
    context = a_context(test_suite=("pytest",))

    (found,) = differences(
        context,
        "providers: {claude: {capability_tier: native}}\n"
        "gates: {tests: {suite: [make, test]}}\n",
    )

    assert "make test" in found and "pytest" in found


def test_a_file_that_will_not_parse_is_reported_rather_than_raised():
    (found,) = differences(a_context(), "providers: [unclosed\n")

    assert "not valid YAML" in found


# --- the config init wrote for this repository -----------------------------


def test_the_config_init_wrote_for_agentforge_itself_reads_back(tmp_path):
    """The fixture is the file `agentforge init` produced when it was run
    against this checkout — dogfooding the command rather than asserting against
    a config somebody hand-wrote to make the test pass."""
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        (FIXTURES / "init_agentforge.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )

    config = load_config(tmp_path)

    assert config.test_suite == ("pytest",)
    assert config.capability_for("claude") is CapabilityTier.NATIVE
