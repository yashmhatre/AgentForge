# ADR-0001: Agents run as coding-agent CLI subprocesses

- Status: Accepted
- Date: 2026-08-24

## Context

AgentForge coordinates roles that edit code, write tests, and audit changes. Something has to do the editing. Three candidates were on the table: call an LLM API directly and build the tool-use loop in-house, import an agent SDK, or shell out to a coding-agent CLI that already has file editing, tool use, and permissions solved.

The target user runs a data engineering repository and already has a coding agent installed. Requiring them to adopt a second, AgentForge-specific agent loop would be a hard sell, and it would put AgentForge in the business of maintaining a tool-use loop that three vendors maintain better.

## Decision

Every Agent invocation is a subprocess call to a coding-agent CLI, behind a `Provider` interface in `providers/base.py`. AgentForge ships adapters for `claude`, and leaves `codex` and `aider` as the proof that the interface is honest.

A Provider takes a Role, a prompt, a Context Pack, a Model Tier, and a working directory. It returns a structured result. It does not stream, and it does not hold a session.

## Consequences

AgentForge never touches a model API, never handles a token, and never implements a tool-use loop. Provider credentials are whatever the user's CLI already uses.

The cost is real. Native subagent definitions, skills, and per-agent model frontmatter are Claude Code features, and they do not exist in `codex` or `aider`. AgentForge cannot use them. Everything a Role knows must travel in the prompt and the Context Pack.

Slash commands are affected the same way. The Reviewer's `/unslop` step works under the `claude` Provider by passing the command in a headless prompt. Under any other Provider it degrades to a prompt fragment, or to nothing. Plugin Commands are therefore specified as scripts and templates first, and as agent instructions only where a script cannot do the job.

Parsing subprocess output is now a first-class problem. Each Provider owns its own result parser, and every parser is a place where a CLI version bump can break AgentForge.

## Alternatives rejected

**Agent SDK in Python.** Cleaner streaming and structured results, but it binds AgentForge to one vendor and puts session, permission, and sandbox handling back on us.

**Direct LLM API.** Maximum control, and roughly a year of work already done by other people.
