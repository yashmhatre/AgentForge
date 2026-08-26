"""Workflow definitions: what loads, and what is refused before anything runs.

A Workflow is data, and the point of validating it at load time is that a typo
costs nothing. Every rejection test here asserts the message names the thing
that was wrong, because the person reading it is holding a YAML file rather
than a stack trace.

Definitions are written to `tmp_path` rather than mocked, following
`tests/test_skills.py`: the parser's job is to read files, so faking the
filesystem would test something else.
"""

from __future__ import annotations

import pytest

from agentforge.core.contracts import ModelTier
from agentforge.core.gates import GATES
from agentforge.core.workflow import (
    WORKFLOWS_ROOT,
    WorkflowError,
    load_workflow,
    parse_workflow,
)

ONE_STEP = """\
name: feature
steps:
  - role: implementer
"""


def _write(root, name: str, text: str):
    path = root / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- what a definition parses into -------------------------------------------


def test_a_step_names_a_role():
    workflow = parse_workflow(ONE_STEP, name="feature")

    assert workflow.name == "feature"
    assert [step.role for step in workflow.steps] == ["implementer"]


def test_the_three_optional_fields_are_carried_by_a_step():
    """Tier is active; Gate and condition remain definitions for later tickets."""
    workflow = parse_workflow(
        "name: feature\nsteps:\n"
        "  - role: implementer\n    tier: deep\n    gate: tests\n    when: always\n",
        name="feature",
    )
    step = workflow.steps[0]

    assert step.tier is ModelTier.DEEP
    assert step.gate == "tests"
    assert step.when == "always"


def test_a_step_with_no_overrides_carries_none_of_them():
    step = parse_workflow(ONE_STEP, name="feature").steps[0]

    assert step.tier is None
    assert step.gate is None
    assert step.when is None


def test_an_empty_step_list_parses():
    """`bugfix` and `review` ship empty until #14 fills them; loading must not fail."""
    assert parse_workflow("name: bugfix\nsteps: []\n", name="bugfix").steps == ()


# --- what is refused ---------------------------------------------------------


def test_an_unknown_role_is_refused_and_named():
    with pytest.raises(WorkflowError, match="dramaturge"):
        parse_workflow("name: feature\nsteps:\n  - role: dramaturge\n", name="feature")


def test_the_tester_is_a_runnable_workflow_role():
    workflow = parse_workflow("name: feature\nsteps:\n  - role: tester\n", name="feature")

    assert workflow.steps[0].role == "tester"


def test_an_unknown_gate_kind_is_refused_and_named():
    with pytest.raises(WorkflowError, match="vibes"):
        parse_workflow(
            "name: feature\nsteps:\n  - role: implementer\n    gate: vibes\n", name="feature"
        )


def test_an_unknown_tier_is_refused_and_named():
    with pytest.raises(WorkflowError, match="turbo"):
        parse_workflow(
            "name: feature\nsteps:\n  - role: implementer\n    tier: turbo\n", name="feature"
        )


def test_unparseable_yaml_names_the_workflow_and_the_parse_failure():
    with pytest.raises(WorkflowError) as exc:
        parse_workflow("name: feature\nsteps: [\n", name="feature")

    assert "feature" in str(exc.value)
    assert "line" in str(exc.value).lower() or "found" in str(exc.value).lower()


def test_steps_that_are_not_a_list_are_refused():
    with pytest.raises(WorkflowError, match="steps"):
        parse_workflow("name: feature\nsteps: implementer\n", name="feature")


def test_a_step_that_is_not_a_mapping_is_refused():
    with pytest.raises(WorkflowError, match="step 1"):
        parse_workflow("name: feature\nsteps:\n  - implementer\n", name="feature")


def test_a_step_with_no_role_is_refused():
    with pytest.raises(WorkflowError, match="role"):
        parse_workflow("name: feature\nsteps:\n  - tier: deep\n", name="feature")


def test_a_definition_with_no_steps_key_is_refused():
    with pytest.raises(WorkflowError, match="steps"):
        parse_workflow("name: feature\n", name="feature")


# --- loading from disk -------------------------------------------------------


def test_a_definition_loads_from_a_directory(tmp_path):
    _write(tmp_path, "custom", ONE_STEP)

    assert load_workflow("custom", root=tmp_path).steps[0].role == "implementer"


def test_an_unknown_workflow_names_what_is_available(tmp_path):
    _write(tmp_path, "custom", ONE_STEP)

    with pytest.raises(WorkflowError, match="custom"):
        load_workflow("nope", root=tmp_path)


def test_a_broken_definition_on_disk_names_the_workflow(tmp_path):
    _write(tmp_path, "broken", "name: broken\nsteps:\n  - role: dramaturge\n")

    with pytest.raises(WorkflowError, match="broken"):
        load_workflow("broken", root=tmp_path)


# --- the shipped definitions -------------------------------------------------


def test_feature_runs_the_implementer_the_tester_and_then_security():
    workflow = load_workflow("feature")

    assert [step.role for step in workflow.steps] == ["implementer", "tester", "security"]


@pytest.mark.parametrize("name", ["feature", "bugfix", "review"])
def test_every_shipped_definition_loads(name):
    assert load_workflow(name, root=WORKFLOWS_ROOT).name == name


def test_the_gate_kinds_are_the_three_m3_ships():
    assert set(GATES) == {"tests", "security", "human"}


def test_a_definition_is_validated_against_the_registry_rather_than_a_list(monkeypatch):
    """The kinds a Workflow may name are the kinds something is registered to
    evaluate. Two lists would let a definition name a Gate nothing answers for."""
    monkeypatch.setitem(GATES, "moonphase", lambda context: None)

    workflow = parse_workflow(
        "name: feature\nsteps:\n  - role: implementer\n    gate: moonphase\n", name="feature"
    )

    assert workflow.steps[0].gate == "moonphase"


# --- resume bookkeeping ------------------------------------------------------


def test_steps_already_completed_are_not_repeated():
    workflow = parse_workflow(
        "name: feature\nsteps:\n  - role: implementer\n  - role: implementer\n", name="feature"
    )

    assert len(workflow.remaining(("implementer",))) == 1
    assert workflow.remaining(("implementer", "implementer")) == ()


def test_nothing_completed_leaves_every_step():
    workflow = parse_workflow(ONE_STEP, name="feature")

    assert len(workflow.remaining(())) == 1
