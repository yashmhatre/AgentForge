# ADR-0026: A Run holds the checkout it was pointed at

ADR-0023 made a concurrent agent's edits visible at Sign-off and said, in as many
words, that visible is not fixed. #110 is the decision that deferred to: stop a
second writer's work from reaching a Run's commit, rather than only naming it
afterwards.

The candidate that would actually prevent it is a dedicated worktree per Run.
`git worktree add` gives the Run its own checkout, so an IDE assistant editing
the tree the human is looking at cannot reach the Run's branch at all. The
hazard stops existing rather than being reported. #110 recorded the costs it
expected — the Agents work somewhere the human is not looking, `implement` has
to place and clean up the worktree, a Run that dies leaves one behind — and
judged them worth paying.

**They are not the costs.** A worktree contains the tracked files and nothing
else, and the environment a repository's suite runs in is untracked by
construction: the virtualenv, the `.env`, `node_modules`, the build cache, and
the `.pth` file that an editable install writes into site-packages. That last
one is not merely absent from the worktree — it is present and pointing
somewhere else.

Checked by invocation on a repository built for the purpose, `src/` layout,
`pip install -e .`, one test asserting which tree it imported:

1. **The Agent edits the worktree; the Tester runs the suite in the worktree;
   the suite imports the main checkout and passes.** Not an error, not a
   warning — a green suite over code that was never executed. The Tester's
   report, the Gate that re-runs it, and the Run Log all say the change is
   tested. `toy.__file__` resolved to the main checkout's `src/` on every run.
2. **A Role that notices and repairs it by reinstalling editable inside the
   worktree** gets a correct suite for the length of the Run, and leaves the
   human's environment pointing at a directory that cleanup then deletes. The
   probe ended with `ModuleNotFoundError: No module named 'toy'` in the human's
   own checkout, from their own venv, after a Run that reported success.

So the worktree does not relocate the Run. It relocates half of it, and the half
left behind is the half `--allow-commands` exists to use. The trade is a hazard
that ADR-0023 already discloses at Sign-off, in exchange for one that is silent
and lands on the Role whose whole output is a claim about whether the code
works. That is the wrong direction, and cleanup — filed in #110 as a chore — is
the step that breaks the human's tooling rather than the step that tidies up.

**A worktree per Run is therefore rejected, and ADR-0023's disclosure stands.**
A second agent writing into the checkout mid-Run still gets its work committed
and attributed to a Role, the pull request still names those paths under
*Committed, but no Agent claimed them*, and the README still says not to do it.
The reopening condition is narrow and specific: a target repository that
declares how its environment is prepared — one command AgentForge could run in a
fresh worktree — makes the worktree buildable, because the thing that defeats it
here is that AgentForge cannot know what to do to a checkout to make its suite
mean anything. ADR-0015 already refuses to guess about somebody else's project,
and this is the same refusal.

## The case the ticket did not separate

#110 treats "a second agent" as one hazard. It is two, and they do not have the
same answer.

An IDE assistant is a foreign writer. AgentForge cannot see it, cannot ask it to
wait, and — per the above — cannot get out of its way without breaking the
Tester. Disclosure is what is available.

**Two AgentForge Runs in one working tree is not that.** Both writers are this
program. The damage is worse than an unclaimed file: each Run calls
`create_branch`, so the second `git checkout -b` moves the branch out from under
the first, and from there both Runs commit to whichever branch the working tree
happens to be on. Neither Run's pull request holds what it says it holds. And
prevention is total, because AgentForge controls both sides.

**A Run holds an exclusive lock on the checkout for its duration, and a second
Run against that checkout is refused before it spends anything.**

The mechanism is an operating system file lock, not a pid written into a file.
That distinction only shows up on one path, and it is the path this has to
survive: a Run killed hard — Ctrl+C twice, a closed terminal, a machine that
lost power — runs no cleanup, and a pid file would still be sitting there
claiming a Run is in progress. Then there is a staleness rule to invent, and it
is a guess. The kernel releases a file lock when the holder dies, whatever
killed it. Verified by invocation on Windows: a holder that exits through
`os._exit` leaves the lock free for the next acquirer, and the test that proves
it kills a real process rather than arguing.

The pid is still recorded, as metadata read by the Run being refused, so the
message names the Issue and the process rather than pointing at a file. It is
never consulted for a decision — `os.kill(pid, 0)` is the liveness check this
would otherwise use, and on Windows it does not ask whether a process is alive,
it terminates it.

The lock lives in the git directory, not the working tree. A file in the tree
would be an untracked file, and ADR-0015 makes untracked files something the
commit step has to take a position on; this one is neither the Run's work nor
its suite's leavings and belongs in nobody's `git status`. Asking git for that
directory rather than assembling `root/.git` also gets linked worktrees right:
two worktrees are two checkouts, they cannot branch over each other, and they
are not made to queue.

It is taken before the dirty-tree check. A tree that another Run is mid-way
through writing is dirty, and *"Commit or stash first"* is the wrong thing to
say to a human whose actual problem is a Run they have forgotten is running.

## Consequences

`agentforge implement` on a checkout another Run holds now fails in the second
it started, having invoked no Provider. The message names the other Run's Issue,
its pid, and how long it has been going, and offers the way forward that works:
a second clone.

The lock is advisory in the only sense that matters — it binds AgentForge and
nothing else. An IDE assistant, a colleague, or a script does not consult it.
This ADR does not claim two agents on one checkout are now safe; it claims that
when both of them are AgentForge, they no longer are two.

A fake repository in the test suite is now a real directory. Every git call is
scripted, so the fakes used a path that did not exist — and a Run that takes a
real lock in a real git directory turned that path into one, created outside the
suite's own temporary space. #84 is the rule that breaks, and it broke on the
first run of the suite after this landed.

Nothing here changes what a Run commits. ADR-0015's staging rule and ADR-0023's
disclosure are untouched, and the `--allow-commands` posture runs where it always
did: in the checkout whose environment the human built.
