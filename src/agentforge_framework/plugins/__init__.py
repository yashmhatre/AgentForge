"""AgentForge plugin packages.

`BUILT_IN` is the registry: a tuple rather than a dict or a set, because
activation order is the order Fragments reach a prompt in, and a reader
comparing two Run Logs should not have to wonder whether the order meant
anything. Registration is an entry here and a module beside it.

Order is also precedence where two Plugins claim one file suffix: the first
registered wins, and `core.registry.extractors_for` is where that is stated and
applied. `python` before `sql` is not a judgement about which matters more —
they claim disjoint suffixes — but the tuple is the tie-breaker the day two of
them do not.

`python` and `sql` are registered. The `pyspark` and `databricks` packages are
the last ticket in the Plugins milestone (#60); they exist as empty modules so
that the shape of the directory is not an argument about where they go.
"""

from __future__ import annotations

from ..core.contracts import Plugin
from .python import PYTHON
from .sql import SQL

#: Every Plugin AgentForge ships, in the order they contribute.
BUILT_IN: tuple[Plugin, ...] = (PYTHON, SQL)

__all__ = ["BUILT_IN", "PYTHON", "SQL"]
