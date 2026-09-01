"""Per-language readers that turn one file into what it defines and what it uses.

An extractor answers two questions about a single file and nothing else: what
does it define, and what does it reach for. It never opens a second file, never
resolves an import, and never decides what belongs in a Context Pack — that is
`context.resolver`'s job, and keeping the two apart is what lets a language be
added as one small module with one fixture.

A file type nobody wrote an extractor for is not an error. `extractor_for`
returns `None`, the resolver carries the path, and nothing is claimed about the
contents — an unfamiliar language degrades the pack rather than failing the Run.

Every extractor takes text rather than a path. Reading is the resolver's job, so
these are pure functions and their tests need no repository.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from . import python as python_extractor
from . import sql as sql_extractor
from . import yaml as yaml_extractor
from .base import Extraction

#: Suffix to extractor. Lowercased before lookup, because a repository written
#: on Windows has `.SQL` files in it and they are the same language.
EXTRACTORS: dict[str, Callable[[str], Extraction]] = {
    ".py": python_extractor.extract,
    ".sql": sql_extractor.extract,
    ".yaml": yaml_extractor.extract,
    ".yml": yaml_extractor.extract,
}


def extractor_for(
    path: str | Path, extractors: Mapping[str, Callable[[str], Extraction]] | None = None
) -> Callable[[str], Extraction] | None:
    """The extractor for this path's file type, or `None` if nobody wrote one.

    `extractors` is the table to look in, defaulting to the built-in three. A
    Run with active Plugins passes a wider one, assembled by `core.registry`,
    and the widening is invisible from here: a Plugin's reader is looked up the
    same way and by the same suffix.
    """
    table = EXTRACTORS if extractors is None else extractors
    return table.get(Path(path).suffix.lower())


def extract(
    path: str | Path,
    text: str,
    extractors: Mapping[str, Callable[[str], Extraction]] | None = None,
) -> Extraction:
    """Read one file with whatever extractor its type has.

    An unknown type and a file that will not parse produce the same empty
    extraction on purpose: in both cases AgentForge has nothing to say about the
    contents, and inventing a difference between them would be inventing a
    claim.

    A Plugin's extractor is caught by the same `except` as a built-in one. A
    Plugin that raises costs the pack one file's contents, never the Run — the
    same bargain `core.registry` makes when a Plugin raises during activation.
    """
    reader = extractor_for(path, extractors)
    if reader is None:
        return Extraction()
    try:
        return reader(text)
    except Exception:  # noqa: BLE001 - a malformed file degrades the pack, never a Run
        return Extraction()


__all__ = ["EXTRACTORS", "Extraction", "extract", "extractor_for"]
