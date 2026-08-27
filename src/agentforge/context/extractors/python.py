"""What a Python module defines, and what it imports.

Parsed with `ast` rather than matched with regular expressions. A regex that
finds `def` also finds it in a docstring, and a Context Pack that names symbols
which are not there costs a Role the tokens to discover that.

A module that will not parse yields nothing. Half a syntax tree is a worse
answer than no answer, and the file is still carried in the pack by path.
"""

from __future__ import annotations

import ast
import sys

from .base import Extraction, ordered

#: Methods of a class are carried as `Class.method`, one level deep. A nested
#: function is a detail of its parent and is not a place a Plan sends anybody.
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def extract(text: str) -> Extraction:
    """The module's top-level definitions and the modules it imports."""
    tree = ast.parse(text)

    symbols: list[str] = []
    references: list[str] = []

    for node in tree.body:
        if isinstance(node, _DEFINITIONS):
            symbols.append(node.name)
            if isinstance(node, ast.ClassDef):
                symbols.extend(
                    f"{node.name}.{child.name}"
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            references.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # A relative import keeps its dots: `..core.contracts` says which
            # package the file belongs to, and `core.contracts` would not.
            references.append("." * node.level + (node.module or ""))

    return Extraction(
        symbols=ordered(symbols),
        references=ordered(name for name in references if not _stdlib(name)),
    )


def _stdlib(name: str) -> bool:
    """Whether an import names the standard library.

    Dropped, because a reference is what a change can break and nothing in a
    Plan breaks `pathlib`. It is also most of what a typical module imports, so
    carrying it would spend the pack's budget on the one part of it every Role
    already knows.
    """
    return name.partition(".")[0] in sys.stdlib_module_names


__all__ = ["extract"]
