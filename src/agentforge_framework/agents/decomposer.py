"""The Decomposer: a Task becomes a set of Issues, in dependency order.

Every planning pass comes through here, whether the source was typed at a shell
(`agentforge plan`) or read from a document the project already keeps
(`agentforge decompose`). The two differ in where the text comes from and in
nothing else, so there is one pipeline rather than a small one and a large one
that drift apart.

It does in four movements what a single Orchestrator pass used to do in one:

- **Grill.** `grill-with-docs`, the same interview `agentforge plan` runs, aimed
  at the document instead of a sentence. A plan document is longer than a Task
  and no less ambiguous -- it records what its author decided and rarely what
  they rejected, and it is the rejected alternatives that a Slice boundary turns
  on.
- **Spec.** `to-spec`, which synthesizes and does not interview. It has the
  document and the grill transcript, and it commits to one reading of them.
- **Cut.** `to-tickets`, which turns that reading into Slices: vertical, each
  sized for one fresh context window, each naming the Slices that block it.
- **Plan.** One ordinary Orchestrator planning pass per approved Slice, so that
  what gets filed is an Issue like any other and `agentforge implement` learns
  nothing new.

Four stages rather than one prompt because they are four jobs, and a single pass
asked to interview and commit to a breakdown at once commits to the breakdown it
had before it asked. The cut is shown to the human before anything is filed:
fifteen wrong Issues take longer to close than one wrong plan takes to reject.

See ADR-0021.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..core.contracts import (
    AgentResult,
    ContextPack,
    ModelTier,
    Outcome,
    PlanDocument,
    Slice,
    Task,
)
from ..core.plan_format import (
    RESULT_CLOSE,
    RESULT_OPEN,
    SLICES_CLOSE,
    SLICES_OPEN,
    SPEC_CLOSE,
    SPEC_OPEN,
    PlanFormatError,
    extract_slices,
    extract_spec,
    order_slices,
)
from .orchestrator import (
    ORCHESTRATOR,
    Exchange,
    Interviewer,
    Orchestrator,
    render_transcript,
)

#: The skill each stage is delivered. One per stage, and never two: a pass given
#: both `to-spec` and `to-tickets` has been handed the method for a job it is not
#: doing yet, and both skills end by publishing to a tracker.
SPEC_SKILLS = ("to-spec",)
SLICE_SKILLS = ("to-tickets",)

#: Slices one document may be cut into. Past this the breakdown is not a
#: breakdown, it is the plan re-typed, and no human is reading forty of them
#: before approving. The cut is asked to merge rather than truncated, because a
#: list cut off at the cap silently drops the end of the plan.
MAX_SLICES = 25

SPEC = """\
You are the Orchestrator in AgentForge, synthesizing what a human gave you into \
a spec that one breakdown pass can cut into work.

Do not interview. The questions have been asked; what came back is below, and it \
is part of the source now. Where the source and an answer disagree, the answer \
is later and wins. Where neither settles something, say so in the spec rather \
than choosing quietly -- an unresolved question named in a spec gets resolved, \
and one you resolved by yourself gets discovered during implementation.

## What the human gave you

{source}

{document}
{interview}
## The repository

You are running in {cwd}. Read whatever you need in order to synthesize \
accurately -- existing structure, conventions, tests, and any CONTEXT.md, \
AGENTS.md, or docs/adr/ the project keeps. Use the project's own vocabulary \
throughout. Do not change any files. This is a synthesis pass.

## What you do not do

You have no issue tracker and no triage labels. AgentForge files every Issue \
itself, later, through its own GitHub boundary, and this pass files nothing. Do \
not publish the spec, do not open an issue, and do not apply a label.

## Required output

End your reply with these two blocks, in this order, and nothing after them.

First the spec. Markdown inside it, not JSON:

{spec_open}
## Problem Statement
...

## Solution
...

## User Stories
...

## Implementation Decisions
...

## Testing Decisions
...

## Out of Scope
...
{spec_close}

Then your own verdict:

{result_open}
```json
{{"outcome": "completed", "summary": "one line on what the spec covers"}}
```
{result_close}

Cover the whole source. Anything in it that reaches no user story reaches no \
Issue and does not get built. Scale the spec to what you were given: a \
one-sentence Task earns a short spec, and padding one out invents scope nobody \
asked for.

If the source is too thin to synthesize without inventing most of it, write the \
verdict block with `"outcome": "escalated"` and a summary naming what you need \
the human to supply. Omit the spec block in that case.\
"""

SLICES = """\
You are the Orchestrator in AgentForge, cutting a spec into the Slices that will \
be filed as Issues.

A Slice is a tracer bullet: a narrow but complete path through every layer the \
work touches, demoable or verifiable on its own. It is not a layer -- "the \
schema changes" is not a Slice, and neither is "the tests".

Two rules decide the size:

- **One Slice is one fresh context window.** Each will be planned and executed \
by an Agent that has never seen this spec, starting from nothing and stopping \
when it opens a pull request. A Slice it cannot finish in one sitting is one it \
finishes badly.
- **Together the Slices are the whole spec.** Every user story lands in exactly \
one. Anything the spec put out of scope stays out.

