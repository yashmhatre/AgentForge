# ADR-0020: The config file holds only what is read

`docs/PLAN.md` promised that `.agentforge/config.yaml` would own tier mapping, Provider selection, plugin activation, and Gate policy. `load_config` reads two keys: `providers.<name>.capability_tier` and `gates.tests.suite`. `agentforge init` had to settle which of those two lists it was writing.

**It writes what is read, and prints the rest.** A key nothing consults is worse than an absent one: the human who edits it has been told it matters, and nothing will tell them it did not. So init writes the two keys `load_config` reads, and everything else it learned — the languages the repository is written in, the Plugins its root markers answer for — is printed to the terminal where it is plainly a report rather than a setting.

Plugin activation is the case that decides the rule. It is computed per Run from the frozen Plan's blast radius (ADR-0016, ADR-0017), which is a better answer than a file could hold: the Plugins that answer for a Plan touching one SQL file are not the ones that answer for a Plan touching a notebook, and a repository-level list would be wrong for one of them. A `plugins:` key would therefore be both inert and misleading, and the file says so where somebody looking for the key will look.

Tier mapping is the same shape from the other side. ADR-0014 made the Roster's tier the tier that runs, frozen in the Issue, so a per-repository default in a config file would be a second answer to a settled question. Neither is written until something reads it.

**The precondition comes before anything is created.** ADR-0002 makes a GitHub remote a hard precondition for a Run, and `open_repository` already raises the three refusals — not a git repository, no `origin`, an `origin` that is not GitHub. init reaches those before it creates a directory or a file, so a repository that cannot host a Run never gets a config file implying it can. Finding this out at setup is the point: the alternative is finding it out when the first Run halts.

**An existing config is never clobbered.** Re-running reports how the file on disk differs from what init would write and exits without writing; `--force` replaces it. The comparison is made against the values `load_config` reads rather than against the text, so a file somebody reformatted, commented, or reordered is not reported as a difference — the question a human is asking is whether their edits are still there.

The file init writes carries a comment on every value saying whether it was detected or defaulted, and what the evidence was. A suite that was guessed and a suite that was found are the same two words in YAML, and only one of them is worth a reader's attention.

## Consequences

Detection can be wrong without being dangerous. Everything it produces is either printed for a human to read or written with a comment saying where it came from, and nothing it writes is a permission: ADR-0007's execution gate stays a per-Run flag, and no standing grant is persisted by this command or any other.

`agentforge init` is not a precondition for anything. `load_config` still returns documented defaults when the file is absent, so the command shortens setup rather than gating it, and a repository that never runs it works exactly as it did.

The keys this file holds will grow as things that read them are built, and each one arrives with its reader rather than ahead of it. That is the rule this ADR is really recording: `docs/PLAN.md` describes where the project is going, and a generated file is not the place to write down an intention.
