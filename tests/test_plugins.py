"""The Plugin seam: what activates, what it contributes, and where it lands.

Two levels, on purpose. The unit tests below hold the registry to its three
promises — deterministic, bounded, survivable — against Plugins written here.
The Run-level tests hold the whole path honest by reading the prompt back out of
the argument vector the Provider was handed, because a Fragment that reaches a
`ContextPack` and not a prompt has contributed nothing.

Offline like everything else: the `python` Plugin declares no root markers, so
nothing here touches a filesystem that is not a `tmp_path`.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agentforge_framework.context.extractors import EXTRACTORS, Extraction
from agentforge_framework.context.prompt import render_context_block
from agentforge_framework.context.resolver import (
    MAX_BYTES,
    MAX_SYMBOLS_PER_FILE,
    resolve_pack,
)
from agentforge_framework.core.commands import run_command
from agentforge_framework.core.contracts import (
    Command,
    ContextPack,
    Extractor,
    FileTemplate,
    Fragment,
    GateEntry,
    GateVerdict,
    Plan,
    PlanStep,
    Plugin,
    Validator,
)
from agentforge_framework.core.gates import GATES, GateContext, evaluate_gate
from agentforge_framework.core.issues import render_context_comment
from agentforge_framework.core.process import MissingBinary
from agentforge_framework.core.registry import (
    MAX_FRAGMENT_CHARS,
    MAX_FRAGMENTS_PER_ROLE,
    Activation,
    activate,
    commands_for,
    contributions,
    extractors_for,
    fragments_for,
    gates_for,
)
from agentforge_framework.core.runtime import Forge, RunFailed, RunStatus
from agentforge_framework.core.workflow import WorkflowError, parse_workflow
from agentforge_framework.plugins import BUILT_IN
from agentforge_framework.plugins.databricks import DATABRICKS
from agentforge_framework.plugins.pyspark import PYSPARK
from agentforge_framework.plugins.python import PYTHON
from agentforge_framework.plugins.sql import DBT_PARSE, SCAFFOLD_MODEL, SQL

from .fakes import FakeRunner, github_repository
from .test_gates import a_context
from .test_runtime import (
    ROOT,
    a_runner,
    agent_says,
    comments_on,
    forge,
    issue_json,
)

# --- the contract ----------------------------------------------------------


def plan_touching(*files: str) -> Plan:
    return Plan(summary="x", steps=(PlanStep(id="s1", intent="do it", files=files),))


def test_a_plugin_carrying_only_fragments_is_legal():
    """Every contribution field optional is the whole point of the seam: #57 and
    #58 add Extractors and validators without every Plugin growing them."""
    plugin = Plugin(name="minimal", fragments=(Fragment(text="be careful"),))

    assert plugin.suffixes == ()
    assert plugin.root_markers == ()
    assert plugin.fragments[0].roles == ()


def test_a_plugin_is_frozen():
    with pytest.raises(FrozenInstanceError):
        PYTHON.name = "something else"


# --- activation ------------------------------------------------------------


def test_a_plugin_answers_for_a_suffix_in_the_blast_radius(tmp_path):
    active = activate(plan_touching("src/loader.py"), tmp_path)

    assert [p.name for p in active.plugins] == ["python"]


def test_a_plugin_answers_for_a_root_marker_whatever_the_plan_touches(tmp_path):
    """The repository is a fact about the work even when one Plan does not
    happen to touch it — a `databricks.yml` says Databricks regardless."""
    (tmp_path / "databricks.yml").write_text("bundle: {}", encoding="utf-8")
    marked = Plugin(name="databricks", root_markers=("databricks.yml",))

    active = activate(plan_touching("notes.md"), tmp_path, plugins=(marked,))

    assert [p.name for p in active.plugins] == ["databricks"]


def test_the_python_plugin_is_not_active_where_python_is_not_in_the_blast_radius(tmp_path):
    """It declares no root markers on purpose. A repository with a
    `pyproject.toml` and a SQL-only Plan is not doing Python work."""
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")

    active = activate(plan_touching("models/orders.sql"), tmp_path)

    # `sql` answers for this Plan and is meant to (#57). The claim here is about
    # `python` alone, and asserting an empty set would only have been asserting
    # that no other Plugin had been written yet.
    assert PYTHON not in active.plugins


def test_activation_is_deterministic_and_in_registration_order(tmp_path):
    first = Plugin(name="a", suffixes=(".py",))
    second = Plugin(name="b", suffixes=(".py",))
    plan = plan_touching("src/loader.py")

    for _ in range(5):
        active = activate(plan, tmp_path, plugins=(first, second))
        assert [p.name for p in active.plugins] == ["a", "b"]


def test_a_plugin_that_raises_is_skipped_and_named_and_the_run_carries_on(tmp_path):
    """Domain knowledge is a nice-to-have. A Run that died because one
    convention list was malformed would be worse than a Run without it."""

    class Exploding:
        """Duck-typed rather than a `Plugin` subclass: the shipped one is frozen,
        and a Plugin that raises in the field is a third-party one that need not
        have been built out of this dataclass at all."""

        name = "broken"
        root_markers = ()
        fragments = ()

        @property
        def suffixes(self):
            raise ValueError("bad suffix table")

    active = activate(plan_touching("src/loader.py"), tmp_path, plugins=(Exploding(), PYTHON))

    assert [p.name for p in active.plugins] == ["python"]
    assert len(active.skipped) == 1
    assert "broken" in active.skipped[0] and "bad suffix table" in active.skipped[0]


# --- what reaches which Role ----------------------------------------------


def test_a_fragment_reaches_the_roles_it_names_and_no_others():
    active = Activation(plugins=(PYTHON,))

    assert fragments_for(active, "implementer")
    assert fragments_for(active, "tester")
    assert fragments_for(active, "reviewer")
    assert fragments_for(active, "security") == ()
    assert fragments_for(active, "architect") == ()


def test_a_fragment_naming_no_role_reaches_every_role():
    active = Activation(plugins=(Plugin(name="all", fragments=(Fragment(text="rule"),)),))

    assert fragments_for(active, "security")
    assert fragments_for(active, "implementer")


