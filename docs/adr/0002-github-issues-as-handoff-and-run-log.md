# ADR-0002: GitHub Issues carry the handoff contract and the run log

- Status: Accepted
- Date: 2026-08-24

## Context

Six Roles run in sequence — Orchestrator, Implementer, Tester, Security, Reviewer — and each needs to know what the last one did. That state has to live somewhere. A local run directory is cheap and fast. A database is durable and invisible. An issue tracker is neither cheap nor fast, and every engineer on the team already reads it.

The invocation AgentForge is built around is `implement #390`. The issue number is the entire input. Whatever state the pipeline needs must be reachable from that number alone.

## Decision

A GitHub issue is the handoff contract. Its body holds the plan and the Roster. Its comments hold the Run Log: each Agent appends its result before the next Agent starts. Labels carry Run status.

All access goes through the `gh` CLI. AgentForge does not implement GitHub authentication, and it does not speak to the REST API directly.

There is no Tracker abstraction. Azure DevOps, GitLab, and Jira are not supported, and no interface pretends they might be.

## Consequences

A Run resumes on any machine with `gh` and repository access, because nothing about it is local. A human can read the entire pipeline in the place they already look, and can intervene by commenting.

Every handoff costs API calls, and a long Run makes a noisy issue. Both are accepted.

Enterprise data engineering skews heavily toward Azure DevOps, and this decision locks those shops out. Reversing it later means introducing a port and rewriting every call site, which is a day of work — not a rewrite. That estimate is the reason the abstraction is being skipped now rather than built speculatively.

Two operational preconditions follow, and neither is currently met by the AgentForge repository itself: the target must be a git repository, and it must have a GitHub remote. `agentforge init` fails loudly when either is missing.

Offline use is not supported. Tests mock `gh`.

## Alternatives rejected

**Tracker interface, GitHub first.** One adapter behind an abstraction designed against a single implementation, which is how leaky abstractions get built. Deferred until a second tracker is actually needed.

**Local run directory.** Fast and testable, but `implement #390` from a second machine stops working, and the review thread the team already uses stays empty.
