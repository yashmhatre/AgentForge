"""The documentation conformance check, and this repo's own docs under it.

Every test builds the smallest documentation tree that exhibits one property
and asserts on the findings that come back. Known-good and known-bad in pairs
for each property: a checker that never passes is as useless as one that never
fails, and the known-good case is deliberately a near miss rather than the
clean tree, because that is where an over-eager rule shows itself.

The last sections drop the fixture tree and assert against the real files:
this repository's own glossary, the two places it records its own version,
and the two places it says which Pythons it supports.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

import agentforge_framework

from .docs_check import check_documentation, parse_glossary

REPO_ROOT = Path(__file__).resolve().parents[1]

CLEAN_GLOSSARY = """\
# Widgets

The widgets context.

## Language

**Sprocket**:
A toothed wheel that drives a chain.
_Avoid_: Cog, gear

**Chain**:
The loop a Sprocket drives.
_Avoid_: Belt
"""

CLEAN_ADR = "# ADR-{n:04d}: A decision\n\nWe decided a thing, for a reason.\n"


def _tree(root: Path, glossary: str = CLEAN_GLOSSARY, adrs: int = 2) -> Path:
    """The smallest documentation tree the check accepts, ready to be broken."""
    (root / "CONTEXT.md").write_text(glossary, encoding="utf-8")
    adr_dir = root / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    for number in range(1, adrs + 1):
        (adr_dir / f"{number:04d}-a-decision.md").write_text(
            CLEAN_ADR.format(n=number), encoding="utf-8"
        )
    return root


def _problems(findings) -> str:
    return "\n".join(f"{f.file}: {f.problem}" for f in findings)


def _repo_terms():
    """The terms this repository's own glossary defines."""
    return parse_glossary((REPO_ROOT / "CONTEXT.md").read_text(encoding="utf-8"))


# --- the glossary parses into terms with definitions -------------------------


def test_a_well_formed_tree_reports_nothing(tmp_path):
    assert check_documentation(_tree(tmp_path)) == []


def test_a_definition_spanning_several_lines_is_accepted(tmp_path):
    """Known-good near miss: a wrapped definition must not read as an absent one."""
    root = _tree(
        tmp_path,
        glossary=(
            "# Widgets\n\n## Language\n\n**Sprocket**:\nA toothed wheel\nthat drives a chain.\n"
            "_Avoid_: Cog\n"
        ),
    )

    assert check_documentation(root) == []


def test_glossary_with_no_language_section_is_reported(tmp_path):
    root = _tree(tmp_path, glossary="# Widgets\n\n## Terms\n\n**Sprocket**:\nA wheel.\n")

    findings = check_documentation(root)

    assert [f.file for f in findings] == ["CONTEXT.md"]
    assert "Language" in findings[0].problem


def test_glossary_that_defines_no_terms_is_reported(tmp_path):
    root = _tree(tmp_path, glossary="# Widgets\n\n## Language\n\nComing soon.\n")

    findings = check_documentation(root)

    assert [f.file for f in findings] == ["CONTEXT.md"]
    assert "no terms" in findings[0].problem


def test_term_with_no_definition_is_reported(tmp_path):
    root = _tree(
        tmp_path,
        glossary="# Widgets\n\n## Language\n\n**Sprocket**:\n\n**Chain**:\nA loop.\n",
    )

    findings = check_documentation(root)

    assert [f.file for f in findings] == ["CONTEXT.md"]
    assert "Sprocket" in findings[0].problem


def test_missing_glossary_is_reported(tmp_path):
    root = _tree(tmp_path)
    (root / "CONTEXT.md").unlink()

    findings = check_documentation(root)

    assert [f.file for f in findings] == ["CONTEXT.md"]


def test_terms_are_parsed_with_their_definitions_and_avoided_synonyms():
    terms = parse_glossary(CLEAN_GLOSSARY)

    assert [t.name for t in terms] == ["Sprocket", "Chain"]
    assert terms[0].definition == "A toothed wheel that drives a chain."
    assert terms[0].avoid == ("Cog", "gear")
    assert terms[1].avoid == ("Belt",)


def test_subheadings_group_terms_without_hiding_them():
    """Grouping is allowed by the format, so grouped terms must still parse."""
    grouped = (
        "# Widgets\n\n## Language\n\n### Parts\n\n**Sprocket**:\nA wheel.\n"
        "\n### Motion\n\n**Chain**:\nA loop.\n"
    )

    assert [t.name for t in parse_glossary(grouped)] == ["Sprocket", "Chain"]


