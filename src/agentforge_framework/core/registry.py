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

from ..context.extractors import EXTRACTORS, extract
from ..context.extractors.base import Extraction
from ..context.resolver import MAX_FILES, file_text, inside
from ..plugins import BUILT_IN
from .contracts import Command, GateEntry, GateVerdict, Plan, Plugin, Validator
from .gates import GATES, GateCheck

#: What one Plugin may contribute to one Role. A Fragment is a few hundred
#: tokens of convention — Unity Catalog three-part naming, DataFrame API over
#: RDD — and anything past this is a document that belongs in the repository the
#: Role is reading anyway.
#:
#: Set from a guess about tokens and confirmed by #61 as a bound on something
#: else. Two Runs of one frozen plan, with and without Plugins, measured a
#: per-Step difference of ±100k tokens in both directions — an agentic Step's
#: own tool calls dwarf its prompt, so the ~800 tokens of Fragment a Role
#: carried never surfaced above the noise. What the cap protects is the Role's
#: attention rather than the bill: four Plugins each spending it is already
#: more standing instruction than the Role's own prompt.
MAX_FRAGMENT_CHARS = 1200

#: How many Fragments one Role's prompt may carry. Four active Plugins each
#: spending the cap above is already more standing instruction than the Role's
#: own prompt, and past that the Plugins are the Role.
MAX_FRAGMENTS_PER_ROLE = 4

