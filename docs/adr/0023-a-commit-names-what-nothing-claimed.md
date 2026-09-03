# ADR-0023: A commit names the files nothing claimed

ADR-0015 settled what a Run commits: every change to a file git already tracks,
and an untracked file only when the Run declared it. The reasoning for the
tracked half is that refusing an edit the Plan forgot to name would silently
drop an Agent's work, which is worse than a stray artifact because the artifact
is visible in the diff and the missing edit is not.

That reasoning holds while the only writers are the Agents and the commands
they were allowed to run. It has one unstated premise: that everything in the
working tree got there because of this Run. AgentForge run from inside an IDE
breaks it — the IDE's own agent edits the same checkout while a Run is going,
and its half-finished work is committed into the Run's branch and attributed to
a Role (#101). Nothing about the file on disk distinguishes the two, and
snapshotting `git status` first does not help, because the Agents and the suite
are writing during the Run for the same reason they always were.

The commit rule does not change. Refusing an undeclared edit to a tracked file
would reintroduce exactly the failure ADR-0015 rejected, and would do it on
every Run to protect against a case that needs two agents and one checkout.
**What changes is that the pull request names the committed files no Agent
claimed.** The Plan's paths and every `files_changed` are already written down,
so the set costs nothing to compute, and Sign-off is where ADR-0015 already
says a human is being asked to look.

This is disclosure, not detection. A Run cannot tell a concurrent agent's edit
from an Agent that under-reported `files_changed`, and both belong in the same
list for the same reason: something is in the diff that nothing in the Run says
it wrote.

## Consequences

A Role that under-reports `files_changed` now shows up in the pull request body
rather than only losing untracked files. That is a second reason for the Role
prompts to ask for it accurately, and the list will name honest omissions
alongside the hazard it was built for — a reader who sees a familiar file there
is being told a Role did not mention it, which is worth knowing either way.

The list is empty on an ordinary Run, so the body reads as it did before. It is
not empty on a `review` Workflow pointed at a diff AgentForge did not write,
where every committed file is unclaimed by construction. That is the honest
answer for that Workflow and not a false alarm: nothing in the Run wrote them.

Running two agents against one checkout is still not safe. This makes the
damage legible at Sign-off rather than preventing it, and a human who reads the
section and merges anyway has made a decision rather than an assumption.

**Amended, 2026-09-03.** #110 took the further decision and this section stands.
A worktree per Run would have prevented the hazard and was rejected on evidence:
it relocates the code without the untracked environment the code is run in, so
the Tester's suite passes against the checkout the Agent did not edit. Disclosure
remains what is available against a foreign writer. What #110 did prevent is the
case where both writers are AgentForge — a Run now holds an exclusive lock on its
checkout, so a second Run is refused rather than committing over the first. See
[ADR-0026](0026-a-run-holds-the-checkout-it-was-pointed-at.md).