def test_terms_after_the_language_section_are_not_collected():
    trailing = CLEAN_GLOSSARY + "\n## Appendix\n\n**Bolt**:\nNot a domain term.\n"

    assert [t.name for t in parse_glossary(trailing)] == ["Sprocket", "Chain"]


# --- ADR numbers are unique --------------------------------------------------


def test_ten_distinct_adrs_are_not_mistaken_for_a_collision(tmp_path):
    """Known-good near miss: `0010` must not collide with `0001` under string sorting."""
    assert check_documentation(_tree(tmp_path, adrs=10)) == []


def test_duplicate_adr_number_names_the_file_that_took_it(tmp_path):
    root = _tree(tmp_path)
    (root / "docs" / "adr" / "0002-a-different-decision.md").write_text(
        CLEAN_ADR.format(n=2), encoding="utf-8"
    )

    findings = check_documentation(root)

    assert [f.file for f in findings] == ["docs/adr/0002-a-different-decision.md"]
    assert "0002" in findings[0].problem
    assert "0002-a-decision.md" in findings[0].problem


# --- ADR numbers form a contiguous sequence from one -------------------------


def test_gap_in_the_sequence_is_reported(tmp_path):
    root = _tree(tmp_path, adrs=2)
    (root / "docs" / "adr" / "0004-a-decision.md").write_text(
        CLEAN_ADR.format(n=4), encoding="utf-8"
    )

    findings = check_documentation(root)

    assert len(findings) == 1, _problems(findings)
    assert "0003" in findings[0].problem


def test_a_sequence_that_does_not_start_at_one_is_reported(tmp_path):
    root = _tree(tmp_path, adrs=2)
    (root / "docs" / "adr" / "0001-a-decision.md").unlink()

    findings = check_documentation(root)

    assert [f.problem for f in findings if "0001" in f.problem], _problems(findings)


def test_an_empty_adr_directory_is_reported(tmp_path):
    root = _tree(tmp_path, adrs=0)

    findings = check_documentation(root)

    assert [f.file for f in findings] == ["docs/adr"]


def test_files_that_are_not_numbered_adrs_are_left_alone(tmp_path):
    root = _tree(tmp_path)
    (root / "docs" / "adr" / "README.md").write_text("# Index\n", encoding="utf-8")

    assert check_documentation(root) == []


# --- every ADR has a title ---------------------------------------------------


def test_a_title_below_leading_blank_lines_is_accepted(tmp_path):
    """Known-good near miss: leading whitespace must not read as a missing title."""
    root = _tree(tmp_path)
    (root / "docs" / "adr" / "0002-a-decision.md").write_text(
        "\n\n# ADR-0002: A decision with `code` and a colon: here\n\nWe decided.\n",
        encoding="utf-8",
    )

    assert check_documentation(root) == []


def test_adr_with_no_title_is_reported(tmp_path):
    root = _tree(tmp_path)
    (root / "docs" / "adr" / "0002-a-decision.md").write_text(
        "We decided a thing.\n", encoding="utf-8"
    )

    findings = check_documentation(root)

    assert [f.file for f in findings] == ["docs/adr/0002-a-decision.md"]
    assert "title" in findings[0].problem


def test_adr_with_an_empty_title_is_reported(tmp_path):
    root = _tree(tmp_path)
    (root / "docs" / "adr" / "0002-a-decision.md").write_text("#\n\nA thing.\n", encoding="utf-8")

    findings = check_documentation(root)

    assert [f.file for f in findings] == ["docs/adr/0002-a-decision.md"]


# --- every finding is reported, not just the first ---------------------------


def test_a_tree_broken_four_ways_reports_every_finding(tmp_path):
    root = _tree(tmp_path, glossary="# Widgets\n\n## Language\n\n**Sprocket**:\n", adrs=2)
    adr_dir = root / "docs" / "adr"
    (adr_dir / "0002-again.md").write_text(CLEAN_ADR.format(n=2), encoding="utf-8")
    (adr_dir / "0005-untitled.md").write_text("No heading here.\n", encoding="utf-8")

    findings = check_documentation(root)
    problems = _problems(findings)

    assert len(findings) == 5, problems
    assert "Sprocket" in problems
    assert "0002-again.md" in problems
    assert "0003" in problems and "0004" in problems  # one finding per gap
    assert "0005-untitled.md" in problems


# --- this repository's own documentation -------------------------------------


def test_this_repositorys_documentation_is_clean():
    """The rework is only done if the check passes against the real files."""
    findings = check_documentation(REPO_ROOT)

    assert findings == [], _problems(findings)