#: The file types an `imports` declaration is asked about. An import is a Python
#: idea, and a `.sql` file's references are tables rather than modules — reading
#: those for a module name would activate a Plugin because a warehouse happened
#: to hold a table with its name. See ADR-0017.
IMPORT_SUFFIXES = (".py",)


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

    A Plugin answers if the Plan's blast radius carries one of its suffixes, if
    the repository root carries one of its markers, or if a Python file in that
    blast radius imports one of the modules it names. Any one is sufficient: a
    Plan touching one `.sql` file in a Python repository is held to both sets of
    conventions, because both are true of the code being written.

    Import detection is what a suffix cannot do. `.py` says a file is Python and
    says nothing about whether it is a Spark job, so the `pyspark` Plugin
    declares the module rather than the suffix and stays silent in the Django
    app next door. See ADR-0017.
    """
    root = Path(root)
    suffixes = _suffixes(plan)

    # Read once for the whole registry, and not at all where no Plugin asks: a
    # repository with neither `pyspark` nor any third-party Plugin declaring an
    # import opens no file to find that out. Memoised in a closure rather than
    # computed up front so that the read happens inside the `try` below, where
    # a Plugin declaring a broken `imports` costs itself and not the Run.
    read: list[frozenset[str]] = []

    def imported() -> frozenset[str]:
        if not read:
            read.append(_imported(plan, root))
        return read[0]

    active: list[Plugin] = []
    skipped: list[str] = []
    for plugin in plugins:
        try:
            if _answers(plugin, suffixes, imported, root):
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


def gates_for(
    activation: Activation, base: Mapping[str, GateCheck] = GATES
) -> dict[str, GateCheck]:
    """The Gate table for a Run: the shipped three, widened by Plugins.

    Assembled the way the extractor table is, and handed to the two places that
    need it — `parse_workflow` validates a definition against it and
    `evaluate_gate` looks a kind up in it — rather than swapped into `GATES`
    globally. A Run's active Plugins are a property of that Run, and a process
    running two of them must not have the first one's Gate kinds available to
    the second.

    **A Plugin cannot redefine a shipped kind.** `human`, `tests`, and `security`
    mean what the shipped Workflows say they mean, and a Plugin that could
    replace `human` could make a human Gate stop stopping. This is the one place
    a Plugin's claim loses to a built-in, which is the opposite of the rule for
    Extractors and is deliberate: a suffix is a question about a file, and a
    Plugin that claims one knows more about that file than the generic reader
    does, while a Gate kind is a promise a Workflow names. See ADR-0018.

    Between two Plugins claiming one kind, the first in registration order wins,
    which is the rule `extractors_for` applies and for the same reason.
    """
    table = dict(base)
    claimed: dict[str, str] = {}

    for plugin in activation.plugins:
        for validator in plugin.validators:
            kind = validator.kind.strip().lower()
            if not kind or kind in base or kind in claimed:
                continue
            claimed[kind] = plugin.name
            table[kind] = _guarded(plugin, validator)

    return table


def _guarded(plugin: Plugin, validator: Validator) -> GateCheck:
    """One Plugin's check, holding it to the bargain the rest of the seam makes.

    A validator that cannot evaluate is supposed to return an errored verdict,
    and `plugins/sql`'s dbt Gate is the worked example of doing it. This is what
    happens when one does not: the Run ends at the Gate with a message on its
    Issue rather than at a traceback in the terminal of whoever started it, and
    the message names the Plugin so the reader knows whose Gate broke.

    Errored rather than blocked, for the reason every other Gate errors: a check
    that raised decided nothing, so waiting would clear nothing. Only a Plugin's
    validators are wrapped — a shipped Gate raising is a defect in AgentForge,
    and dressing it as a verdict would hide it. See ADR-0018.
    """

    def check(context) -> GateEntry:
        try:
            return validator.check(context)
        except Exception as exc:  # noqa: BLE001 — a Plugin must not end a Run
            return GateEntry(
                kind="",
                verdict=GateVerdict.ERRORED,
                summary=(
                    f"the {validator.kind!r} Gate, contributed by the {plugin.name!r} "
                    f"Plugin, raised {type(exc).__name__}: {exc}. A Gate that could not "
                    "evaluate has nothing here for a later Run to clear."
                ),
            )

    return check


def commands_for(activation: Activation) -> dict[str, Command]:
    """The Commands this repository's active Plugins contribute, by name.

    The fourth table, assembled the way the other three are, and the only one
    with no shipped floor: AgentForge itself has no chores, and a Command that
    is not a Plugin's is nobody's. A repository no Plugin answers for therefore
    gets an empty table, and `agentforge run` says so rather than offering a
    list of things that would fail.

    Two Plugins claiming one name resolve by registration order, first wins,
    which is the rule the other tables apply.
    """
    table: dict[str, Command] = {}

    for plugin in activation.plugins:
        for command in plugin.commands:
            name = command.name.strip().lower()
            if name and name not in table:
                table[name] = command

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
        if plugin.validators:
            # The kinds rather than the count, because the kind is what a
            # Workflow writes: a reader who sees `dbt` here can go and find the
            # Step whose Gate it is, or add one.
            parts.append(f"Gate kind(s) {_named_kinds(plugin)}")
        if plugin.commands:
            # Named for the same reason, and because a Command is the one
            # contribution a human can go and type.
            parts.append(f"Command(s) {_named_commands(plugin)}")
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


def _named_kinds(plugin: Plugin) -> str:
    """The Gate kinds this Plugin contributes, in declared order."""
    names: list[str] = []
    for validator in plugin.validators:
        if validator.kind not in names:
            names.append(validator.kind)
    return ", ".join(names)


def _named_commands(plugin: Plugin) -> str:
    """The Commands this Plugin contributes, in declared order."""
    names: list[str] = []
    for command in plugin.commands:
        if command.name not in names:
            names.append(command.name)
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


def _imported(plan: Plan, root: Path) -> frozenset[str]:
    """Every top-level module the Plan's Python files import.

    Detection by suffix cannot tell a Spark job from a Django view: both are
    `.py`, and only one of them wants to be told about the DataFrame API. So a
    Plugin may declare the imports it answers for, and this reads the blast
    radius to find them (ADR-0017).

    Held to the same three promises as everything else here. It reads the files
    the Plan names and never searches, so the answer is a function of the frozen
    Plan and the repository. It reads at most `MAX_FILES` of them, through the
    resolver's own size bound, so a Plan naming forty files costs forty reads
    the pack was about to do anyway. And a file that is missing, unreadable, or
    will not parse contributes nothing rather than raising — a Plugin whose
    detection could be broken by a syntax error would be worse than no Plugin.

    Imports are read with the built-in Python extractor rather than with a
    regular expression: it is already the thing in this codebase that knows what
    an import is, and `import pyspark` inside a docstring is not one. With the
    built-in table rather than the widened one, because the widened table is
    assembled from the Plugins this function is being asked to choose.
    """
    names: set[str] = set()

    for raw in _planned_files(plan)[:MAX_FILES]:
        path = inside(raw, root)
        if path is None or Path(path).suffix.lower() not in IMPORT_SUFFIXES:
            continue
        text = file_text(root / path)
        if not text:
            continue
        # `extract` swallows a file that will not parse, which is the answer
        # detection wants: a Plugin that could be switched off by a syntax error
        # in one file would be worse than a Plugin nobody wrote.
        extraction = extract(path, text)
        # A relative import keeps its dots and names no distributed package, and
        # `pyspark.sql.functions` answers for `pyspark` the same way the bare
        # import does.
        names.update(
            reference.partition(".")[0]
            for reference in extraction.references
            if not reference.startswith(".")
        )

    return frozenset(name for name in names if name)


def _planned_files(plan: Plan) -> list[str]:
    """Every path the frozen Plan names, in Plan order, without duplicates."""
    seen: dict[str, None] = {}
    for step in plan.steps:
        for path in step.files:
            if path:
                seen.setdefault(str(path), None)
    return list(seen)


def _answers(
    plugin: Plugin,
    suffixes: set[str],
    imported: Callable[[], frozenset[str]],
    root: Path,
) -> bool:
    """Whether one Plugin answers for this blast radius or this repository.

    Any of the three detections is sufficient, and they are asked in the order
    they cost in: a suffix is a string comparison, a root marker is a `stat`,
    and an import is the blast radius read — which `imported` defers until a
    Plugin actually declares one.
    """
    if any(suffix.lower() in suffixes for suffix in plugin.suffixes):
        return True
    if any((root / marker).exists() for marker in plugin.root_markers):
        return True
    declared = getattr(plugin, "imports", ())
    return bool(declared) and any(name in imported() for name in declared)


__all__ = [
    "IMPORT_SUFFIXES",
    "MAX_FRAGMENTS_PER_ROLE",
    "MAX_FRAGMENT_CHARS",
    "NO_PLUGINS",
    "Activation",
    "activate",
    "commands_for",
    "contributions",
    "extractors_for",
    "fragments_for",
    "gates_for",
]
