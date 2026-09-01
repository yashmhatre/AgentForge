"""Which Plugins are active for a Run, and what they contribute.

One question, asked once per Run: given the blast radius the frozen Plan names
and the repository it names it in, which Plugins answer for this work? Everything
downstream — Fragments now, Extractors and Gate kinds and Commands in the tickets
that follow — hangs off that answer, which is why activation lives here rather
than inside any one of them.

Three properties this module owes its callers, the same three the Context Pack
resolver owes:

- **Deterministic.** The same Plan against the same repository yields the same
  active set, in the same order. Nothing here iterates a set, and the registry
  is a tuple rather than a dict so that registration order is the answer's order.
- **Bounded.** Fragments make prompts longer, and the Context Pack milestone
  exists to make them shorter. A Plugin cannot spend more than `MAX_FRAGMENT_CHARS`
  on one Role, and no Role receives more than `MAX_FRAGMENTS_PER_ROLE` of them.
- **Survivable.** A Plugin that raises while contributing is skipped and named,
  and the Run carries on. Domain knowledge is a nice-to-have; a Run that died
  because a convention list was malformed would be worse than one without it.

Activation reads the Plan rather than the resolved Context Pack. The two agree
in the ordinary case, but a control Run resolves no pack at all (ADR-0010), and
a Plugin set that changed depending on whether the control was running would
make the control meaningless.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..context.extractors import EXTRACTORS
from ..context.extractors.base import Extraction
from ..plugins import BUILT_IN
from .contracts import Plan, Plugin

#: What one Plugin may contribute to one Role. A Fragment is a few hundred
#: tokens of convention — Unity Catalog three-part naming, DataFrame API over
#: RDD — and anything past this is a document that belongs in the repository the
#: Role is reading anyway. Set from a guess; #61 re-sets it from a measurement.
MAX_FRAGMENT_CHARS = 1200

#: How many Fragments one Role's prompt may carry. Four active Plugins each
#: spending the cap above is already more standing instruction than the Role's
#: own prompt, and past that the Plugins are the Role.
MAX_FRAGMENTS_PER_ROLE = 4


@dataclass(frozen=True)
class Activation:
    """Which Plugins answered for a Run, and which could not be asked.

    `skipped` is not an error path the runtime branches on. It is what the Run
    Log prints so that a prompt which did not grow has a reason a human can
    read, the same way the pack itself does.
    """

    plugins: tuple[Plugin, ...] = ()
    skipped: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.plugins)


#: The answer for a Run that activated nothing — `--no-plugins`, or a repository
#: no Plugin claims. A singleton so that callers can default to it without each
#: building an empty one.
NO_PLUGINS = Activation()


def activate(
    plan: Plan, root: Path | str, plugins: Sequence[Plugin] = BUILT_IN
) -> Activation:
    """The Plugins that answer for this Plan in this repository.

    A Plugin answers if the Plan's blast radius carries one of its suffixes, or
    if the repository root carries one of its markers. Either is sufficient: a
    Plan touching one `.sql` file in a Python repository is held to both sets of
    conventions, because both are true of the code being written.
    """
    root = Path(root)
    suffixes = _suffixes(plan)

    active: list[Plugin] = []
    skipped: list[str] = []
    for plugin in plugins:
        try:
            if _answers(plugin, suffixes, root):
                active.append(plugin)
        except Exception as exc:  # noqa: BLE001 — a Plugin must not end a Run
            skipped.append(f"{plugin.name} ({type(exc).__name__}: {exc})")

    return Activation(plugins=tuple(active), skipped=tuple(skipped))


def fragments_for(activation: Activation, role: str) -> tuple[str, ...]:
    """What the active Plugins have to say to one Role, in registration order.

    Empty `roles` on a Fragment means every Role. A Fragment past the size cap
    is truncated rather than dropped: the first sentences of a convention list
    are the convention, and a Role silently held to nothing is worse than one
    held to most of it.
    """
    name = role.strip().lower()
    collected: list[str] = []

    for plugin in activation.plugins:
        for fragment in plugin.fragments:
            if fragment.roles and name not in {r.strip().lower() for r in fragment.roles}:
                continue
            text = fragment.text.strip()
            if not text:
                continue
            collected.append(f"**{plugin.name}**\n{text[:MAX_FRAGMENT_CHARS].rstrip()}")
            break  # one Fragment per Plugin per Role, so the cap is a cap

    return tuple(collected[:MAX_FRAGMENTS_PER_ROLE])


def extractors_for(
    activation: Activation,
    base: Mapping[str, Callable[[str], Extraction]] = EXTRACTORS,
) -> dict[str, Callable[[str], Extraction]]:
    """The extractor table for a Run: the built-in three, widened by Plugins.

    The base is the floor rather than the default. A suffix no Plugin claims is
    read the way it has always been read, which is what makes a Run with no
    active Plugin resolve exactly the pack it resolved before Plugins existed.

    **Two Plugins claiming one suffix: the first in registration order wins.**
    Registration order is the order of `plugins.BUILT_IN`, so the answer is a
    property of the shipped tuple rather than of dictionary insertion, and it
    is the same rule `extractors.base.ordered` applies to names — first
    occurrence wins. The loser is not an error: a Plugin whose reader is
    shadowed for one suffix still contributes everything else it declares.

    A Plugin's claim beats a built-in one for the same suffix. That is the
    whole point of contributing a reader: `sql` knows what a `ref()` is and the
    built-in SQL extractor does not, and a Run where dbt is active should get
    the answer from the one that knows.
    """
    table = dict(base)
    claimed: dict[str, str] = {}

    for plugin in activation.plugins:
        for extractor in plugin.extractors:
            for suffix in extractor.suffixes:
                key = suffix.lower()
                if key in claimed:
                    continue  # first registration wins, and says so above
                claimed[key] = plugin.name
                table[key] = extractor.read

    return table


def contributions(activation: Activation) -> tuple[tuple[str, str], ...]:
    """Each active Plugin and what it contributed, for the Run Log.

    Named per Plugin rather than totalled, because the reader's question is
    which Plugin grew this prompt rather than by how much.
    """
    listed: list[tuple[str, str]] = []
    for plugin in activation.plugins:
        parts = []
        if plugin.fragments:
            roles = _named_roles(plugin)
            parts.append(
                f"{len(plugin.fragments)} Fragment(s) for {roles}" if roles
                else f"{len(plugin.fragments)} Fragment(s)"
            )
        if plugin.extractors:
            # The suffixes rather than the count: a reader is only interesting
            # to the person reading the Run Log if they can tell which of their
            # files it changed the reading of.
            parts.append(f"Extractor(s) for {_named_suffixes(plugin)}")
        listed.append((plugin.name, ", ".join(parts) if parts else "nothing"))
    return tuple(listed)


def _named_suffixes(plugin: Plugin) -> str:
    """The file types this Plugin contributes a reader for, in declared order."""
    names: list[str] = []
    for extractor in plugin.extractors:
        for suffix in extractor.suffixes:
            if suffix not in names:
                names.append(suffix)
    return ", ".join(names)


def _named_roles(plugin: Plugin) -> str:
    """The Roles this Plugin speaks to, or empty where it speaks to all of them."""
    names: list[str] = []
    for fragment in plugin.fragments:
        if not fragment.roles:
            return "every Role"
        for role in fragment.roles:
            if role not in names:
                names.append(role)
    return ", ".join(names)


def _suffixes(plan: Plan) -> set[str]:
    """Every file suffix the frozen Plan names, lowercased.

    The Plan rather than the repository: a Plugin activates for the work being
    done, not for every technology that happens to be checked in. A repository
    with one stray notebook does not become a notebook repository.
    """
    return {
        Path(path).suffix.lower()
        for step in plan.steps
        for path in step.files
        if path and Path(path).suffix
    }


def _answers(plugin: Plugin, suffixes: set[str], root: Path) -> bool:
    """Whether one Plugin answers for this blast radius or this repository."""
    if any(suffix.lower() in suffixes for suffix in plugin.suffixes):
        return True
    return any((root / marker).exists() for marker in plugin.root_markers)


__all__ = [
    "MAX_FRAGMENTS_PER_ROLE",
    "MAX_FRAGMENT_CHARS",
    "NO_PLUGINS",
    "Activation",
    "activate",
    "contributions",
    "extractors_for",
    "fragments_for",
]
