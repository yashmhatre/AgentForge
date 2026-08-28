# ADR-0006: Third-party skills ship as vendored package data

AgentForge depends on five skills it does not own, and they are agent instructions and loose scripts rather than published packages — there is nothing to declare as a dependency. They are vendored into `src/agentforge_framework/skills/` as package data, invoked as subprocesses through the Command Runner and never imported, with upstream repository, commit SHA, licence, and deliberate exclusions recorded in `skills/MANIFEST.yaml` so that a reader finding someone else's MIT code in the source tree learns why instead of removing it.

## Consequences

Refreshing a bundle means re-vendoring from upstream and updating the manifest, so a fix goes upstream and comes back rather than being patched in place. Each bundle's internal layout is load-bearing — the `unslop` scanners import a sibling module and resolve fixtures relative to their own directory — so flattening or partially copying one breaks it silently.