A wide refactor is the exception to cutting vertically. Where one mechanical \
change fans across the codebase and no vertical slice can land green, sequence \
it expand-migrate-contract instead: add the new form beside the old, migrate the \
call sites in batches each blocked by the expand, then delete the old form in a \
Slice blocked by every batch.

Give each Slice the Slices that must be finished before it can start. A Slice \
with no blockers can start immediately, and there must be at least one of those \
or nothing can begin. Do not invent an edge to express a preference: an edge \
means the later Slice genuinely cannot be built until the earlier one is, and \
every edge you add is a Slice that cannot run beside another.

Cut as many Slices as the spec has work in it and no more. A spec that is \
genuinely one sitting's work cuts to one Slice, and manufacturing a breakdown \
for it files Issues nobody needed and puts edges between them. Never cut more \
than {cap}: if the spec seems to need more, the Slices are too small, so merge \
the ones that share a seam rather than dropping any of the spec.

## The spec

{spec}

## The repository

You are running in {cwd}. Read whatever you need. Title each Slice in the \
project's own vocabulary. Do not change any files.

## What you do not do

You have no issue tracker and no triage labels. AgentForge files these itself, \
in the order your blocking edges imply, through its own GitHub boundary. Do not \
publish anything, do not open an issue, and do not apply a label.

## Required output

End your reply with these two blocks, in this order, and nothing after them.

First the cut:

{slices_open}
```json
{{
  "slices": [
    {{
      "id": "a short slug, unique, referenced by other slices",
      "title": "what a human reads in a list of thirty issues",
      "delivers": "the end-to-end behaviour this Slice makes work, from the user's perspective, not a layer-by-layer implementation list",
      "acceptance": ["how this Slice is known to be done, checkable by reading the repository"],
      "blocked_by": ["the ids of the Slices that must finish first, or an empty list"]
    }}
  ]
}}
```
{slices_close}

Then your own verdict:

{result_open}
```json
{{"outcome": "completed", "summary": "one line on how you cut it and why"}}
```
{result_close}

If the spec cannot be cut without guessing at something it left open, write the \
verdict block with `"outcome": "escalated"` and a summary naming what has to be \
settled first. Omit the slices block in that case.\
"""

#: What one approved Slice looks like when it is handed to a planning pass. The
#: Spec travels with it: the Slice says what to build and the Spec says what the
#: rest of the work is, which is how a planning pass knows what not to build
#: here. The blockers are named because a Slice that assumes work an earlier one
#: does should say so rather than repeating it.
SLICE_TASK = """\
{delivers}

This is one Slice of a larger plan, filed as its own Issue. Plan this Slice and \
nothing else: the rest of the plan is other Issues, and work you do here that \
belongs to one of them is work done twice.

## Acceptance criteria for this Slice

{acceptance}

## What is already done when this starts

{blockers}

## The wider spec, for context only

Everything below is the whole plan. It is here so that you know where this Slice \
sits and what it must not duplicate. Do not plan any of it.

