# AgentForge

AgentForge is a standalone Python framework for coordinating specialized software agents through reusable workflows.

## Status

The project currently contains the initial structure only. Runtime behavior, provider integrations, plugins, and workflow execution will be added in later iterations.

## Requirements

- Python 3.11 or newer

## Project layout

- `core/` contains runtime, routing, registry, orchestration, and contract modules.
- `agents/` contains role-specific agent modules.
- `context/` contains context resolution and format extractors.
- `plugins/` contains language and platform plugin locations.
- `providers/` contains provider interfaces and integrations.
- `workflows/` contains workflow definitions.
- `cli.py` is the future command-line entry point.