def test_one_plugin_cannot_dominate_a_prompt_the_context_pack_exists_to_shrink():
    huge = Plugin(name="huge", fragments=(Fragment(text="x" * (MAX_FRAGMENT_CHARS * 5)),))

    (delivered,) = fragments_for(Activation(plugins=(huge,)), "implementer")

    assert len(delivered) <= MAX_FRAGMENT_CHARS + len("**huge**\n")


def test_no_role_carries_more_fragments_than_the_cap():
    many = tuple(
        Plugin(name=f"p{i}", fragments=(Fragment(text=f"rule {i}"),))
        for i in range(MAX_FRAGMENTS_PER_ROLE + 3)
    )

    assert len(fragments_for(Activation(plugins=many), "implementer")) == MAX_FRAGMENTS_PER_ROLE


def test_the_prompt_block_keeps_the_orchestrators_conventions_apart_from_a_plugins():
    """Different authors, different headings. A Role reading one run-on list
    cannot tell which half the Plan actually asked for."""
    pack = ContextPack(
        files=("src/loader.py",),
        conventions=("Do not change the public signature of `load`.",),
        fragments=("**python**\nAnnotate new public functions.",),
    )

    block = render_context_block(pack)

    assert "**Follow these conventions:** Do not change the public signature" in block
    assert "This repository's technology is held to these conventions:" in block
    assert block.index("Follow these conventions") < block.index("held to these conventions")


def test_fragments_never_travel_in_an_issue_body():
    """ADR-0011: the Issue body is the stable surface. Plugins are resolved
    against the repository the Run is in, so freezing one machine's answer into
    the Issue would hand the next Run conventions it may not be held to."""
    pack = ContextPack(files=("a.py",), fragments=("**python**\nrule",))

    assert "fragments" not in pack.to_dict()
    assert ContextPack.from_dict(pack.to_dict()).fragments == ()


# --- through a whole Run ---------------------------------------------------


def prompts_from(runner: FakeRunner) -> list[str]:
    """What each Agent was actually handed, read off the argument vector."""
    return [call[call.index("-p") + 1] for call in runner.matching("claude")]


def a_python_run() -> FakeRunner:
    runner = a_runner()
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))
    return runner


def test_a_fragment_reaches_the_prompt_of_the_roles_it_is_keyed_to():
    runner = a_python_run()

    forge(runner).implement(12, allow_commands=True)

    implementer, tester, security, reviewer = prompts_from(runner)
    assert "Type-annotate new public functions" in implementer
    assert "Type-annotate new public functions" in tester
    assert "Type-annotate new public functions" in reviewer
    assert "Type-annotate new public functions" not in security


def test_a_repository_matching_no_plugin_produces_the_prompts_it_produces_today():
    with_plugins = a_python_run()
    forge(with_plugins).implement(12, allow_commands=True)

    without = a_python_run()
    forge(without).implement(12, allow_commands=True, use_plugins=False)

    assert prompts_from(with_plugins) != prompts_from(without)
    # The Security Role is keyed to no Fragment, so its prompt is the control
    # for every other difference between the two Runs.
    assert prompts_from(with_plugins)[2] == prompts_from(without)[2]


def test_no_plugins_keeps_the_pack_and_drops_only_the_fragments():
    runner = a_python_run()

    forge(runner).implement(12, allow_commands=True, use_plugins=False)

    implementer = prompts_from(runner)[0]
    assert "## Context Pack" in implementer, "the pack itself is still resolved"
    assert "Type-annotate new public functions" not in implementer


def test_no_context_pack_is_the_combined_control():
    """Fragments ride in the pack, so the pack's control suppresses both. That
    is why #61 needs the separate switch above."""
    runner = a_python_run()

    forge(runner).implement(12, allow_commands=True, resolve_context=False)

    implementer = prompts_from(runner)[0]
    assert "## Context Pack" not in implementer
    assert "Type-annotate new public functions" not in implementer


def test_a_fragment_reaches_both_capability_tiers_the_same_way():
    """ADR-0005 says a skill is inlined where the Provider offers no native
    equivalent. A Plugin's conventions have no native equivalent at any tier —
    there is no `/agentforge:` command that means "Unity Catalog naming" — so
    the existing rule yields one answer, always inline, and ADR-0016 does not
    add a second rule to reach it.

    `codex` is the Fragment-tier Provider and `claude` the native one, and the
    same convention has to land in both.
    """
    runner = a_python_run()
    Forge(cwd=ROOT, provider="codex", runner=runner).implement(12, allow_commands=True)

    prompts = [call[call.index("exec") + 1] for call in runner.matching("codex")]
    assert "Type-annotate new public functions" in prompts[0]


def test_the_context_pack_comment_names_the_active_plugins_and_what_each_gave():
    runner = a_python_run()

    forge(runner).implement(12, allow_commands=True)

    (pack_comment,) = [c for c in comments_on(runner) if "### Context Pack" in c]
    assert "**Plugins active (1):**" in pack_comment
    assert "`python`" in pack_comment
    assert "implementer, tester, reviewer" in pack_comment


def test_the_control_run_says_it_carried_no_fragments_either():
    assert "Plugin Fragments ride in the pack" in render_context_comment(ContextPack())


def test_a_skipped_plugin_is_named_in_the_run_log():
    comment = render_context_comment(
        ContextPack(files=("a.py",)), skipped=("broken (ValueError: bad table)",)
    )

    assert "**Plugins skipped:**" in comment
    assert "broken (ValueError: bad table)" in comment


def test_a_run_with_plugins_still_reaches_sign_off():
    """The seam is additive. Nothing about activating a Plugin changes how a Run
    ends, and this is the test that fails first if that stops being true."""
    runner = a_python_run()

    state = forge(runner).implement(12, allow_commands=True)

    assert state.status is RunStatus.AWAITING_SIGNOFF


