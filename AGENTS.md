# AgentForge

A Python framework that coordinates specialized software agents through reusable workflows. A human states a Task; the Orchestrator files a GitHub Issue carrying a frozen plan and a Roster of Roles; `agentforge implement <n>` runs that Roster and opens a draft pull request for human Sign-off.

Read [`CONTEXT.md`](./CONTEXT.md) before writing anything. Its terms are load-bearing, and several of them mean something narrower here than in general usage.

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues, accessed through the `gh` CLI. See [`docs/agents/issue-tracker.md`](./docs/agents/issue-tracker.md).

### Triage labels

The five canonical triage roles, each label string equal to its name. See [`docs/agents/triage-labels.md`](./docs/agents/triage-labels.md).

### Domain docs

Single-context: one `CONTEXT.md` and one `docs/adr/` at the repo root. See [`docs/agents/domain.md`](./docs/agents/domain.md).

## Working in this repo

**Decisions are recorded, not re-litigated.** [`docs/adr/`](./docs/adr/) holds the architectural decisions. Six of them constrain almost everything: agents run as coding-agent CLI subprocesses (0001), GitHub Issues carry both the handoff contract and the Run Log (0002), the plan freezes once the Orchestrator writes it (0003), Roles declare a Model Tier rather than a model (0004), skill delivery follows a declared Capability Tier (0005), and third-party skills ship as vendored package data (0006). If your work contradicts one, say so explicitly rather than quietly working around it.

**Everything external is a subprocess, behind one seam.** `gh`, the coding-agent CLIs, and the vendored unslop scanners all route through a single Command Runner port. Add a new external dependency by going through that port, never by calling `subprocess` directly.

**`src/agentforge_framework/skills/` is vendored third-party code.** Never edit it in place. A local change upstream doesn't know about turns every future refresh into a manual merge. Provenance, commit SHAs, and what was deliberately excluded from each bundle are in `skills/MANIFEST.yaml`.

**Tests run offline.** No network, no GitHub account, no coding-agent CLI installed. If a change makes that untrue, the seam is in the wrong place.
