# AgentForge

AgentForge is a standalone Python framework for coordinating specialized software agents through reusable workflows.

A human states a Task. The Orchestrator files a GitHub issue carrying a frozen plan and the Roster of Roles that will execute it. `agentforge implement <n>` runs the Issue's Workflow and opens a draft pull request for a human to sign off. No workflow ever merges.

![An Issue carries the frozen plan and the Roster. Each Agent reads that Issue and appends its result as the Run Log. The Workflow ends at a draft pull request that only a human can merge.](docs/assets/one-issue-number.svg)

## Status

The M3 runtime now runs multiple Roles in Workflow order. The default `feature`
Workflow invokes the Implementer, the Tester, and then Security, posting each
Agent Result to the Issue before starting the next Step.

```console
$ agentforge plan "add a retry to the loader"
Filed issue #12: https://github.com/acme/pipelines/issues/12
  Roster: implementer (standard)

Run it with:  agentforge implement 12

$ agentforge implement 12 --allow-commands
  [ok] implementer (standard) — Wrapped the fetch in a bounded retry.
  [ok] tester (standard) — pytest: 24 passed.
  [ok] security (deep) — Audited the change; no findings.

Draft pull request: https://github.com/acme/pipelines/pull/13
AgentForge stops at Sign-off. A human merges.
```

The two commands can run on different machines. Nothing is shared between them but the issue number.

Without `--allow-commands`, the Implementer remains default-deny and the Tester
reports that it could not run the suite; it never substitutes reading tests and
claims completion. The Security Role needs no such flag — auditing is reading.
Still to come: the Architect and Reviewer Roles; context packs; plugins; and
`agentforge init`. See [`docs/PLAN.md`](docs/PLAN.md).

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
| `agentforge implement <n>` | Reads Issue `<n>`, runs its Workflow on a branch, posts each Agent Result, and opens a draft PR. Add `--allow-commands` when the Workflow must execute a suite. |
| `agentforge unslop <file>` | Scans prose for machine-writing tells. Deterministic; no model involved. |

Both agent commands take `--provider` and `--tier`. A bare `--tier deep` moves every Role; `--tier implementer=deep` moves one.

## Project configuration

AgentForge reads `.agentforge/config.yaml` from the target repository when it
exists. M3 is read-only: it never creates the directory or writes the file.
Without a file, the documented Provider capability defaults are Claude
`native` and every other Provider `fragment`.

```yaml
providers:
  claude:
    capability_tier: native
  codex:
    capability_tier: fragment

gates:
  tests:
    suite: pytest
```

A Role declares the Vendored Skills it needs. A native Provider receives them
through its CLI's skill mechanism; a fragment Provider receives the same
`SKILL.md` text appended to the prompt. Capability Tiers are configuration,
never the result of probing an installed CLI.

A Workflow step declaring `gate: tests` runs `gates.tests.suite` and holds the
Run when it fails, posting the output to the Issue. The default is `pytest`. A
string is split the way a shell would split it; a list is taken as written,
which is how a path with a space in it gets named. The Gate runs the suite
itself rather than believing what the Tester said about it, and it needs no
`--allow-commands`: ADR-0007 governs what a Role may run, and this is the
project's own declared suite rather than a command a model chose.

A suite that ran and failed suspends the Run — the commit that fixes it clears
the Gate. A suite that could not be run at all halts the Run, because there is
nothing there for a later Run to clear.

`gate: security` needs no configuration. It reads the Security Agent's Findings
out of the Run Log: none of them clears it, and any of them suspends the Run and
marks the Security Step to run again, so the audit that resumes reads the fixed
code rather than the verdict about the old code.

## Project layout

- `core/` — the contracts, the command runner, the GitHub boundary, the plan format, and the run loop.
- `agents/` — the Role definitions and their prompts.
- `providers/` — one adapter per coding-agent CLI.
- `workflows/` — shipped Workflow definitions; `context/` and `plugins/` are later milestones.
- `skills/` — vendored third-party skills. Never edited in place; see `skills/MANIFEST.yaml`.

Read [`CONTEXT.md`](CONTEXT.md) before writing anything, and [`docs/adr/`](docs/adr/) for the decisions that constrain it.

## Tests

```console
$ pip install -e ".[dev]"
$ pytest
```

The suite runs offline: no network, no GitHub account, and no coding-agent CLI installed. One fake command runner stands in for every external process.
