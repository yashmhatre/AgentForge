"""What `agentforge init` learns about a repository, and what it writes down.

Three phases, kept apart because they are held to different standards.
Detection reads the repository and answers three questions — which Plugins
answer for it, what its suite appears to be, which Provider it will drive — and
is allowed to be wrong, because everything it produces is printed for a human to
correct. Verification asks the machine one question about the answer detection
liked least, through the Command Runner it is handed and never a process of its
own. Rendering turns what survives into `.agentforge/config.yaml`, and is
allowed to write only what `core.config` reads back.

That second rule is the whole shape of this module. `docs/PLAN.md` promised a
config file owning tier mapping, Provider selection, plugin activation, and Gate
policy; `load_config` reads two keys. Writing the other three would be writing
keys nothing consults, which is worse than not writing them: a human who edits a
key that has no effect has been lied to by the file. So what init detects and
cannot yet persist it prints, and the gap stays visible. See ADR-0020.

Nothing here decides whether the repository may be written to. `open_repository`
answers that, and the CLI asks it first, so a repository that cannot host a Run
never gets a config file suggesting it can.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml as pyyaml

from .config import DEFAULT_CAPABILITIES, DEFAULT_TEST_SUITE, CapabilityTier
from .process import CommandRunner

CONFIG_DIR = ".agentforge"
CONFIG_FILE = "config.yaml"

#: How many tracked files the language census reads. A repository's languages
#: are visible in the first couple of thousand files, and the census is a line
#: of output rather than a decision anything turns on.
MAX_CENSUS = 2000

#: Where a Python project keeps its interpreter, and where the interpreter sits
#: inside it. Both layouts are checked on both platforms rather than the one this
#: process is running on: a repository is shared between them, and the venv that
#: is on disk is the one that answers.
VENV_DIRS = (".venv", "venv")
VENV_INTERPRETERS = ("Scripts/python.exe", "bin/python")

#: How far below the root init looks for a project. One or two directories down
#: is ordinary — a service in a monorepo, a package beside its docs — and a
#: repository that buries one deeper is telling us it has more than one, where
#: picking would be guessing rather than detecting (#120).
MAX_DEPTH = 2

#: Suffix to the name a human calls it. Deliberately short: this names what
#: AgentForge might have something to say about, and a census listing `.gitignore`
#: as a language would be noise dressed as information.
LANGUAGES = {
    ".py": "Python",
    ".sql": "SQL",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".ipynb": "Notebook",
    ".scala": "Scala",
    ".java": "Java",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".sh": "Shell",
    ".md": "Markdown",
}


@dataclass(frozen=True)
class ProjectContext:
    """What AgentForge learned about one repository at `agentforge init`.

    `suite_detected`, `suite_note`, and `plugins` are the fields that exist
    because a human reads this before a machine does. The first two say where the
    suite came from and what is likely to go wrong with it, which is the
    difference between a line to leave alone and a line to correct. `plugins` is
    printed and never written: activation is decided per Run from the frozen
    Plan's blast radius, and a `plugins:` key would be a key nothing consults.
    """

    root: Path
    provider: str
    capability_tier: CapabilityTier
    test_suite: tuple[str, ...] = DEFAULT_TEST_SUITE
    suite_detected: str = ""
    #: The caveat on the suite, when there is one: no interpreter to pin it to,
    #: or an interpreter that could not run it. Separate from the evidence
    #: because the two answer different questions — where the suite came from,
    #: and what is likely to go wrong with it — and because a fallback changes
    #: the second without making the first untrue.
    suite_note: str = ""
    languages: tuple[str, ...] = ()
    plugins: tuple[str, ...] = ()


def detect(
    root: Path | str,
    provider: str,
    tracked: tuple[str, ...] = (),
    plugins: tuple[str, ...] = (),
) -> ProjectContext:
    """Everything init has to say about this repository.

    `tracked` is the repository's files as git reports them, and `plugins` the
    names `core.registry` activated for it. Both are passed in rather than read
    here: this module opens no process and imports no registry, so detection is
    a pure function of what it was handed and a test needs no repository.
    """
    suite, because, note = _suite(Path(root), tracked)
    return ProjectContext(
        root=Path(root),
        provider=provider,
        capability_tier=DEFAULT_CAPABILITIES.get(provider, CapabilityTier.FRAGMENT),
        test_suite=suite,
        suite_detected=because,
        suite_note=note,
        languages=_languages(tracked),
        plugins=tuple(plugins),
    )


def verified(context: ProjectContext, runner: CommandRunner) -> ProjectContext:
    """The suite, after asking the machine whether it can run at all.

    Detection reads a repository and can only be as right as what is committed
    to it; a virtualenv is not. So the one question worth a subprocess is asked
    here — can the interpreter this suite names import pytest — and it is asked
    with `--version` rather than by running the suite, because init is setup and
    a repository's tests are not init's to run.

    The answer is a correction and never a refusal. A config file is written
    either way: whoever runs init knows this repository better than detection
    does, and a command that refused to write anything until the environment was
    perfect would be a worse setup step than one that writes a line and says
    what it doubts.
    """
    suite = context.test_suite
    if len(suite) > 2 and suite[1] == "-m":
        # The interpreter is stored relative to the repository and resolved
        # against it here, not left to the OS: a relative program path resolves
        # against the child's directory on POSIX and the parent's on Windows,
        # and a suite that runs on one of those is not a suite that runs.
        probe = (str(context.root / suite[0]), "-m", suite[2], "--version")
        if runner.run(probe, cwd=context.root).ok:
            return context
        return replace(
            context,
            test_suite=("pytest", *suite[3:]),
            suite_note=(
                f"`{suite[0]}` could not run {suite[2]}, so the suite falls back to whichever "
                "is on PATH — install it in that virtualenv, or correct this line"
            ),
        )

    if not runner.has_binary(suite[0]):
        return replace(
            context,
            suite_note=(
                f"`{suite[0]}` is not on PATH on this machine, so the `tests` Gate will halt "
                "a Run rather than report on the code until this line names a suite it can run"
            ),
        )

    return context


def config_path(root: Path | str) -> Path:
    return Path(root) / CONFIG_DIR / CONFIG_FILE


def render_config(context: ProjectContext) -> str:
    """The file, with a comment on every line a human might want to change.

    Written by hand rather than dumped, because the comments are the point. A
    reader who cannot tell a detected value from a default has to re-derive both
    before touching either, and the first thing anybody does to a generated
    config is edit it.
    """
    suite = ", ".join(_quoted(part) for part in context.test_suite)
    because = (
        f"detected: {context.suite_detected}"
        if context.suite_detected
        else "not detected — this is the documented default, so correct it if it is wrong"
    )
    # The caveat goes in the file and not only on the terminal: init is run once
    # and the file is read for as long as the repository lives.
    note = "\n    # " + context.suite_note if context.suite_note else ""

    return f"""\