def test_the_shipped_registry_is_ordered_and_holds_the_python_plugin():
    assert isinstance(BUILT_IN, tuple)
    assert PYTHON in BUILT_IN


def test_contributions_reads_as_a_line_a_human_can_act_on():
    assert contributions(Activation(plugins=(PYTHON,))) == (
        ("python", "1 Fragment(s) for implementer, tester, reviewer"),
    )


# --- a Plugin's Extractors (#57) -------------------------------------------


def an_extractor(marker: str):
    """A reader that is unmistakably not one of the built-in three."""

    def read(text: str) -> Extraction:
        return Extraction(symbols=(marker,))

    return read


def test_the_table_starts_from_the_built_in_extractors():
    """The base is a floor, not a default. A Run with no active Plugin reads
    every suffix the way it has always been read, which is what makes the
    control in #61 a control for readers as well as for prompts.
    """
    table = extractors_for(Activation())

    assert table == EXTRACTORS
    assert table is not EXTRACTORS  # a copy, so no caller can widen the built-ins


def test_a_plugins_extractor_takes_precedence_over_a_built_in_one():
    claimant = Plugin(
        name="claimant",
        extractors=(Extractor(suffixes=(".sql",), read=an_extractor("from-the-plugin")),),
    )

    table = extractors_for(Activation(plugins=(claimant,)))

    assert table[".sql"](".sql text") == Extraction(symbols=("from-the-plugin",))
    assert table[".py"] is EXTRACTORS[".py"]  # a suffix nobody claimed is untouched


def test_two_plugins_claiming_one_suffix_resolve_in_registration_order():
    """Documented as first-registered-wins, and asserted both ways round so
    that a passing test cannot be dictionary insertion order agreeing by
    accident.
    """
    first = Plugin(
        name="first",
        extractors=(Extractor(suffixes=(".sql",), read=an_extractor("first")),),
    )
    second = Plugin(
        name="second",
        extractors=(Extractor(suffixes=(".sql",), read=an_extractor("second")),),
    )

    assert extractors_for(Activation(plugins=(first, second)))[".sql"]("") == Extraction(
        symbols=("first",)
    )
    assert extractors_for(Activation(plugins=(second, first)))[".sql"]("") == Extraction(
        symbols=("second",)
    )


def test_a_shadowed_plugin_still_contributes_its_other_suffixes():
    """Losing one suffix is not being switched off."""
    first = Plugin(
        name="first",
        extractors=(Extractor(suffixes=(".sql",), read=an_extractor("first")),),
    )
    second = Plugin(
        name="second",
        extractors=(
            Extractor(suffixes=(".sql", ".ddl"), read=an_extractor("second")),
        ),
    )

    table = extractors_for(Activation(plugins=(first, second)))

    assert table[".sql"]("") == Extraction(symbols=("first",))
    assert table[".ddl"]("") == Extraction(symbols=("second",))


def test_a_suffix_is_claimed_case_insensitively():
    """A repository written on Windows has `.SQL` files and they are the same
    language — the rule `EXTRACTORS` already applies at lookup.
    """
    shouty = Plugin(
        name="shouty",
        extractors=(Extractor(suffixes=(".SQL",), read=an_extractor("claimed")),),
    )

    assert ".sql" in extractors_for(Activation(plugins=(shouty,)))


def test_an_extractor_that_raises_yields_nothing_and_the_file_is_still_carried(tmp_path):
    """The bargain the registry makes at activation, one layer down: a Plugin
    costs the pack one file's contents and never the Run.
    """

    def explode(text: str) -> Extraction:
        raise RuntimeError("this reader is broken")

    broken = Plugin(
        name="broken",
        extractors=(Extractor(suffixes=(".sql",), read=explode),),
    )
    (tmp_path / "query.sql").write_text("select id from orders", encoding="utf-8")

    pack = resolve_pack(
        plan_touching("query.sql"),
        tmp_path,
        None,
        extractors_for(Activation(plugins=(broken,))),
    )

    assert pack.files == ("query.sql",)
    assert pack.symbols == ()
    assert pack.references == ()


def test_a_plugins_output_is_held_to_the_resolvers_caps(tmp_path):
    """An added Plugin cannot make a pack grow without bound. The cap is the
    resolver's and falls on a Plugin's reader exactly as it falls on a built-in
    one, which is why the reader here is deliberately incontinent.
    """
    flood = Plugin(
        name="flood",
        extractors=(
            Extractor(
                suffixes=(".sql",),
                read=lambda text: Extraction(
                    symbols=tuple(f"col{n}" for n in range(500))
                ),
            ),
        ),
    )
    (tmp_path / "query.sql").write_text("select 1", encoding="utf-8")

    pack = resolve_pack(
        plan_touching("query.sql"),
        tmp_path,
        None,
        extractors_for(Activation(plugins=(flood,))),
    )

    assert len(pack.symbols) == MAX_SYMBOLS_PER_FILE


def test_two_resolutions_of_one_plan_agree_plugins_included(tmp_path):
    (tmp_path / "model.sql").write_text(
        "select id from {{ ref('stg_orders') }}", encoding="utf-8"
    )
    plan = plan_touching("model.sql")
    table = extractors_for(Activation(plugins=(SQL,)))

    assert resolve_pack(plan, tmp_path, None, table) == resolve_pack(
        plan, tmp_path, None, table
    )


def test_a_repository_with_no_active_plugin_resolves_the_pack_it_resolves_today(
    tmp_path,
):
    """The control that matters most: adding the `sql` Plugin to `BUILT_IN` must
    not have changed what a Run without it sees.
    """
    (tmp_path / "query.sql").write_text(
        "select o.id from analytics.orders as o", encoding="utf-8"
    )
    plan = plan_touching("query.sql")

    assert resolve_pack(plan, tmp_path, None, extractors_for(Activation())) == (
        resolve_pack(plan, tmp_path)
    )


def test_contributions_names_the_suffixes_a_plugin_reads():
    assert contributions(Activation(plugins=(SQL,))) == (
        (
            "sql",
            (
                "Extractor(s) for .sql, .yml, .yaml, Gate kind(s) dbt, "
                "Command(s) scaffold-dbt-model"
            ),
        ),
    )


