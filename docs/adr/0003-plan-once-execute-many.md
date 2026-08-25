# ADR-0003: The Issue body is a frozen execution contract

Four Roles in sequence would each explore the repository and re-derive what the Task means, paying the same reasoning cost four times, and token efficiency is a product requirement here rather than an optimization. The Orchestrator therefore reasons once and writes a plan detailed enough to execute without further interpretation; downstream Agents execute against it, never re-plan or re-scope, and are not given the human's original phrasing of the Task. An Agent that finds the plan wrong stops and escalates rather than improvising a correction.

## Consequences

Reasoning is paid for once at the highest Model Tier and execution runs cheaply beneath it, which is what makes the tiering in ADR-0004 worth anything. A frozen plan goes stale — a plan written on Monday can be wrong by Thursday — so escalation is the release valve, and how often it fires is the main signal of whether the Orchestrator is good enough. The plan format is now an interface every Role parses, so changing its shape is a coordinated change across all of them.
