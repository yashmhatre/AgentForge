"""What tables a statement reads from or writes to, and what columns it names.

There is no SQL parser here and there is deliberately not going to be one. A
Context Pack is a head start, so an extractor that is right about ordinary
statements and silent about exotic ones is worth more than a dependency and a
dialect argument. What it must never do is claim something that is not there,
which is why it strips comments and string literals before it looks: a table
name inside a quoted string is prose, not a reference.

Tables are references — what the query reads from and writes to. Columns are
symbols — the names inside it a change is likely to be about.
"""

from __future__ import annotations

import re

from .base import Extraction, ordered

#: An identifier as the dialects AgentBastion cares about write one:
#: `orders`, `analytics.orders`, `catalog.schema.table`, and the bracketed or
#: backticked forms of each.
_NAME = r'[A-Za-z_][\w$]*|"[^"]+"|`[^`]+`|\[[^\]]+\]'
_QUALIFIED = rf"(?:{_NAME})(?:\s*\.\s*(?:{_NAME}))*"

#: Where a table name follows a keyword. `INTO` covers both `INSERT INTO` and
#: `MERGE INTO`; `UPDATE` and `TABLE` cover the statements that name their
#: target without one.
_TABLE = re.compile(
    rf"\b(?:FROM|JOIN|INTO|UPDATE|TABLE|USING)\s+(?!SELECT\b)({_QUALIFIED})",
    re.IGNORECASE,
)

#: `alias.column` anywhere in the statement. The right-hand side is the column;
#: the left is an alias this extractor does not try to resolve back to a table.
_QUALIFIED_COLUMN = re.compile(rf"\b({_NAME})\s*\.\s*({_NAME})\b")

#: The projection of a `SELECT`, which is where a bare column name is a column
#: rather than a keyword. Bounded by the first `FROM` at any depth, because a
#: subquery in the projection is not what this is trying to read.
_SELECT = re.compile(r"\bSELECT\b(?:\s+DISTINCT\b)?(.*?)(?:\bFROM\b|$)", re.IGNORECASE | re.DOTALL)

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRINGS = re.compile(r"'(?:[^']|'')*'")

#: Words that appear where a column name would and are not one.
_NOT_A_COLUMN = frozenset(
    {"as", "case", "when", "then", "else", "end", "null", "distinct", "all", "and", "or", "not"}
)


def extract(text: str) -> Extraction:
    """The tables a statement touches and the columns it names."""
    statement = _STRINGS.sub("''", _COMMENTS.sub(" ", text))

    tables = [_unquote(match.group(1)) for match in _TABLE.finditer(statement)]
    columns = [_unquote(match.group(2)) for match in _QUALIFIED_COLUMN.finditer(statement)]

    for projection in _SELECT.findall(statement):
        columns.extend(_projected(projection))

    # A qualified name is `schema.table`, so its right-hand side reached the
    # column list as well. Dropping anything that is also a table keeps the pack
    # from telling a Role that `orders` is a column of itself.
    named = {_unquote(part) for table in tables for part in table.split(".")}
    return Extraction(
        symbols=tuple(column for column in ordered(columns) if column not in named),
        references=ordered(tables),
    )


def _projected(projection: str) -> list[str]:
    """The columns a `SELECT` list names, where it names them plainly.

    One bare or qualified identifier per item is read as a column. An expression
    or a function call is skipped rather than guessed at: `SUM(o.total)` has
    already given up `total` through the qualified-column pass, and reading
    `SUM` as a column would put a keyword in the pack.
    """
    columns = []
    for item in _split_top_level(projection):
        candidate = item.strip().rstrip(",").strip()
        if not candidate or "(" in candidate or candidate.endswith("*"):
            continue
        # `o.total AS revenue` is about `total`; the alias is a name the query
        # invents, and nothing downstream of the query is in the pack.
        head = candidate.split()[0]
        name = _unquote(head.rsplit(".", 1)[-1])
        if name and name.lower() not in _NOT_A_COLUMN and re.fullmatch(r"[\w$]+", name):
            columns.append(name)
    return columns


def _split_top_level(projection: str) -> list[str]:
    """Split a `SELECT` list on the commas that separate its items."""
    items, depth, start = [], 0, 0
    for index, char in enumerate(projection):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            items.append(projection[start:index])
            start = index + 1
    items.append(projection[start:])
    return items


def _unquote(name: str) -> str:
    """`"orders"`, `` `orders` ``, and `[orders]` are all `orders`."""
    name = " ".join(name.split()).replace(" . ", ".").replace(". ", ".").replace(" .", ".")
    for opening, closing in (('"', '"'), ("`", "`"), ("[", "]")):
        parts = [
            part[1:-1] if part.startswith(opening) and part.endswith(closing) else part
            for part in name.split(".")
        ]
        name = ".".join(parts)
    return name


__all__ = ["extract"]