# --- the PySpark and Databricks Plugins (#60) ------------------------------


def repository(tmp_path, files: dict[str, str]):
    """A repository holding the named files, and nothing else."""
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def active_in(tmp_path, *files) -> list[str]:
    """The shipped Plugins that answer for a Plan touching these files."""
    return [p.name for p in activate(plan_touching(*files), tmp_path).plugins]


def test_pyspark_answers_for_a_file_in_the_blast_radius_that_imports_it(tmp_path):
    repository(tmp_path, {"jobs/daily.py": "from pyspark.sql import functions as F\n"})

    assert active_in(tmp_path, "jobs/daily.py") == ["python", "pyspark"]


def test_a_suffix_alone_does_not_make_a_module_a_spark_job(tmp_path):
    """The reason `pyspark` declares an import and no suffix. `.py` is the
    suffix of a Spark job and of a Django view, and a repository told about the
    DataFrame API while it edited its views would switch Plugins off."""
    repository(
        tmp_path,
        {"app/views.py": "import django\n\n\ndef index(request):\n    return None\n"},
    )

    assert active_in(tmp_path, "app/views.py") == ["python"]


def test_a_plain_python_repository_sees_neither_new_plugin(tmp_path):
    """The whole promise of detection: a repository with no Spark and no
    workspace pays nothing for either Plugin existing."""
    repository(
        tmp_path,
        {
            "pyproject.toml": "[project]\nname = 'app'\n",
            "src/loader.py": "import httpx\n",
        },
    )

    assert active_in(tmp_path, "src/loader.py") == ["python"]


def test_an_import_is_read_wherever_it_is_written(tmp_path):
    """Deferred inside a function is how a job avoids importing Spark at module
    scope, and it is the same import."""
    repository(
        tmp_path,
        {"jobs/daily.py": "def run():\n    import pyspark\n\n    return pyspark\n"},
    )

    assert "pyspark" in active_in(tmp_path, "jobs/daily.py")


def test_a_module_named_in_a_docstring_is_not_an_import(tmp_path):
    """Why detection reads the syntax tree rather than matching a pattern: the
    word appears in the prose of repositories that stopped using it."""
    repository(
        tmp_path,
        {"docs/notes.py": '"""Ported off import pyspark last year."""\n'},
    )

    assert "pyspark" not in active_in(tmp_path, "docs/notes.py")


def test_a_file_that_will_not_parse_switches_nothing_on_and_ends_no_run(tmp_path):
    """The survivability promise, one layer earlier than a Plugin raising: a
    syntax error costs detection its answer and never the Run."""
    repository(tmp_path, {"jobs/broken.py": "import pyspark\ndef (\n"})

    assert active_in(tmp_path, "jobs/broken.py") == ["python"]


def test_detection_reads_nothing_outside_the_repository(tmp_path):
    """The resolver refuses a Plan naming `../../.ssh/id_rsa`, and detection
    reads through that same rule rather than a second copy of it."""
    repository(tmp_path, {"job.py": "import pyspark\n"})
    root = tmp_path / "repo"
    root.mkdir()

    # `python` still answers, because a `.py` in the blast radius is a suffix
    # and a suffix claims nothing about a file being readable. What is asserted
    # here is that nothing was read: the import above is outside the repository.
    assert "pyspark" not in active_in(root, "../job.py")


def test_detection_obeys_the_size_bound_the_pack_reads_by(tmp_path):
    """A file too large for the pack to read is too large to detect from. The
    alternative is an activation whose evidence nobody can see."""
    repository(tmp_path, {"jobs/generated.py": "import pyspark\n# " + "x" * MAX_BYTES})

    assert "pyspark" not in active_in(tmp_path, "jobs/generated.py")


def test_databricks_answers_for_the_workspace_markers_it_declares(tmp_path):
    """A bundle says the code in this tree is deployed to a workspace, whatever
    one Plan happens to touch — and there is no import to read instead, because
    the runtime binds `spark` and `dbutils` for a notebook that imports nothing.
    """
    for index, marker in enumerate(DATABRICKS.root_markers):
        root = repository(tmp_path / f"workspace{index}", {marker: "bundle: {}"})

        assert active_in(root, "notebooks/load.py") == ["python", "databricks"]


def test_databricks_stays_silent_in_a_repository_with_no_workspace(tmp_path):
    repository(tmp_path, {"models/orders.sql": "select 1"})

    assert "databricks" not in active_in(tmp_path, "models/orders.sql")


def test_the_pyspark_fragment_reaches_the_roles_that_write_and_review_code():
    active = Activation(plugins=(PYSPARK,))

    (implementer,) = fragments_for(active, "implementer")
    assert "DataFrame and Column expressions" in implementer
    assert fragments_for(active, "tester") and fragments_for(active, "reviewer")
    # `.rdd` is not a vulnerability, and a style convention in the Security
    # prompt competes with the audit rather than supporting it.
    assert fragments_for(active, "security") == ()
    assert fragments_for(active, "architect") == ()


def test_a_fragment_differs_by_role_where_the_roles_need_different_things():
    """What the Security Role needs to know about a workspace is not what the
    Implementer needs, and one Plugin says both without saying either twice."""
    active = Activation(plugins=(DATABRICKS,))

    (implementer,) = fragments_for(active, "implementer")
    (security,) = fragments_for(active, "security")

    assert "MERGE INTO" in implementer and "MERGE INTO" not in security
    assert "secret scope" in security and "secret scope" not in implementer
    assert "catalog.schema.table" in implementer


def test_the_run_log_names_a_plugin_that_speaks_to_more_than_one_role():
    assert contributions(Activation(plugins=(DATABRICKS,))) == (
        ("databricks", "2 Fragment(s) for implementer, tester, reviewer, security"),
    )


