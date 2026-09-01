# AgentForge

AgentForge coordinates specialized software agents through reusable workflows: a human states a Task, the Orchestrator files an Issue carrying a frozen plan, and a Roster of Roles executes it. This file holds meanings only — decisions live in `docs/adr/`, mechanics live in the code, and a word earns a place here once it has been used to settle an argument.

## Language

### The work

**Task**:
A unit of software work stated by a human, in a human's words. A Task has no structure and no assignee until the Orchestrator processes it.
_Avoid_: Request, ask, prompt

**Issue**:
The handoff contract: a GitHub issue whose body carries the frozen plan and the Roster that will execute it. It is the only thing an Agent needs in order to start work — no session, no memory, no prior conversation. See ADR-0002 and ADR-0003.
_Avoid_: Ticket, task, work item, story

**Roster**:
The ordered list of Roles an Issue requires, chosen by the Orchestrator and recorded in the Issue body. A bug fix and a schema migration draw different Rosters.
_Avoid_: Team, crew, pipeline, lineup

### Who does it

**Orchestrator**:
The Role that turns a Task into an Issue, assembling the Context Pack, choosing the Roster, and drafting the plan. The Orchestrator reasons; nothing downstream of it does.
_Avoid_: Planner, coordinator, manager, dispatcher

**Role**:
A named specialization with a fixed job, a Model Tier, and a prompt. A Role is a definition, not a running thing; AgentForge ships six — Architect, Implementer, Tester, Security, Reviewer, and the Orchestrator.
_Avoid_: Agent, persona, worker, specialist

**Agent**:
A Role in execution — one Provider invocation with a Context Pack, working against a repository. Agents never talk to each other: they read the Issue and write to the Run Log.
_Avoid_: Role, bot, worker, session

**Agent Result**:
The structured verdict one Agent hands back: what it did, whether it completed or escalated, and the prose that reaches the Run Log. A Role owns how it fills one in and never how it is transported.
_Avoid_: Output, response, return value

**Finding**:
Something an Agent noticed and did not fix, carried in its Agent Result as a location, a risk, and why that risk matters in this repository. A Finding is not an Escalation: the plan was executable and was executed. Nothing is stopped by a Finding itself — a Gate reads them, and no Findings at all is what a clean pass looks like.
_Avoid_: Issue, bug, vulnerability, problem

### Running it

**Workflow**:
A YAML-declared sequence of Roles with Gates between them. `feature`, `bugfix`, and `review` ship with AgentForge; projects may add their own.
_Avoid_: Pipeline, process, sequence, playbook

**Step**:
One ordered unit of a Workflow, pairing a Role invocation with the Gate that follows it.
_Avoid_: Stage, phase, task

**Gate**:
A point in a Workflow where execution stops until a condition is met — a passing test suite, a clean security pass, a human, or a check an active Plugin contributes. AgentForge ships three kinds and a Plugin widens that table for one Run; the shipped three cannot be redefined by a Plugin. See ADR-0008 and ADR-0018.
_Avoid_: Checkpoint, guard, stage, barrier

**Sign-off**:
The terminal Gate: AgentForge opens a pull request and stops, and a human merges. No Workflow ends by merging.
_Avoid_: Approval, merge, release, acceptance

**Run**:
One execution of one Workflow against one Issue. A Run has an identifier, a start, and an end state.
_Avoid_: Job, execution, session

**Run State**:
Where a Run has got to — its current step, its status, and the steps already behind it — derived from its Issue and nothing else. There is no local run directory and no database. See ADR-0002.
_Avoid_: Progress, run status, checkpoint

**Halted**:
The Run State a Run enters when a Role escalates or a Gate errors: stopped for good, awaiting a human, with completed steps preserved. Halted is not Suspended, which is a Run waiting on a Gate it can still clear.
_Avoid_: Failed, crashed, aborted, cancelled

**Suspended**:
The Run State of a Run stopped at a Gate that can still clear on its own — a suite that will pass on the next commit, a human who has not looked yet. Nothing is wrong with the plan, which is what separates it from Halted.
_Avoid_: Paused, blocked, waiting, stalled

**Escalation**:
A Role's report, carried in its Agent Result, that the frozen plan does not match the repository. It is the verdict rather than the state: an Escalation Halts the Run, and how often one fires is the signal of Orchestrator quality. See ADR-0003.
_Avoid_: Failure, error, rejection, abort

**Run Log**:
The record of a Run, kept as comments on its Issue, with each Agent appending its result before the next one starts. The Run Log is why a Run survives a lost laptop. See ADR-0002.
_Avoid_: Transcript, history, trace, journal