# AgentForge project configuration, written by `agentforge init`.
#
# This file holds what AgentForge reads and nothing else. Which Plugins answer
# for this repository is decided per Run from the frozen Plan's blast radius
# (ADR-0016), so there is no `plugins:` key here to edit.

providers:
  # What this Provider's CLI can be relied on to support, declared rather than
  # probed (ADR-0005). `native` delivers a Role's skills as the CLI's own
  # commands; `fragment` inlines them into the prompt instead.
  {context.provider}:
    capability_tier: {context.capability_tier}

gates:
  tests:
    # The argument vector the `tests` Gate runs, in this repository, from its
    # root. An interpreter named here is resolved against that root.
    # {because}{note}
    suite: [{suite}]

context:
  # Whether the Context Pack comment names the symbols and imports it resolved,
  # or only counts them (ADR-0024). A Run posts that comment to the Issue, and
  # a tracker can have a wider audience than the code — so the names are off
  # unless this repository says otherwise. The file list is published either
  # way: the frozen Plan on the Issue already carries it.
  publish_inventory: false
"""


def differences(context: ProjectContext, existing: str) -> tuple[str, ...]:
    """How the config on disk differs from the one init would write.

    Compared as the values `load_config` would read rather than as text, so a
    file somebody reformatted, commented, or reordered is not reported as a
    difference. The point of the comparison is to tell a human whether their
    edits are still there, and a diff that fired on whitespace would not.
    """
    try:
        data = pyyaml.safe_load(existing) or {}
    except pyyaml.YAMLError as exc:
        return (f"the file on disk is not valid YAML: {exc}",)

    if not isinstance(data, dict):
        return ("the file on disk is not a mapping",)

    found: list[str] = []

    providers = data.get("providers") or {}
    tier = (providers.get(context.provider) or {}).get("capability_tier")
    if tier is None:
        found.append(f"it names no capability tier for {context.provider!r}")
    elif str(tier) != str(context.capability_tier):
        found.append(
            f"{context.provider} capability tier: {tier} on disk, "
            f"{context.capability_tier} from detection"
        )

    suite = ((data.get("gates") or {}).get("tests") or {}).get("suite")
    if suite is not None:
        rendered = " ".join(suite) if isinstance(suite, list) else str(suite)
        if rendered.split() != list(context.test_suite):
            found.append(
                f"test suite: `{rendered}` on disk, "
                f"`{' '.join(context.test_suite)}` from detection"
            )

    return tuple(found)


def _suite(root: Path, tracked: tuple[str, ...]) -> tuple[tuple[str, ...], str, str]:
    """The suite this repository appears to run, the evidence, and the caveat.

    Ordered by how specific the evidence is rather than by popularity: a
    repository declaring a pytest section is telling us directly, and one with a
    `tests/` directory is telling us by convention. A repository that shows
    nothing gets the documented default and is told it was a default — guessing
    silently is how a Gate ends up running the wrong command for a month.

    Python is the one ecosystem searched below the root, because pytest is the
    one runner that takes the directory as an argument: a Gate runs from the
    repository root, so `pytest subproject` is expressible there and a node
    project one level down is not. A repository whose only npm project is in a
    subdirectory is told the suite was not detected, which is true (#120).
    """
    files = set(tracked)

    where = _python_project(root, files)
    if where is not None:
        return _pytest_suite(root, *where)

    package = _read(root / "package.json")
    if '"test"' in package:
        return ("npm", "test"), "a `test` script in package.json", ""

    if "go.mod" in files:
        return ("go", "test", "./..."), "a go.mod at the repository root", ""

    if "Cargo.toml" in files:
        return ("cargo", "test"), "a Cargo.toml at the repository root", ""

    return DEFAULT_TEST_SUITE, "", ""


def _python_project(root: Path, files: set[str]) -> tuple[str, str] | None:
    """Where the pytest evidence is, and what the evidence was.

    The root is asked first and answers for almost every repository. Only when
    it says nothing does this descend, shallowest directory first and then
    alphabetically, so a repository with two candidates picks the same one every
    time it is asked rather than whichever the filesystem listed first.
    """
    for candidate in ("", *_subdirectories(files)):
        because = _pytest_evidence(root, files, candidate)
        if because:
            return candidate, because
    return None


def _subdirectories(files: set[str]) -> tuple[str, ...]:
    """The directories git reports files in, down to `MAX_DEPTH`.

    Read off the tracked paths rather than by walking the tree, for the reason
    the language census is: a `.venv` or a `node_modules` nobody committed is
    full of somebody else's `tests/`, and walking would find it.
    """
    found: set[str] = set()
    for path in files:
        parts = path.split("/")[:-1]
        if not parts or parts[0].startswith("."):
            continue
        for depth in range(1, min(len(parts), MAX_DEPTH) + 1):
            found.add("/".join(parts[:depth]))
    return tuple(sorted(found, key=lambda name: (name.count("/"), name)))


def _pytest_evidence(root: Path, files: set[str], where: str) -> str:
    """What says this directory runs pytest, most specific first."""
    prefix = f"{where}/" if where else ""
    inside = f" under `{where}/`" if where else " at the repository root"

    if "[tool.pytest" in _read(root / prefix / "pyproject.toml"):
        return f"a `[tool.pytest]` section in {prefix}pyproject.toml"

    if "[tool:pytest]" in _read(root / prefix / "setup.cfg"):
        return f"a `[tool:pytest]` section in {prefix}setup.cfg"

    if f"{prefix}pytest.ini" in files or f"{prefix}conftest.py" in files:
        return f"pytest configuration{inside}"

    if any(path.startswith(f"{prefix}tests/") for path in files):
        return f"a `tests/` directory{inside}"

    return ""


def _pytest_suite(root: Path, where: str, because: str) -> tuple[tuple[str, ...], str, str]:
    """pytest, pinned to an interpreter if this repository has one to pin to.

    Bare `pytest` is whichever one PATH answers with, which in a project with a
    venv is the interpreter the project does not use: the suite then fails on
    imports that are installed, and reads as a broken repository rather than a
    misconfigured one (#120). `-m` also puts the working directory on
    `sys.path`, which the console script does not.

    Without a venv the vector stays bare, and that is deliberate rather than
    lazy: `python -m pytest` in an environment without pytest exits 1, which is
    the status a suite spends on real failures, while a `pytest` that is not
    there cannot be started at all. The Gate reports those two differently, and
    only the second is honest about what happened.
    """
    target = (where,) if where else ()
    interpreter = _interpreter(root, where)
    if interpreter is None:
        return (
            ("pytest", *target),
            because,
            "no virtualenv found beside it, so this is whichever `pytest` is on PATH",
        )
    return (
        (interpreter, "-m", "pytest", *target),
        because,
        f"pinned to `{interpreter}`, so the suite runs under the project's own interpreter",
    )


def _interpreter(root: Path, where: str) -> str | None:
    """The virtualenv interpreter to pin the suite to, relative to the root.

    The project's own first and the repository's second, so a subdirectory that
    brought its own environment is not run under the one at the top. Relative
    because this is written into a file that gets committed: an absolute path is
    correct on exactly one machine, and wrong on every clone of it.
    """
    for base in dict.fromkeys((where, "")):
        for venv in VENV_DIRS:
            for interpreter in VENV_INTERPRETERS:
                candidate = "/".join(part for part in (base, venv, interpreter) if part)
                if (root / candidate).is_file():
                    return candidate
    return None


def _languages(tracked: tuple[str, ...]) -> tuple[str, ...]:
    """The languages this repository is written in, commonest first.

    A census rather than a claim: it counts the suffixes git already knows
    about, so a `.venv` nobody committed does not make this a repository full of
    somebody else's Python.
    """
    counts: dict[str, int] = {}
    for path in tracked[:MAX_CENSUS]:
        language = LANGUAGES.get(Path(path).suffix.lower())
        if language:
            counts[language] = counts.get(language, 0) + 1

    # Sorted by count and then by name, so two languages with the same number of
    # files do not swap places between two runs against one repository.
    return tuple(name for name, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _quoted(part: str) -> str:
    """A suite argument as YAML. Quoted, because `-q` unquoted is not a string."""
    return '"' + part.replace('"', '\\"') + '"'


__all__ = [
    "CONFIG_DIR",
    "CONFIG_FILE",
    "LANGUAGES",
    "MAX_CENSUS",
    "MAX_DEPTH",
    "VENV_DIRS",
    "VENV_INTERPRETERS",
    "ProjectContext",
    "config_path",
    "detect",
    "differences",
    "render_config",
    "verified",
]
