"""Turning a frozen Plan into the Context Pack its Agents are handed.

Six Roles run against one Issue, and before this existed each of them opened the
repository and rediscovered the same files. The frozen Plan already names what
the work touches (ADR-0003), so the reading can be done once, by AgentForge,
and handed to every Role. That is the whole idea; ADR-0010 records why it is
resolved here rather than declared by the Orchestrator or scanned per Step.

Three properties this module owes its callers:

- **Deterministic.** The same Plan against the same repository resolves to the
  same pack, so two Runs of one Issue can be compared to each other. Nothing
  here iterates a set or walks a directory in filesystem order.
- **Bounded.** A Plan naming forty files must not produce a pack larger than
  the repository, so every list has a cap and the caps are constants up here
  where a project can argue with them.
- **Inside the repository.** A Plan naming `../../.ssh/id_rsa` resolves to
  nothing. An Issue body is editable by anyone who can comment on it, and a
  resolver that read whatever it was pointed at would be the way in.

What it is not is a search. Nothing here guesses at files the Plan did not
name — a pack assembled from a fresh scan would drift between Steps, and the
frozen Plan exists so that it does not.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from ..core.contracts import ContextPack, Plan
from .extractors import extract
from .extractors.base import Extraction

#: How large a pack may get. These bound the pack rather than the repository:
#: the point of a Context Pack is that it is smaller than looking, and a cap of
#: a thousand symbols would be a pack nobody saved anything by reading.
MAX_FILES = 40
MAX_SYMBOLS = 120
MAX_SYMBOLS_PER_FILE = 25
MAX_REFERENCES = 60

#: Files past this size are carried by path and not read. A generated migration
#: or a checked-in dataset yields hundreds of symbols and no understanding, and
#: reading one is slower than every other thing this module does.
MAX_BYTES = 200_000


def resolve_pack(
    plan: Plan,
    root: Path | str,
    declared: ContextPack | None = None,
    extractors: Mapping[str, Callable[[str], Extraction]] | None = None,
) -> ContextPack:
    """The pack for this Plan: what it names, and what those files contain.

    `declared` is the Orchestrator's own pack, carried in the Issue body. It is
    kept rather than replaced — the Orchestrator read the repository while it
    planned, and its conventions are a judgement no extractor produces — and
    what the Plan's own steps name comes first, because that is the work.

    A file the Plan names and the repository does not have is still carried. It
    is a file the Run is about to create, and dropping it would leave a Role
    reading a pack that disagrees with the plan it was handed.

    `extractors` is the table to read with, and `None` means the built-in three.
    A Run with active Plugins passes the wider table `core.registry` assembles,
    which is the whole of how a Plugin's reader reaches a pack: nothing else in
    this module knows a Plugin exists, and the caps below fall on a Plugin's
    output exactly as they fall on a built-in extractor's.
    """
    root = Path(root)
    declared = declared or ContextPack()

    paths = _paths(plan, declared, root)
    symbols = list(declared.symbols)
    references: list[str] = list(declared.references)

    for path in paths:
        extraction = _read(root / path, extractors)
        symbols.extend(
            f"{path}::{name}" for name in extraction.symbols[:MAX_SYMBOLS_PER_FILE]
        )
        references.extend(extraction.references)

    return ContextPack(
        files=tuple(paths),
        symbols=_capped(symbols, MAX_SYMBOLS),
        references=_capped(references, MAX_REFERENCES),
        conventions=declared.conventions,
    )


def _paths(plan: Plan, declared: ContextPack, root: Path) -> list[str]:
    """Every file the Plan and the declared pack name, in that order.

    Plan first because a step's files are the work itself, and the cap should
    fall on the Orchestrator's supporting reading rather than on the thing the
    Run was filed to change.
    """
    named = [path for step in plan.steps for path in step.files]
    named += list(declared.files)

    resolved: dict[str, None] = {}
    for raw in named:
        path = inside(raw, root)
        if path is not None:
            resolved.setdefault(path, None)
    return list(resolved)[:MAX_FILES]


def inside(raw: str, root: Path) -> str | None:
    """The path as a repository-relative posix string, or `None` if it escapes.

    Absolute paths and `..` are refused rather than clamped. A Plan that names
    one is wrong about the repository, and a resolver that quietly reinterpreted
    it would hand a Role a file nobody asked for.
    """
    text = str(raw).strip().replace("\\", "/")
    if not text:
        return None

    candidate = Path(text)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None

    # Resolving both sides catches a symlink pointing out of the tree, which the
    # `..` check above does not see.
    try:
        (root / candidate).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None

    return candidate.as_posix().removeprefix("./")


def _read(
    path: Path, extractors: Mapping[str, Callable[[str], Extraction]] | None = None
) -> Extraction:
    """What one file contains, or an empty extraction if it cannot be read.

    A missing file, a directory, an unreadable one, and a file too large to be
    worth reading all land here, and all of them mean the same thing: the pack
    carries the path and claims nothing about the contents.
    """
    text = file_text(path)
    return extract(path, text, extractors) if text else Extraction()


def file_text(path: Path) -> str:
    """One file's text, or empty where reading it is not worth it or not possible.

    Public because `core.registry` reads the same files when it detects a Plugin
    by what the blast radius imports, and the two must agree about which files
    are readable. A detection that read a two-hundred-megabyte file the pack
    skips would be paying for an answer the pack never uses.
    """
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _capped(values, limit: int) -> tuple[str, ...]:
    """Deduplicated in first-seen order, then truncated to the cap."""
    seen: dict[str, None] = {}
    for value in values:
        text = str(value).strip()
        if text:
            seen.setdefault(text, None)
    return tuple(seen)[:limit]


__all__ = [
    "MAX_BYTES",
    "MAX_FILES",
    "MAX_REFERENCES",
    "MAX_SYMBOLS",
    "MAX_SYMBOLS_PER_FILE",
    "file_text",
    "inside",
    "resolve_pack",
]
