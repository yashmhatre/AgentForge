# ADR-0007: A Role runs no commands unless a human opens the gate

An Agent that may edit files but not run them cannot satisfy acceptance criteria written as commands, and the M1 acceptance run produced exactly that: an Implementer that traced seven tests by hand, reported `completed`, and disclosed the substitution only because it happened to be scrupulous. Every Provider therefore runs default-deny, and execution is opened for one Run by an explicit flag on `agentforge implement` that refuses unless the working tree is clean and a branch exists; a Role denied a command it needs reports that denial in its Agent Result rather than substituting inspection. The posture is set in one place per adapter — `permission_mode` in `claude.py`, its equivalent in every other — and never in prompt text, because a permission expressed as an instruction is a permission the model can talk itself out of.

## Considered Options

Forbidding the Orchestrator from writing command-shaped acceptance criteria was rejected: it makes every plan worse to work around a permissions problem. A per-Role or per-project allowlist is the better long-run answer and is deferred to M5, where Project Context can supply what a project's test and lint commands actually are — the set is bounded there, which is what makes persisting it appropriate when a standing grant is not.

## Consequences

A per-Run flag rather than a configuration key means no standing grant persists in a repository, and the cost is that an unattended Run cannot execute anything until the M5 allowlist arrives. Default-deny also does load-bearing work elsewhere: a vendored skill that would otherwise file its own issue cannot reach `gh`, so the single-Issue guarantee holds by construction rather than by asking the skill nicely.

**Amended, 2026-09-03.** Everything above described a posture that neither CLI
had. Checked by invocation (#115, and the spike recorded in ADR-0025), both
denied postures executed arbitrary shell commands on a Run that had been granted
none: `claude`'s `acceptEdits` governs edits and leaves commands to an
auto-approving classifier, and `codex exec` discards `--ask-for-approval`
entirely and always runs `never`. The decision is unchanged. What it takes to
hold is not.

On `claude` the refusal is now an `ask` rule on the tools that start a process —
`Bash` and `PowerShell`, asked of the CLI rather than assumed — carried inline to
`--settings`, which keeps it in the argument vector where this ADR requires it.
`ask` rather than `deny`, because a `deny` rule removes the tool and a Role with
no tool reports a fact about itself instead of the denial this ADR asks it to
report. The refusal is legible twice over: the Role is told, and the envelope's
`permission_denials` carries the exact command for the Run Log. It also outranks
a permissive rule in the user's own settings, which matters — the machine this
was verified on allows several `Bash(gh …)` patterns at user level, and the
denied posture refused them anyway.

On `codex` there is no denied posture, and this ADR now says so rather than
sending a flag the CLI throws away. `AskForApproval::Granular`, the policy that
would consult an allowlist, is present in the binary and rejected by the CLI;
hooks could refuse and are gated behind `--dangerously-bypass-hook-trust`. A
Run that asks for the denied posture on `codex` is refused before it spends
anything, and the message names the two ways forward: `--allow-commands`, which
makes the grant deliberate, or `--provider claude`, which can refuse. Editing and
executing are the same channel on that CLI — a file is written by running a
command — so "may edit but not execute" is not merely unavailable there, it is
not expressible.

The claim in Consequences that a vendored skill cannot reach `gh` was false
for as long as it stood, on both Providers. On `claude` it is now true. On
`codex` it is true only because a denied Run no longer starts.