def test_each_shipped_fragment_is_delivered_whole():
    """Within the bound separately. A Fragment past the cap is truncated rather
    than dropped, which is the right rule and a bad way to ship one: a
    convention list that stops mid-sentence is one nobody can act on the end of.
    """
    for plugin in BUILT_IN:
        for fragment in plugin.fragments:
            assert len(fragment.text) <= MAX_FRAGMENT_CHARS, plugin.name

    for role in ("implementer", "tester", "reviewer", "security"):
        for delivered in fragments_for(Activation(plugins=BUILT_IN), role):
            assert delivered.rstrip().endswith(".")


def test_the_shipped_plugins_stay_within_the_bound_together(tmp_path):
    """Within the bound together. A Databricks repository running Spark jobs in
    Python activates three of the four at once, which is the worst case anyone
    ships, and it is still inside the cap the Context Pack exists to defend."""
    repository(
        tmp_path,
        {"databricks.yml": "bundle: {}", "jobs/daily.py": "import pyspark\n"},
    )

    active = activate(plan_touching("jobs/daily.py"), tmp_path)
    assert [p.name for p in active.plugins] == ["python", "pyspark", "databricks"]

    for role in ("implementer", "tester", "reviewer", "security"):
        delivered = fragments_for(active, role)
        assert len(delivered) <= MAX_FRAGMENTS_PER_ROLE
        assert sum(len(text) for text in delivered) <= (
            MAX_FRAGMENTS_PER_ROLE * MAX_FRAGMENT_CHARS
        )


def test_the_registry_ships_four_plugins_in_the_order_they_contribute():
    """The general before the specific: a PySpark job is Python and is held to
    both, and the Fragment about annotating a function reaches the prompt before
    the one about the DataFrame API."""
    assert BUILT_IN == (PYTHON, SQL, PYSPARK, DATABRICKS)


def a_databricks_spark_repository(tmp_path) -> FakeRunner:
    """A Run's worth of fakes against a repository that activates both."""
    repository(
        tmp_path,
        {
            "databricks.yml": "bundle:\n  name: pipelines\n",
            "src/loader.py": "from pyspark.sql import functions as F\n",
        },
    )
    runner = github_repository(FakeRunner(), tmp_path)
    runner.script("gh", "issue", "view", stdout=issue_json())
    runner.script(
        "gh", "pr", "create", stdout="https://github.com/acme/pipelines/pull/13\n"
    )
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))
    return runner


def test_a_data_engineering_repository_gets_both_conventions_in_the_right_prompts(
    tmp_path,
):
    """The end of the milestone, read off the argument vector: the Implementer
    is told how this shop writes a MERGE and a Spark job, and the Security Role
    is told where the secrets come from and nothing about the DataFrame API."""
    runner = a_databricks_spark_repository(tmp_path)

    Forge(cwd=tmp_path, provider="claude", runner=runner).implement(
        12, allow_commands=True
    )

    implementer, tester, security, reviewer = prompts_from(runner)
    assert "DataFrame and Column expressions" in implementer
    assert "catalog.schema.table" in implementer
    assert "DataFrame and Column expressions" in tester
    assert "catalog.schema.table" in reviewer
    assert "secret scope" in security
    assert "DataFrame and Column expressions" not in security
    assert "MERGE INTO" not in security


def test_the_run_log_says_which_plugins_a_data_engineering_repository_activated(
    tmp_path,
):
    runner = a_databricks_spark_repository(tmp_path)

    Forge(cwd=tmp_path, provider="claude", runner=runner).implement(
        12, allow_commands=True
    )

    (pack_comment,) = [c for c in comments_on(runner) if "### Context Pack" in c]
    assert "**Plugins active (3):**" in pack_comment
    assert "`pyspark`" in pack_comment and "`databricks`" in pack_comment


# --- a Plugin's Gate kinds (#58) -------------------------------------------


def a_check(summary: str = "waxing", invalidates: str = ""):
    """A Gate that clears and says which one it was, for tracing a table."""

    def check(context: GateContext) -> GateEntry:
        return GateEntry(
            kind="",
            verdict=GateVerdict.CLEARED,
            invalidates=invalidates,
            summary=summary,
        )

    return check


def contributing(kind: str, name: str = "moon", **kwargs) -> Plugin:
    return Plugin(name=name, validators=(Validator(kind=kind, check=a_check(**kwargs)),))


def test_a_plugins_gate_kind_widens_the_table_the_shipped_three_floor():
    table = gates_for(Activation(plugins=(contributing("moonphase"),)))

    assert set(table) == set(GATES) | {"moonphase"}
    assert table["human"] is GATES["human"]


def test_a_workflow_naming_a_plugins_gate_loads_where_that_plugin_is_active():
    """The trap #56 placed activation ahead of the Workflow load for: a
    definition naming a Plugin's Gate has to load, and it only can if the Plugin
    that defines the kind was activated first."""
    table = gates_for(Activation(plugins=(contributing("moonphase"),)))

    workflow = parse_workflow(
        "name: feature\nsteps:\n  - role: implementer\n    gate: moonphase\n",
        name="feature",
        gates=table,
    )

    assert workflow.steps[0].gate == "moonphase"


def test_a_gate_kind_no_active_plugin_contributes_is_refused_and_names_what_there_is():
    """The same definition in a repository the Plugin does not answer for.
    Refused rather than carried, because nothing there would evaluate it."""
    with pytest.raises(WorkflowError) as refused:
        parse_workflow(
            "name: feature\nsteps:\n  - role: implementer\n    gate: moonphase\n",
            name="feature",
            gates=gates_for(Activation()),
        )

    assert "moonphase" in str(refused.value)
    assert "human, security, tests" in str(refused.value)


def test_a_plugin_cannot_redefine_a_shipped_gate_kind():
    """The one place a Plugin's claim loses to a built-in, and the opposite of
    the rule for Extractors. A suffix is a question about a file; a Gate kind is
    a promise a Workflow names, and a Plugin that could replace `human` could
    make a human Gate stop stopping."""
    usurper = contributing("human", name="usurper", summary="cleared, trust me")

    table = gates_for(Activation(plugins=(usurper,)))

    assert table["human"] is GATES["human"]
    assert evaluate_gate("human", a_context("human"), gates=table).blocked


