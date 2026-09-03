# Changelog

What changed in each release of AgentForge. Dates are the day the tag was cut.

## 0.2.4 — 2026-09-03

What a Run says about itself, made true. ADR-0007 has promised since 0.1 that an
Agent runs no commands unless the gate is opened, and neither Provider was
keeping that promise — so this release is one finding, its fix, and the tier
work that came out of reading what the two adapters actually send.

**Before upgrading:** a `codex` Run without `--allow-commands` is now refused
before it spends anything, where it used to start and execute anyway. That is
the point rather than a side effect, and it will stop invocations that worked
yesterday. Pass `--allow-commands` deliberately, or run those Workflows on
`claude`.

- **Both denied postures now deny.** Neither did. `claude`'s `acceptEdits`
  governs edits and hands commands to an auto-approving classifier, and
  `codex exec` discards `--ask-for-approval` and always runs `never` — so a Run
  without `--allow-commands` executed arbitrary shell commands on both
  Providers while the Run Log said it could not, and ADR-0007's default-deny
  had never been in force. `claude` now carries an `ask` rule on `Bash` and
  `PowerShell` inline to `--settings`, which refuses the command, tells the
  Role, records it in `permission_denials`, and outranks a permissive rule in
  the user's own settings file. `codex` has no denied posture to carry: the
  approval policy that would consult an allowlist is rejected by its CLI, and
  editing and executing are the same channel there, so a denied Run is refused
  before it spends anything and names both ways forward. Verified by
  invocation. See [ADR-0007](docs/adr/0007-command-execution-is-default-deny.md),
  amended.
- **Neither CLI's denied posture denies, and neither expresses a bounded command
  set.** The M5 allowlist ADR-0007 deferred was unblocked, so #108 went to ask
  the two CLIs whether they could carry a project's declared suite in the
  argument vector. Checked by invocation against `claude` 2.1.251 and
  `codex-cli` 0.147.0, not from help text. `--allowedTools` is additive rather
  than exclusive, `codex exec` discards `--ask-for-approval` and always runs
  `never`, and the approval policy that reads execpolicy rules is unreachable
  from the CLI. Both adapters' `DENIED` posture executed an arbitrary shell
  command on a Run that had not been granted one, which means ADR-0007's
  default-deny has not been in force on either Provider. Nothing is fixed here:
  the spike changed no code, and the postures are now a defect to file rather
  than an enhancement to build. See
  [ADR-0025](docs/adr/0025-neither-cli-expresses-a-bounded-command-set.md).
- **Reasoning effort is a Role's to declare, not a tier's to imply.** A Role now
  carries two axes: a Model Tier that picks the model and an Effort that picks
  how hard it thinks. `claude --effort` had never been sent at all, so an Agent
  reasoned as deeply as its model happened to default to; `codex` sent one
  pinned value for every Role alike. Both now send what the Role asked for.
- **Security audits at `standard` and thinks at `high`.** Its `deep` was bought
  with the one argument in ADR-0004's table that is not about cost — a missed
  finding is silent — and that argument was about reasoning, not model size. It
  keeps the depth, above the Implementer whose work it reads, and stops paying
  frontier prices for it. The row to move back if audits start missing things.
- **Model identifiers are pinned rather than aliased.** `claude` ran on `opus` /
  `sonnet` / `haiku`, which follow whatever the CLI currently calls latest — a
  pinned tier mapping that silently re-points is the failure ADR-0004 exists to
  prevent. The tiers now name `claude-opus-5`, `claude-sonnet-5` and
  `claude-haiku-4-5`. `codex` moves to the same-generation spread its own
  adapter named as the alternative it had declined: `gpt-5.6-sol`,
  `gpt-5.6-terra`, `gpt-5.6-luna`.
- **`.agentforge/config.yaml` overrides both axes, and the model table.** ADR-0004
  promised in 0.1 that users could override the tier-to-model mapping without
  touching Role definitions, and nothing ever read the key. `providers.<name>.models.<tier>`,
  `roles.<name>.tier` and `roles.<name>.effort` are now read. `roles.<name>.model`
  is refused with an error rather than ignored — a model named per Role does not
  survive a release and does not port across Providers. See
  [ADR-0004](docs/adr/0004-model-tiers-declared-not-named.md), amended.

## 0.2.3 — 2026-09-02

Two disclosure decisions and the answers to a compatibility question. Nothing
here is a fix to a broken feature; 0.2.2 was that. What changes is what a Run
publishes about a codebase, and what a pull request tells the human signing it
off.

- **The Context Pack comment no longer publishes a map of the codebase.** A Run
  posted every symbol it resolved and the import graph between them to the
  Issue, which can have a wider audience than the code. The counts and the file
  list stay — the frozen Plan already carries the paths, and a count is not a
  map — and the names are withheld unless `context.publish_inventory: true`
  says the tracker's audience is the code's audience. `agentforge init` writes
  the key. See
  [ADR-0024](docs/adr/0024-the-issue-publishes-no-more-than-the-plan-does.md).
