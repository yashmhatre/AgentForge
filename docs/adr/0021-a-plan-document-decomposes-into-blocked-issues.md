# ADR-0021: Planning cuts a Task into a set of blocked Issues

One Orchestrator pass filed one Issue. For a sentence of work that is right. For a plan document somebody spent an afternoon writing it produces a single frozen contract spanning weeks, and ADR-0003 says what happens to a contract too large to execute without interpretation: every Role that reads it interprets it differently.

So planning grills the human first, synthesizes what was typed and what came back into a Spec, cuts that Spec into Slices, and then plans each Slice on its own. Four invocations. The last one runs once per Slice and produces an ordinary Issue, which is the point — `agentforge implement <n>` is handed nothing it has not seen before.

The stages stay separate because they are separate jobs, and each is delivered exactly the skill for its own. `grill-with-docs` asks. `to-spec` commits to a reading without asking again. `to-tickets` cuts. A single prompt holding all three would interview and commit to a breakdown in the same breath, and the breakdown it committed to would be the one it had before it asked anything.

Both entry points run all four stages. `agentforge plan "<task>"` takes the source as typed; `agentforge decompose <path>` reads it from a file the repository already keeps. They differ nowhere else, so there is one pipeline to reason about rather than a small one and a large one drifting apart.

## Consequences

`agentforge plan` no longer files exactly one Issue. A one-sentence Task cuts to a single Slice and behaves as it always did. Anything larger does not, and code counting Issues per invocation is now wrong.

Ordering is data. A Slice names its blockers, those are filed first, and the edges therefore carry real numbers — written as GitHub's native issue dependencies, which is the representation the tracker's own board filters on. `implement` refuses a Slice whose blockers have not signed off, so nobody has to hold the sequence in their head. `--ignore-blockers` is there because the person who wrote the plan sometimes knows more than the graph does.

This costs more than planning used to. One grill, one Spec, one cut, then a `deep` planning pass for every Slice. All of it is spent before a line of code is written, which is the cheapest place to reject a breakdown — and the breakdown is shown and confirmed before a single Issue exists, because closing fifteen wrong Issues by hand takes considerably longer than saying no once. Where nobody is attached and no `--yes` was passed, nothing is filed.

`PlanDocument` gains `blocked_by`, an added field carrying a default. Issues filed last month still parse, so the plan format version stays at 1.
