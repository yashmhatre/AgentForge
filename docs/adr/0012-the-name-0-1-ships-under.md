# ADR-0012: AgentForge is renamed to AgentBastion before 0.1 is tagged

A larger, older project already is AgentForge, and it is in this one's category rather than merely sharing a word with it. `DataBassGit/AgentForge` has been active since 2023, carries 843 stars, ships as `agentforge` on PyPI at 0.6.6 across 120-odd releases, and describes itself as a low-code framework for building and testing autonomous agents and multi-agent systems, configured in YAML. It also holds `agentforge.net`. Anybody searching for this project finds that one, and reasonably concludes they have found it.

The collision is technical and not only reputational. Both projects import as `agentforge`, so a machine with both installed has one shadowing the other. Publishing under some decorated distribution name would have left that intact: the import path is what collides, and the index name is not.

The reason to do it now rather than at 0.2 is that this project's name is inside its own stable surface. The Issue body carries `<!-- agentforge:plan -->`, `<!-- agentforge:result -->`, and `<!-- agentforge:gate -->`, and a Run wears `agentforge:planned`, `agentforge:running`, and the rest as labels. ADR-0011 promises, as of 0.1, that exactly those keep parsing. Renaming them afterwards would not be a rename; it would be a break of the promise made one release earlier, and it would need every old marker and label read forever alongside the new one. `LEGACY_LABELS` already carries `agentforge:escalated` for precisely that reason, from a rename that happened while issues were open — which is a small taste of the same bill.

Today the bill is zero. No release exists, no wheel has been published, and the only issues wearing these labels are this repository's own and the smoke repository's.

Two alternatives were live. **Keeping the name and publishing under a different distribution name** was the cheapest and was rejected because it fixes the half that does not hurt: `pip install` gets a new spelling while `import agentforge` still collides and every search still lands on the older project. **Renaming at 0.2** was rejected because it turns a mechanical sweep into a compatibility event, and because the README, the CHANGELOG, and these ADRs would be rewritten in either case — the only question is whether a published tag sits in the middle of it.

`agentfortress` was the first choice for the new name and is taken on PyPI by a real package, released in April 2026, doing runtime security monitoring for AI agents. `bastion` alone is free but already means a jump host to the infrastructure audience this tool is built for. `agentbastion` is free on PyPI under both spellings, is unused as a repository name on GitHub, and keeps the fortified-position reading that suits a system whose central mechanism is a Gate holding a Run until somebody clears it.

## Consequences

The rename covers the repository, the distribution, the import path, the console script, the two markers and the gate marker, the status labels, and the documentation. It is one sweep rather than an expand-and-contract migration, because nothing outside this repository depends on any of those yet, and that is the whole reason for doing it before the tag rather than after.

No `agentforge:*` label is read back, and `LEGACY_LABELS` is left empty rather than filled. One Issue anywhere wore one of them — `agentforge:awaiting-signoff`, on the smoke repository — and relabelling it by hand cost one command against the alternative of reading a dead spelling for as long as the project lives. The `agentforge:escalated` entry that was already there went the same way: no Issue in either repository still wore it, so the Escalated-to-Halted rename it was carrying is finished. The mechanism stays where it is, empty, for the first rename that lands with somebody else's Issues open.

The name question was always described as gating publication rather than the tag, and that stays true of the question. This particular answer puts the rename ahead of the tag anyway: a first release carrying a name the project is already leaving would make the 0.1 artifact the one thing nobody can correct afterwards. A renamed repository keeps resolving its old links; a published artifact does not.

So the sweep lands before CI builds a wheel rather than after. The artifact and the console script it runs both carry the name, and building one in order to throw it away is the kind of work that only gets done twice.
