# ADR-0019: A Command runs outside a Run, and decides nothing

Scaffolding a dbt model is a file whose shape was never in question, and the only way to get one before this was to ask a Role at `standard` tier — the most expensive way to produce something nobody had a decision to make about, and one that then has to be read for hallucination. A Command is the other half of what a Plugin knows: not what the conventions are, but what the chore is.

**`agentforge run <command> [args]` files no Issue and starts no Run.** The point of a chore is that it should not cost a plan, a branch, six Role invocations, and a pull request. It also needs no git remote, which ADR-0002 makes a hard precondition for a Run and which has nothing to do with writing two files into a working tree.

That leaves the question of which Plugins answer when there is no frozen Plan to read. Activation outside a Run has no blast radius, so what answers is what the repository *is* — its root markers alone (ADR-0017's third axis has nothing to read either, since no file has been named). A dbt project has dbt chores whatever anybody is editing today. The consequence is that a Plugin detected only by suffix or by import contributes no Command anybody can reach from the CLI; a Plugin that wants its chores reachable declares a root marker, which is the same thing as saying that its chores are a property of the repository rather than of one Plan.

**A Command is data.** It declares its positional arguments by name, the files it writes as templates, and the argument vector it runs — and it carries no callable, unlike an Extractor's reader or a validator's check. Those two answer questions; a Command performs an action, and the whole of what it will do should be readable without running it. That is what makes "no inference" a property somebody can check rather than a promise.

Templates are `string.Template` sources (`$name`, `${name}`, `$$` for a literal dollar) rather than `str.format`. A dbt model is Jinja, and a `{}` format string full of `{{ ref(...) }}` would have to double every brace it already carries — a trap that fires the first time somebody adds a macro to a template that looked fine.

**A Command writes into the tree and takes nothing back.** It never replaces an existing file, and it writes nothing at all if any of its targets exists, because half a scaffold is worse than none: the tree carries files whose partner is missing, and re-running cannot finish the job. Rendered paths go through the Context Pack resolver's containment rule, so a template that renders outside the repository is refused rather than clamped — a template path is data, and this is the one other place in the codebase where a path comes from something other than a human. Nothing is staged and nothing is committed: the output is an ordinary diff, reviewed the way ADR-0015 has a human review everything else.

**ADR-0007 governs the process half.** A Command that declares an `argv` is command execution, and the check happens before a single file is written, so a refusal never leaves a half-run chore behind. Typing `agentforge run` is the grant — explicit, attended, and per-invocation, which is exactly the shape that ADR asks for — and a Command reached from inside a Run carries that Run's `--allow-commands`, so a Plugin cannot become the route by which an unattended Agent executes arbitrary code. A Command with no `argv` starts no process and needs no grant: writing a file is what an Agent does anyway.

## Consequences

`core/registry.py` now assembles four tables, and this is the only one with no shipped floor. AgentForge has no chores of its own, so a repository no Plugin answers for gets an empty table and `agentforge run` says so rather than offering a list of things that would fail.

Nothing in the runtime invokes a Command yet. The posture above is enforced at the one function that runs one, so wiring a Command into a Workflow later is a call site rather than a second decision about permissions.

`plugins/sql` ships the first Command: `scaffold-dbt-model`, writing a model and the schema entry beside it. It leaves every judgement visible — what the model selects from, what its description says, what its columns are tested for — because a scaffold that guessed those would be the thing a reviewer has to check, which is the cost this ADR exists to avoid.
