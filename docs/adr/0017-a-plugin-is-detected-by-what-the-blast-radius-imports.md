# ADR-0017: A Plugin is detected by what the blast radius imports

ADR-0016 gave a Plugin two ways to be detected: a suffix in the frozen Plan's blast radius, and a marker at the repository root. Both were enough for `python`, `sql`, and `databricks`. Neither is enough for `pyspark`, which is the Plugin the milestone was filed for.

A Spark job is a `.py` file. So is a Django view, a Flask route, a test, and every file in this repository. Claiming `.py` would hold every Python repository on the machine to the DataFrame API, which is not a mild cost: a Fragment is standing instruction in a prompt, and a team told about `collect()` while editing its serializers switches Plugins off and never turns them back on. A root marker fails from the other direction — there is no file that means "this repository has Spark in it", and `pyspark` in a lockfile says a dependency is installed rather than that this Plan touches a job.

**A Plugin may declare the module names it answers for, and `core.registry` reads the Python files the frozen Plan names to find them.** `Plugin.imports` is a tuple of top-level module names; `pyspark` matches `import pyspark` and `from pyspark.sql import functions as F` alike. Any of the three detections is sufficient, as before.

Three alternatives were considered and rejected. A detection *callable* on the Plugin — `detect(plan, root) -> bool` — is the most flexible and stops a Plugin being data: the glossary's definition and ADR-0016 both turn on a Plugin having no behaviour, and `agentforge init` (M5) is supposed to write down what detection computed, which it can do with a tuple of module names and cannot do with a function. A *content substring* — "activate where the text contains `pyspark`" — matches the word in a docstring, a comment, and a migration note about having stopped using it. A dependency-manifest read — `pyproject.toml`, `requirements.txt` — answers a question about the environment rather than about the work, and the whole point of reading the blast radius is that a repository with Spark jobs and a Plan that touches none of them is not doing Spark work this Run.

**Detection reads the same files the Context Pack was about to read, through the same rules.** The paths come from the frozen Plan in Plan order, capped at `MAX_FILES`, filtered through the resolver's containment check so that a Plan naming `../../.ssh/id_rsa` reads nothing, and read through the resolver's size bound. Only `.py` is read: an import is a Python idea, and a `.sql` file's references are table names, so asking a query whether it "imports pyspark" would activate a Plugin because a warehouse happened to hold a table with that name. The read happens once per Run and only where some Plugin declares an import at all, so a repository with no such Plugin opens no file to discover that.

Imports are read with the built-in Python extractor rather than with a regular expression. It already knows what an import is, it ignores the word in a docstring, and a file that will not parse yields nothing — which is the answer detection wants, because a Plugin that a syntax error in one file could switch off would be worse than a Plugin nobody wrote. It reads with the built-in extractor table rather than the Plugin-widened one, since the widened table is assembled from the Plugins this decision is choosing.

## Consequences

Activation now touches the filesystem for reasons other than a root marker. The three promises of `core/registry.py` survive it: the answer is still a function of the frozen Plan and the repository, so it is deterministic; it is bounded by the resolver's own caps rather than by new ones; and a missing, unreadable, oversized, or unparsable file contributes nothing rather than raising.

The cost is at most `MAX_FILES` reads of files the pack resolves immediately afterwards, and it is paid before the first Agent is invoked, where a subprocess launch dwarfs it. It is paid on a control Run too — `--no-context-pack` suppresses what a Plugin contributes, not whether it was detected — because an active set that changed with the control would make the control meaningless.

A file too large for the pack to read is too large to be detected from, so a generated module that imports `pyspark` and runs to a megabyte activates nothing. That is the honest behaviour: the alternative is an activation whose evidence nobody can see.

`Plugin.imports` is declarative, so `agentforge init` can record what detection computed rather than inventing a second activation mechanism, which is what user story 31 of the milestone asks for.
