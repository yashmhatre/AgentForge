# ADR-0011: The Issue body is the stable surface; the Python API is private

At 0.1 AgentForge promises that what it writes into a GitHub issue keeps parsing, and promises nothing about what `import agentforge` gives you.

The Issue is where the promise has to be. ADR-0002 makes it both the handoff contract and the Run Log, so `agentforge implement 12` works from a clone that has never seen the Run — the plan, the Roster, the results, and the status all come off the Issue and nothing local is consulted. That is the product claim, and it is only true across time and machines if the format an older AgentForge wrote is one a newer AgentForge can still read. A tool whose central promise is "an issue number is all you need" cannot also treat the issue body as an implementation detail.

The surface is everything a later Run replays, not the plan block alone. A resumed Run parses four things: the `agentforge:plan` block for the Plan and the Roster, the `agentforge:result` blocks for what each Agent did, the `agentforge:gate` blocks for what each Gate said, and the status label for where the Run stands. Naming only the first would be a narrower promise than the code already keeps — ADR-0008 says the result and gate markers are a compatibility surface as much as the plan block is, and `LEGACY_LABELS` exists because a renamed status label still has to be read off issues that were open when the rename landed.

The rule for changing it is already written and already enforced. `PLAN_FORMAT_VERSION` is bumped when a field is removed or when its meaning changes; a field added with a default does not require a bump, because an older Issue still parses. Two fixtures hold that honest from opposite directions: `issue_body_v1.md` is an Issue an older AgentForge filed and must keep parsing, and `issue_body_current.md` is what the renderer produces today and is re-recorded only when the format changes on purpose. The Context Pack work exercised exactly this — it added a `references` field with a default and left the version at 1 — so the policy is one the project has followed rather than one this ADR invents.

Everything importable under `agentforge.*` is private, and will change without notice. AgentForge is driven through a command line and a tracker; nothing about it is designed to be called. The internal seams are also the parts most likely to move: the Provider port grows a third adapter, the Gate registry and the extractor registry both open up to Plugins, and `agentforge init` rewrites how configuration is read. Freezing those at 0.1 would freeze the work that has not been done yet.

There is no `py.typed`, and its absence is a choice rather than an oversight. Shipping the marker tells a type checker that this package is meant to be consumed as a library, which is the opposite of the promise being made here. Adding one later is then a decision somebody makes deliberately, instead of a correction to something that was always half-true.

Promising both surfaces was the real alternative. The wheel installs a package, every module in it is importable, and somebody will import one; saying so out loud would have been friendlier than staying quiet. It was rejected because a promise costs most where it is least deserved — the Python surface is the half that is still being built, and the half nobody needs in order to use the tool. Promising nothing at all was rejected for the opposite reason: it would leave cross-machine resumption unsupported, and that is the product.

## Consequences

A change to any of the four blocks or to the label scheme is a compatibility decision, and the version rule above is how it gets made. A change under `agentforge.*` is not, however public the name looks.

A downstream type checker treats this package as untyped. That is the mechanism, not a side effect: the missing marker is what enforces the decision, and this file only explains it.

Private now is not private forever. A later release may promise some narrow, deliberately chosen piece of the Python surface, and it will argue for that in an ADR of its own.

None of this rests on anybody remembering it. `issue_body_v1.md` fails the suite on the day a change stops an old Issue from parsing, which is the day this decision would otherwise have been broken quietly.
