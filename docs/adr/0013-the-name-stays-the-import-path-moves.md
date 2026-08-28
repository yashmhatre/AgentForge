# ADR-0013: AgentForge keeps its name; the distribution and the import path move

This supersedes ADR-0012, which decided the project would become AgentBastion. It reverses that decision on evidence ADR-0012 did not have, and keeps everything ADR-0012 got right about why the question had to be settled before 0.1 is tagged.

The project is AgentForge. It publishes as `agentforge-framework`, imports as `agentforge_framework`, and puts `agentforge` on the path.

## What ADR-0012 assumed, and what is actually true

ADR-0012 rejected the decorated-distribution answer in one sentence: publishing under another index name "fixes the half that does not hurt: `pip install` gets a new spelling while `import agentforge` still collides." That is only true if the import package keeps the plain name. It does not have to. `agentforge-framework` installing a top-level `agentforge_framework` collides with nothing, and a machine can hold both projects with neither shadowing the other.

The second thing ADR-0012 did not check is what the other project actually claims. `agentforge` 0.6.6 ships a single top-level package and **no console script at all** — no `entry_points.txt` in the wheel. The command a person types is unclaimed. So the decoration stops at the two names nobody reads out loud, and `agentforge implement 12` stays exactly what it was.

That leaves the reputational half, and there ADR-0012 understated the problem rather than overstating it. It describes one older project. There are at least eight AgentForge-shaped packages on PyPI from unrelated authors — `agentforge-py` and `agentforge-core` from Scaffoldic, `agentforge-ai`, `agentforge-runtime`, `agentforge-cli`, `agentsforge`, `agentforgex` — and more than thirty GitHub repositories, one of which describes itself as a terminal AI coding-agent harness. The name is not owned by a big neighbour; it is a commons this category keeps reaching for.

That cuts both ways, and it is why the decision changed. A crowded name is a discoverability cost, and it is one every AgentForge pays, including the 844-star one. It is not a technical break, and ADR-0012 treated it as though it were. Trading a name the project has been built and documented under for a clean search result is a marketing decision priced as an engineering one.

## Why this is cheaper than ADR-0012's answer

ADR-0012's strongest argument was about timing: the name is inside the stable surface, so renaming after 0.1 would break a promise made one release earlier. That argument is exactly right and it is why this ADR exists now rather than at 0.2.

It also disappears under this decision. The Issue body still carries `<!-- agentforge:plan -->`, a Run still wears `agentforge:planned`, a Run branch is still `agentforge/issue-12`, and a project still configures the tool in `.agentforge/config.yaml`. None of them changes, so ADR-0011's promise is kept by leaving it alone rather than by racing a tag. `LEGACY_LABELS` keeps its one entry and gains nothing.

The change is the package directory, the imports in the tests, four lines of `pyproject.toml`, and the paths in the docs that name the source tree. Nothing a user of 0.1 will ever type is different.

## Consequences

`pip install agentforge-framework` is a spelling somebody will get wrong, and the wheel is `agentforge_framework-0.1.0-py3-none-any.whl` rather than something that matches the command it installs. The README says so at the install step, which is the only place it can be said usefully.

Somebody who guesses `pip install agentforge` gets a different project. That is true under any answer short of owning the name, including ADR-0012's, and it is the cost being accepted here.

`agentforge_framework` is a long name to read at the top of every test file. ADR-0011 already makes that surface private, so it is read inside this repository and nowhere else.

If the name is ever worth abandoning, this decision does not make that harder. It is `pyproject.toml` and a directory rename again, and the markers and labels only come into it on the day the project name itself changes — which is the day ADR-0012 was actually describing.
