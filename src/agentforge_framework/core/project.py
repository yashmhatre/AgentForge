"""What `agentforge init` learns about a repository, and what it writes down.

Two halves, kept apart because they are held to different standards. Detection
reads the repository and answers three questions — which Plugins answer for it,
what its suite appears to be, which Provider it will drive — and is allowed to
be wrong, because everything it produces is printed for a human to correct.
Rendering turns the answers into `.agentforge/config.yaml`, and is allowed to
write only what `core.config` reads back.

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

from dataclasses import dataclass
from pathlib import Path

import yaml as pyyaml

from .config import DEFAULT_CAPABILITIES, DEFAULT_TEST_SUITE, CapabilityTier

CONFIG_DIR = ".agentforge"
CONFIG_FILE = "config.yaml"

#: How many tracked files the language census reads. A repository's languages
#: are visible in the first couple of thousand files, and the census is a line
#: of output rather than a decision anything turns on.
MAX_CENSUS = 2000

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

    `suite_detected` and `plugins` are the two fields that exist because a human
    reads this before a machine does. The first says whether the suite in the
    file was found or assumed, which is the difference between a line to leave
    alone and a line to correct. The second is printed and never written:
    activation is decided per Run from the frozen Plan's blast radius, and a
    `plugins:` key would be a key nothing consults.
    """

    root: Path
    provider: str
    capability_tier: CapabilityTier
    test_suite: tuple[str, ...] = DEFAULT_TEST_SUITE
    suite_detected: str = ""
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
    suite, because = _suite(Path(root), tracked)
    return ProjectContext(
        root=Path(root),
        provider=provider,
        capability_tier=DEFAULT_CAPABILITIES.get(provider, CapabilityTier.FRAGMENT),
        test_suite=suite,
        suite_detected=because,
        languages=_languages(tracked),
        plugins=tuple(plugins),
    )


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
    # The argument vector the `tests` Gate runs, in this repository.
    # {because}
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


def _suite(root: Path, tracked: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    """The suite this repository appears to run, and the evidence for it.

    Ordered by how specific the evidence is rather than by popularity: a
    repository declaring a pytest section is telling us directly, and one with a
    `tests/` directory is telling us by convention. A repository that shows
    nothing gets the documented default and is told it was a default — guessing
    silently is how a Gate ends up running the wrong command for a month.
    """
    files = set(tracked)

    text = _read(root / "pyproject.toml")
    if "[tool.pytest" in text:
        return ("pytest",), "a `[tool.pytest]` section in pyproject.toml"

    if "pytest.ini" in files or "conftest.py" in files:
        return ("pytest",), "pytest configuration at the repository root"

    if any(path.startswith("tests/") for path in files):
        return ("pytest",), "a `tests/` directory"

    package = _read(root / "package.json")
    if '"test"' in package:
        return ("npm", "test"), "a `test` script in package.json"

    if "go.mod" in files:
        return ("go", "test", "./..."), "a go.mod at the repository root"

    if "Cargo.toml" in files:
        return ("cargo", "test"), "a Cargo.toml at the repository root"

    return DEFAULT_TEST_SUITE, ""


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
    "ProjectContext",
    "config_path",
    "detect",
    "differences",
    "render_config",
]
