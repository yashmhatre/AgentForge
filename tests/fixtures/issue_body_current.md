## Task

> add a retry to the loader

## Plan

Add a retry to the loader.

### Steps

1. **s1** — Wrap the fetch in a bounded retry
   - Files: `src/loader.py`
   - Done when: A transient failure retries three times and then raises.
2. **s2** — Cover the exhausted-retry path
   - Files: `tests/test_loader.py`

### Constraints

- Do not change the public signature of `load`.

## Roster

Running the `feature` Workflow, in this order. A Gate between two Steps holds the Run until it clears.

| Order | Role | Model Tier |
| --- | --- | --- |
| 1 | implementer | `standard` |

## Context Pack

- Files: src/loader.py, tests/test_loader.py
- Symbols: load
- Conventions: ruff, line length 100

## Notes

- The `tester` Role was requested but is not implemented yet (M2).

---

<!-- agentbastion:plan -->
```json
{
  "context": {
    "conventions": [
      "ruff, line length 100"
    ],
    "files": [
      "src/loader.py",
      "tests/test_loader.py"
    ],
    "references": [],
    "symbols": [
      "load"
    ]
  },
  "notes": [
    "The `tester` Role was requested but is not implemented yet (M2)."
  ],
  "plan": {
    "constraints": [
      "Do not change the public signature of `load`."
    ],
    "steps": [
      {
        "acceptance": "A transient failure retries three times and then raises.",
        "files": [
          "src/loader.py"
        ],
        "id": "s1",
        "intent": "Wrap the fetch in a bounded retry"
      },
      {
        "acceptance": "",
        "files": [
          "tests/test_loader.py"
        ],
        "id": "s2",
        "intent": "Cover the exhausted-retry path"
      }
    ],
    "summary": "Add a retry to the loader."
  },
  "roster": [
    {
      "role": "implementer",
      "tier": "standard"
    }
  ],
  "version": 1,
  "workflow": "feature"
}
```
<!-- /agentbastion:plan -->

*Filed by AgentBastion. The block above is the frozen execution contract (ADR-0003). Every Role parses it; edit it rather than the prose.*
