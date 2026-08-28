# Changelog

What changed in each release of AgentForge. Dates are the day the tag was cut.

## Unreleased

Nothing yet.

## 0.1.0 — 2026-08-27

The first release. You state a task in your own words; AgentForge files a
GitHub issue carrying a frozen plan, and running that issue produces a draft
pull request that only a human merges.

### The two commands that do the work

- `agentforge plan "<task>"` interviews you while you are still at the keyboard,
  then files one issue carrying the plan, the Roster, and the Workflow to run.
  With nothing interactive attached it plans from what you typed rather than
  waiting for an answer that will never come.
- `agentforge implement <n>` runs that issue's Workflow and opens a draft pull
  request. An issue number is all it needs: no session, no local state, no
  memory of the machine that filed it.

Also `agentforge unslop <path>` to scan prose on its own, and
`agentforge --version`. `agentforge init` is listed in `--help` and exits
non-zero; it is not built.

### What runs

- **Six Roles.** The Orchestrator plans and everything after it executes. The
  Architect, the Implementer, the Tester, the Security Role, and the Reviewer do
  the work, each declaring the class of model it needs rather than a model name.
- **Three Workflows.** `feature`, `bugfix`, and `review`, declared in YAML. A
  project can add its own.
- **Three Gate kinds.** `human`, `tests`, and `security` — a Workflow can stop
  between two steps until a person looks, a suite passes, or an audit comes back
  clean.
- **Two coding-agent CLIs.** `claude` is supported; `codex` exists to keep the
  provider boundary honest. AgentForge touches no model API and holds no
  credentials of its own.

### The issue is the handoff and the log

Everything a run needs is on the issue, and everything it did goes back there:
each Agent appends its result as a comment before the next one starts. A run
survives a lost laptop, and a run that stops says on the issue whether it is
waiting on a gate it can still clear, halted for a person to decide, or failed.

A Role that finds the plan does not match the repository stops and says so
rather than improvising a correction.

### Context Packs and what a run costs

- Before the first Role is invoked, AgentForge resolves a **Context Pack** from
  the frozen plan and hands the same one to every Role, so six agents do not
  each rediscover one repository. It is a head start rather than a boundary: a
  Role that needs a file the pack does not name reads that file.
- **Extractors** for Python, SQL, and YAML read what a file defines and what it
  reaches for. A file type nobody wrote an extractor for is carried by path,
  with nothing claimed about its contents.
- Every Run Log entry ends with **what that step consumed**, in whatever unit
  the CLI reports — dollars from `claude`, tokens from `codex`, and "not
  reported" where a CLI says nothing, because a blank reads as free. The last
  comment carries the run's total.
- `--no-context-pack` hands every Role nothing, so what the pack is worth on
  your own repository is a comparison rather than a claim.

### Safety

Agents edit files but cannot run commands unless you open that gate for a single
run with `--allow-commands`. The grant is never persisted to configuration.

### Prose

The Reviewer writes what a human reads at sign-off, and that prose is scanned
before it is posted. A finding sends the Reviewer its own findings to rewrite
against, twice at most; prose that still scans dirty is posted anyway with the
report attached, because holding a finished run on a cosmetic check trades a
real cost for a stylistic one.

### Stability

The stable surface is the issue body: what AgentForge writes into an issue keeps
parsing, so a run filed by one version resumes under a later one. Everything
importable under `agentforge_framework.*` is private and changes without notice.
See [ADR-0011](docs/adr/0011-the-issue-body-is-the-stable-surface.md).

### About the name

The project is AgentForge and the command is `agentforge`. The distribution is
`agentforge-framework` and it imports as `agentforge_framework`, because an
older and larger project holds `agentforge` on PyPI and imports under that name;
decorating both clears the collision without changing anything a user types. The
issue markers, the status labels, the run branch prefix, and
`.agentforge/config.yaml` are all unaffected. See
[ADR-0013](docs/adr/0013-the-name-stays-the-import-path-moves.md).

### Not in this release

`agentforge init`, which will detect a project's languages and write its
configuration. Plugins carrying the conventions of one technology. Publication
to PyPI — install from the wheel attached to the release, or from a clone.
