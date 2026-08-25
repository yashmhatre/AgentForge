# ADR-0001: Agents run as coding-agent CLI subprocesses

Something has to do the file editing, and three vendors already maintain a tool-use loop that does it better than we would. Every Agent invocation is therefore a subprocess call to a coding-agent CLI behind the `Provider` interface in `providers/base.py`, so AgentForge never touches a model API, handles a token, or implements a tool-use loop — Provider credentials are whatever the user's CLI already uses.

## Considered Options

An agent SDK gives cleaner streaming and structured results, but binds AgentForge to one vendor and puts session, permission, and sandbox handling back on us. Calling an LLM API directly gives maximum control and roughly a year of work already done by other people.

## Consequences

Provider-specific features — native subagents, skills, per-agent model frontmatter — cannot be relied on, which is what ADR-0005 exists to manage. Plugin Commands are therefore specified as scripts and templates first, and as agent instructions only where a script cannot do the job. Each Provider owns its own output parser, so every parser is a place a CLI version bump can break AgentForge.
