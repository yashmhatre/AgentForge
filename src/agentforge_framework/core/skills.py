"""Access to the skill bundle.

Skills ship as package data, never as importable modules. Markdown is read as
text; Python is invoked as a subprocess through the Command Runner, which is the
one process boundary in the codebase.

Most of the bundle is vendored third-party work (ADR-0006) and never edited in
place. A few skills are AgentForge's own; `FIRST_PARTY` names them, because a
refresh re-copies upstream over this directory and anything of ours that is not
findable goes with it.

One of ours is a composite: a skill whose job is to run two others together on
one task. `COMPOSED` says what each expands to, because a composite has to
survive both Capability Tiers — natively it fans out through the Skill tool, and
as a Fragment there is no tool to fan out with, so the delivery path inlines what
it names.

A skill's author can also forbid autonomous invocation outright, by marking
`disable-model-invocation` in the frontmatter. ADR-0005 chose delivery from one
input — what the Provider can do — and had none for what the skill permits, so a
marked skill offered natively is refused by the Skill tool and the Role escalates
instead of working. `fragment_only` reads the mark from the bundle as shipped, so
the rule survives a refresh that marks a skill we have never heard of.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from .process import CommandRunner, SubprocessRunner

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"

#: Scanners the `unslop` Command runs, in report order. The remaining scripts in
#: the bundle serve upstream's voice and calibration features and are not wired
#: into AgentForge.
UNSLOP_SCANNERS = (
    "banned_phrase_scan.py",
    "structure_scan.py",
    "silhouette_scan.py",
)


#: Every skill in the bundle that AgentForge wrote. Not all of them are
#: composites — `write-plainly` is derived from what the scanners above enforce
#: and composes nothing — so this is the list a refresh has to be careful of,
#: and `COMPOSED` is not.
FIRST_PARTY: tuple[str, ...] = ("grill-with-docs", "write-plainly")


#: The composites, and the vendored skills each is built out of. A composite adds
#: the job the parts are doing together and restates neither: a Fragment is the
#: degraded delivery of a skill and never a second copy of one, so the method
#: stays in exactly one file.
COMPOSED: dict[str, tuple[str, ...]] = {
    "grill-with-docs": ("grilling", "domain-modeling"),
}


def expand(names: Sequence[str]) -> tuple[str, ...]:
    """Declared skills, with each composite followed by what it is made of.

    Order matters and duplicates do not survive it: the composite states the job
    before the methods it draws on, and a Role that declared a part directly as
    well gets it once.
    """
    ordered: list[str] = []
    for name in names:
        for part in (name, *COMPOSED.get(name, ())):
            if part not in ordered:
                ordered.append(part)
    return tuple(ordered)


#: The frontmatter key by which a skill's author says a model may not invoke it
#: autonomously — only a human typing the command may.
MODEL_INVOCATION_KEY = "disable-model-invocation"


def _frontmatter(text: str) -> dict:
    """The YAML block a SKILL.md opens with, or nothing if it has none."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    data = yaml.safe_load(parts[1])
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def fragment_only() -> frozenset[str]:
    """Skills the bundle forbids a model to invoke, read from the bundle itself.

    Hand-listing these would have exactly the problem vendoring exists to
    prevent: a refresh that marks a third skill would be picked up by nobody,
    and the failure arrives as an escalation from a planning pass rather than as
    anything a reader would connect to a bundle bump.

    Frontmatter that will not parse counts as marked. Fragment delivery costs
    tokens; getting this wrong the other way costs the Run.
    """
    marked = set()
    for path in sorted(SKILLS_ROOT.iterdir()):
        source = path / "SKILL.md"
        if not source.is_file():
            continue
        try:
            data = _frontmatter(source.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            marked.add(path.name)
            continue
        if data.get(MODEL_INVOCATION_KEY):
            marked.add(path.name)
    return frozenset(marked)


def forbids_model_invocation(name: str) -> bool:
    """Whether a skill, or anything it composes, may not be model-invoked.

    A composite is checked through its parts as well: natively it fans out
    through the Skill tool, so a composite naming a marked skill would reach the
    same refusal one indirection later.
    """
    marked = fragment_only()
    return any(part in marked for part in expand((name,)))


def split_delivery(names: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Partition declared skills into the natively deliverable and the rest.

    This is consulted before the Capability Tier, not after it. A marked skill
    has no native delivery at any tier, so the Fragment is not the degraded form
    here — it is the only one.
    """
    native: list[str] = []
    fragment: list[str] = []
    for name in names:
        (fragment if forbids_model_invocation(name) else native).append(name)
    return tuple(native), tuple(fragment)


class SkillNotFound(LookupError):
    """A skill was requested that is not in the vendored bundle."""


def skill_path(name: str) -> Path:
    """Return the directory of a vendored skill."""
    path = SKILLS_ROOT / name
    if not path.is_dir():
        available = ", ".join(sorted(p.name for p in SKILLS_ROOT.iterdir() if p.is_dir()))
        raise SkillNotFound(f"no vendored skill named {name!r}; available: {available}")
    return path


def read_skill(name: str) -> str:
    """Return a skill's SKILL.md as text, for prompt-fragment delivery."""
    return (skill_path(name) / "SKILL.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class ScanResult:
    """One scanner's verdict on one file."""

    scanner: str
    violations: int
    clean: bool
    report: dict | None = None
    error: str | None = None


@dataclass(frozen=True)
class UnslopReport:
    """The aggregate verdict across every scanner."""

    path: Path
    results: list[ScanResult] = field(default_factory=list)

    @property
    def violations(self) -> int:
        return sum(r.violations for r in self.results)

    @property
    def clean(self) -> bool:
        return all(r.clean for r in self.results)

    @property
    def failed(self) -> list[ScanResult]:
        """Scanners that could not run at all, as distinct from ones that found faults."""
        return [r for r in self.results if r.error is not None]

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "clean": self.clean,
            "violations": self.violations,
            "scanners": {
                r.scanner: (
                    {"error": r.error}
                    if r.error
                    else {"violations": r.violations, "clean": r.clean, "report": r.report}
                )
                for r in self.results
            },
        }


def _count_violations(report: dict) -> int:
    """Pull a violation count out of a scanner report.

    The scanners agree on exit codes but not on payload shape:

    - ``banned_phrase_scan`` reports ``total_violations`` plus a ``violations`` list.
    - ``structure_scan`` and ``silhouette_scan`` report a ``flags`` list and a
      ``flagged`` dict keyed by metric name.

    Exit code remains the authority on pass or fail; this is only for reporting
    how much was found.
    """
    total = report.get("total_violations")
    if isinstance(total, int):
        return total

    for key in ("violations", "flags"):
        value = report.get(key)
        if isinstance(value, list):
            return len(value)

    flagged = report.get("flagged")
    if isinstance(flagged, dict):
        return sum(1 for hit in flagged.values() if hit)

    return 0


def _describe(result: ScanResult) -> list[str]:
    """One line per finding, using whichever detail keys the scanner provides."""
    if not result.report:
        return []

    # `or ""` rather than a `get` default throughout: the scanners write an
    # explicit null where they have no suggestion, and a default only fires on a
    # missing key. The line read "'in today's' - None" until this was found by
    # running the real scanners over real prose.
    lines: list[str] = []
    for violation in result.report.get("violations", []):
        where = violation.get("line_number") or "?"
        phrase = violation.get("phrase") or "?"
        suggestion = violation.get("suggestion") or ""
        lines.append(f"line {where}: {phrase!r} - {suggestion}".rstrip(" -"))
    for flag in result.report.get("flags", []):
        metric = flag.get("metric") or "?"
        detail = flag.get("detail") or ""
        suggestion = flag.get("suggestion") or ""
        lines.append(f"{metric}: {detail} {suggestion}".strip())
    return lines


def render_report(report: UnslopReport) -> list[str]:
    """One line per scanner, and one per finding beneath it.

    Shared by the Reviewer's Run Log entry and its own rewrite prompt, which is
    the point: what a human reads about the prose and what the Role is asked to
    act on are the same text, so neither can quietly say more than the other.
    """
    lines: list[str] = []
    for result in report.results:
        if result.error:
            lines.append(f"- {result.scanner}: could not run — {result.error}")
            continue
        verdict = "clean" if result.clean else f"{result.violations} finding(s)"
        lines.append(f"- {result.scanner}: {verdict}")
        lines += [f"    - {line}" for line in _describe(result)]
    return lines


def run_unslop(
    path: str | Path,
    scanners: tuple[str, ...] = UNSLOP_SCANNERS,
    runner: CommandRunner | None = None,
) -> UnslopReport:
    """Scan a file for machine-writing tells. Deterministic: no model involved."""
    target = Path(path).resolve()
    if not target.is_file():
        raise FileNotFoundError(target)

    runner = runner or SubprocessRunner()
    scripts = skill_path("unslop") / "scripts"
    results: list[ScanResult] = []

    for scanner in scanners:
        script = scripts / scanner
        if not script.is_file():
            results.append(
                ScanResult(scanner, violations=0, clean=True, error=f"missing script: {script}")
            )
            continue

        completed = runner.run([sys.executable, str(script), str(target)])

        try:
            report = json.loads(completed.stdout)
        except json.JSONDecodeError:
            detail = (completed.stderr or completed.stdout or "").strip()
            results.append(
                ScanResult(
                    scanner,
                    violations=0,
                    clean=True,
                    error=f"unparsable output (exit {completed.returncode}): {detail[:400]}",
                )
            )
            continue

        # The scanners exit 1 when they find something, so a non-zero exit is a
        # verdict rather than a crash. Trust the payload for the count.
        results.append(
            ScanResult(
                scanner,
                violations=_count_violations(report),
                clean=completed.returncode == 0,
                report=report,
            )
        )

    return UnslopReport(path=target, results=results)
