"""The SQL Plugin: what a `.sql` file means when dbt is what builds it.

Deliberately two Extractors, one Gate kind, and no Fragment. The conventions a
SQL change is held to are a `sql` Fragment's job and nobody has written one
worth a Role's tokens yet; what this Plugin has that nothing else does is
knowledge of what a dbt model *depends on* and of what it means for a project to
still parse — one belongs in the pack and the other in a Gate, and neither
belongs in a prompt.

The built-in SQL extractor reads tables out of a statement. In a dbt project
that reading is not wrong so much as beside the point: a model says
`{{ ref('stg_orders') }}` and compiles to a table name this repository does not
contain, so a generic read finds either nothing or the wrong thing. What breaks
when the model changes is the models that `ref()` it, and that is what a Role
needs to be told.

The `dbt` Gate is the worked example of a validator. A Workflow in a dbt project
writes `gate: dbt` after the Step that edits models, and the Run holds there
until the project parses — the same YAML a Workflow writes for `tests`, and a
Workflow in a repository this Plugin does not answer for is refused at load
time, because nothing there would evaluate it.

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
from ...core.contracts import (
    Command,
    Extractor,
    FileTemplate,
    GateEntry,
    GateVerdict,
    Plugin,
    Validator,
)
from ...core.gates import GateContext, command_tail
from ...core.process import MissingBinary

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


#: What the `dbt` Gate runs. `parse` rather than `build` or `test`: parsing
#: resolves every `ref()` and `source()` and compiles every model without
#: touching a warehouse, so the Gate holds a Run on a project that no longer
#: hangs together without needing a connection, a profile, or data.
DBT_PARSE = ("dbt", "parse")

#: The status dbt spends on "it ran and the project has a problem", which is a
#: report on the repository. It spends 2 on a usage error — no project here, a
#: flag it does not know — and that is a report on the invocation instead.
DBT_FAILED = 1


def parses(context: GateContext) -> GateEntry:
    """The dbt Gate: parse the project, and read the exit status.

    The same three answers the test-suite Gate gives, for the same reasons. It
    parsed. It ran and found the project broken — a model referencing one that
    was renamed, a macro that no longer resolves — which the next commit can
    fix, so the Run suspends rather than halting. Or it never reached a verdict,
    and a Gate with nothing to clear halts the Run rather than inviting a resume
    that suspends again.

    It names nobody in `invalidates`. This verdict comes from re-running dbt
    against the working tree rather than from reading what a Role said about it,
    so no Step's output has been judged and every Step behind this Gate stays
    behind it (ADR-0008).

    A Plugin's Gate degrades a Run and never ends it: dbt missing from the
    machine, or refusing to start, is an errored verdict rather than an
    exception, which is what the runtime has a way of reporting.
    """
    if not context.runner.has_binary(DBT_PARSE[0]):
        return _cannot_parse(f"{DBT_PARSE[0]!r} is not installed or not on PATH")

    try:
        result = context.runner.run(DBT_PARSE, cwd=context.root)
    except MissingBinary as exc:
        return _cannot_parse(str(exc))

    if result.ok:
        return GateEntry(
            kind="",
            verdict=GateVerdict.CLEARED,
            summary="`dbt parse` resolved the project.",
        )

    if result.returncode == DBT_FAILED:
        return GateEntry(
            kind="",
            verdict=GateVerdict.BLOCKED,
            summary=(
                "`dbt parse` failed, so the project does not resolve. The Run stops "
                f"here rather than carrying that to Sign-off.\n\n{command_tail(result)}"
            ),
        )

    return GateEntry(
        kind="",
        verdict=GateVerdict.ERRORED,
        summary=(
            f"`dbt parse` exited {result.returncode}, which is not a report on the "
            "project: it did not run to a verdict, so there is nothing here for a "
            f"later Run to clear.\n\n{command_tail(result)}"
        ),
    )


def _cannot_parse(reason: str) -> GateEntry:
    """dbt never started, which says nothing about the project.

    Errored rather than blocked, for the reason the test-suite Gate errors:
    waiting clears nothing, and what has to change is the machine rather than
    the repository.
    """
    return GateEntry(
        kind="",
        verdict=GateVerdict.ERRORED,
        summary=(
            f"the dbt Gate cannot run `{' '.join(DBT_PARSE)}`: {reason}. Install dbt "
            "where the Run executes, or drop the `dbt` Gate from this Workflow."
        ),
    )


#: The model a scaffold writes. Deliberately a shape rather than a guess: a
#: staging CTE and a final select is what a reviewer expects to read, and every
#: decision that needs a person — what it selects from, what it filters, what it
#: is materialized as — is left where they will see it rather than filled in
#: with something plausible. `$$name` in a template is a literal dollar; `$name`
#: is the argument.
_MODEL_SQL = """with source as (

    select * from {{ ref('stg_$name') }}

),

renamed as (

    select
        -- Name the columns this model exposes. `select *` here is how a
        -- downstream break becomes a surprise.
        *

    from source

)

select * from renamed
"""

#: The schema entry beside it. A model with no description and no test is the
#: thing `dbt parse` is happy with and a reviewer is not, so the scaffold writes
#: the places both belong and fills in neither.
_MODEL_YML = """version: 2

models:
  - name: $name
    description: ""
    columns:
      - name: id
        description: ""
        data_tests:
          - unique
          - not_null
"""

#: The one chore this Plugin knows: two files, in the places dbt looks for them,
#: with no inference anywhere. Writing them by asking a Role at `standard` tier
#: is the most expensive way to produce a file whose shape was never in question.
SCAFFOLD_MODEL = Command(
    name="scaffold-dbt-model",
    summary="Write a dbt model and the schema entry beside it.",
    arguments=("name",),
    templates=(
        FileTemplate(path="models/$name.sql", text=_MODEL_SQL),
        FileTemplate(path="models/$name.yml", text=_MODEL_YML),
    ),
)


SQL = Plugin(
    name="sql",
    suffixes=(".sql",),
    root_markers=("dbt_project.yml", "dbt_project.yaml"),
    extractors=(
        Extractor(suffixes=(".sql",), read=extract_model),
        Extractor(suffixes=(".yml", ".yaml"), read=extract_schema),
    ),
    validators=(Validator(kind="dbt", check=parses),),
    commands=(SCAFFOLD_MODEL,),
)

__all__ = [
    "DBT_FAILED",
    "DBT_PARSE",
    "MAX_ITEMS",
    "SCAFFOLD_MODEL",
    "SQL",
    "extract_model",
    "extract_schema",
    "parses",
]
