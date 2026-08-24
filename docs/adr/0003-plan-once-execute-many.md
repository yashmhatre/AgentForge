# ADR-0003: The issue body is a frozen execution contract

- Status: Accepted
- Date: 2026-08-24

## Context

A newcomer running a coding agent burns most of their tokens before any code changes. The agent explores the repository, re-derives what the task means, re-reads files the last step already read, and rebuilds an understanding it had ten minutes ago. Four Roles in sequence pay that cost four times.

Token efficiency is a product requirement for AgentForge, not an optimization. The system is aimed at people who should not have to learn how to keep an agent cheap.

## Decision

The Orchestrator reasons once. It resolves context, chooses the Roster, and writes a plan detailed enough to execute without further interpretation. That plan goes into the issue body and is treated as frozen.

Downstream Agents execute against the plan. They do not re-plan, they do not re-scope, and they are not given the original human phrasing of the Task.

An Agent that finds the plan wrong stops and escalates. It does not improvise a correction.

## Consequences

Reasoning is paid for once at the highest Model Tier, and execution runs cheaply beneath it. This is what makes tiering in ADR-0004 worth anything.

A frozen plan goes stale. The repository moves, and a plan written on Monday can be wrong by Thursday. Escalation is the release valve, and how often it fires is the main signal of whether the Orchestrator is good enough.

Orchestrator quality becomes the ceiling on system quality. A vague plan produces four confused Agents rather than one, and the failure shows up late, in the Implementer, wearing a disguise.

The plan format is now an interface. Every Role parses it, so changing its shape is a coordinated change across all of them.

## Alternatives rejected

**Each Role plans its own step.** Robust against staleness, resilient to a weak Orchestrator, and it reintroduces exactly the repeated-reasoning cost this decision exists to remove.

**One continuous session carrying context forward.** Simplest to build, and the most expensive per Run — every stage pays for the full accumulated history.
