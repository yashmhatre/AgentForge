"""What an extractor hands back.

Its own module so that an extractor can import it without importing the
registry that imports every extractor. `providers/base.py` splits for the same
reason.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Extraction:
    """What one file defines, and what it reaches for outside itself.

    Two tuples rather than one, because a Role reading the pack asks different
    questions of them: `symbols` is where to make the change, and `references`
    is what the change can break. A Python module's functions and a YAML file's
    keys are both symbols; its imports and a query's source tables are both
    references.
    """

    symbols: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.symbols or self.references)


def ordered(names) -> tuple[str, ...]:
    """First occurrence wins, order preserved, blanks dropped.

    Every extractor needs this and every extractor needs it to behave the same
    way: ADR-0010 makes a pack's contents deterministic for a given Plan and
    repository, and a set would make the order of two symbols depend on the
    hash seed.
    """
    seen: dict[str, None] = {}
    for name in names:
        text = str(name).strip()
        if text:
            seen.setdefault(text, None)
    return tuple(seen)


__all__ = ["Extraction", "ordered"]
