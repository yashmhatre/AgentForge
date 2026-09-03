# ADR-0004: Roles declare a Model Tier, never a model

Running every Role on the strongest available model is the largest avoidable cost in the system, and ADR-0001 removed the easy fix — a Provider shelling out to three CLIs cannot rely on per-agent model frontmatter, and each CLI accepts different model identifiers. A Role therefore declares an intent-named tier, and each Provider maps tiers onto its own CLI's model flag; users override the mapping in `.agentforge/config.yaml` without touching Role definitions.

Default assignment:

| Role         | Tier     | Effort | Reason                                              |
|--------------|----------|--------|-----------------------------------------------------|
| Orchestrator | deep     | high   | Pays for all downstream reasoning once (ADR-0003)   |
| Architect    | deep     | high   | Design errors are the expensive kind                |
| Reviewer     | deep     | high   | Speaks last; nothing downstream catches a thin one  |
| Implementer  | standard | medium | Executes a plan it did not write                    |
| Security     | standard | high   | Missed findings are silent — see the 2026-09-03 amendment |
| Tester       | cheap    | medium | Reports what the suite said rather than deciding it |

One exception to "a Role declares a tier": the Reviewer's rewrites run at `cheap` regardless of the tier the Reviewer is on. Two different jobs share that Step — judging a diff against a frozen Plan, and applying scanner findings that already name the phrase, the line, and a replacement. The declared tier is chosen for the first. This is the only Role that does it, and it is not a general licence: a second tier inside a Step is worth it only where the two jobs are that far apart.

## Consequences

Three tiers is a coarse instrument: a Provider whose CLI exposes no model flag collapses to one, and AgentForge cannot detect that in advance.

**Amended, 2026-08-26.** This ADR predicted that the Reviewer at `cheap` was the assignment most likely to be wrong, and that it would move to `standard`. It moved further, to `deep`. The reason is the one the original gave — it writes the prose a human reads at Sign-off — plus one the original missed: it is the last Role to speak, so nothing downstream catches a review that is wrong or thin. The next thing after it is a person deciding whether to merge.

The Tester moved the other way in the same amendment, from `standard` to `cheap`, and that one contradicts its recorded reason rather than fulfilling it. "Edge cases need reasoning" is still true, and reasoning about an edge case nobody wrote a test for is the capability being traded away here. What is bought is that the expensive tiers sit where a mistake is unrecoverable: the Orchestrator's plan, the Security audit, and the Reviewer's report. The Tester's own claims are checked by something that does not reason at all — the suite either passed or it did not, and #10's Gate re-runs it rather than believing the report. If the Tester starts missing flaws a human then finds at Sign-off, this is the row to move back.

**Amended, 2026-09-03.** A Role now declares two axes, not one. The Model Tier keeps its job — it picks the model — and a new Effort picks how much reasoning that model spends. Both are named by intent, both are overridden in `.agentforge/config.yaml`, and neither implies the other.

The original ADR folded them together because the CLIs of the day mostly did. They no longer do: `claude --effort` and codex's `model_reasoning_effort` each take five levels independent of the model, and `codex.py` had already hit the problem from the other side — it pinned one effort across every tier, with a comment explaining that letting a tier move two things at once would stop a tier meaning one thing. That objection was right. Pinning was the wrong half of the fix; the right half is that effort was never the tier's to move.

Splitting them says something the single axis could not. Security drops to `standard` and stays at `high`. Its `deep` was bought with the one argument in the table that is not about cost — a missed finding is silent — and that argument was always about reasoning depth rather than model size. It now buys the depth directly, at `high`, deliberately above the Implementer whose work it audits, and stops paying frontier prices for the part it never needed. This is the row most likely to be wrong: if Security starts missing what a human catches at Sign-off, move the tier back and leave the effort where it is.

Two things this does not do. Effort is not a per-Step or per-Run flag — there is no `--effort` on the command line to match `--tier`, because nothing has yet wanted one and an axis nobody overrides interactively does not need a flag to prove it exists. And `roles.<name>.model` remains refused in configuration, loudly rather than silently: a model named per Role does not survive a release and does not port across Providers, which is the whole of this ADR. Configuration overrides a Role's tier and effort, and a Provider's tier-to-model table. Nothing names a model per Role.

The `models` override this ADR promised in its first paragraph was, until now, read by nothing — the file held only what `load_config` parsed (ADR-0020), and it never parsed a model. `providers.<name>.models.<tier>` is that sentence, kept.