### What an Agent is given

**Context Pack**:
The bounded set of files, symbols, and conventions handed to an Agent at invocation, resolved from the frozen Plan before the first Role runs. A Context Pack is the exploration already done, which is why six Roles do not each rediscover one repository — and it is a head start rather than a boundary, so a Role that needs something the pack does not carry reads it. See ADR-0010.
_Avoid_: Context, payload, bundle, briefing

**Extractor**:
The per-language reader that turns one file into what it defines and what it reaches for — a Python module's functions and imports, a query's columns and tables, a config file's keys. An Extractor answers for one file and never opens a second, and a file type no Extractor claims degrades to its path rather than to an error. AgentForge ships three, and a Plugin contributes more: where two claim one suffix the first registered wins, and a Plugin's claim beats a built-in one, because a Plugin claims a suffix precisely where it knows something the built-in reader does not. See ADR-0010 and ADR-0016.
_Avoid_: Parser, analyzer, scanner, reader

**Plugin**:
A bundle of domain knowledge for one technology — Python, SQL, PySpark, Databricks — contributing Extractors, Fragments, validators, and Commands. Every contribution is optional. A Plugin is data with no behaviour: it declares the file suffixes, root markers, and imports it answers for, and `core/registry.py` decides which Plugins are active for a Run — an import because a `.py` file says nothing about whether it is a Spark job. Plugins are what make AgentForge useful in a data engineering repository rather than merely usable in one. See ADR-0016 and ADR-0017.
_Avoid_: Extension, module, pack

**Command**:
A repeated data-engineering chore expressed as a template or an argument vector that runs with no inference. Scaffolding a dbt model is a Command; deciding whether the model is correct is not. Contributed by a Plugin, invoked directly as `agentforge run <command>` with no Issue and no Run, and committed by nobody: its output is an ordinary diff. See ADR-0019.
_Avoid_: Script, recipe, macro, tool

**Usage**:
What one Agent invocation consumed, in whatever unit its Provider reports — dollars, tokens, both, or neither. A figure nobody reported is absent rather than zero, so that a Run's total can say how much of itself is missing. The Run Log labels it *Cost*, because that is the question the person reading it is asking. See ADR-0009.
_Avoid_: Cost, spend, price, tokens

**Project Context**:
What AgentForge learns about a target repository at `agentforge init` — its languages, its suite, the Plugins its root markers answer for, and the Provider it will drive. The part that is read back is stored in `.agentforge/config.yaml` in the target repository, not in AgentForge; the rest is printed, because a key nothing consults is a lie told to whoever edits it. See ADR-0020.
_Avoid_: Config, settings, profile, environment

### How an Agent is invoked

**Command Runner**:
The one port every external process routes through — `gh`, `git`, the coding-agent CLIs, and the vendored scanners. Despite the name it does not run Commands; nothing else in the codebase imports `subprocess`.
_Avoid_: Shell, executor, process wrapper

**Provider**:
An adapter over one coding-agent CLI — `claude`, `codex`, `aider` — that knows how to invoke it headlessly, pass it a model and a prompt, and parse what comes back. Providers are interchangeable by design. See ADR-0001.
_Avoid_: Backend, driver, client, engine

**Model Tier**:
The class of model a Role runs on, named by intent — `deep`, `standard`, `cheap` — and declared in configuration rather than in code. Each Provider maps a tier onto whatever flag its CLI accepts. See ADR-0004.
_Avoid_: Model, size, level, strength

**Capability Tier**:
What a Provider's CLI can be relied on to support, declared in configuration rather than discovered by probing. It decides whether a skill reaches an Agent natively or as a Fragment. See ADR-0005.
_Avoid_: Feature flag, support level, capability level

**Fragment**:
Standing instruction inlined into an Agent's prompt. It has two sources. A skill becomes a Fragment where the Provider's Capability Tier offers no native equivalent, and there it is the degraded delivery of that skill, never a second copy of one. A Plugin contributes Fragments outright — the conventions its technology is held to — and those have no native form at any Capability Tier, so they are inlined for every Provider. See ADR-0005 and ADR-0016.
_Avoid_: Snippet, blurb, inline skill, partial

**Vendored Skill**:
A third-party skill shipped inside `src/agentforge_framework/skills/` as package data, invoked as a subprocess and never imported. Provenance and deliberate exclusions live in `skills/MANIFEST.yaml`. See ADR-0006.
_Avoid_: Bundled skill, dependency, plugin, third-party package
