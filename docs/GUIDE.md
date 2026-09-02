# The AgentForge guide

Everything needed to drive AgentForge: what it is, how a Run works, and every command, flag,
Role, Gate and Plugin. [`README.md`](../README.md) is the overview and this is the walkthrough;
[`CONTEXT.md`](../CONTEXT.md) is the authority on what the words mean, and
[`docs/adr/`](adr/) on why any of it is the way it is.

## Contents

- [What it is](#what-it-is)
- [How a Run works](#how-a-run-works)
- [Install and requirements](#install-and-requirements)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Roles and Model Tiers](#roles-and-model-tiers)
- [Workflows](#workflows)
- [Gates and Run States](#gates-and-run-states)
- [Plugins](#plugins)
- [Configuration](#configuration)
- [What a Run may touch](#what-a-run-may-touch)
- [Troubleshooting](#troubleshooting)

## What it is

AgentForge coordinates specialized software agents through reusable workflows. It is a Python CLI
that drives a coding-agent CLI you already have installed, and it keeps every piece of state in one
GitHub issue.

The unit of work is a **Task** — a sentence a human types. AgentForge turns it into an **Issue**
carrying a plan that then never changes, and a **Roster**: the ordered list of Roles that will
execute it. Running that Issue produces a branch, a commit, and a draft pull request.

Five properties are worth knowing before anything else.

- **The Issue is the whole state.** No local run directory, no database, no session.
  `agentforge implement 12` works from a clone that has never seen the Run, including one on
  another machine. See [ADR-0002](adr/0002-github-issues-as-handoff-and-run-log.md).
- **The plan freezes when it is filed.** One Role reasons about what to do; the ones after it
  execute. A Role that finds the plan wrong stops and says so rather than improvising. See
  [ADR-0003](adr/0003-plan-once-execute-many.md).
- **Roles declare a tier, not a model.** A Role asks for `deep`, `standard`, or `cheap`, and each
  Provider adapter maps that onto whatever its CLI accepts. Model names change; tier names do not.
  See [ADR-0004](adr/0004-model-tiers-declared-not-named.md).
- **Your repository's technology reaches the prompts.** A PySpark repository's Roles are told how
  Spark is written; a plain Python one hears nothing about it. See
  [ADR-0016](adr/0016-a-plugin-contributes-through-the-context-pack.md).
- **It stops at Sign-off.** No Workflow ends by merging, and nothing here has the ability to.

What it is not: a model API client (it handles no credentials and never talks to a model directly),
a chat (every Agent invocation is one headless call), or a merge bot.

## How a Run works

| Stage | What happens | Who |
|---|---|---|
| 1. Task | You type a sentence describing the work. | You |
| 2. Plan | The Orchestrator interviews you, then writes a plan and picks a Roster. | Orchestrator, `deep` |
| 3. Issue | Plan and Roster are filed on GitHub. From here the plan is frozen. | `agentforge plan` |
| 4. Context Pack | The files, symbols and conventions the plan touches, resolved once for every Role. | `agentforge implement` |
| 5. Steps | Each Role runs in order on a branch, appending its result to the Issue. | The Roster |
| 6. Gates | Between Steps: a suite, a clean audit, a human, or a check a Plugin added. | The Workflow |
| 7. Sign-off | A draft pull request is opened. You review the diff and merge. | You |

Everything a Run produces has a home, and none of it is on the machine that started it:

| Thing | Where it lives |
|---|---|
| The plan and Roster | The Issue body — the one stable surface ([ADR-0011](adr/0011-the-issue-body-is-the-stable-surface.md)) |
| Each Agent's result | A comment on the Issue: the Run Log |
| Gate verdicts | A comment on the Issue, in prose a human can act on |
| Run status | A label on the Issue — `agentforge:running`, `agentforge:suspended`, … |
| Cost | Every Run Log comment, in whatever unit the Provider reported ([ADR-0009](adr/0009-usage-is-reported-in-the-unit-the-provider-gives.md)) |
| The code | A branch, then a draft pull request |

## Install and requirements

```console
$ pip install agentforge-framework
$ agentforge --version
agentforge 0.2.2
```

The distribution is `agentforge-framework` and it imports as `agentforge_framework`, because an
older, unrelated project holds `agentforge` on PyPI. Installing puts two identical commands on your
path, `agentforge` and `agentforge-framework`; use the short one unless you also have that other
project installed, in which case whichever was installed last wins the short name. See
[ADR-0013](adr/0013-the-name-stays-the-import-path-moves.md).

| Requirement | Why |
|---|---|
| Python 3.11 or newer | Every release from 3.11 upward is tested in CI |
| `git`, and a repository with a GitHub remote | The Issue is the handoff contract |
| [`gh`](https://cli.github.com), authenticated | Every Issue read and write goes through it: `gh auth login` |
| A coding-agent CLI | `claude` ships supported; `codex` exists to keep the Provider port honest |

AgentForge never touches a model API and handles no credentials of its own. Whatever your
coding-agent CLI is already authenticated with is what a Run costs.

## Quick start

### 1. Configure the repository

Run this inside the repository you want AgentForge to work on.

```console
$ agentforge init
Repository: /repo/pipelines
  Languages: Python, SQL, YAML
  Provider:  claude (native capability tier)
  Suite:     `pytest` — a `tests/` directory
  Plugins:   sql
             printed, not written: which Plugins answer is decided per Run
             from the frozen plan's blast radius, not from this file.

Wrote /repo/pipelines/.agentforge/config.yaml
```

Optional — every value has a documented default — but it is the fastest way to find out whether the
repository can host a Run at all, since it raises the same precondition refusals a Run would.

### 2. State the Task

```console
$ agentforge plan "add a retry to the loader"

The Orchestrator has questions before it writes anything down.

  Which loader — the orders one, or the returns feed?
  > orders

  Retry on 5xx only, or timeouts too?
  > both, cap it at three attempts

Filed issue #12: https://github.com/acme/pipelines/issues/12
  Roster: implementer (standard)
  Interview: 2 question(s) answered

Run it with:  agentforge implement 12
```

The interview happens while you are still at the keyboard because the plan freezes the moment it is
filed. Press Enter on an empty line to plan with what it already has. With nothing interactive
attached there is no interview at all.

### 3. Read the Issue before spending anything

The plan is on GitHub in a form a human can judge: what it intends, which files it expects to touch,
and which Roles run at which tier. If it is wrong, edit the Issue body or file a better Task. That
is cheaper than a bad Run.

### 4. Run it

```console
$ agentforge implement 12 --allow-commands
  [ok] implementer (standard) — Wrapped the fetch in a bounded retry.
  [ok] tester (cheap) — pytest: 24 passed.
  [ok] security (deep) — Audited the change; no findings.
  [ok] reviewer (deep) — The change matches the plan. unslop: clean on attempt 2.

  Cost: $0.42 across 4 of 4 Steps

Draft pull request: https://github.com/acme/pipelines/pull/13
AgentForge stops at Sign-off. A human merges.
```

## Commands

| Command | What it does | Invokes a model? |
|---|---|---|
| `agentforge init` | Inspects the repository and writes `.agentforge/config.yaml` | No |
| `agentforge plan "<task>"` | Turns a Task into an Issue carrying a frozen plan and a Roster | Yes — one `deep` call |
| `agentforge implement <n>` | Runs that Issue's Workflow on a branch and opens a draft PR | Yes — one call per Step |
| `agentforge run [<command> args…]` | Runs a chore a Plugin contributes | No |
| `agentforge unslop <file>` | Scans prose for machine-writing tells | No |

### `agentforge init`

| Flag | Meaning |
|---|---|
| `--provider NAME` | Which coding-agent CLI this repository drives. Default `claude`; an unknown name is refused rather than written |
| `-C, --directory DIR` | The repository to configure |
| `--force` | Replace an existing config. Without it, init reports what differs and writes nothing |

It refuses before creating a directory or a file if the repository is not a git repository, has no
`origin`, or has an `origin` that is not GitHub. It writes only what `load_config` reads back and
prints the rest — see [ADR-0020](adr/0020-the-config-file-holds-only-what-is-read.md).

### `agentforge plan "<task>"`

| Flag | Meaning |
|---|---|
| `task` | The work, in your own words. Quote it |
| `--provider NAME` | Coding-agent CLI to drive |
| `--tier TIER` | Model Tier for the Orchestrator: `deep`, `standard`, `cheap` |
| `-C, --directory DIR` | The repository to plan against |

### `agentforge implement <n>`

| Flag | Meaning |
|---|---|
| `issue` | The Issue number. Nothing else is required |
| `--allow-commands` | Let Agents run commands, not just edit files. Off by default, granted for this Run only, never persisted ([ADR-0007](adr/0007-command-execution-is-default-deny.md)) |
| `--tier TIER` | Repeatable. `--tier deep` moves every Role; `--tier implementer=deep` moves one. Either beats the tier frozen in the Issue ([ADR-0014](adr/0014-the-roster-names-the-tier-that-runs.md)) |
| `--provider NAME` | Coding-agent CLI to drive |
| `--no-plugins` | Resolve the Context Pack as usual but activate no Plugins, so no Fragment reaches a prompt. The control for what the Fragments cost |
| `--no-context-pack` | Hand every Role an empty Context Pack. The combined control: it removes the pack and the Fragments together |
| `-C, --directory DIR` | The repository to work in |

Re-running is resuming: `implement` on an Issue that already has a Run Log continues where it
stopped, completed Steps are not repeated, and a suspended Gate is asked again. An Issue with
nothing left to run says so and changes nothing.

### `agentforge run [<command> args…]`

```console
$ agentforge run
Commands this repository's Plugins contribute:

  scaffold-dbt-model <name>
      Write a dbt model and the schema entry beside it.

$ agentforge run scaffold-dbt-model orders
  wrote models/orders.sql
  wrote models/orders.yml

Review them as a diff and commit them yourself: a Command commits nothing.
```

No Issue, no Run, no model, and nothing to review for hallucination. A Command never replaces a file
that already exists, writes nothing at all if any of its targets is in the way, and refuses a path
that would land outside the repository. Which Commands you have depends on which Plugins answer for
the repository — outside a Run, that is what its root markers say. See
[ADR-0019](adr/0019-a-command-runs-outside-a-run-and-decides-nothing.md).

### `agentforge unslop <file>`

| Flag | Meaning |
|---|---|
| `path` | The file to scan |
| `--json` | Emit the full report as JSON instead of a summary |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | It did the thing. For `implement`: the Run reached Sign-off and a draft PR is open |
| 1 | It stopped somewhere a human has to look — a Halted or Suspended Run, an Escalation, an existing config that differs, prose with findings |
| 2 | It could not proceed: a failed precondition, an unknown name, a Run that failed outright |

## Roles and Model Tiers

Six Roles ship. A Role is a definition — a fixed job, a Model Tier, and a prompt — and an Agent is
one Role in execution. Agents never talk to each other: they read the Issue and write to the Run Log.

| Role | Tier | Its job |
|---|---|---|
| `orchestrator` | `deep` | Turns a Task into an Issue: assembles the Context Pack, picks the Roster, drafts the plan. The only Role that reasons about what to do |
| `architect` | `deep` | Designs an approach when a Task needs one before code is written |
| `implementer` | `standard` | Writes the change the plan describes, and nothing the plan does not |
| `tester` | `cheap` | Writes and runs tests. Reports that it could not run the suite rather than reading tests and claiming success |
| `security` | `deep` | Audits the change against production standards and reports Findings it does not fix |
| `reviewer` | `deep` | Speaks last: reports on what everything before it did, and rewrites its own prose against the unslop scan |

A Role declares a tier by intent, never a model name. Each Provider adapter maps the three tiers
onto whatever its CLI accepts:

| Tier | `claude` | `codex` |
|---|---|---|
| `deep` | `opus` | `gpt-5.6-sol` |
| `standard` | `sonnet` | `gpt-5.5` |
| `cheap` | `haiku` | `gpt-5.4` |

The tier beside a Role in the Issue's Roster table is the tier that Step runs at — the Orchestrator's
judgement about how hard this Task is, frozen with the rest of the plan, so a resumed Run costs what
the first invocation would have. A `--tier` flag beats it.

## Workflows

A Workflow is a YAML-declared sequence of Roles with Gates between them. The Orchestrator picks one
while it plans and names it in the Issue.

| Workflow | Steps | When |
|---|---|---|
| `feature` | implementer → tester → security → reviewer | The default: build something that was not there before |
| `bugfix` | implementer → tester → reviewer | A fix, verified and reported on. No Security Step — a bug fix that touches auth is routed to `feature` instead |
| `review` | security → reviewer | Point it at a branch somebody else wrote and it reports on that. The only shipped Workflow with no Implementer |

Drop a YAML file beside the shipped ones and the Orchestrator can choose it:

```yaml
# Hold the Run on the repository's own suite, then on a person.
name: hardened
steps:
  - role: implementer
    gate: tests
  - role: security
    gate: security
  - role: reviewer
    gate: human
```

A definition naming a Role that cannot run, a Gate kind that does not exist, or a tier that was
never a tier is refused at load time, so a typo costs nothing rather than costing a deep-tier
planning pass. The shipped `feature` Workflow declares no Gate on purpose: a Gate suspends the Run
until it clears, and the default stopping to wait on somebody is a choice a project makes rather
than one it inherits.

## Gates and Run States

| Gate | Clears when | Blocks when |
|---|---|---|
| `tests` | The project's declared suite exits zero | The suite ran and reported failures. It re-runs the suite rather than believing what the Tester said about it |
| `security` | The Security Agent's audit reported no Findings | It reported any. The Security Step is marked to run again, so the audit that resumes reads the fixed code |
| `human` | You re-run `agentforge implement`. Coming back is the acknowledgement | The first time it is asked |
| `dbt` | `dbt parse` resolves the project. Contributed by the `sql` Plugin, so it exists only where that Plugin answers | The project no longer resolves — a renamed model, a macro that is gone |

Three verdicts, and the difference between two of them decides your next move:

- **Cleared** — the Run carries on, and nothing is written to the Run Log.
- **Blocked** — the Run is **Suspended**. Nothing is wrong with the plan and the Gate can still
  clear; the next commit may well do it.
- **Errored** — the Run is **Halted**. A Gate that could not evaluate has nothing to clear, so
  waiting would invite a resume that waits again.

| Run State | What happened | Your move |
|---|---|---|
| `planned` | The Issue carries a plan and nothing has run | `agentforge implement <n>` |
| `running` | Steps are executing | Wait |
| `suspended` | A Gate blocked. It can still clear | Fix what it named, then re-run the same Issue |
| `halted` | A Role escalated, or a Gate errored | Correct the plan block on the Issue, then re-run |
| `awaiting-signoff` | Every Step ran and a draft PR is open | Review the diff and merge |
| `failed` | AgentForge could not finish the Run at all | Read the Issue; the last comment says what broke |

An **Escalation** is a Role reporting that the frozen plan does not match the repository, rather than
improvising around it. How often one fires is the honest measure of Orchestrator quality.

## Plugins

A Plugin is a bundle of domain knowledge for one technology. Four ship.

| Plugin | Active when | What it contributes |
|---|---|---|
| `python` | A `.py` or `.pyi` file is in the plan's blast radius | Conventions for the Implementer, Tester and Reviewer: match the module you are editing, annotate new public functions, raise specific exceptions, prefer the standard library |
| `sql` | A `.sql` file is in the blast radius, or a `dbt_project.yml` sits at the root | dbt-aware readers — a model's `ref()` and `source()` targets reach the Context Pack — plus the `dbt` Gate and the `scaffold-dbt-model` Command |
| `pyspark` | A Python file in the blast radius *imports* pyspark ([ADR-0017](adr/0017-a-plugin-is-detected-by-what-the-blast-radius-imports.md)) | DataFrame and Column expressions over RDDs, built-ins before UDFs, a declared schema, named-key joins, no unbounded `collect()` |
| `databricks` | A `databricks.yml`, `databricks.yaml`, or `.databrickscfg` at the root | Unity Catalog three-part naming and Delta MERGE idioms for the Roles that write code; workspace posture for the Security Role — secret scopes, service principals, the narrowest grant |

A Plugin costs nothing where it does not apply. A plain Python repository activates `python` and
hears nothing about Unity Catalog, and detection reads the frozen plan's blast radius, so a
repository with Spark jobs and a plan touching none of them stays quiet too.

The Context Pack comment on every Issue names which Plugins were active and what each contributed,
so a prompt that grew has a reason a human can read. To measure what that costs, run one Issue
twice — once normally, once with `--no-plugins` — and compare the cost lines.

## Configuration

`.agentforge/config.yaml` in the target repository, written by `agentforge init` and read by
everything else.

```yaml
providers:
  claude:
    capability_tier: native
  codex:
    capability_tier: fragment

gates:
  tests:
    suite: [pytest]

context:
  publish_inventory: false
```

| Key | What it decides | Default |
|---|---|---|
| `providers.<name>.capability_tier` | `native` delivers a Role's skills through the CLI's own skill mechanism; `fragment` inlines the same text into the prompt. Declared, never discovered by probing ([ADR-0005](adr/0005-capability-tiers-declared-not-probed.md)) | `claude` native, everything else fragment |
| `gates.tests.suite` | The argument vector the `tests` Gate runs. A string is split the way a shell would; a list is taken as written | `pytest` |
| `context.publish_inventory` | Whether the Context Pack comment names the symbols and imports it resolved, or only counts them. A Run posts that comment to the Issue, and a tracker can have a wider audience than the code ([ADR-0024](adr/0024-the-issue-publishes-no-more-than-the-plan-does.md)) | `false` |

There is no `plugins:` key: which Plugins answer is decided per Run from the frozen plan, so a
repository-level list would be inert and misleading. The file is not a precondition either — without
one, every value falls back to the documented default.

## What a Run may touch

**Agents run no commands unless you open the gate.** Every Provider runs default-deny. Without
`--allow-commands` an Agent may edit files but not execute them, and a Role denied a command it
needs reports that denial rather than substituting inspection. The posture is set in the adapter,
never in prompt text, because a permission expressed as an instruction is one the model can talk
itself out of. See [ADR-0007](adr/0007-command-execution-is-default-deny.md).

**A Run commits only what it declared.** Opening that gate is exactly what makes a Run produce files
nobody asked for: running a suite writes `__pycache__`, coverage data, a cache directory. So
AgentForge commits every change to a file git already tracks, and an untracked file only when the
frozen plan or an Agent's own result named it. Everything else stays in your working tree and is
listed in the pull request under *Left uncommitted*. See
[ADR-0015](adr/0015-a-run-commits-what-it-declared.md).

**It stops at Sign-off.** The terminal Gate is a draft pull request and a human.

A Run also refuses to start on a dirty working tree. Commit or stash first; the alternative is a
diff at Sign-off that mixes your work with an Agent's.

## Troubleshooting

**"…is not inside a git repository" / "has no `origin` remote".** The Issue is the handoff contract,
so a repository with a GitHub remote is a hard precondition. Add the remote, or run somewhere else.

**"`origin` points at … which is not GitHub".** GitHub is the only tracker implemented, and no
configuration changes that.

**The Run suspended and I do not know what it wants.** The last comment on the Issue is the Gate
verdict and says what it is waiting on. Fix that, then re-run the same Issue number.

**A Role escalated.** It found that the frozen plan does not match the repository, which is a report
about the plan rather than a crash. Correct the plan block in the Issue body and re-run.

**The Tester says it could not run the suite.** Either `--allow-commands` was not passed, or
`gates.tests.suite` names something this machine does not have. The distinction matters: a suite
that ran and failed suspends the Run, and a suite that could not run at all halts it.

**A Workflow was refused for naming a Gate kind.** The message lists the kinds that Run has. A
Plugin's Gate exists only where that Plugin answers for the repository, so a Workflow naming `dbt` is
refused where `sql` is silent — nothing there would evaluate it.

**Two `agentforge` commands, and the wrong one answers.** An unrelated project declares an
`agentforge` console script too, and whichever was installed last wins. Use `agentforge-framework`,
which is the same program under a name that cannot be taken.

**Nothing about the Run reached the terminal.** It is all on the Issue: each Agent's result, each
Gate verdict, the cost line, and the status label. A Run survives the terminal it was started from.