{spec}\
"""


@dataclass(frozen=True)
class Filed:
    """One Slice, once a planning pass has turned it into something fileable."""

    slice: Slice
    document: PlanDocument
    #: Issue numbers, filled in by the runtime as blockers are filed. A Slice
    #: names its blockers by id; only the runtime knows what number each got.
    blocked_by: tuple[int, ...] = ()


@dataclass(frozen=True)
class Decomposed:
    """What a decomposition pass produced, or the reason there is nothing."""

    #: Every invocation the pass made, in order, so the caller can price it and
    #: say which stage stopped when one did.
    results: tuple[AgentResult, ...] = ()
    spec: str = ""
    slices: tuple[Slice, ...] = ()
    interview: tuple[Exchange, ...] = ()
    #: Set when a stage did not produce what the next one needs. The pass stops
    #: there; nothing is filed.
    failure: AgentResult | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None and bool(self.slices)


class Decomposer:
    """Runs the stages that turn one document into an approved breakdown."""

    def __init__(self, provider, tier: ModelTier | None = None) -> None:
        self.provider = provider
        self.tier = tier or ORCHESTRATOR.tier

    # --- the stages --------------------------------------------------------

    def _invoke(self, skills: Sequence[str], prompt: str, cwd: Path) -> AgentResult:
        role = replace(ORCHESTRATOR.at_tier(self.tier), skills=tuple(skills))
        return self.provider.invoke(
            role=role, prompt=prompt, context=ContextPack(), tier=self.tier, cwd=cwd
        )

    def synthesize(
        self, document: str, source: str, cwd: Path, exchanges: Sequence[Exchange] = ()
    ) -> AgentResult:
        interview = ""
        if exchanges:
            interview = (
                "\n## What the human told you when you asked\n\n"
                + render_transcript(exchanges)
                + "\n\nThese answers are part of the plan now.\n"
            )
        return self._invoke(
            SPEC_SKILLS,
            SPEC.format(
                document=document.strip(),
                source=source,
                interview=interview,
                cwd=cwd,
                spec_open=SPEC_OPEN,
                spec_close=SPEC_CLOSE,
                result_open=RESULT_OPEN,
                result_close=RESULT_CLOSE,
            ),
            cwd,
        )

    def cut(self, spec: str, cwd: Path) -> AgentResult:
        return self._invoke(
            SLICE_SKILLS,
            SLICES.format(
                spec=spec.strip(),
                cwd=cwd,
                cap=MAX_SLICES,
                slices_open=SLICES_OPEN,
                slices_close=SLICES_CLOSE,
                result_open=RESULT_OPEN,
                result_close=RESULT_CLOSE,
            ),
            cwd,
        )

    # --- the pass ----------------------------------------------------------

    def decompose(
        self,
        document: str,
        source: str,
        cwd: Path,
        interviewer: Interviewer | None = None,
    ) -> Decomposed:
        """Grill, synthesize, cut. Nothing here files anything.

        Each stage stops the pass rather than degrading into the next one. A
        breakdown cut from a spec that was never written is a breakdown of
        whatever the model remembered of the document, which is the one failure
        that would not look like a failure.
        """
        task = Task(statement=document)
        exchanges = (
            Orchestrator(self.provider, tier=self.tier).interview(task, cwd, interviewer)
            if interviewer
            else ()
        )

        spec_result = self.synthesize(document, source, cwd, exchanges)
        results: tuple[AgentResult, ...] = (spec_result,)
        if spec_result.outcome is not Outcome.COMPLETED:
            return Decomposed(results=results, interview=exchanges, failure=spec_result)

        try:
            spec = extract_spec(spec_result.raw)
        except PlanFormatError as exc:
            return Decomposed(
                results=results,
                interview=exchanges,
                failure=_failed(
                    self.tier, f"the synthesis pass wrote no usable spec: {exc}", spec_result
                ),
            )

        cut_result = self.cut(spec, cwd)
        results += (cut_result,)
        if cut_result.outcome is not Outcome.COMPLETED:
            return Decomposed(results=results, spec=spec, interview=exchanges, failure=cut_result)

        try:
            slices = order_slices(extract_slices(cut_result.raw))
        except PlanFormatError as exc:
            return Decomposed(
                results=results,
                spec=spec,
                interview=exchanges,
                failure=_failed(
                    self.tier, f"the breakdown pass wrote no usable slices: {exc}", cut_result
                ),
            )

        if len(slices) > MAX_SLICES:
            return Decomposed(
                results=results,
                spec=spec,
                interview=exchanges,
                failure=_failed(
                    self.tier,
                    f"the breakdown pass cut {len(slices)} Slices against a cap of "
                    f"{MAX_SLICES}; the plan document wants splitting before it is decomposed",
                    cut_result,
                ),
            )

        return Decomposed(results=results, spec=spec, slices=slices, interview=exchanges)


def slice_task(one: Slice, spec: str, blockers: Sequence[Slice] = ()) -> Task:
    """One Slice, phrased as the Task a planning pass is given."""
    acceptance = "\n".join(f"- {criterion}" for criterion in one.acceptance) or (
        "- None stated. Write acceptance criteria the executing Role can check."
    )
    done = "\n".join(
        f"- {blocker.title}: {blocker.delivers}".rstrip(": ") for blocker in blockers
    ) or "- Nothing. This Slice starts from the repository as it is."

    return Task(
        statement=SLICE_TASK.format(
            delivers=(one.delivers or one.title).strip(),
            acceptance=acceptance,
            blockers=done,
            spec=spec.strip(),
        )
    )


def render_breakdown(slices: Sequence[Slice]) -> list[str]:
    """The cut, as the human is shown it before anything is filed."""
    by_id = {one.id: one for one in slices}
    lines: list[str] = []
    for index, one in enumerate(slices, start=1):
        lines.append(f"{index}. {one.title}  [{one.id}]")
        if one.delivers:
            lines.append(f"     Delivers: {one.delivers}")
        blockers = ", ".join(by_id[b].title for b in one.blocked_by if b in by_id)
        lines.append(f"     Blocked by: {blockers or 'nothing -- can start immediately'}")
        for criterion in one.acceptance:
            lines.append(f"     - {criterion}")
    return lines


def _failed(tier: ModelTier, summary: str, source: AgentResult) -> AgentResult:
    """A stage that answered, but not in the shape the next stage needs."""
    return AgentResult(
        role=ORCHESTRATOR.name,
        tier=tier,
        outcome=Outcome.FAILED,
        summary=summary,
        detail=source.raw,
        raw=source.raw,
        usage=source.usage,
    )


#: The human, as a callable, answering once. `None` where nobody is attached --
#: and there the breakdown is not filed, because approving fifteen Issues on
#: somebody's behalf is not a default worth having.
Approver = Callable[[Sequence[Slice]], bool]
