"""Context Packs: what an extractor claims, and what the resolver hands over.

Two properties are worth more than the rest and both are asserted directly. An
extractor must never claim something a file does not contain — a pack that names
a symbol which is not there costs a Role the tokens to discover that, which is
the opposite of the point. And the resolver must be bounded and deterministic:
ADR-0010 makes two Runs of one Issue comparable to each other, and that is the
only way to find out whether a pack saved anything.

The extractors are tested against recorded files in `fixtures/` rather than
against strings written in the assertions, so a fixture that stops parsing is a
failure here rather than a surprise in a Run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentforge_framework.agents import (
    architect,
    implementer,
    reviewer,
    security,
    tester,
)
from agentforge_framework.context.extractors import Extraction, extract, extractor_for
from agentforge_framework.context.extractors import python as python_extractor
from agentforge_framework.context.extractors import sql as sql_extractor
from agentforge_framework.context.extractors import yaml as yaml_extractor
from agentforge_framework.context.prompt import render_context_block
from agentforge_framework.context.resolver import (
    MAX_FILES,
    MAX_SYMBOLS_PER_FILE,
    resolve_pack,
)
from agentforge_framework.core.contracts import ContextPack, Plan, PlanStep

FIXTURES = Path(__file__).parent / "fixtures"


def recorded(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def a_plan(*files: str) -> Plan:
    return Plan(
        summary="add a retry to the loader",
        steps=(PlanStep(id="s1", intent="wrap the fetch", files=tuple(files)),),
    )


def a_repository(tmp_path: Path, **files: str) -> Path:
    """A repository with the named files in it. `__` in a name is a directory."""
    for name, text in files.items():
        path = tmp_path / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


# --- the Python extractor --------------------------------------------------


def test_a_python_module_yields_what_it_defines_including_class_methods():
    extraction = python_extractor.extract(recorded("extract_module.py"))

    assert "load" in extraction.symbols
    assert "Loader" in extraction.symbols
    assert "Loader.read" in extraction.symbols
    assert "Loader.read_async" in extraction.symbols


def test_a_function_defined_inside_another_is_not_a_place_a_plan_sends_anybody():
    """`parse` is a detail of `load`. A pack that named it would be pointing a
    Role at something it cannot reach."""
    extraction = python_extractor.extract(recorded("extract_module.py"))

    assert "parse" not in extraction.symbols


def test_a_python_module_reaches_for_its_dependencies_and_not_the_standard_library():
    """A reference is what a change can break, and nothing in a Plan breaks
    `pathlib`. Carrying it would spend the pack on what every Role knows."""
    extraction = python_extractor.extract(recorded("extract_module.py"))

    assert "pyyaml_stand_in" in extraction.references
    assert "agentforge_framework.core.contracts" in extraction.references
    assert "json" not in extraction.references
    assert "pathlib" not in extraction.references


def test_a_module_that_will_not_parse_claims_nothing_rather_than_half_of_it():
    assert extract("broken.py", "def load(:") == Extraction()


# --- the SQL extractor -----------------------------------------------------


def test_a_query_names_the_tables_it_reads_from_and_writes_to():
    extraction = sql_extractor.extract(recorded("extract_query.sql"))

    assert "analytics.orders" in extraction.references
    assert "analytics.daily_revenue" in extraction.references
    assert "customers" in extraction.references


def test_a_table_named_only_in_a_comment_or_a_string_is_not_one_the_query_touches():
    """The one thing an extractor must never do is invent a reference. A Role
    told the query reads `payments_backup` goes looking for a join that is not
    there."""
    extraction = sql_extractor.extract(recorded("extract_query.sql"))

    assert not any("payments_backup" in reference for reference in extraction.references)


def test_a_query_names_the_columns_a_role_would_be_changing():
    extraction = sql_extractor.extract(recorded("extract_query.sql"))

    assert "order_id" in extraction.symbols
    assert "customer_name" in extraction.symbols
    assert "total" in extraction.symbols
    # `SUM` is a function, not a column. Reading one as the other puts a keyword
    # in the pack and sends a Role looking for it.
    assert "SUM" not in extraction.symbols


# --- the YAML extractor ----------------------------------------------------


def test_a_config_file_yields_the_keys_it_sets_as_dotted_paths():
    """The shape without the values: a Role learns where a setting lives without
    opening the file, and the value is what it is about to change anyway."""
    extraction = yaml_extractor.extract(recorded("extract_config.yaml"))

    assert "providers.claude.capability_tier" in extraction.symbols
    assert "gates.tests.suite" in extraction.symbols
    assert "workflows.name" in extraction.symbols


def test_yaml_that_will_not_parse_degrades_to_nothing():
    assert extract("config.yaml", "providers:\n  - [unclosed") == Extraction()


# --- unknown file types ----------------------------------------------------


def test_a_file_type_nobody_wrote_an_extractor_for_makes_no_claims():
    assert extractor_for("notes.rst") is None
    assert extract("notes.rst", "Whatever this is") == Extraction()


def test_an_unfamiliar_language_still_reaches_the_pack_by_path(tmp_path):
    """Degrading the pack rather than failing the Run. A Role handed the path
    can read the file; a Run that raised on it can do nothing at all."""
    root = a_repository(tmp_path, **{"pipeline.scala": "object Pipeline {}"})

    pack = resolve_pack(a_plan("pipeline.scala"), root)

    assert pack.files == ("pipeline.scala",)
    assert pack.symbols == ()


# --- the resolver ----------------------------------------------------------


def test_the_pack_carries_the_files_the_plan_names_in_the_order_it_names_them(tmp_path):
    root = a_repository(
        tmp_path,
        **{"src__loader.py": "def fetch():\n    pass", "query.sql": "SELECT id FROM orders"},
    )

    pack = resolve_pack(a_plan("src/loader.py", "query.sql"), root)

    assert pack.files == ("src/loader.py", "query.sql")


def test_a_symbol_is_qualified_by_the_file_it_was_read_out_of(tmp_path):
    """A Role handed a thousand-line module is pointed at the function the Plan
    is about, which needs the file as much as the name."""
    root = a_repository(tmp_path, **{"src__loader.py": "def fetch():\n    pass"})

    pack = resolve_pack(a_plan("src/loader.py"), root)

    assert pack.symbols == ("src/loader.py::fetch",)


def test_a_file_the_plan_names_and_the_repository_lacks_is_still_carried(tmp_path):
    """It is the file the Run is about to write. A pack that dropped it would
    disagree with the Plan handed to the same Role in the same prompt."""
    pack = resolve_pack(a_plan("src/new_loader.py"), a_repository(tmp_path))

    assert pack.files == ("src/new_loader.py",)
    assert pack.symbols == ()


def test_the_orchestrators_own_pack_survives_resolution(tmp_path):
    """Its conventions are a judgement no extractor produces, and its files are
    the supporting reading it did while planning."""
    declared = ContextPack(files=("docs/adr/0003.md",), conventions=("no new dependencies",))

    pack = resolve_pack(a_plan("src/loader.py"), a_repository(tmp_path), declared)

    assert pack.files == ("src/loader.py", "docs/adr/0003.md")
    assert pack.conventions == ("no new dependencies",)


@pytest.mark.parametrize("escape", ["../secrets.env", "/etc/passwd", "src/../../outside.py"])
def test_a_path_that_escapes_the_repository_resolves_to_nothing(tmp_path, escape):
    """An Issue body is editable by anybody who can comment on it, so a resolver
    that read whatever it was pointed at would be the way in."""
    assert resolve_pack(a_plan(escape), a_repository(tmp_path)).files == ()


def test_the_pack_refuses_to_grow_without_bound(tmp_path):
    """A Plan touching sixty files must not produce a pack larger than the
    repository, or the pack costs more than the rediscovery it replaces."""
    names = [f"module_{index:03d}.py" for index in range(MAX_FILES + 20)]
    root = a_repository(tmp_path, **{name: "def f():\n    pass" for name in names})

    pack = resolve_pack(a_plan(*names), root)

    assert len(pack.files) == MAX_FILES


def test_one_enormous_file_does_not_become_the_whole_pack(tmp_path):
    body = "\n".join(f"def f{index}():\n    pass" for index in range(MAX_SYMBOLS_PER_FILE * 3))
    root = a_repository(tmp_path, **{"huge.py": body})

    pack = resolve_pack(a_plan("huge.py"), root)

    assert len(pack.symbols) == MAX_SYMBOLS_PER_FILE


def test_the_same_plan_against_the_same_repository_resolves_to_the_same_pack(tmp_path):
    """Two Runs of one Issue have to be comparable to each other, which is the
    whole of the measurement ADR-0010 promises."""
    root = a_repository(
        tmp_path,
        **{
            "src__loader.py": recorded("extract_module.py"),
            "query.sql": recorded("extract_query.sql"),
            "config.yaml": recorded("extract_config.yaml"),
        },
    )
    plan = a_plan("src/loader.py", "query.sql", "config.yaml")

    assert resolve_pack(plan, root) == resolve_pack(plan, root)


def test_a_pack_survives_a_round_trip_through_an_issue_body(tmp_path):
    """A Run resumed from an issue number is handed the pack the Run that filed
    it wrote down: ADR-0002's claim, applied to ADR-0010's artifact."""
    from agentforge_framework.agents import resolve_role
    from agentforge_framework.core.contracts import PlanDocument, Roster, Task
    from agentforge_framework.core.plan_format import (
        parse_issue_body,
        render_issue_body,
    )

    root = a_repository(tmp_path, **{"src__loader.py": recorded("extract_module.py")})
    plan = a_plan("src/loader.py")
    pack = resolve_pack(plan, root)
    document = PlanDocument(
        plan=plan, roster=Roster((implementer.IMPLEMENTER,)), context=pack
    )

    body = render_issue_body(Task("add a retry"), document)

    assert parse_issue_body(body, resolve_role).context == pack


# --- what a Role is handed -------------------------------------------------


def test_the_block_tells_a_role_the_pack_is_a_head_start_and_not_a_boundary():
    """The reason a resolver mistake costs tokens rather than correctness: a
    Role that reads the pack as the whole repository stops looking."""
    block = render_context_block(ContextPack(files=("src/loader.py",)))

    assert "src/loader.py" in block
    assert "read that file" in block


def test_an_empty_pack_renders_no_heading_at_all():
    """A Run with no pack should not spend its prompt saying so."""
    assert render_context_block(ContextPack()) == ""


@pytest.mark.parametrize("module", [implementer, architect, tester, security, reviewer])
def test_every_role_is_handed_the_pack(module):
    """Six Agents rediscovering the same files is what the pack exists to stop,
    so a Role whose prompt drops it is a Role paying for the rediscovery."""
    pack = ContextPack(files=("src/loader.py",), symbols=("src/loader.py::fetch",))

    prompt = module.build_prompt(a_plan("src/loader.py"), pack, Path("/repo"))

    assert "## Context Pack" in prompt
    assert "`src/loader.py` — fetch" in prompt


def test_symbols_are_grouped_under_the_file_they_came_from():
    """The pack stores a symbol qualified, because that is what survives an
    Issue body. Spending the prompt on twenty-five copies of one path would be
    paying in the currency this whole mechanism exists to save."""
    pack = ContextPack(
        files=("src/loader.py",),
        symbols=("src/loader.py::fetch", "src/loader.py::Loader.read"),
    )

    block = render_context_block(pack)

    assert "- `src/loader.py` — fetch, Loader.read" in block
    assert "src/loader.py::" not in block


def test_a_symbol_the_orchestrator_named_without_a_file_still_reaches_the_role():
    """It declares symbols while it plans and does not always say where they
    live. Dropping those throws away the one part of the pack a human wrote."""
    block = render_context_block(ContextPack(files=("src/loader.py",), symbols=("load",)))

    assert "**The work is in these symbols:** load" in block


def test_a_file_no_extractor_claims_is_a_line_with_no_symbols_after_it():
    block = render_context_block(ContextPack(files=("pipeline.scala",)))

    assert "- `pipeline.scala`" in block
    assert "—" not in block