def test_the_glossary_defines_exactly_these_terms():
    """Pinned: these names are referenced across AGENTS.md, the ADRs, and the skills.

    Removing or renaming one should cost a deliberate edit here, not pass quietly.

    All seventeen original terms were audited against the format's
    "project-specific concepts only" rule and all seventeen survived: each is
    either coined here or given a meaning narrower than its general one, so
    none would mean the same thing in another codebase. Capability Tier,
    Fragment, and Vendored Skill were added by ADR-0005 and ADR-0006, and
    Command Runner because AGENTS.md and ADR-0006 both lean on it while it
    collides by name with Command.

    Suspended earned its place alongside Halted once both became labels a person
    reads off an Issue: the two are the same word in ordinary usage and opposite
    instructions here.

    Finding arrived with the Security Role: it is what the clean-pass Gate reads,
    it is deliberately not an Escalation, and "issue" was already taken by the
    tracker.

    Extractor and Usage arrived with the Context Pack work. An Extractor answers
    for one file and is not the resolver that calls it, and a Usage is what one
    invocation consumed in whatever unit its Provider reports — which is why it
    is not called Cost, the word the Run Log shows a human.

    Spec and Slice arrived with ADR-0021, and both are narrower here than in
    general usage. A Spec is an intermediate nothing executes against, and a
    Slice stops existing the moment it is filed — which is the distinction that
    keeps "Ticket" out of the vocabulary, since what gets filed is an Issue.
    """
    assert [t.name for t in _repo_terms()] == [
        "Task",
        "Issue",
        "Roster",
        "Spec",
        "Slice",
        "Orchestrator",
        "Role",
        "Agent",
        "Agent Result",
        "Finding",
        "Workflow",
        "Step",
        "Gate",
        "Sign-off",
        "Run",
        "Run State",
        "Halted",
        "Suspended",
        "Escalation",
        "Run Log",
        "Context Pack",
        "Extractor",
        "Plugin",
        "Command",
        "Usage",
        "Project Context",
        "Command Runner",
        "Provider",
        "Model Tier",
        "Capability Tier",
        "Fragment",
        "Vendored Skill",
    ]


def test_every_term_records_the_synonyms_it_rejects():
    """The `_Avoid_` line is the drift guard; a term without one has no guard."""
    assert [t.name for t in _repo_terms() if not t.avoid] == []


def test_role_and_agent_reject_each_other():
    """The collision this glossary most needs to prevent (docs/agents/domain.md)."""
    terms = {t.name: t for t in _repo_terms()}

    assert "Agent" in terms["Role"].avoid
    assert "Role" in terms["Agent"].avoid


# --- the version is recorded twice and may not drift -------------------------


def test_the_package_and_the_distribution_agree_on_the_version():
    """#46 kept two literals rather than single-sourcing one through build
    metadata, on the grounds that a two-line test is less machinery than the
    import dance the alternative costs. This is that test: without it, the two
    are free to disagree and the wheel ships a version its own CLI denies.
    """
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == agentforge_framework.__version__


# --- the Python the metadata claims is the Python CI runs --------------------


def test_the_matrix_runs_every_python_the_metadata_claims():
    """#52 found `requires-python` promising 3.13 while the matrix stopped at
    3.12, and settled it by widening the matrix rather than narrowing the claim.
    Whichever way a later change moves them, it has to move both.

    An open-ended `>=` claims releases that do not exist yet, and no offline
    test can know when one arrives. What this catches is drift that has already
    happened: a floor that goes untested, or a gap in the middle.
    """
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    claim = metadata["project"]["requires-python"]
    assert claim.startswith(">="), f"unhandled requires-python form: {claim!r}"

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    matrix = workflow["jobs"]["test"]["strategy"]["matrix"]["python-version"]

    floor = int(claim.removeprefix(">=").split(".")[1])
    tested = [int(version.split(".")[1]) for version in matrix]

    assert tested == list(range(floor, max(tested) + 1))


def test_the_suite_runs_on_windows_somewhere_in_ci():
    """#100 was a Windows process limit that every `ubuntu-latest` job stepped
    over without noticing, on a project whose author develops on Windows. It
    survived a release that way.

    This asks only that some job runs the suite on Windows — which job, and on
    which Python, is left open.
    """
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )

    on_windows = [
        job
        for job in workflow["jobs"].values()
        if "windows" in str(job.get("runs-on", "")) + str(job.get("strategy", ""))
    ]
    assert on_windows, "no CI job runs on Windows"
    assert any(
        "pytest" in str(step.get("run", "")) for job in on_windows for step in job.get("steps", [])
    ), "a Windows job exists but nothing runs the suite on it"