- **A pull request names the committed files no Agent claimed.** A Run commits
  every change to a file git already tracks, however it got there (ADR-0015) —
  which is right when the only writers are the Agents and their commands, and
  is how a second agent sharing the checkout gets its half-finished work
  committed into the Run's branch and attributed to a Role. The commit rule is
  unchanged; the body now lists what nothing in the Run said it wrote, so the
  reader at Sign-off sees it. See
  [ADR-0023](docs/adr/0023-a-commit-names-what-nothing-claimed.md).
- **Antigravity IDE is documented as an environment, not a Provider.** Checked
  by running it: `antigravity-ide chat` is a window launcher that returns
  immediately with nothing on stdout, so there is nothing for an adapter's
  `parse_output` to read. Recorded in the README so it is not re-derived.
- **The README says to clone and install editable.** Patching an installed
  `site-packages` copy is the one change nothing catches, and it is what
  happened the last time a Run failed badly enough to look unusable.

## 0.2.2 — 2026-09-02

The release that makes 0.2.1 true. `agentforge decompose` shipped as 0.2.1's
headline feature and could not complete a single invocation: it failed at the
first stage on every platform, at the second once that was fixed, and at the
third on Windows. Three bugs, none of which 661 offline tests could reach, all
found by running the thing against a real model. This release is those fixes
and the live run that proves them — `decompose` now cuts a plan document into
Slices end to end.

Anyone on 0.2.1 who tried `decompose` or `plan` should upgrade; there is no
workaround on 0.2.1 and no configuration that avoids the failure.

- **The prompt reaches a coding-agent CLI on standard input, not in argv.**
  Windows caps a command line at 32,767 characters and an AgentForge prompt
  carries a Spec or a Context Pack, so `decompose` died as `[WinError 206]` on
  any plan document worth decomposing. Linux allows roughly 2MB, which is why
  every `ubuntu-latest` job in CI passed throughout. Always stdin rather than
  stdin past a size threshold, so the path a Run takes is the path the tests
  take. `build_argv` no longer takes the prompt — an out-of-tree adapter
  implementing the Provider port needs the same change.
- **CI runs the suite on Windows.** One job at the floor of `requires-python`.
  The bug above was found by hand, on the author's own machine, after a release.
- **A skill that forbids model invocation is delivered as a Fragment at every
  Capability Tier.** `to-spec` and `to-tickets` are both marked
  `disable-model-invocation` upstream, so offering them natively got them
  refused by the Skill tool and the planning stage escalated instead of running
  — the better the Provider, the more certainly it failed. The set is read from
  the vendored bundle rather than listed in our source, so a refresh that marks
  another skill is picked up rather than missed. See
  [ADR-0022](docs/adr/0022-a-skill-can-refuse-native-delivery.md).
- **`agentforge decompose` and `agentforge plan` work against a real model.**
  The two prompts added in 0.2.1 asked for their own block and stopped, and a
  Provider fails any invocation that comes back without a result block -- so
  the pipeline failed on its first call every time. Both stages now ask for
  their block and then a verdict, and either can escalate rather than guess.
  0.2.1's headline feature did not work; this is the fix.
- **The release verification no longer races PyPI's index.** An index takes an
  upload some seconds before it serves it, so the clean-environment install
  check asked too early and failed the 0.2.1 release over a package that was
  fine. Resolution is now retried against a five-minute deadline; a version
  that resolves and then answers wrong still fails immediately, because that is
  the failure the check exists to catch. The rehearsal pins the version it just
  uploaded rather than installing whatever TestPyPI already had.

## 0.2.1 — 2026-09-02

A patch number carrying one breaking change, which is a deliberate exception
rather than an oversight: 0.3.0 is reserved for a major release, so this went
under it. If you script `agentforge plan` and count the Issues it files, read
the first section before upgrading.

### Planning cuts a Task into a set of Issues

Planning was one pass that filed one Issue, which is the right shape for one
sentence and the wrong shape for anything larger. It is now four stages, and
both entry points run all four: grill the human, synthesize a Spec, cut it into
Slices, then plan each Slice into an Issue of its own. See
[ADR-0021](docs/adr/0021-a-plan-document-decomposes-into-blocked-issues.md).

- **`agentforge decompose <path>`** reads a plan document the project already
  keeps and runs it through the same pipeline. This is the way in for work that
  was written down before AgentForge was asked about it.
- **`agentforge plan "<task>"` no longer files exactly one Issue.** A
  one-sentence Task cuts to one Slice and looks as it always did; anything
  larger files one Issue per Slice. Anything counting on one Issue per
  invocation needs to count differently.
