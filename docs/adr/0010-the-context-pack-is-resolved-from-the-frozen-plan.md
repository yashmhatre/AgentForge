# ADR-0010: The Context Pack is resolved from the frozen Plan, once per Run

A Workflow invokes six Roles against one Issue, and each of them used to open the repository and rediscover the same files. That rediscovery is exactly what ADR-0003 froze the Plan to remove: the Plan already names the files the work touches, so the reading can be done once and handed to every Role.

Three places could have done it. The Orchestrator could resolve the pack while it plans, and it partly does — what it declares travels in the Issue body and is kept. But it plans before the branch exists and a week before the Run may start, so a pack frozen there would name symbols the repository no longer has. Each Role could resolve its own, which is the rediscovery under another name and lets what two Roles see drift inside one Run. So AgentForge resolves it, at the start of `implement`, from the frozen Plan and the repository as it stands.

The resolver reads and never searches. It resolves what the Plan's steps name, plus the Orchestrator's own declared files, and nothing else — a pack assembled from a fresh scan would be a second opinion about the work, and there is supposed to be exactly one. Per-language extractors turn each file into what it defines and what it reaches for; a file type nobody wrote an extractor for degrades to its path rather than to an error.

Three properties are load-bearing. It is **deterministic**, so two Runs of one Issue are comparable and ADR-0009's measurement means something. It is **bounded**, by caps on files, symbols and references, because a pack larger than the repository costs more than the rediscovery it replaces. And it is **confined to the repository**: an Issue body is editable by anybody who can comment on it, so a Plan naming `../../.ssh/id_rsa` resolves to nothing.

## Consequences

The pack is a head start and never a boundary, and every Role's prompt says so outright. A Role that needs a file the pack does not name reads it, which is what makes a resolver mistake cost tokens rather than correctness.

The pack each Run resolved is posted to its Run Log before the first Agent is invoked, so a Run that went wrong is diagnosed against what its Agents were actually shown. It carries no machine block: it is resolved again on every invocation, so it is a record of a Run rather than a contract the next one reads back.

`--no-context-pack` hands every Role nothing. That is the control Run — the same Issue, the same Roster, no pack — and comparing its total against a packed Run's is the only honest way to find out what the pack is worth.

`ContextPack` gained a `references` field. Added fields with defaults do not bump `PLAN_FORMAT_VERSION`, so an Issue filed before this still parses.
