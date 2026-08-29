# ADR-0016: A Plugin contributes through the Context Pack

A Plugin is a bundle of domain knowledge for one technology, and the first thing it contributes is a Fragment: a few hundred tokens of convention that a Role's prompt carries so the Implementer stops writing code a reviewer would reject on sight. Three questions had to be answered before any of that could be built, and answering them in the ticket that adds Extractors or the one that adds Gate kinds would mean answering them three times.

**A Fragment rides in the Context Pack handed to a Step.** Fragments are per Role and the pack is per Run, so something has to hold both, and the candidates were the pack, the Role runner, and the Provider. The Role runner was rejected because five of them would each grow the same folding code and drift. The Provider was rejected because it would put domain knowledge behind the port that exists to keep AgentForge indifferent to which CLI it drives — a Fragment that reached a Role under `claude` and not under `codex` would be a Plugin that works on one machine. So the runtime folds the active Plugins' Fragments for a Role into the pack just before that Role is invoked. No Role runner's signature changes, one place does the folding, and the pack recorded in the Run Log stays the Run-level one so that two Runs of an Issue are still compared against the same object.

`ContextPack.fragments` is kept apart from `ContextPack.conventions` rather than merged into it. The two have different authors: `conventions` is the Orchestrator's judgement about this Task, and `fragments` is what the repository's technology is held to whatever the Task. A Role reading one run-on list cannot tell which half the Plan actually asked for, and the half it can ignore is not the same half in the two cases. `fragments` is absent from `to_dict`, so it never travels in an Issue body — it is resolved against the repository the Run is in, and freezing one machine's answer into the stable surface (ADR-0011) would hand the next Run conventions it may not be held to.

**Activation happens after the Run State is derived and before the Workflow is loaded.** A later ticket lets a Plugin register a Gate kind, and `parse_workflow` refuses an unknown kind at load time. Run these two the other way round and a Workflow naming a Plugin's Gate is rejected before the Plugin that defines it exists. The ordering costs nothing today and is unrecoverable later, which is the only reason it is decided here rather than by whoever writes #58.

Activation reads the frozen Plan rather than the resolved pack. The two agree in the ordinary case, but a control Run resolves no pack at all, and an active set that changed depending on whether the control was running would make the control meaningless. Detection is the suffixes in the Plan's blast radius plus each Plugin's declared root markers, either being sufficient: a Plan touching one `.sql` file in a Python repository is held to both sets of conventions, because both are true of the code being written. The `python` Plugin declares no root markers on purpose — a repository with a `pyproject.toml` and a SQL-only Plan is not doing Python work.

**`--no-context-pack` stays the combined control, and `--no-plugins` is the new isolated one.** Fragments ride in the pack, so the existing flag already suppresses them, and that is the honest meaning of it: a Run with no resolved context at all. But #61 has to measure what the Fragments cost, and a single flag that removes the pack and the Fragments together cannot say which of the two moved the total. So there are three conditions rather than two:

| Invocation | Pack | Fragments | What it measures |
|---|---|---|---|
| `agentforge implement <n>` | yes | yes | the shipped configuration |
| `agentforge implement <n> --no-plugins` | yes | no | what the Fragments cost |
| `agentforge implement <n> --no-context-pack` | no | no | what the pack is worth, ADR-0010's control |

**A Plugin's Fragment is inlined at every Capability Tier, and that is ADR-0005's rule rather than a second one.** ADR-0005 says a skill reaches an Agent natively where the Provider offers a native equivalent and as an inlined Fragment where it does not. A Plugin's conventions have no native equivalent at any tier — there is no CLI command that means "Unity Catalog three-part naming" for the native path to invoke — so the existing rule yields one answer for every Provider. Nothing here degrades, because there is no better delivery to degrade from. This is the sense in which the glossary's Fragment entry had to widen: it defined a Fragment as *the degraded delivery of a skill*, and a Plugin's conventions are inlined instruction that was never a skill. Both are text in a prompt and both go through the same assembly; only one of them has a native form it is standing in for.

## Consequences

Fragments are bounded, and the bound is a guess. One Plugin may spend 1200 characters on one Role and no Role carries more than four of them. The numbers are constants in `core/registry.py` because #61 is expected to re-set them from a measurement rather than from this paragraph.

A Plugin that raises while being asked what it contributes is skipped, named in the Run Log, and the Run carries on. Domain knowledge is a nice-to-have, and a Run that died because one convention list was malformed would be worse than a Run without it. This is the only place in AgentForge where a broad `except Exception` is the right shape, and it is deliberate: a Plugin is the extension point third parties reach for first.

The registry is a tuple and activation never iterates a set, so the same Plan against the same repository yields the same active set in the same order. A Run Log is comparable to the one before it only if this holds.

The Context Pack comment names the active Plugins and what each contributed. A prompt that grew has a reason a human can read, and a prompt that did not grow when a reader expected it to has one as well.
