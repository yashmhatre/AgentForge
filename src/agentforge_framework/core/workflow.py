"""Workflow definitions: the Roster as data rather than as Python.

A Workflow is a YAML-declared sequence of Roles with Gates between them. The
runtime walks the steps; it does not know which Roles exist, which is what makes
adding a seventh Role a definition and a line of YAML rather than an edit to the
engine.

Everything here happens before a Provider is invoked. A definition naming a Role
that cannot run, a Gate kind that does not exist, or a tier that was never a
tier is refused at load time, so a typo in a Workflow costs nothing rather than
costing a deep-tier planning pass.

A Step declares four things. The Role is resolved and run, the Model Tier
override is applied for that invocation, and the Gate is looked up in
`core.gates` and evaluated once the Step is behind the Run. The skip condition is
parsed and carried, waiting for the conditional-step work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..agents import UnknownRole, resolve_role
from .contracts import ModelTier, outstanding
from .gates import GATES

#: Shipped definitions live beside the package, like the vendored skills.
WORKFLOWS_ROOT = Path(__file__).resolve().parent.parent / "workflows"


class WorkflowError(ValueError):
    """A Workflow definition cannot be used. The message names the file and the fault."""


@dataclass(frozen=True)
class Step:
    """One Role invocation, plus the three things that qualify it.

    `tier` overrides the Role's ADR-0004 default for this step alone. `gate`
    names the kind of Gate that must clear before the next step begins. `when` is
    the condition under which the step is skipped, and is not acted on yet.
    """

    role: str
    tier: ModelTier | None = None
    gate: str | None = None
    when: str | None = None


@dataclass(frozen=True)
class Workflow:
    """An ordered sequence of Steps, named."""

    name: str
    steps: tuple[Step, ...] = ()

    def remaining(self, done: tuple[str, ...]) -> tuple[Step, ...]:
        """Steps that have not yet completed, in definition order."""
        return outstanding(self.steps, done, lambda step: step.role)


def available_workflows(root: Path | None = None) -> tuple[Workflow, ...]:
    """Every definition that loads, by name.

    The Orchestrator picks one and names it in the Issue, so it has to be told
    what there is. Read off the directory rather than listed in a prompt, so a
    project that drops a definition beside the shipped ones can have it chosen.
    A definition that does not load is left out rather than raising: one bad
    file in the directory should not stop planning against the others.
    """
    directory = Path(root) if root is not None else WORKFLOWS_ROOT
    workflows = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            workflows.append(parse_workflow(path.read_text(encoding="utf-8"), name=path.stem))
        except WorkflowError:
            continue
    return tuple(workflows)


def load_workflow(name: str, root: Path | None = None) -> Workflow:
    """Read and validate one definition by name."""
    directory = Path(root) if root is not None else WORKFLOWS_ROOT
    path = directory / f"{name}.yaml"
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in directory.glob("*.yaml")))
        raise WorkflowError(
            f"no Workflow named {name!r} in {directory}; available: {available or 'none'}"
        )
    return parse_workflow(path.read_text(encoding="utf-8"), name=name)


def parse_workflow(text: str, *, name: str) -> Workflow:
    """Validate a definition's text. Every rejection names `name` and the fault."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WorkflowError(f"Workflow {name!r} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise WorkflowError(f"Workflow {name!r} must be a mapping, not {_kind(data)}")

    if "steps" not in data:
        raise WorkflowError(f"Workflow {name!r} declares no `steps`")

    raw_steps = data["steps"] or []
    if not isinstance(raw_steps, list):
        raise WorkflowError(f"Workflow {name!r}: `steps` must be a list, not {_kind(raw_steps)}")

    steps = tuple(
        _parse_step(raw, index=index, workflow=name)
        for index, raw in enumerate(raw_steps, start=1)
    )
    return Workflow(name=str(data.get("name") or name), steps=steps)


def _parse_step(raw: object, *, index: int, workflow: str) -> Step:
    where = f"Workflow {workflow!r} step {index}"

    if not isinstance(raw, dict):
        raise WorkflowError(f"{where} must be a mapping with a `role`, not {_kind(raw)}")

    role = raw.get("role")
    if not role or not isinstance(role, str):
        raise WorkflowError(f"{where} declares no `role`")

    try:
        resolve_role(role)
    except UnknownRole as exc:
        raise WorkflowError(f"{where} names {role!r}: {exc}") from exc

    return Step(
        role=role.strip().lower(),
        tier=_parse_tier(raw.get("tier"), where=where),
        gate=_parse_gate(raw.get("gate"), where=where),
        when=_optional_str(raw.get("when")),
    )


def _parse_tier(value: object, *, where: str) -> ModelTier | None:
    if value is None:
        return None
    try:
        return ModelTier(str(value).strip().lower())
    except ValueError as exc:
        tiers = ", ".join(tier.value for tier in ModelTier)
        raise WorkflowError(f"{where} names tier {value!r}; tiers are: {tiers}") from exc


def _parse_gate(value: object, *, where: str) -> str | None:
    """A Gate kind is valid when something is registered to evaluate it.

    Validated against `core.gates.GATES` rather than a list kept here: two lists
    would let a definition name a Gate nothing answers for, which is a check that
    silently never runs.
    """
    if value is None:
        return None
    kind = str(value).strip().lower()
    if kind not in GATES:
        kinds = ", ".join(sorted(GATES))
        raise WorkflowError(f"{where} names Gate kind {value!r}; kinds are: {kinds}")
    return kind


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _kind(value: object) -> str:
    return type(value).__name__


__all__ = [
    "WORKFLOWS_ROOT",
    "Step",
    "Workflow",
    "WorkflowError",
    "available_workflows",
    "load_workflow",
    "parse_workflow",
]