- **The vendored skills do the work they were shipped for.** `grill-with-docs`
  interviews, `to-spec` synthesizes without interviewing, `to-tickets` cuts.
  One skill per stage: a pass holding the method for a job it is not doing yet
  is a pass that starts doing that job.
- **The cut is shown before anything is filed.** `--yes` skips the confirmation;
  with nobody at a terminal and no `--yes`, nothing is filed at all.
- **Blocking edges are real.** A Slice names what must finish first, blockers
  are filed first so the edges carry Issue numbers, and they are recorded as
  GitHub's native issue dependencies as well as in the frozen plan block.
  `agentforge implement` refuses an Issue whose blockers have not reached
  Sign-off, and `--ignore-blockers` is how you say you know better.
- **Every filed Issue wears `agentforge:planned` and `ready-for-agent`.** A
  Slice is agent-grabbable by construction, so saying so is the point of filing
  it.
- **`PlanDocument` gains `blocked_by`**, an added field with a default. An
  Issue filed before this still parses and the plan format version does not
  move.
- **`Spec` and `Slice` enter the glossary.** Both are narrower here than in
  general usage: a Spec is an intermediate nothing executes against, and a
  Slice stops existing the moment it becomes an Issue.

### The install

- `pip install agentforge-framework` is the install, and the README says so.
  0.2.0 is the first release on PyPI, published by `release.yml` through
  Trusted Publishing: no API token exists to leak or rotate, and the workflow
  proves the install from a clean environment after the upload rather than
  claiming it.

## 0.2.0 — 2026-09-01

Plugins, and one command to set a repository up. A repository's technology now
reaches the prompts of the Roles that work on it, contributes the readers its
files are read with and the Gate kinds its Workflows can name, and carries the
chores it repeats. A repository that matches no Plugin runs exactly as it did.

### Plugins reach a prompt

- **A Plugin contributes domain knowledge through one registry.** A Plugin is a
  frozen data object naming the file suffixes and root markers it answers for
  and what it contributes; every contribution is optional. `core/registry.py`
  answers which Plugins are active for a Run, detected from the frozen Plan's
  blast radius, deterministic for a given Plan and repository.
- **`python` ships one Fragment**, reaching the Implementer, Tester, and
  Reviewer. A repository no Plugin claims produces exactly the prompts it
  produced at 0.1.
- **`--no-plugins`** runs an Issue with the Context Pack resolved and no
  Fragments, which is the control for what the Fragments cost.
  `--no-context-pack` removes both, as it always did. See
  [ADR-0016](docs/adr/0016-a-plugin-contributes-through-the-context-pack.md).
- The Context Pack comment names the active Plugins and what each contributed,
  and names any that raised and were skipped.

### A Plugin reads the files it knows about

- **A Plugin contributes Extractors.** `core/registry.py` assembles the
  extractor table for a Run — the built-in three as the floor, widened by the
  active Plugins — and the resolver reads with whatever table it is handed. A
  Plugin's reader beats a built-in one for a suffix it claims; two Plugins
  claiming one suffix resolve by registration order, first wins.
- **`sql` ships two readers and no Fragment.** A dbt model's `ref()` and
  `source()` targets are carried as references, in front of the table names a
  generic read finds, because those are the names the repository actually
  contains. A dbt schema file is read as dbt: a model and a column are symbols,
  a column is qualified by its model, and a test is a reference — it is what
  breaks when the column changes.
- YAML that is not dbt-shaped falls through to the reader that already handled
  it, so a repository with a `dbt_project.yml` still gets ordinary YAML read
  ordinarily.
- A Plugin's Extractor is a pure function of file text, is held to the
  resolver's caps, and costs the pack one file's contents rather than the Run
  when it raises. A repository with no active Plugin resolves exactly the pack
  it resolved before.

### The conventions a data engineering repo is held to

- **`pyspark` ships one Fragment**, reaching the Implementer, Tester, and
  Reviewer: DataFrame and Column expressions over RDDs, built-ins before UDFs,
  a declared schema, and a bounded `collect()`.
- **A Plugin can be detected by what the blast radius imports.** `.py` is the
  suffix of a Spark job and of a Django view, so `pyspark` declares the module
  rather than the suffix and stays silent next door. Detection reads the files
  the frozen Plan names, through the resolver's own containment and size
  bounds, with the Python extractor rather than a pattern — so the word in a
  docstring is not an import and a file that will not parse switches nothing
  on. See [ADR-0017](docs/adr/0017-a-plugin-is-detected-by-what-the-blast-radius-imports.md).
- **`databricks` ships two Fragments and speaks differently to different
  Roles.** The Implementer, Tester, and Reviewer get Unity Catalog three-part
  naming and the Delta MERGE idioms; the Security Role gets the workspace
  instead — secret scopes, service principals over personal access tokens,
  the narrowest grant, and what a notebook widget is. It activates on the
  workspace markers it declares, because a notebook imports nothing.