def test_two_plugins_claiming_one_gate_kind_resolve_in_registration_order():
    first = contributing("moonphase", name="first", summary="from first")
    second = contributing("moonphase", name="second", summary="from second")

    def summary_from(*plugins) -> str:
        table = gates_for(Activation(plugins=plugins))
        return evaluate_gate("moonphase", a_context("moonphase"), gates=table).summary

    assert summary_from(first, second) == "from first"
    assert summary_from(second, first) == "from second"


def test_an_unknown_kind_errors_against_the_table_the_run_assembled():
    """A Workflow cannot reach here with an unknown kind, but a Run whose Plugin
    was skipped can. The message lists what this Run has rather than what some
    other Run would have had."""
    table = gates_for(Activation(plugins=(contributing("moonphase"),)))

    entry = evaluate_gate("vibes", a_context("vibes"), gates=table)

    assert entry.verdict is GateVerdict.ERRORED
    assert "moonphase" in entry.summary


def test_a_plugin_gate_acts_through_the_runner_and_the_tree_the_context_carries():
    """Registering a kind is the whole cost of adding one: a validator that
    shells out is handed the Command Runner and the working tree like any
    shipped Gate, and the runtime still names no kind."""

    def check(context: GateContext) -> GateEntry:
        result = context.runner.run(("sqlfluff", "lint"), cwd=context.root)
        return GateEntry(
            kind="",
            verdict=GateVerdict.CLEARED if result.ok else GateVerdict.BLOCKED,
            summary="linted",
        )

    runner = FakeRunner().install("sqlfluff")
    runner.script("sqlfluff", stdout="All finished!")
    table = gates_for(
        Activation(plugins=(Plugin(name="lint", validators=(Validator("dialect", check),)),))
    )

    entry = evaluate_gate(
        "dialect", a_context("dialect", runner=runner, root="/repo/warehouse"), gates=table
    )

    assert entry.verdict is GateVerdict.CLEARED
    assert runner.only("sqlfluff") == ("sqlfluff", "lint")
    assert runner.cwds[-1] == str(Path("/repo/warehouse"))


def test_a_plugin_gate_drawing_its_verdict_from_a_role_names_that_role():
    """ADR-0008's re-run rule is a property of the verdict rather than of which
    Gate produced it, so a Plugin's Gate keeps it by filling in the same field."""
    table = gates_for(
        Activation(plugins=(contributing("review-notes", invalidates="reviewer"),))
    )

    entry = evaluate_gate("review-notes", a_context("review-notes", step=3), gates=table)

    assert entry.invalidates == "reviewer"
    assert entry.kind == "review-notes" and entry.step == 3


def test_a_plugin_gate_that_raises_errors_rather_than_ending_the_run():
    """A validator is supposed to return an errored verdict when it cannot
    evaluate — `dbt` below is the worked example — and this is what happens when
    one does not. The Run ends at the Gate with a message on its Issue rather
    than at a traceback in the terminal of whoever started it, which is the same
    bargain activation makes when a Plugin raises."""

    def explodes(context: GateContext) -> GateEntry:
        raise RuntimeError("this validator is broken")

    table = gates_for(
        Activation(plugins=(Plugin(name="broken", validators=(Validator("boom", explodes),)),))
    )

    entry = evaluate_gate("boom", a_context("boom"), gates=table)

    assert entry.verdict is GateVerdict.ERRORED
    assert "broken" in entry.summary and "this validator is broken" in entry.summary


def test_a_shipped_gate_that_raises_is_not_dressed_up_as_a_verdict(monkeypatch):
    """Only a Plugin's validators are wrapped. A shipped Gate raising is a defect
    in AgentForge, and turning it into an errored verdict would hide it."""

    def explodes(context):
        raise RuntimeError("the human Gate is broken")

    monkeypatch.setitem(GATES, "human", explodes)

    with pytest.raises(RuntimeError):
        evaluate_gate("human", a_context("human"), gates=gates_for(Activation()))


# --- the dbt Gate, which `sql` contributes ---------------------------------


def a_dbt(stdout: str = "", returncode: int = 0, stderr: str = "") -> FakeRunner:
    """A machine with dbt on it, answering with one scripted status."""
    runner = FakeRunner().install("dbt")
    runner.script("dbt", stdout=stdout, stderr=stderr, returncode=returncode)
    return runner


def dbt_verdict(runner: FakeRunner, root: str = "/repo/warehouse") -> GateEntry:
    table = gates_for(Activation(plugins=(SQL,)))
    return evaluate_gate("dbt", a_context("dbt", runner=runner, root=root), gates=table)


def test_the_sql_plugin_contributes_the_dbt_gate_kind():
    assert "dbt" in gates_for(Activation(plugins=(SQL,)))
    assert "dbt" not in GATES, "a Plugin's kind does not leak into the shipped table"


def test_a_project_that_parses_clears_the_gate():
    runner = a_dbt(stdout="Found 12 models, 4 sources")

    assert dbt_verdict(runner).verdict is GateVerdict.CLEARED
    assert runner.only("dbt") == DBT_PARSE
    assert runner.cwds[-1] == str(Path("/repo/warehouse"))


def test_a_project_that_does_not_resolve_blocks_rather_than_errors():
    """A model referencing one that was renamed is a report on the repository,
    and the next commit can clear it. Suspended exactly."""
    entry = dbt_verdict(
        a_dbt(stderr="Compilation Error: model 'stg_orders' not found", returncode=1)
    )

    assert entry.verdict is GateVerdict.BLOCKED
    assert "stg_orders" in entry.summary


def test_a_dbt_invocation_that_never_reached_a_verdict_errors():
    """Exit 2 is dbt's usage error: no project here, or a flag it does not know.
    Nothing about the models was decided, so there is nothing to clear."""
    entry = dbt_verdict(a_dbt(stderr="No dbt_project.yml found", returncode=2))

    assert entry.verdict is GateVerdict.ERRORED
    assert "No dbt_project.yml found" in entry.summary


