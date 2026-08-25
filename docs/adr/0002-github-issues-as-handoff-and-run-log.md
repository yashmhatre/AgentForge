# ADR-0002: GitHub Issues carry the handoff contract and the Run Log

`agentforge implement #390` takes an issue number as its entire input, so every piece of state a Run needs must be reachable from that number alone. A GitHub issue body holds the plan and the Roster, its comments hold the Run Log, and all access goes through the `gh` CLI — which means a Run resumes on any machine with `gh` and repository access, and a human can follow the pipeline where they already look. There is no Tracker abstraction: Azure DevOps, GitLab, and Jira are unsupported, and no interface pretends they might be.

## Consequences

The target must be a git repository with a GitHub remote; `agentforge init` fails loudly when either is missing. Offline use is not supported, and the test suite mocks `gh` rather than reaching for it. Enterprise data engineering skews toward Azure DevOps, and this locks those shops out until someone spends the day it would take to introduce a port and rewrite the call sites — that estimate is why the abstraction is skipped rather than built speculatively.
