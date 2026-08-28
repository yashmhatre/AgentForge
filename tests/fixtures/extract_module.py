"""A recorded Python module, read by the extractor test and by nothing else.

It is deliberately ordinary: a couple of imports, a function, a class with two
methods, and one name defined inside a function that must not reach the pack.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyyaml_stand_in
from agentforge_framework.core.contracts import Plan


def load(path: Path) -> dict:
    def parse(text: str) -> dict:
        return json.loads(text)

    return parse(path.read_text())


class Loader:
    """Reads a plan off disk."""

    def read(self, path: Path) -> Plan:
        return Plan(summary=str(path))

    async def read_async(self, path: Path) -> Plan:
        return self.read(path)
