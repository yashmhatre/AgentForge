<title>AgentForge Context</title>

# AgentForge — Shared Vocabulary

This file defines the words AgentForge uses. It holds meanings only. Design decisions live in `docs/adr/`, and mechanics live in the code.

A word appears here once it has been used to settle an argument. If a term is still fuzzy, it does not belong on this page yet.

---

## Task

A unit of software work stated by a human, in a human's words. "Add a late-arriving-facts handler to the orders pipeline" is a task. A task has no structure and no assignee until the Orchestrator processes it.

## Issue

The handoff contract. A GitHub issue whose body carries both the implementation plan and the agent roster that will carry it out. The Issue is the only thing an agent needs in order to start work — no session, no memory, no prior conversation.

An Issue is written once and read many times. See ADR-0003.

## Orchestrator

The component that turns a Task into an Issue. It resolves project context, decides which roles are required, drafts the plan, and files the Issue. The Orchestrator reasons; nothing downstream of it does.

## Role

A named specialization with a fixed job, a model tier, and a prompt. AgentForge ships six: Architect, Implementer, Tester, Security, Reviewer, and the Orchestrator itself.

A Role is a definition. It is not a running thing.

## Agent

A Role in execution — one provider invocation, with a context pack, working against a repository. Agents do not talk to each other. They read the Issue and write to the Run Log.

## Roster

The ordered list of Roles an Issue requires, chosen by the Orchestrator and recorded in the Issue body. A bug fix and a schema migration draw different rosters.

## Provider

An adapter over a coding-agent CLI — `claude`, `codex`, `aider`. A Provider knows how to invoke one CLI headlessly, pass it a model and a prompt, and parse what comes back. Providers are interchangeable by design. See ADR-0001.

## Model Tier

The class of model a Role runs on, declared in configuration rather than in code. Tiers are named by intent — `deep`, `standard`, `cheap` — and each Provider maps a tier onto whatever flag its CLI accepts. See ADR-0004.

## Context Pack

The bounded set of files, symbols, and conventions handed to an Agent at invocation. A Context Pack replaces repository exploration. It is assembled by the resolver from plugin extractors, and it is the reason a Role does not need to read a repository to work in one.

## Plugin

A bundle of domain knowledge for one technology — Python, SQL, PySpark, Databricks. A Plugin contributes extractors, prompt fragments, validators, and Commands. Plugins are what make AgentForge useful in a data engineering repository rather than merely usable in one.

## Command

A repeated data-engineering chore expressed as a template or script that runs with no inference. Scaffolding a dbt model is a Command. Deciding whether the model is correct is not.

## Workflow

A YAML-declared sequence of Roles with gates between them. `feature`, `bugfix`, and `review` ship with AgentForge; projects may add their own.

## Gate

A point in a Workflow where execution stops until a condition is met. A Gate may require a passing test suite, a clean security pass, or a human.

## Sign-off

The terminal Gate. AgentForge opens a pull request and stops. A human merges. No Workflow ends by merging.

## Run

One execution of one Workflow against one Issue. A Run has an identifier, a start, and an end state.

## Run Log

The record of a Run, kept as comments on the Issue. Each Agent appends its result before the next one starts. The Run Log is the reason a Run survives a lost laptop. See ADR-0002.

## Project Context

What AgentForge learns about a target repository at `agentforge init` — its languages, its layout, its conventions, its active plugins. Stored in `.agentforge/config.yaml` in the target repository, not in AgentForge.
