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

The general before the specific, which is the order a reader wants them in: a
PySpark job is Python and is held to both, and the Fragment about annotating a
public function reaches its prompt before the one about the DataFrame API. The
four are also four different worked examples of the interface — `python`
contributes Fragments and nothing else, `sql` Extractors and nothing else,
`pyspark` is detected by what a file imports, and `databricks` by a marker at
the root and speaks differently to different Roles — so the fifth Plugin is
written by reading the one nearest to it rather than by reading the framework.
"""

from __future__ import annotations

from ..core.contracts import Plugin
from .databricks import DATABRICKS
from .pyspark import PYSPARK
from .python import PYTHON
from .sql import SQL

#: Every Plugin AgentForge ships, in the order they contribute.
BUILT_IN: tuple[Plugin, ...] = (PYTHON, SQL, PYSPARK, DATABRICKS)

__all__ = ["BUILT_IN", "DATABRICKS", "PYSPARK", "PYTHON", "SQL"]
