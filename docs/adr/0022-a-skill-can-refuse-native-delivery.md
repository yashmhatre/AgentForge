# ADR-0022: A skill can refuse native delivery

ADR-0005 decides how a skill reaches an Agent from one input: the Provider's
Capability Tier. A skill's author has a say too. Upstream frontmatter can carry
`disable-model-invocation: true`, which means no model may invoke this
autonomously — only a human typing the command. `to-spec` and `to-tickets`, the
two skills the planning pipeline is built on, both carry it.

Offered natively, such a skill is declared on the Role, offered through the
Provider's Skill tool, and refused there — correctly, by a tool doing what the
frontmatter asked. The Role escalates instead of working, so the higher the
Capability Tier, the more certainly the pipeline fails; a Provider with no
native skills at all would have inlined the `SKILL.md` and worked. That is
backwards, and it is why 0.2.1 shipped a `decompose` that could not finish.

So delivery takes a second input, consulted before the Capability Tier: a skill
that forbids model invocation is delivered as a Fragment at every tier. Here the
Fragment is not the degraded delivery ADR-0005 describes — it is the only one.
The set is read from the vendored frontmatter on each run rather than listed in
our source, because ADR-0006 has the bundle refreshed from upstream and a
hand-kept list would go stale exactly when a refresh marked a third skill. A
composite inherits the ban from any part it names, since natively it fans out
through the same Skill tool and would meet the same refusal one step later.

## Consequences

A skill can now make itself more expensive to run — the Fragment costs prompt
tokens on every invocation — by a line its author wrote for a different reason,
and no configuration of ours can override it. That is the intended reading:
`disable-model-invocation` is a constraint on autonomous use, and AgentForge
uses skills autonomously or not at all.

The mark is honored by inlining the skill's text, not by declining to use it.
An author who meant "a model may not follow this method unattended" rather than
"a model may not call this as a tool" is not served by that distinction, and
upstream frontmatter does not separate the two. If a skill ever needs to be
refused outright, that is a further decision and not this one.
