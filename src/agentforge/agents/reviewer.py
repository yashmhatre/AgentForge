"""The Reviewer: one comment a human reads instead of four they reconstruct.

It compares what changed against the frozen Plan, says whether the Run did what
it said it would, and writes the prose that a human reads at Sign-off.

That prose is scanned before it is posted. The `unslop` scanners are
deterministic and no model is involved in judging them, so a finding is a fact
about the text rather than an opinion about it; the Reviewer is handed its own
findings and rewrites, twice at most.

The scan is a Command and not a Gate. Prose that still scans dirty on the third
attempt is posted anyway with its report attached, because holding a finished
Run on a cosmetic check trades a real cost for a stylistic one. The report goes
to the Run Log either way — thin prose that nobody can diagnose is how a check
like this quietly stops meaning anything.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

from ..core.contracts import AgentResult, ContextPack, ModelTier, Outcome, Plan, Role
from ..core.plan_format import RESULT_CLOSE, RESULT_OPEN
from ..core.skills import UnslopReport, render_report, run_unslop
from .implementer import render_steps

#: Rewrites after the first attempt. Three attempts in total, then the prose is
#: posted as it stands: a fourth try costs another invocation to improve a
#: sentence nobody has asked to be perfect.
MAX_REWRITES = 2

#: The tier a rewrite runs at, which is not the tier the review runs at. Two
#: different jobs share this Step: judging a diff against a frozen Plan, and
#: applying findings that already name the phrase, the line, and a replacement.
#: The Role's declared tier is chosen for the first. Paying it for the second
#: buys a stronger model to run find-and-replace, twice. See ADR-0004.
REWRITE_TIER = ModelTier.CHEAP

INSTRUCTIONS = """\
You are the Reviewer in AgentForge. You are the last Role to speak before a \
human reads this Run, and what you write is what they read.

Compare what the Run changed against the frozen Plan and say plainly whether it \
did what it said it would: which steps were carried out, which were not, and \
anything done that the Plan did not ask for.

Then write the documentation a human needs at Sign-off -- what changed, why, and \
what to look at first. Write nothing to the repository; your work is this \
report.

Write like a colleague explaining the change to another colleague. Say what \
happened. Do not pad, do not hedge every claim, and do not summarize the \
summary.\
"""

PROMPT = """\
{instructions}

## The frozen Plan

{summary}

### Steps

{steps}

## Working directory

{cwd}

You are on the branch the change was made on. Read the files the Plan names and \
compare them against what it asked for. Change nothing and commit nothing.

## Required output

End your reply with this block and nothing after it:

{result_open}
```json
{{
  "outcome": "completed",
  "summary": "one line: does the change match the Plan",
  "detail": "the review a human reads at Sign-off",
  "files_changed": []
}}
```
{result_close}

Use `"outcome": "escalated"` only if the Plan cannot be reviewed against the \
repository at all -- it names files that are not there, or nothing was changed.\
"""

REWRITE = """\
{instructions}

## Your previous review

{prose}

## What a scanner found in it

{findings}

These are findings about the writing, not about the change you reviewed. Each \
one names the line and what to do about it. Rewrite the review to say the same \
things without them.

Keep every claim you made about the change. Losing a fact to fix a phrase is a \
worse review, and the review is the point.

## Required output

End your reply with this block and nothing after it:

