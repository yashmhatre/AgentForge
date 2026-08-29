"""The Python Plugin: what a Python change is held to here.

Deliberately narrow. This ships one Fragment, aimed at the Roles that write and
check code, and says only things that change what an Agent produces. A
convention an Agent would have followed anyway is tokens spent on agreement.

No root markers: this Plugin answers for the blast radius alone. A repository
with a `pyproject.toml` and a Plan that touches only SQL is not doing Python
work, and holding that Run to Python conventions would be the first way this
mechanism starts costing more than it returns.
"""

from __future__ import annotations

from ...core.contracts import Fragment, Plugin

#: Aimed at the three Roles that produce or judge code. The Security Role is
#: absent on purpose: it audits against production standards, and a style
#: convention in its prompt competes with that rather than supporting it.
_CONVENTIONS = """\
Follow these Python conventions unless the file you are editing plainly does otherwise:

- Match the module you are editing. Its import grouping, quote style, and naming
  are the convention here, and a file that reads as two styles costs every
  future reader more than either style saves.
- Type-annotate new public functions and dataclass fields. Leave existing
  unannotated signatures alone unless the Plan names them.
- Raise a specific exception with a message naming what was wrong and what was
  expected. A bare `raise Exception` or a swallowed `except: pass` is a defect,
  not a shortcut.
- Prefer a standard-library answer to a new dependency. Adding one is a decision
  the Plan has to have made.
- Tests assert on behaviour through the public surface, not on private helpers.
  A test that breaks on a rename was testing the rename.\
"""

PYTHON = Plugin(
    name="python",
    suffixes=(".py", ".pyi"),
    fragments=(
        Fragment(text=_CONVENTIONS, roles=("implementer", "tester", "reviewer")),
    ),
)

__all__ = ["PYTHON"]
