"""AgentForge plugin packages.

`BUILT_IN` is the registry: a tuple rather than a dict or a set, because
activation order is the order Fragments reach a prompt in, and a reader
comparing two Run Logs should not have to wonder whether the order meant
anything. Registration is an entry here and a module beside it.

Only `python` is registered at 0.2. The `sql`, `pyspark`, and `databricks`
packages are the next tickets in the Plugins milestone (#57, #60); they exist as
empty modules so that the shape of the directory is not an argument about where
they go.
"""

from __future__ import annotations

from ..core.contracts import Plugin
from .python import PYTHON

#: Every Plugin AgentForge ships, in the order they contribute.
BUILT_IN: tuple[Plugin, ...] = (PYTHON,)

__all__ = ["BUILT_IN", "PYTHON"]