{result_open}
```json
{{
  "outcome": "completed",
  "summary": "one line: does the change match the Plan",
  "detail": "the rewritten review",
  "files_changed": []
}}
```
{result_close}
"""

#: The Reviewer runs `deep`. It speaks last, and what it writes is the whole of
#: what a human reads at Sign-off: whether the Run did what it said it would, and
#: what to look at first. A thin review is one nobody can act on, and nothing
#: downstream catches it — the next thing after this Role is a person deciding
#: whether to merge. Its rewrites run at `REWRITE_TIER` instead.
REVIEWER = Role(name="reviewer", tier=ModelTier.DEEP, instructions=INSTRUCTIONS)


def build_prompt(
    plan: Plan,
    context: ContextPack,
    cwd: Path,
    role: Role = REVIEWER,
) -> str:
    return PROMPT.format(
        instructions=role.instructions,
        summary=plan.summary.strip(),
        steps=render_steps(plan),
        cwd=cwd,
        result_open=RESULT_OPEN,
        result_close=RESULT_CLOSE,
    )


def build_rewrite_prompt(prose: str, report: UnslopReport, role: Role = REVIEWER) -> str:
    """The second and third attempts: the Reviewer's own prose and its findings.

    The scanners' own output and nothing else. A skill's doctrine inlined here
    would be AgentForge teaching a Role to write, when each finding already
    carries the phrase, the line, and a suggestion.
    """
    findings = "\n".join(render_report(report)) or "_the scanners reported nothing_"
    return REWRITE.format(
        instructions=role.instructions,
        prose=prose.strip(),
        findings=findings,
        result_open=RESULT_OPEN,
        result_close=RESULT_CLOSE,
    )


def prose_of(result: AgentResult) -> str:
    """What the Reviewer wrote, as one piece of text to be scanned.

    The summary is part of it. It is the line most people read and the least
    likely to have been thought about twice.
    """
    return "\n\n".join(part for part in (result.summary.strip(), result.detail.strip()) if part)


class Reviewer:
    """One Reviewer invocation, plus up to two rewrites of its own prose."""

    def __init__(self, provider) -> None:
        self.provider = provider

    def run(
        self,
        *,
        plan: Plan,
        context: ContextPack,
        cwd: Path,
        role: Role = REVIEWER,
        tier: ModelTier | None = None,
    ) -> AgentResult:
        tier = tier or role.tier
        result = self.provider.invoke(
            role=role,
            prompt=build_prompt(plan, context, cwd, role),
            context=context,
            tier=tier,
            cwd=cwd,
        )

        # A rewrite is a different job from the review and is priced as one.
        # `at_tier` rather than a second mechanism: varying a Role for one
        # invocation is what it is for.
        rewriter = role.at_tier(REWRITE_TIER)

        attempt = 1
        report = self._scan(result)
        while report is not None and not report.clean and attempt <= MAX_REWRITES:
            rewritten = self.provider.invoke(
                role=rewriter,
                prompt=build_rewrite_prompt(prose_of(result), report, rewriter),
                context=context,
                tier=REWRITE_TIER,
                cwd=cwd,
            )
            attempt += 1
            if rewritten.outcome is not Outcome.COMPLETED:
                # A rewrite that could not be produced is the Run's business,
                # not the scanner's. Hand it back as it came.
                return rewritten
            result, report = rewritten, self._scan(rewritten)

        # The tier the Run Log reports is the one the review was written at. A
        # result carries whatever tier produced it, so without this a `deep`
        # review reads as `cheap` for the only reason that a phrase was fixed.
        if report is None:
            return replace(result, tier=tier)
        return replace(
            result, tier=tier, detail=_with_report(result.detail, report, attempt)
        )

    def _scan(self, result: AgentResult) -> UnslopReport | None:
        """Scan what the Reviewer wrote, or nothing if it did not review.

        The prose is written outside the working tree on purpose: AgentForge
        commits whatever a Role leaves behind, and a scratch file left in the
        repository would be committed and reviewed by nobody.
        """
        if result.outcome is not Outcome.COMPLETED:
            return None

        prose = prose_of(result)
        if not prose:
            return None

        runner = getattr(self.provider, "runner", None)
        with tempfile.TemporaryDirectory(prefix="agentforge-review-") as directory:
            path = Path(directory) / "review.md"
            path.write_text(prose, encoding="utf-8")
            return run_unslop(path, runner=runner)


def _with_report(detail: str, report: UnslopReport, attempt: int) -> str:
    """The review, then what the scanners made of it. See ADR-0002: the Run Log
    is the only place a later reader looks."""
    # Where the money went, for a reader wondering why a `deep` Role's entry
    # mentions three attempts: only the first was written at that tier.
    rewritten = "" if attempt == 1 else f", rewritten at `{REWRITE_TIER}`"
    tries = f"attempt {attempt} of {MAX_REWRITES + 1}{rewritten}"
    if report.clean:
        headline = f"**`unslop` scan** — clean on {tries}."
        lines = [headline]
        if report.failed:
            lines += ["", *render_report(report)]
    else:
        lines = [
            (
                f"**`unslop` scan** — {report.violations} finding(s) still standing after "
                f"{tries}. Posted anyway: the scan is a Command and not a Gate, and a "
                "finished Run does not wait on a sentence."
            ),
            "",
            *render_report(report),
        ]

    return "\n\n".join(part for part in (detail.strip(), "---", "\n".join(lines)) if part)


__all__ = [
    "INSTRUCTIONS",
    "MAX_REWRITES",
    "REVIEWER",
    "REWRITE_TIER",
    "Reviewer",
    "build_prompt",
    "build_rewrite_prompt",
    "prose_of",
]
