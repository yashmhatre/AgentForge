# ADR-0014: The Roster names the tier that runs

The Roster table in an Issue tells a human which Roles are about to touch their repository and at what Model Tier. Until now that table was decoration: `core/runtime.py` resolved each Step's tier from the CLI flags, the Workflow YAML, and the Role's declared default, and never once read `state.roster`. None of the three shipped Workflows pin a Step tier, so every Run fell through to the Role default and the Orchestrator's per-Role judgement had never taken effect in any Run. The end-to-end smoke Run for #67 filed an Issue advertising `security` at `standard` and then ran it at `deep`, and nothing anywhere said so.

**The Roster is authoritative.** The runtime reads the tier out of the frozen plan block, slotted between the Workflow's Step tier and the Role's default. The full order, highest first: `--tier role=tier`, then `--tier`, then the Workflow's `tier:` on the Step, then the Roster, then the Role's declared default.

The alternative was to stop the Orchestrator choosing tiers at all — drop the tier from the Roster it writes, render the resolved tier in the table, delete the sentence in `align_to_workflow` that promises tiers survive. That is cheaper, and it removes a judgement the Orchestrator was never able to exercise. It was rejected because the judgement is one worth having. ADR-0004 makes a tier a statement of intent rather than a model name, and the Role whose difficulty varies most between two Tasks is exactly the one a per-Task decision helps: the same Security Role reading a config change and a deserialization path is not doing the same work. Deleting the only place that decision could be made in order to make the table honest is fixing the wrong half.

There was a third answer available and it is the one to refuse: render the table from Role defaults and leave the Orchestrator's tier sitting in the plan JSON. That makes the prose and the Run agree by hiding the third thing that disagrees with both, and ADR-0003 makes the plan block the contract — a Run whose contract says `deep` and whose behaviour says `standard` is broken whichever way the table reads.

The Roster sits below the Workflow's Step tier rather than above it, which is the one part of the order that needs an argument. `align_to_workflow` already resolves it that way when it writes the Roster — a Step that pins a tier gives the Roster that tier, and the Orchestrator's request fills the gap only where the definition left one. Reading it back in a different order than it was written would make the two disagree the moment anyone pinned a Step, so the resolution mirrors the alignment. A Workflow that pins a Step is a project stating how that Step is always run; the Roster is the Orchestrator's judgement about one Task, and it fills what the definition did not settle.

Tiers are keyed by Role name, not by position. That is how `align_to_workflow` collapses a requested Roster onto a Workflow, and it is the only reading the table supports: a Workflow naming one Role twice shows that Role once in a table with one tier beside it.

## Consequences

A resumed Run resolves tiers exactly as the invocation that filed the plan would have, because both read the same frozen block. A Roster edited between two invocations changes what the later one costs, and a stale Roster pins an old tier — which is what freezing is for, and is the same rule ADR-0003 already applies to every other field in the block.

An Issue filed before this decision still runs. Its Roster is short — the shipped Roster covers every Step, but `issue_body_v1.md` names one Role for a four-Step Workflow — and a Role the Roster does not mention falls through to its declared default, which is what those Runs did.

`--tier` and `--tier role=tier` still win over everything. The person at the keyboard overriding a tier is answering a question the Orchestrator answered a week ago with less information.

The `align_to_workflow` docstring now describes the code. It claimed tiers survive alignment, and they did — into a plan block nothing read.
