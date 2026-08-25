"""Workflow definitions: the Roster as data rather than as Python.

A Workflow is a YAML-declared sequence of Roles with Gates between them. The
runtime walks the steps; it does not know which Roles exist, which is what makes
adding a seventh Role a definition and a line of YAML rather than an edit to the
engine.

Everything here happens before a Provider is invoked. A definition naming a Role
that cannot run, a Gate kind that does not exist, or a tier that was never a
tier is refused at load time, so a typo in a Workflow costs nothing rather than
costing a deep-tier planning pass.

A step declares four things and this milestone acts on one. The Role is
resolved and run; the tier override, the Gate, and the skip condition are parsed
and carried, waiting for #6, #9, and the conditional-step work respectively.
Parsing them now means the shipped definitions do not change shape later.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..agents import UnknownRole, resolve_role
from .contracts import ModelTier, outstanding

#: Shipped definitions live beside the package, like the vendored skills.
WORKFLOWS_ROOT = Path(__file__).resolve().parent.parent / "workflows"

#: Gate kinds M3 ships: a passing test suite, a clean Security pass, and a
#: human. #9 turns this into a registry with predicates behind it; until then
#: the set exists so a Workflow naming `vibes` is refused rather than carried.
GATE_KINDS = frozenset({"tests", "security", "human"})


class WorkflowError(ValueError):
    """A Workflow definition cannot be used. The message names the file and the fault."""


@dataclass(frozen=True)
class Step:
    """One Role invocation, plus the three things that qualify it.

    `tier` overrides the Role's ADR-0004 default for this step alone. `gate`
    must clear before the next step begins. `when` is the condition under which
    the step is skipped. Only `role` is acted on today.
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
    if value is None:
        return None
    kind = str(value).strip().lower()
    if kind not in GATE_KINDS:
        kinds = ", ".join(sorted(GATE_KINDS))
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
    "GATE_KINDS",
    "WORKFLOWS_ROOT",
    "Step",
    "Workflow",
    "WorkflowError",
    "load_workflow",
    "parse_workflow",
]