def test_dbt_missing_from_the_machine_errors_before_anything_runs():
    runner = FakeRunner().uninstall("dbt")

    entry = dbt_verdict(runner)

    assert entry.verdict is GateVerdict.ERRORED
    assert "dbt" in entry.summary
    assert not runner.calls, "the Gate ran something on a machine that has nothing"


def test_dbt_that_will_not_start_errors_rather_than_crashing_the_run():
    """A Plugin's Gate makes the same bargain the Plugin does: it degrades the
    Run and never ends it, so this is an errored verdict and not an exception."""

    class WillNotStart(FakeRunner):
        def run(self, argv, **kwargs):
            raise MissingBinary(str(argv[0]))

    entry = dbt_verdict(WillNotStart().install("dbt"))

    assert entry.verdict is GateVerdict.ERRORED
    assert "dbt" in entry.summary


def test_the_dbt_gate_judges_no_roles_output_so_it_invalidates_no_step():
    """It re-runs dbt against the tree rather than reading what a Role said
    about it, so every Step behind it stays behind it (ADR-0008)."""
    entry = dbt_verdict(a_dbt(stderr="Compilation Error", returncode=1))

    assert entry.invalidates == ""


# --- through a whole Run ---------------------------------------------------


def a_dbt_project(tmp_path, monkeypatch, workflow: str) -> tuple[Path, FakeRunner]:
    """A dbt repository, a Workflow of the test's own making, and the fakes."""
    root = repository(tmp_path / "repo", {"dbt_project.yml": "name: warehouse\n"})
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "feature.yaml").write_text(workflow, encoding="utf-8")
    monkeypatch.setattr("agentforge_framework.core.workflow.WORKFLOWS_ROOT", workflows)

    runner = github_repository(FakeRunner(), root)
    runner.script("gh", "issue", "view", stdout=issue_json())
    runner.script(
        "gh", "pr", "create", stdout="https://github.com/acme/pipelines/pull/13\n"
    )
    runner.script("git", "status", "--porcelain", stdout=["", " M src/loader.py\n"])
    runner.script("claude", stdout=agent_says("completed", "Added a bounded retry."))
    return root, runner


DBT_GATED = "name: feature\nsteps:\n  - role: implementer\n    gate: dbt\n  - role: tester\n"


def test_a_workflow_naming_a_plugins_gate_runs_it_and_suspends_on_a_blocked_verdict(
    tmp_path, monkeypatch
):
    """The whole path: the Plugin activates on the repository, its kind widens
    the table the definition is validated against, the Gate runs through the
    Command Runner, and a blocked verdict suspends the Run like any other."""
    root, runner = a_dbt_project(tmp_path, monkeypatch, DBT_GATED)
    runner.install("dbt").script("dbt", stderr="Compilation Error", returncode=1)

    state = Forge(cwd=root, provider="claude", runner=runner).implement(
        12, allow_commands=True
    )

    assert state.status is RunStatus.SUSPENDED
    assert runner.only("dbt") == DBT_PARSE
    assert len(runner.matching("claude")) == 1, "the Step after the Gate ran anyway"
    assert any("`dbt parse` failed" in c for c in comments_on(runner))


def test_a_run_whose_plugin_gate_clears_carries_on_to_sign_off(tmp_path, monkeypatch):
    root, runner = a_dbt_project(tmp_path, monkeypatch, DBT_GATED)
    runner.install("dbt").script("dbt", stdout="Found 12 models")

    state = Forge(cwd=root, provider="claude", runner=runner).implement(
        12, allow_commands=True
    )

    assert state.status is RunStatus.AWAITING_SIGNOFF
    assert len(runner.matching("claude")) == 2


def test_a_workflow_naming_a_plugins_gate_is_refused_where_that_plugin_is_silent(
    tmp_path, monkeypatch
):
    """A plain Python repository has no dbt Gate to name, and finds out before a
    Provider is invoked rather than at the Gate."""
    root, runner = a_dbt_project(tmp_path, monkeypatch, DBT_GATED)
    (root / "dbt_project.yml").unlink()

    with pytest.raises(RunFailed) as refused:
        Forge(cwd=root, provider="claude", runner=runner).implement(12, allow_commands=True)

    assert "dbt" in str(refused.value)
    assert "human, security, tests" in str(refused.value)
    assert not runner.ran("claude"), "a Provider was invoked for a Workflow that cannot run"


# --- a Plugin's Commands (#59) ---------------------------------------------


def scaffolding(*templates: tuple[str, str], name: str = "chore", **kwargs) -> Plugin:
    """A Plugin contributing one Command, written here rather than shipped."""
    return Plugin(
        name="chores",
        commands=(
            Command(
                name=name,
                summary="does a chore",
                arguments=kwargs.pop("arguments", ("name",)),
                templates=tuple(FileTemplate(path=p, text=t) for p, t in templates),
                **kwargs,
            ),
        ),
    )


def test_the_command_table_has_no_shipped_floor():
    """The one table with no built-in half. AgentForge has no chores of its own,
    and a Command that is not a Plugin's is nobody's."""
    assert commands_for(Activation()) == {}


def test_a_plugins_commands_are_the_table():
    assert list(commands_for(Activation(plugins=(SQL,)))) == ["scaffold-dbt-model"]


def test_two_plugins_claiming_one_command_name_resolve_in_registration_order():
    first = scaffolding(("a.sql", "from first"), name="scaffold")
    second = scaffolding(("b.sql", "from second"), name="scaffold")

    table = commands_for(Activation(plugins=(first, second)))

    assert table["scaffold"].templates[0].path == "a.sql"


def test_a_command_writes_the_files_it_declares(tmp_path):
    plugin = scaffolding(("models/$name.sql", "select * from $name"))

    outcome = run_command(
        plugin.commands[0], ["orders"], root=tmp_path, runner=FakeRunner()
    )

    assert outcome.ok
    assert outcome.written == ("models/orders.sql",)
    assert (tmp_path / "models" / "orders.sql").read_text(encoding="utf-8") == (
        "select * from orders"
    )


