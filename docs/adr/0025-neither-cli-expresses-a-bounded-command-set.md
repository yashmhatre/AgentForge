# ADR-0025: Neither CLI expresses a bounded command set, and neither deny posture denies

ADR-0007 deferred an execution allowlist to M5 on the grounds that Project
Context would eventually supply a bounded set. M5 landed, `gates.tests.suite`
now says what a project's suite is, and Issue #108 went to find out whether
either Provider could carry that set into its CLI in the argument vector. The
spike ran `claude` 2.1.251 and `codex-cli` 0.147.0 as installed, rather than
reading their help text, because that is the lesson 0.2.2 was released to learn.

It found something larger than the question it was asked. **Both adapters'
denied posture is inert.** Neither CLI refuses commands in the mode AgentForge
selects for a Run that has not been granted execution, so the default-deny
ADR-0007 describes has not been in force on either Provider.

On `claude`, `--permission-mode acceptEdits` denies nothing. With user, project
and local settings cut off by `--setting-sources ""`, an Agent asked to run
`touch probe_c1.txt` ran it and the file appeared on disk. `acceptEdits` in this
release governs file edits and leaves commands to an auto-approving classifier;
ADR-0007's reading of it — "lets an Agent write files and nothing else" —
described a CLI that no longer exists.

On `codex`, `--ask-for-approval untrusted` is discarded. `codex exec` prints
`approval: never` whatever that flag says, and does the same for
`-c approval_policy="untrusted"`, because a non-interactive run has nobody to
escalate to. An Agent under the denied posture was asked to create a file with a
shell command and did. The `DENIED` constant in `codex.py` has never reached the
CLI as anything.

The consequence reaches past this ticket. ADR-0007 claims default-deny does
load-bearing work elsewhere — "a vendored skill that would otherwise file its own
issue cannot reach `gh`, so the single-Issue guarantee holds by construction".
It does not hold by construction. It has been holding because no skill tried.

## What each CLI can express

`claude` accepts `--allowedTools "Bash(pytest:*)"`, and the unit is a command
pattern rather than a tool name, which is the stronger of the two things #108
asked about. It is additive. Under `--permission-mode manual` with that single
entry, `pytest -q` ran, and so did `git log --oneline -1` and `echo hello`,
neither of which is in the list. An allowlist that does not exclude is not a
bound. Pushing the floor down does not help: `--disallowedTools "Bash"` removes
the tool wholesale and the narrower `--allowedTools` does not survive it, and a
`{"permissions":{"ask":["Bash"]}}` floor passed through `--settings` denies the
allowlisted `pytest` along with everything else. Broad rules beat narrow ones in
both directions, which leaves the same all-or-nothing ADR-0007 already has.

What does express a bounded set on `claude` is a `PreToolUse` hook, supplied as
inline JSON to `--settings` and therefore in the argument vector, enforced by the
CLI, and invisible to the model as instruction. A hook that allowed only
`pytest` ran the suite and refused `echo hello`, and the refusal arrived in two
parsable places: the `permission_denials` array of the `--output-format json`
envelope, carrying the exact command, and the model's own report of the reason
text.

`codex` has a genuine argv-vector matcher — `prefix_rule(pattern=[...],
decision="allow")` in execpolicy `.rules` files, evaluable offline through the
undocumented `codex execpolicy check`. It cannot be reached from a Run. The
approval policy that consults rules is `AskForApproval::Granular`, which exists
in the binary as a struct variant with a `rules` field and is rejected by the
CLI: `-a granular` fails with `[possible values: untrusted, on-request, never]`.
Spelling it as `-c approval_policy={granular={rules=true}}` does not error — it
degrades silently to `never`, the most permissive posture of the three, which is
the worst available failure mode for a configuration mistake.

## What the matcher binds

Neither matcher binds the command that finally executes.

`claude`'s hook binds the requested shell command line, and a command line is not
an argv vector. A prefix matcher over its first token allowed
`pytest -q; touch build_stamp.txt`, logged the match against the declared prefix
`["pytest"]`, and `build_stamp.txt` was created. One semicolon defeated it. A
matcher hardened to refuse shell metacharacters, bare-name programs only, held
against that and against `sh -c`, `bash -lc` and a `PATH=` assignment — so a
bound is reachable, but only by a matcher that understands it is parsing shell
rather than reading a program name.

`codex`'s matcher binds real argv tokens, which is the stronger guarantee, and it
never sees the command. On Windows the CLI submits every model command as
`["…\powershell.exe", "-Command", "<the entire line>"]`, so a `["pytest"]`
prefix rule cannot match one. The rules a real install accumulates confirm it:
`~/.codex/rules/default.rules` is a ledger of whole PowerShell command lines
approved one at a time. That is an exact-match record of literal invocations, not
a bounded set of commands. Turning on `--resolve-host-executables` trades the
problem rather than solving it — it matches basenames, so `/usr/bin/pytest`
resolved to Git's `pytest`, and a shim written to `<workspace>/shim/pytest`
resolved and was allowed, which is the evasion #108 named.

## The bound a test runner cannot have

The last finding is the one no matcher fixes. Under the hardened hook, an Agent
was asked to add a test and run the suite. The gate saw exactly `pytest -q`,
matched the declared prefix, and allowed it — correctly, by every rule it had.
The test it had just written executed and wrote `marker.txt`.

A command allowlist bounds the command, never the code the command runs. A test
runner exists to execute the repository's own files, and a Role that may edit
files may edit those. Allowing a project's declared suite to a Role with edit
access *is* arbitrary execution, by a path that requires no metacharacter, no
shim, and no evasion. This is not a defect in either CLI. It is what a test
runner is.

## Consequences

ADR-0007's default-deny stands as a decision and does not stand as a fact, and
the gap is the finding #109 closes against. #109 does not build a
`gates.commands.allowlist`: on `codex` there is nothing to build it on, and on
`claude` the buildable version bounds a command whose whole purpose is to run
code the Role wrote. A key that reads as a bound and is not one is worse than
the honest binary flag, which is ADR-0020's reason for existing pointed at this
ticket.

What the postures actually do is now the open question, and it is a defect rather
than an enhancement. `claude.py` and `codex.py` are untouched here, per #108's
own scope, but `DENIED` means nothing on either adapter today and the Run Log
says a Run is denied execution when it is not.

The `PreToolUse` hook is the one mechanism worth keeping in view. It is
argv-reachable, CLI-enforced, legible in `permission_denials`, and it is the only
thing found that refuses a command a Role asked for. Should AgentForge ever want
a bound narrower than "everything", that is where it goes — with a matcher that
parses shell, and with no illusion that allowing a test runner bounds anything.
