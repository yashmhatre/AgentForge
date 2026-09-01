<title>AgentForge Build Plan</title>

# AgentForge — Build Plan

Vocabulary for everything below lives in [`CONTEXT.md`](../CONTEXT.md). Decisions live in [`docs/adr/`](adr/). This file covers order of work.

**On the numbers.** The milestone numbers here are the ones the code comments use — the Tester arrived in M2, Context Packs are M3, `agentforge init` is M5. The Issue tracker numbered the same work differently: it opened Issue #1 as M1, then skipped a number, so what this file calls M2 was filed and closed as "M3: full Roster and Workflow runtime". Nothing is missing between M1 and M2. Prefer these numbers when writing a comment, and name the milestone rather than its number when writing an Issue title.

## What is being built

A human types `agentforge plan "add a late-arriving-facts handler to the orders pipeline"`. The Orchestrator resolves project context, picks a Roster, writes a plan, and files a GitHub issue.

Later, anyone types `agentforge implement 390`. AgentForge reads issue 390, runs the Roster in order — Implementer, Tester, Security, Reviewer — posting each result to the issue as it goes, and opens a draft pull request for a human to sign off.

No Workflow ever merges.

---

## Four things to fix before any of this works

**The repository is not a repository.** `g:\Projects\AgentForge` has no `.git` and no remote. Every handoff in ADR-0002 runs through `gh`, which needs both. This blocks the first end-to-end test, not the first line of code.

**The package does not install.** `pyproject.toml` declares the distribution as `agentforge`, then tells setuptools to find `core*`, `agents*`, `context*`, `plugins*`, and `providers*`. A `pip install` would drop five generic top-level names into site-packages, where `context` and `core` will collide with something. Move everything under one package directory, and the `pip install + agentforge init` model in ADR-0002 starts working.

**Two Roles have no home.** The skeleton ships Architect, Implementer, Tester, and Reviewer. The pipeline you described also needs Security and Orchestrator. Neither exists in `agents/`.

**Skills are not portable.** The Reviewer is specified to document changes using `/unslop`, and slash commands belong to Claude Code. Under the `claude` Provider this works — headless mode accepts the command in the prompt. Under `codex` or `aider` it does not exist. ADR-0001 covers the degradation path: Commands ship as scripts and templates first, prompt instructions second. Worth knowing before the Reviewer gets written, because it constrains how `/unslop` is invoked.

---

## M1 — Walking skeleton

One Role, running end to end, proving ADR-0001 through ADR-0004 at once. Nothing here is throwaway.

Restructure to `src/agentforge_framework/` and add a `[project.scripts]` entry pointing at the CLI. Delete the top-level `cli.py` once `agentforge_framework.cli:main` replaces it.

Write `core/contracts.py`. It is the load-bearing file in the project and everything else imports from it: `Task`, `Plan`, `Roster`, `Role`, `ContextPack`, `AgentResult`, `RunState`, `ModelTier`. Dataclasses, no behavior. ADR-0003 makes `Plan` an interface parsed by every Role, so its serialized shape gets designed here and changed rarely.

Write `providers/base.py` as an abstract `Provider` with one method — take a Role, a prompt, a Context Pack, a tier, and a working directory; return an `AgentResult`. Then `providers/claude.py` implements it over `claude -p`, mapping `deep`/`standard`/`cheap` onto `--model`. Result parsing lives in the adapter and nowhere else.

Write `core/issues.py` around `gh`: `read_issue`, `post_comment`, `set_label`, `open_draft_pr`. Every GitHub call in the codebase goes through this module, so the day Azure DevOps support becomes real, this is the only file that gets rewritten.

Then the two commands. `agentforge plan "<task>"` runs the Orchestrator at tier `deep` and files an issue. `agentforge implement <n>` reads the issue, runs the Implementer against the frozen plan, posts a result comment, and opens a draft PR.

M1 is done when a task typed on one machine can be implemented from another with nothing shared but an issue number.

## M2 — The full roster

Add `agents/security.py` and `agents/orchestrator.py`, then fill in Tester and Reviewer.

The Tester writes test cases, runs edge cases, and reports flaws. The Security Role audits against production standards and posts findings. The Reviewer reviews the diff against the plan and writes the documentation a human reads at sign-off, using `/unslop` under the `claude` Provider.

