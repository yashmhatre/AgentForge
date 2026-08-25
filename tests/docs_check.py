"""A conformance check over a documentation tree.

`docs/agents/domain.md` sends every engineering skill to `CONTEXT.md` before it
explores a repository, which makes the glossary's format a contract rather than
a preference. The ADR record has the same problem from the other side: this
framework exists to run several agents against one repository, so two of them
each writing `0007` is a predictable failure rather than a hypothetical one.

`check_documentation` is a pure function from a repository root to a list of
findings. It reads the filesystem and nothing else — no process, no network, no
configuration — so it needs no test double and adds no seam to the codebase.

It reports every independent problem rather than stopping at the first, because
the value of a check that runs after everyone has forgotten the rules is in the
whole list. A glossary that will not parse at all is the one exception: it
yields a single finding, since every term-level check downstream of it would be
noise.

It lives in the test suite deliberately. There is no CLI subcommand and no
continuous integration wiring; both are cheap to add once the shape settles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

GLOSSARY_FILE = "CONTEXT.md"
ADR_DIR = "docs/adr"

_LANGUAGE_HEADING = re.compile(r"^##\s+Language\s*$")
_SECTION_HEADING = re.compile(r"^##(?!#)")
_TERM = re.compile(r"^\*\*(.+?)\*\*:\s*(.*)$")
_AVOID = re.compile(r"^_Avoid_:\s*(.*)$")
_ADR_FILENAME = re.compile(r"^(\d+)-.+\.md$")
_TITLE = re.compile(r"^#(?!#)\s*(.*)$")


@dataclass(frozen=True)
class Term:
    """One glossary entry: what the word means, and what not to call it."""

    name: str
    definition: str
    avoid: tuple[str, ...]


@dataclass(frozen=True)
class Finding:
    """One broken property, named so that fixing it needs no reading of this file."""

    file: str
    problem: str


def check_documentation(root: Path) -> list[Finding]:
    """Every way this documentation tree has stopped conforming."""
    return _check_glossary(root) + _check_adrs(root)


def parse_glossary(text: str) -> list[Term]:
    """The terms defined under `## Language`, in the order they appear.

    Terms may be grouped under `###` subheadings; anything after the next `##`
    heading belongs to another section and is not vocabulary.
    """
    terms: list[Term] = []
    name: str | None = None
    definition: list[str] = []
    avoid: tuple[str, ...] = ()
    in_language = False

    def flush() -> None:
        nonlocal name, definition, avoid
        if name is not None:
            terms.append(Term(name, " ".join(definition).strip(), avoid))
        name, definition, avoid = None, [], ()

    for line in text.splitlines():
        if _SECTION_HEADING.match(line):
            flush()
            in_language = bool(_LANGUAGE_HEADING.match(line))
            continue
        if not in_language:
            continue

        term = _TERM.match(line)
        if term:
            flush()
            name = term.group(1).strip()
            definition = [term.group(2).strip()] if term.group(2).strip() else []
            continue
        if name is None:
            continue

        avoided = _AVOID.match(line)
        if avoided:
            avoid = tuple(w.strip() for w in avoided.group(1).split(",") if w.strip())
            continue
        if line.startswith("###"):
            flush()
            continue
        if line.strip():
            definition.append(line.strip())

    flush()
    return terms


def _check_glossary(root: Path) -> list[Finding]:
    path = root / GLOSSARY_FILE
    if not path.is_file():
        return [Finding(GLOSSARY_FILE, "not found; the skills read this file before exploring")]

    text = path.read_text(encoding="utf-8")
    if not any(_LANGUAGE_HEADING.match(line) for line in text.splitlines()):
        return [Finding(GLOSSARY_FILE, "no `## Language` section; the glossary will not parse")]

    terms = parse_glossary(text)
    if not terms:
        return [Finding(GLOSSARY_FILE, "the `## Language` section defines no terms")]

    return [
        Finding(GLOSSARY_FILE, f"term `{term.name}` has no definition")
        for term in terms
        if not term.definition
    ]


def _check_adrs(root: Path) -> list[Finding]:
    directory = root / ADR_DIR
    if not directory.is_dir():
        return [Finding(ADR_DIR, "not found; the decision record has no home")]

    numbered: dict[int, str] = {}
    findings: list[Finding] = []

    for path in sorted(directory.iterdir()):
        match = _ADR_FILENAME.match(path.name)
        if not path.is_file() or not match:
            continue

        relative = f"{ADR_DIR}/{path.name}"
        number = int(match.group(1))

        if number < 1:
            findings.append(Finding(relative, "ADR numbers start at 0001"))
        elif number in numbered:
            findings.append(
                Finding(relative, f"ADR number {number:04d} is already taken by {numbered[number]}")
            )
        else:
            numbered[number] = path.name

        if not _has_title(path):
            findings.append(Finding(relative, "no title; the first heading must be `# ...`"))

    if not numbered:
        return [*findings, Finding(ADR_DIR, "holds no numbered ADRs")]

    findings += [
        Finding(ADR_DIR, f"ADR number {missing:04d} is missing; the sequence runs from 0001")
        for missing in range(1, max(numbered) + 1)
        if missing not in numbered
    ]
    return findings


def _has_title(path: Path) -> bool:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        title = _TITLE.match(line)
        return bool(title and title.group(1).strip())
    return False


__all__ = ["ADR_DIR", "GLOSSARY_FILE", "Finding", "Term", "check_documentation", "parse_glossary"]
