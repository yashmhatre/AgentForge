# AgentForge

AgentForge is a standalone Python framework for coordinating specialized software agents through reusable workflows.

A human states a Task. The Orchestrator files a GitHub issue carrying a frozen plan and the Roster of Roles that will execute it. `agentforge implement <n>` runs that Roster and opens a draft pull request for a human to sign off. No workflow ever merges.

## Status

M1 — the walking skeleton — works: one Role runs end to end through a GitHub issue.

```console
$ agentforge plan "add a retry to the loader"
Filed issue #12: https://github.com/acme/pipelines/issues/12
  Roster: implementer (standard)

Run it with:  agentforge implement 12

$ agentforge implement 12
  [ok] implementer (standard) — Wrapped the fetch in a bounded retry.

Draft pull request: https://github.com/acme/pipelines/pull/13
AgentForge stops at Sign-off. A human merges.
```

The two commands can run on different machines. Nothing is shared between them but the issue number.

Still to come: the Architect, Tester, Security, and Reviewer Roles; the workflow runtime and its gates; context packs; plugins; and `agentforge init`. See [`docs/PLAN.md`](docs/PLAN.md).

## Requirements

- Python 3.11 or newer
- `git`, and a repository with a GitHub remote
- The [GitHub CLI](https://cli.github.com), authenticated
- A coding-agent CLI. `claude` ships supported; `codex` exists to keep the provider port honest.

AgentForge never touches a model API and handles no credentials of its own. Whatever your coding-agent CLI is already authenticated with is what a Run costs.

## Commands

| Command | What it does |
| --- | --- |
| `agentforge plan "<task>"` | Runs the Orchestrator at the `deep` tier and files an issue carrying the plan and roster. |
| `agentforge implement <n>` | Reads issue `<n>`, runs its roster on a branch, posts each result to the issue, and opens a draft PR. |
| `agentforge unslop <file>` | Scans prose for machine-writing tells. Deterministic; no model involved. |

Both agent commands take `--provider` and `--tier`. A bare `--tier deep` moves every Role; `--tier implementer=deep` moves one.

## Project layout

- `core/` — the contracts, the command runner, the GitHub boundary, the plan format, and the run loop.
- `agents/` — the Role definitions and their prompts.
- `providers/` — one adapter per coding-agent CLI.
- `context/`, `plugins/`, `workflows/` — later milestones.
- `skills/` — vendored third-party skills. Never edited in place; see `skills/MANIFEST.yaml`.

Read [`CONTEXT.md`](CONTEXT.md) before writing anything, and [`docs/adr/`](docs/adr/) for the decisions that constrain it.

## Tests

```console
$ pip install -e ".[dev]"
$ pytest
```

The suite runs offline: no network, no GitHub account, and no coding-agent CLI installed. One fake command runner stands in for every external process.
