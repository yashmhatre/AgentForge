"""What keys a YAML file sets, as dotted paths.

A config file's shape is the useful thing about it — `gates.tests.suite` tells
a Role where a setting lives without opening the file. The values are not
carried: they are what the Role is about to change, and a pack that quoted them
would be a copy of the file with extra steps.

A file that will not parse yields nothing rather than failing. It is still
carried in the pack by path, and a Role that needs it reads it.
"""

from __future__ import annotations

import yaml as pyyaml

from .base import Extraction, ordered

#: How deep a dotted path is followed. Deep enough for a config file's shape,
#: shallow enough that a document nesting twenty levels does not become the
#: whole pack. The cap belongs here rather than in the resolver because it is
#: about what a key path means, not about how large a pack may grow.
MAX_DEPTH = 4

#: How many entries of a list are walked. A list of two hundred jobs has the
#: same shape as a list of two, and the pack only needs the shape.
MAX_ITEMS = 3


def extract(text: str) -> Extraction:
    """Every key the document sets, in the order the file writes them."""
    document = pyyaml.safe_load(text)
    return Extraction(symbols=ordered(_keys(document, prefix="", depth=1)))


def _keys(node, prefix: str, depth: int) -> list[str]:
    if depth > MAX_DEPTH:
        return []

    if isinstance(node, dict):
        found = []
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            found.append(path)
            found.extend(_keys(value, path, depth + 1))
        return found

    if isinstance(node, list):
        # A list index is not a key, so the prefix does not grow: two mappings
        # in a list of steps contribute the same paths, which is the shape.
        return [
            path
            for item in node[:MAX_ITEMS]
            for path in _keys(item, prefix, depth)
        ]

    return []


__all__ = ["MAX_DEPTH", "MAX_ITEMS", "extract"]
