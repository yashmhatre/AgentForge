"""The SQL Plugin: what a `.sql` file means when dbt is what builds it.

Deliberately two Extractors and no Fragment. The conventions a SQL change is
held to are a `sql` Fragment's job and nobody has written one worth a Role's
tokens yet; what this Plugin has that nothing else does is knowledge of what a
dbt model *depends on*, and that belongs in the pack rather than in a prompt.

The built-in SQL extractor reads tables out of a statement. In a dbt project
that reading is not wrong so much as beside the point: a model says
`{{ ref('stg_orders') }}` and compiles to a table name this repository does not
contain, so a generic read finds either nothing or the wrong thing. What breaks
when the model changes is the models that `ref()` it, and that is what a Role
needs to be told.

Detection and reading are separate, which is why `suffixes` here is `.sql`
alone. A repository is a dbt project because of `dbt_project.yml`, and a Plan
touching a `.sql` file is doing SQL work wherever it is done. Neither of those
is a reason to activate on every `.yml` in every repository — but once this
Plugin *is* active, the schema files beside the models are worth reading as
what they are, so the YAML Extractor claims those suffixes and hands back
anything that is not dbt-shaped to the reader that already handles it.
"""

from __future__ import annotations

import re

import yaml as pyyaml

from ...context.extractors import sql as builtin_sql
from ...context.extractors import yaml as builtin_yaml
from ...context.extractors.base import Extraction, ordered
from ...core.contracts import Extractor, Plugin

#: `ref('model')`, `ref("model")`, and the two-argument `ref('package', 'model')`
#: that a model in an installed package is reached by. The last argument is the
#: model either way, which is why the first is captured and discarded.
_REF = re.compile(
    r"""\bref\s*\(\s*['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]+)['"]\s*)?\)""",
    re.IGNORECASE,
)

#: `source('name', 'table')`. Both halves matter and both are carried: the
#: source name alone would not say which table, and the table alone would not
#: say which source declared it.
_SOURCE = re.compile(
    r"""\bsource\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""",
    re.IGNORECASE,
)

#: How many entries of a list in a schema file are read. The same bound the
#: built-in YAML extractor applies, and for the same reason: a `models:` list of
#: two hundred has the shape of a list of three.
MAX_ITEMS = builtin_yaml.MAX_ITEMS


def extract_model(text: str) -> Extraction:
    """A dbt model's dependencies as references, on top of the generic read.

    Composed rather than replacing: a model is still SQL, and the columns the
    built-in extractor finds are still where a change lands. What this adds is
    the edges — `ref()` and `source()` targets, carried in front of the compiled
    table names, because they are the names the repository actually contains and
    so the ones a Role can go and read.

    A file with no `ref()` and no `source()` in it extracts exactly what the
    built-in extractor extracts. That is the ordinary case in a repository that
    has one `.sql` file and no dbt, and it costs that repository nothing.
    """
    dependencies: list[str] = []

    for package, model in _REF.findall(text):
        # Two-argument `ref` captures ('package', 'model'); one-argument
        # captures ('model', ''). The model is the last non-empty group.
        dependencies.append(f"ref:{model or package}")

    for source, table in _SOURCE.findall(text):
        dependencies.append(f"source:{source}.{table}")

    generic = builtin_sql.extract(text)
    return Extraction(
        symbols=generic.symbols,
        references=ordered([*dependencies, *generic.references]),
    )


def extract_schema(text: str) -> Extraction:
    """A dbt schema file read as dbt, or as ordinary YAML when it is not one.

    A `schema.yml` and a CI config are both YAML and are not both worth the same
    reading. Read generically, a model's name, one of its column's names, and
    the name of a test on that column are three keys at three depths and nothing
    distinguishes them. Read as dbt they are three different kinds of thing:

    - a model or a source table is a **symbol**, because it is the thing a Plan
      sends somebody to change
    - a column is a **symbol**, qualified by the model it belongs to, so that
      two models with an `id` are two symbols rather than one
    - a **test** is neither. It is carried as a reference, because a test is what
      breaks when the column changes, which is the question `references` answers

    Anything without a dbt shape falls through to the built-in YAML extractor.
    That fall-through is what makes claiming `.yml` safe: this Plugin activates
    on a `dbt_project.yml` at the root, and a repository that has one still has
    ordinary YAML in it that nobody wants read as dbt.
    """
    document = pyyaml.safe_load(text)
    if not _is_dbt_schema(document):
        return builtin_yaml.extract(text)

    symbols: list[str] = []
    references: list[str] = []

    for key in ("models", "seeds", "snapshots"):
        for node in _entries(document.get(key)):
            name = _name(node)
            if not name:
                continue
            symbols.append(name)
            _read_columns(node, name, symbols, references)

    for source in _entries(document.get("sources")):
        source_name = _name(source)
        if not source_name:
            continue
        for table in _entries(source.get("tables")):
            table_name = _name(table)
            if not table_name:
                continue
            # A source table is a symbol the same way a model is — it is a named
            # thing in this file — and its `source:` reference is what a model
            # reaching for it will have written.
            qualified = f"{source_name}.{table_name}"
            symbols.append(qualified)
            references.append(f"source:{qualified}")
            _read_columns(table, qualified, symbols, references)

    return Extraction(symbols=ordered(symbols), references=ordered(references))


def _is_dbt_schema(document) -> bool:
    """Whether this document is a dbt schema file rather than any other YAML.

    A mapping carrying at least one of dbt's node lists. `version: 2` is not the
    test: plenty of YAML declares a version, and a schema file that omits it is
    still a schema file.
    """
    return isinstance(document, dict) and any(
        isinstance(document.get(key), list)
        for key in ("models", "sources", "seeds", "snapshots")
    )


def _read_columns(node, owner: str, symbols: list[str], references: list[str]) -> None:
    """A node's columns as `owner.column`, and its tests as references."""
    for test in _tests(node):
        references.append(f"test:{test} on {owner}")

    for column in _entries(node.get("columns")):
        name = _name(column)
        if not name:
            continue
        symbols.append(f"{owner}.{name}")
        for test in _tests(column):
            references.append(f"test:{test} on {owner}.{name}")


def _tests(node) -> list[str]:
    """The tests declared on one node, under either spelling.

    dbt renamed `tests:` to `data_tests:` and reads both, so this reads both. A
    test is a mapping when it takes arguments (`relationships:` with a `to:`)
    and a string when it does not, and the name is what matters either way.
    """
    named: list[str] = []
    for key in ("tests", "data_tests"):
        for test in _entries(node.get(key)) if isinstance(node, dict) else ():
            if isinstance(test, str):
                named.append(test)
            elif isinstance(test, dict) and test:
                named.append(str(next(iter(test))))
    return named


def _entries(value) -> list:
    """The first `MAX_ITEMS` of a list, or nothing at all if it is not one."""
    return value[:MAX_ITEMS] if isinstance(value, list) else []


def _name(node) -> str:
    """A node's `name:`, or empty where it has none."""
    if not isinstance(node, dict):
        return ""
    name = node.get("name")
    return str(name).strip() if name is not None else ""


SQL = Plugin(
    name="sql",
    suffixes=(".sql",),
    root_markers=("dbt_project.yml", "dbt_project.yaml"),
    extractors=(
        Extractor(suffixes=(".sql",), read=extract_model),
        Extractor(suffixes=(".yml", ".yaml"), read=extract_schema),
    ),
)

__all__ = ["MAX_ITEMS", "SQL", "extract_model", "extract_schema"]
