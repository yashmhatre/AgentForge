"""The Context Pack as a Role is handed it.

One renderer for every Role, because the pack is the same head start whatever
the Role does with it, and five copies of this would drift the first time one of
them was improved.

Symbols are grouped under the file they came from rather than listed as forty
`path::name` strings. The pack is stored qualified — that is what survives an
Issue body and what a resolver can be deterministic about — but a prompt that
repeated one path twenty-five times would spend on punctuation the tokens this
whole milestone exists to save.

The two sentences at the top of the block matter as much as the lists. A Role
that reads the pack as an exhaustive account of the repository stops looking,
and a resolver mistake then costs correctness rather than tokens — so the block
says outright what the pack is and what it is not.
"""

from __future__ import annotations

from ..core.contracts import ContextPack

PREAMBLE = """\
AgentBastion resolved this from the frozen Plan before you were invoked, so you \
do not have to rediscover it. It is a head start and not a boundary: if you \
need a file it does not name, read that file.\
"""

#: How a symbol names the file it was read out of. The resolver writes it and
#: this module is the only thing that takes it apart again.
QUALIFIER = "::"


def render_context_block(context: ContextPack) -> str:
    """The `## Context Pack` section of a Role's prompt, or nothing.

    An empty pack renders as an empty string rather than as a heading with
    nothing under it. A Run started with no pack should not spend its prompt
    telling a Role that AgentBastion has nothing for it.
    """
    if not context:
        return ""

    parts = [PREAMBLE]

    if context.files:
        parts.append("**Read these files:**\n\n" + "\n".join(_file_lines(context)))

    loose = _unqualified(context)
    if loose:
        parts.append("**The work is in these symbols:** " + ", ".join(loose))

    if context.references:
        parts.append("**Those files reach for:** " + ", ".join(context.references))

    if context.conventions:
        parts.append("**Follow these conventions:** " + ", ".join(context.conventions))

    return "\n## Context Pack\n\n" + "\n\n".join(parts) + "\n"


def _file_lines(context: ContextPack) -> list[str]:
    """One line per file, carrying the symbols read out of it.

    A file with no symbols is still a line. It is either a file the Run is about
    to create or one of a type no Extractor claims, and in both cases the path
    is the whole of what AgentBastion knows.
    """
    lines = []
    for path in context.files:
        prefix = f"{path}{QUALIFIER}"
        names = [
            symbol.removeprefix(prefix) for symbol in context.symbols
            if symbol.startswith(prefix)
        ]
        listed = ", ".join(names)
        lines.append(f"- `{path}` — {listed}" if listed else f"- `{path}`")
    return lines


def _unqualified(context: ContextPack) -> list[str]:
    """Symbols that name no file in the pack — the Orchestrator's own.

    It declares symbols while it plans and does not always say where they live.
    Dropping those would throw away the one part of the pack a human wrote.
    """
    return [
        symbol
        for symbol in context.symbols
        if not any(symbol.startswith(f"{path}{QUALIFIER}") for path in context.files)
    ]


__all__ = ["PREAMBLE", "QUALIFIER", "render_context_block"]