def test_a_command_that_only_writes_files_starts_no_process():
    """The point of a Command: no model, and here not even a subprocess. The
    output is a diff, and there is nothing to review for hallucination."""
    runner = FakeRunner()
    plugin = scaffolding(("$name.sql", "select 1"))

    run_command(plugin.commands[0], ["orders"], root=Path("."), runner=runner)

    assert not runner.calls


def test_a_command_never_replaces_a_file_that_is_already_there(tmp_path):
    """A Command that clobbered a file would be one nobody dares run twice."""
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "orders.sql").write_text("the real model", encoding="utf-8")
    plugin = scaffolding(("models/$name.sql", "a scaffold"))

    outcome = run_command(
        plugin.commands[0], ["orders"], root=tmp_path, runner=FakeRunner()
    )

    assert not outcome.ok
    assert "already exists" in outcome.error
    assert (tmp_path / "models" / "orders.sql").read_text(encoding="utf-8") == (
        "the real model"
    )


def test_a_command_writes_nothing_at_all_when_one_of_its_files_is_in_the_way(tmp_path):
    """Checked across every template before the first is written. Half a
    scaffold is worse than none: the tree carries files whose partner is
    missing, and the Command cannot be re-run to finish the job."""
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "orders.yml").write_text("the real schema", encoding="utf-8")

    outcome = run_command(
        SCAFFOLD_MODEL, ["orders"], root=tmp_path, runner=FakeRunner()
    )

    assert not outcome.ok
    assert not (tmp_path / "models" / "orders.sql").exists()


def test_a_command_refuses_to_write_outside_the_repository(tmp_path):
    """A template path is data, and data that renders to `../authorized_keys`
    is refused rather than clamped — the resolver's rule, in the one other
    place a path in this codebase comes from something other than a human."""
    root = tmp_path / "repo"
    root.mkdir()
    plugin = scaffolding(("../$name.sql", "escaped"))

    outcome = run_command(plugin.commands[0], ["orders"], root=root, runner=FakeRunner())

    assert not outcome.ok
    assert "outside the repository" in outcome.error
    assert not (tmp_path / "orders.sql").exists()


def test_the_wrong_number_of_arguments_is_a_usage_line_rather_than_a_traceback(tmp_path):
    outcome = run_command(SCAFFOLD_MODEL, [], root=tmp_path, runner=FakeRunner())

    assert not outcome.ok
    assert "agentforge run scaffold-dbt-model <name>" in outcome.error


def test_a_command_that_runs_a_process_runs_it_through_the_command_runner(tmp_path):
    """No second process boundary: a Command reaches the same port `gh`, git,
    and the coding-agent CLIs reach."""
    runner = FakeRunner().install("dbt")
    runner.script("dbt", stdout="Completed successfully")
    plugin = scaffolding(argv=("dbt", "run", "--select", "$name"), arguments=("name",))

    outcome = run_command(
        plugin.commands[0], ["orders"], root=tmp_path, runner=runner, allow_commands=True
    )

    assert outcome.ok
    assert runner.only("dbt") == ("dbt", "run", "--select", "orders")
    assert runner.cwds[-1] == str(tmp_path)


def test_a_command_that_runs_a_process_is_refused_where_execution_is_denied(tmp_path):
    """ADR-0007 in the place a Plugin could otherwise have got around it. A
    human typing `agentforge run` is the grant; an unattended Run is not."""
    runner = FakeRunner().install("dbt")
    plugin = scaffolding(
        ("models/$name.sql", "select 1"),
        argv=("dbt", "run", "--select", "$name"),
    )

    outcome = run_command(plugin.commands[0], ["orders"], root=tmp_path, runner=runner)

    assert not outcome.ok
    assert "ADR-0007" in outcome.error and "--allow-commands" in outcome.error
    assert not runner.calls
    assert not (tmp_path / "models").exists(), "refused, and yet it wrote the files"


def test_a_process_that_will_not_start_is_reported_rather_than_raised(tmp_path):
    class WillNotStart(FakeRunner):
        def run(self, argv, **kwargs):
            raise MissingBinary(str(argv[0]))

    plugin = scaffolding(argv=("dbt", "run"), arguments=())

    outcome = run_command(
        plugin.commands[0],
        [],
        root=tmp_path,
        runner=WillNotStart().install("dbt"),
        allow_commands=True,
    )

    assert not outcome.ok
    assert "dbt" in outcome.error


def test_a_process_that_fails_is_carried_in_the_outcome(tmp_path):
    runner = FakeRunner().install("dbt")
    runner.script("dbt", stderr="Database Error", returncode=1)
    plugin = scaffolding(argv=("dbt", "run"), arguments=())

    outcome = run_command(
        plugin.commands[0], [], root=tmp_path, runner=runner, allow_commands=True
    )

    assert not outcome.ok
    assert outcome.result.returncode == 1


# --- the dbt scaffold, which `sql` contributes -----------------------------


def test_the_scaffold_writes_a_model_and_the_schema_entry_beside_it(tmp_path):
    outcome = run_command(
        SCAFFOLD_MODEL, ["orders"], root=tmp_path, runner=FakeRunner()
    )

    assert outcome.written == ("models/orders.sql", "models/orders.yml")
    model = (tmp_path / "models" / "orders.sql").read_text(encoding="utf-8")
    schema = (tmp_path / "models" / "orders.yml").read_text(encoding="utf-8")
    assert "{{ ref('stg_orders') }}" in model, "Jinja survived the templating"
    assert "name: orders" in schema


def test_the_scaffold_decides_nothing_a_person_has_to_decide(tmp_path):
    """It writes the shape and leaves every judgement visible: what the model
    selects, what it is called in prose, what its columns are tested for. A
    scaffold that guessed those would be the thing a reviewer has to check."""
    run_command(SCAFFOLD_MODEL, ["orders"], root=tmp_path, runner=FakeRunner())

    schema = (tmp_path / "models" / "orders.yml").read_text(encoding="utf-8")
    assert 'description: ""' in schema
