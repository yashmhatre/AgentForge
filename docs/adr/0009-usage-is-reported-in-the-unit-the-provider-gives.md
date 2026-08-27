# ADR-0009: Usage is reported in the unit the Provider gives

AgentForge is described as token-efficient, and until an Agent Result carried what its invocation consumed there was no way to know whether that was true. The obvious shape — one number, in dollars, on every Run Log entry — cannot be built. The `claude` envelope reports `total_cost_usd` and a token split; a `codex` transcript ends with `tokens used: 21044` and no price; a third CLI may report nothing at all. A dollar figure derived from a rate card would be a number nobody was charged, and it would go stale the first time a vendor changed a price.

So a `Usage` carries whatever its Provider gave — dollars, tokens, both, or neither — every field optional, and each Run Log line says which of those it is holding. Where a Provider reports tokens only, the line says tokens and says that is all the CLI gave. Where it reports nothing, the line says so, because a blank is indistinguishable from free.

Nothing is ever recorded as zero for want of a figure. Absent and zero are different claims: the `claude` CLI reports a real `total_cost_usd` of `0.0` when it fails before spending anything, and a Run's total has to be able to say how much of itself is missing rather than averaging silence into the number.

Parsing lives in the adapter that owns the envelope, beside the parsing of the result text that was already there. That is the same seam ADR-0001 draws: adding a third Provider adds a third parser and no third place that knows how the second one reports itself.

## Consequences

Every Run Log comment ends with a cost line and the terminal comment carries the Run's total, counted over the Steps that reported one and naming how many did not. The Reviewer sums its own rewrites into one figure, because a Role that spends three invocations and reports the last one's price understates itself forever.

A tiering decision can now be settled by a number. The Tester moved to `cheap` and the Reviewer to `deep` on reasoning alone, because no other kind of argument was available; the next such move has one.

The unit is not comparable across Providers. A Run on `codex` and a Run on `claude` cannot be put beside each other, and a total spanning two of them names neither CLI rather than implying the figures are the same kind of thing. Making them comparable would mean pricing tokens ourselves, which is the thing this ADR refuses.