- A plain Python repository activates neither, and its prompts are unchanged.

### A Plugin holds a Run on its own check

- **A Plugin contributes Gate kinds.** `core/registry.py` assembles the Gate
  table for a Run — the shipped three as the floor, widened by the active
  Plugins — and hands it to the Workflow parser and to the evaluator, so a
  definition loads and is evaluated against one table. A Workflow names a
  Plugin's Gate in the YAML it already writes, and nothing in the runtime knows
  a kind.
- **`sql` ships the `dbt` Gate**, which runs `dbt parse`: a project that no
  longer resolves blocks the Run rather than reaching Sign-off, a dbt that never
  ran to a verdict halts it, and neither needs a warehouse, a profile, or data.
- A Plugin cannot redefine `human`, `tests`, or `security`, and a validator that
  raises becomes an errored verdict naming the Plugin rather than a traceback.
  See [ADR-0018](docs/adr/0018-a-plugin-widens-the-gate-table-a-run-is-validated-against.md).
- A Workflow naming a Gate kind no active Plugin contributes is refused before a
  Provider is invoked, and the refusal names the kinds this Run does have.

### A chore runs without a Run

- **`agentforge run <command> [args]`** invokes a Plugin's Command directly: no
  Issue, no Run, no branch, no model, and no git remote. `agentforge run` with
  no name lists what this repository's Plugins contribute, and an unknown name
  exits non-zero naming what there is.
- **`sql` ships `scaffold-dbt-model`**, which writes a model and the schema
  entry beside it and leaves every judgement visible -- what it selects from,
  what its description says, what its columns are tested for.
- **A Command is data**: its arguments, the files it writes as templates, and
  the argument vector it runs, so what it will do is readable without running
  it. It never replaces an existing file, writes nothing at all if one of its
  targets is in the way, refuses a template path that renders outside the
  repository, and commits nothing.
- A Command that runs a process runs it through the Command Runner and is bound
  by ADR-0007: typing `agentforge run` is the grant, and a Command reached
  inside a Run carries that Run's `--allow-commands`. See
  [ADR-0019](docs/adr/0019-a-command-runs-outside-a-run-and-decides-nothing.md).

### Setting a repository up is one command

- **`agentforge init`** inspects the repository in the working directory,
  reports what it found -- the languages git tracks, the Plugins its root
  markers answer for, the suite it detected and the evidence for it -- and
  writes `.agentforge/config.yaml`. `--help` no longer says "not built yet".
- It refuses before creating a directory or a file when the repository is not a
  git repository, has no `origin`, or has an `origin` that is not GitHub, with
  the refusal that names ADR-0002. Setup is a better place to learn that than
  the first Run's halt.
- **It writes only what `load_config` reads**, and prints the rest: there is no
  `plugins:` key, because activation is decided per Run from the frozen plan's
  blast radius. Every value it writes carries a comment saying whether it was
  detected and on what evidence. See
  [ADR-0020](docs/adr/0020-the-config-file-holds-only-what-is-read.md).
- Re-running reports how an existing config differs from what init would write
  and writes nothing; `--force` replaces it. The comparison is made against the
  values the loader reads, so a file you reformatted is not a difference.

## 0.1.0 — 2026-08-28

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
  The Orchestrator may move one for a task it judges harder or easier than
  usual, and the tier beside a Role in the issue's Roster table is the tier that
  step runs at — so the table a human reads before approving anything is what
  the run actually costs. `--tier` at the command line still wins. See
  [ADR-0014](docs/adr/0014-the-roster-names-the-tier-that-runs.md).
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

Opening it is also what makes a run produce files nobody asked for: running a
suite writes bytecode, and possibly coverage data and a cache directory.
AgentForge commits every change to a file git already tracks, and an untracked
file only when the frozen plan or an agent's own result named it. Everything
else stays in your working tree and is listed in the pull request under *Left
uncommitted*, so a repository with no `.gitignore` still gets a diff that is
only the work. AgentForge does not write a `.gitignore` for you. See
[ADR-0015](docs/adr/0015-a-run-commits-what-it-declared.md).

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
older and larger project holds `agentforge` on PyPI and imports under that name.
The issue markers, the status labels, the run branch prefix, and
`.agentforge/config.yaml` are all unaffected.

`agentforge-framework` is installed as a second name for the same command. That
other project declared an `agentforge` console script in its 0.5.0 through
0.6.5, so on a machine carrying one of those the short name belongs to whichever
was installed last. The long name is always this one. See
[ADR-0013](docs/adr/0013-the-name-stays-the-import-path-moves.md).

### Not in this release

`agentforge init`, which will detect a project's languages and write its
configuration. Plugins carrying the conventions of one technology. Publication
to PyPI — install from the wheel attached to the release, or from a clone.