Build the Workflow runtime in `core/runtime.py` and give `workflows/*.yaml` real content — the three files are currently `steps: []`. A step names a Role, a tier override, and a Gate. Gates block on a passing test suite, a clean security pass, or a human.

Escalation is the piece that carries the most risk and gets built here rather than deferred. ADR-0003 requires an Agent that finds the plan wrong to stop rather than improvise, so the runtime needs a path for a Role to halt a Run, label the issue, and hand back to a human. How often that fires is the honest measure of Orchestrator quality.

## M3 — Token economy

Two of the three mechanisms you picked are already structural by this point. Tiering arrived with the Provider in M1, and plan-once-execute-many is the shape of the whole pipeline. Context packs are the remaining work.

Build `context/resolver.py` to assemble a Context Pack from a plan — the files and symbols the task touches, and nothing else. Fill in the extractors in `context/extractors/` for Python, SQL, and YAML.

Add a cost line to every Run Log comment. Without measurement, "token efficient" stays an adjective.

## M4 — Plugins

`plugins/python`, `plugins/sql`, `plugins/pyspark`, `plugins/databricks`, each contributing extractors, prompt fragments, validators, and Commands.

The seam landed first (#56, ADR-0016): a Plugin is a frozen data object, `core/registry.py` answers which ones are active for a Run, and a Fragment rides in the Context Pack handed to a Step. `plugins/python` ships one Fragment; the remaining contribution kinds plug into the same registry (#57 Extractors, #58 validators as Gate kinds, #59 Commands still to come). `--no-plugins` is the control that isolates what the Fragments cost, because `--no-context-pack` removes the pack and the Fragments together.

`plugins/pyspark` and `plugins/databricks` landed with #60, and they are the milestone's point rather than two more entries in the tuple: a data engineer gets the conventions their code is held to, and the fifth Plugin is written by reading one of the four rather than by reading the framework. `pyspark` is detected by what a file imports rather than by its suffix, because `.py` says nothing about whether a module is a Spark job (ADR-0017); `databricks` is detected by the workspace markers it declares, and says one thing to the Roles that write code and another to the Security Role.

Validators landed with #58: a Plugin contributes Gate kinds, `core/registry.py` assembles the table one Run is validated and evaluated against, and `sql` ships a `dbt` Gate that holds a Run until `dbt parse` resolves the project. The shipped kinds are reserved and a Workflow naming a kind no active Plugin contributes is refused before a Provider is invoked (ADR-0018).

Prompt fragments are the cheapest quality win in the project: Unity Catalog three-part naming, Delta MERGE idioms, DataFrame API over RDD. A few hundred tokens of convention per Role invocation, and the Implementer stops writing code that a reviewer would reject on sight.

Commands are the expensive-to-build, high-payoff half. Scaffolding a dbt model or a pytest fixture runs as a template with zero inference. Start with the three chores you repeat most; a Command that saves a task nobody runs saves nothing.

Registration goes in `core/registry.py`.

## M5 — Plug and play

`agentforge init` inspects a target repository, detects languages and platform markers, enables the matching plugins, and writes `.agentforge/config.yaml`. It fails loudly when the repository has no git remote, because ADR-0002 makes that a hard precondition.

The config file owns tier mapping, Provider selection, plugin activation, and Gate policy. A team retunes cost without editing a Role.

Dogfood it: run `agentforge init` against AgentForge itself, then build M6 through the pipeline.

---

## Order and risk

M1 and M2 are sequential. M3, M4, and M5 are independent of each other once M2 lands, and M5 is what the phrase "any data engineering project" actually cashes out to.

The largest risk in the plan sits in M2, in the Orchestrator. ADR-0003 makes its output the ceiling on everything downstream, and a vague plan produces four confused Agents instead of one. The failure surfaces late, inside the Implementer, looking like an implementation bug. Escalation counts are the early warning, which is why they get built in the same milestone rather than added after the first bad Run.

The second risk is Provider output parsing. Every adapter owns its own parser, and a CLI version bump breaks it. Pin the CLIs and test the parsers against recorded fixtures.
