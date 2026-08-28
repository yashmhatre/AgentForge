# ADR-0015: A Run commits the surface it declared

`--allow-commands` lets the Tester run the repository's suite. Running pytest writes `__pycache__`. The commit step staged the working tree with `git add -A`, so the Run committed that too: draft PR #4 on the smoke repository changed four files, of which two were the change and two were byte-compiled Python.

A target repository whose `.gitignore` covers `__pycache__` never sees this, which is why it took an end-to-end Run to surface. The defect is not the smoke repository's missing `.gitignore`. Before ADR-0007's gate existed, Agents only edited files and staging everything was the same thing as staging the change; opening the gate is exactly what makes a Run produce files it did not write, and the commit step was never revisited. The blast radius is not `__pycache__`: a suite that writes `.coverage`, a build directory, or a temporary fixture puts all of it in the diff a human reads at Sign-off, and Sign-off is the one place AgentForge asks for human attention.

**A Run stages every change to a file git already tracks, and stages an untracked file only when the Run declared it.** The declared surface is the union of two things the Run already writes down: the files the frozen Plan names per Step, and the `files_changed` each Agent reports in its Agent Result. Paths match exactly — a declared directory does not admit what is under it, because a Plan naming `src/` would re-admit `src/__pycache__/loader.pyc` and reintroduce the bug through the fix.

The tracked/untracked line is where the decision actually lives, and it settles the question #72 asked out loud: **an out-of-plan edit to an existing file still reaches the commit; an out-of-plan new file does not.** The asymmetry is not a compromise between two rules, it is one rule about who is entitled to say a file belongs to this repository. Git already knows about a tracked file — a human put it under version control — so a modification to it is a change to the project however it happened, and refusing it because the Plan forgot to name the file would silently drop an Agent's work. Nothing has vouched for an untracked file; something has to, and the only candidates are the Plan and the Agent that claims to have written it.

The strict form — stage only the Plan's declared paths, tracked or not — was the narrow fix #72 proposed, and it was rejected for what it costs when the Orchestrator is imprecise. Plans routinely name the file a Step is about and not the one that has to move with it, and a Run that quietly declines to commit an Implementer's edit produces a green Run and an incomplete pull request. That is a worse failure than a stray `.pyc`, because the `.pyc` is visible in the diff and the missing edit is not.

Taking the Agents at their word on `files_changed` is not the same as taking them at their word on whether work happened. `carries_work_against` still asks git that question, for the reasons it always did. This asks a narrower one — which of the files now in the tree were the Agents' — and no property of a file on disk answers it, because the suite writes during the Run alongside the Agents. Snapshotting `git status` before the first Role does not answer it either, for the same reason. What an Agent reports is imperfect evidence and it is the only evidence there is, and a suite has never yet claimed to have written `__pycache__/loader.cpython-311.pyc`.

AgentForge does not write a `.gitignore` into the target repository, and does not carry a built-in list of artifact patterns. Both are guesses about somebody else's project, and the second is the kind of list that is wrong for the first repository that uses a build tool nobody here has heard of.

## Consequences

An undeclared file is left in the working tree of the machine that ran the Run, not deleted and not hidden. The pull request body lists it under *Left uncommitted*, and a Run that committed nothing but held such files says so in its failure rather than reporting an empty working tree it did not have — a human is the only one who can tell an Agent that wandered from a suite that ran.

`files_changed` stopped being decoration. It is read from the Run Log, so a Role's prompt has to ask for it accurately, and a Role that under-reports loses the files it did not mention. The Implementer and Tester prompts say so now.

A suspended Run commits the same surface as one that reaches Sign-off. There is one rule about what belongs in a commit, and a Gate stopping the Run is not a reason to relax it.

The first repository this helps is a repository with no `.gitignore` at all, which is what the smoke repository is and what a scaffolded project is on its first day.
