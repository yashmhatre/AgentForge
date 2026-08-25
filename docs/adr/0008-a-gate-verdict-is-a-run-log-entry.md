# ADR-0008: A Gate's verdict is a Run Log entry of its own

A Gate that blocks on a Role's output has to mark that Role's Step for re-run, or the Run deadlocks: Security finds something, the Gate blocks, a human fixes the code, and the next `agentforge implement` re-runs no completed Step — so the Gate re-reads the same stale finding and blocks again, forever. ADR-0002 leaves exactly one place to record that a Step was invalidated, because Run State is derived from the Issue and nothing else. So a Gate posts its verdict to the Run Log as a comment carrying an `agentforge:gate` block, and `RunState.done_roles` un-retires a Step when a blocked verdict names the Role it was drawn from.

The verdict is not an `AgentResult` and does not travel in a result block. A Gate is not an Agent — the glossary is explicit — and an `Outcome` is a Role's verdict on its own work. Reusing either would have been cheaper, at the price of `parse_run_log` returning things that are not Agent Results, and of a Gate's refusal retiring the very Step it had just refused.

## Consequences

`parse_run_log` still returns Agent Results only; `parse_gate_log` reads the other stream, and `run_state` carries both. A human reading the Issue sees why the Run stopped and which Step will run again, in prose, above the block that says the same thing to the next Run. A Gate that cleared writes nothing, so resuming past one costs no comment.

A Gate entry records the position of the Step it stands behind. That is the one piece of a Gate's identity nothing else recovers — unlike a result's position, which `current_step` derives — and without it a Workflow declaring the same kind of Gate twice would have the first one's verdict answer for the second.

Only Gate verdicts and Agent Results are replayed, so the two markers are now a compatibility surface as much as the plan block is: a Run Log written today is read by an `agentforge implement` running next month.
