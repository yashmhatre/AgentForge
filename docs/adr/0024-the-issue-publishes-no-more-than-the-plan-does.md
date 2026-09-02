# ADR-0024: The Issue publishes no more of a codebase than the Plan already does

A Run posts its Context Pack to the Issue before the first Role is invoked, and
that comment named every file the pack carried, every symbol in it, and the
import graph between them. Nobody chose this. It falls out of ADR-0002 putting
the Run Log on the Issue and ADR-0010 resolving a pack of exactly the files a
task touches: the more precisely the pack is scoped, the more precisely the
comment describes the internals.

The tracker can have a wider audience than the code. A private repository whose
Issues are shared with a client, a public repository whose reviewers never
expected the symbol inventory of a security-sensitive module enumerated in a
comment — in both, an unattended Run publishes a map nobody asked it to. And
the disclosure is the unrecoverable half: a comment posted to a public tracker
is published, indexed, and not retrieved by deleting it.

**The line is that the Issue publishes no more about a codebase than it already
carries.** ADR-0003 freezes the Plan into the Issue body, and the Plan names its
files. So the pack's file list discloses nothing new and stays. Private symbol
names and the graph between modules appear nowhere else on the Issue; those are
the disclosure, and they are withheld unless the repository asks for them.

The counts stay in either case. "17 files, 240 symbols" is most of what the
comment is read for — a pack that resolved more or less than a reader expected
is the diagnosis — and a count is not a map. A reader who wants the names has
the file list and the repository, which is where the names came from.

`context.publish_inventory` turns them back on. Per ADR-0020 the key arrives
with its reader, which is this change, and `agentforge init` writes it. The
default is off because the two mistakes are not symmetric: publishing when you
should not have is permanent, and withholding costs a diagnostician one step
they can take themselves.

The other two surfaces the question named are left alone, for the same reason
the file list is. An Agent Result's `files_changed` is the same category as the
Plan's files. A Finding's location is the point of the Finding — a security
finding that cannot say where is not a finding — and it is deliberate
disclosure to a human who is being warned, not an inventory produced as a side
effect.

## Consequences

The comment is less useful to somebody diagnosing a Run on a repository where
the inventory would have been fine, until they set the key. That is the cost of
defaulting to the reversible mistake, and it is one line in a file `init`
already writes.

A repository that turns it on has said its tracker's audience is its code's
audience. Nothing checks that claim, and nothing can: whether an Issue is read
by people who may not read the source is not a fact AgentForge can observe.

This does not make a Run private. The Plan, the file paths, the Run Log, and
every Finding are still posted to the Issue, because that is what ADR-0002 is
for. A repository that cannot publish any of that should not be filing its Runs
on a tracker with a wider audience, and no key here changes that.
