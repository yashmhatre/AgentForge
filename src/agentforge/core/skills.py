"""Access to the vendored skill bundle.

Skills ship as package data, never as importable modules. Markdown is read as
text; Python is invoked as a subprocess. See docs/adr/0006.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"

#: Scanners the `unslop` Command runs, in report order. The remaining scripts in
#: the bundle serve upstream's voice and calibration features and are not wired
#: into AgentForge.
UNSLOP_SCANNERS = (
    "banned_phrase_scan.py",
    "structure_scan.py",
    "silhouette_scan.py",
)


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

    lines: list[str] = []
    for violation in result.report.get("violations", []):
        where = violation.get("line_number", "?")
        phrase = violation.get("phrase", "?")
        suggestion = violation.get("suggestion", "")
        lines.append(f"line {where}: {phrase!r} - {suggestion}".rstrip(" -"))
    for flag in result.report.get("flags", []):
        metric = flag.get("metric", "?")
        detail = flag.get("detail") or ""
        suggestion = flag.get("suggestion", "")
        lines.append(f"{metric}: {detail} {suggestion}".strip())
    return lines


def run_unslop(path: str | Path, scanners: tuple[str, ...] = UNSLOP_SCANNERS) -> UnslopReport:
    """Scan a file for machine-writing tells. Deterministic: no model involved."""
    target = Path(path).resolve()
    if not target.is_file():
        raise FileNotFoundError(target)

    scripts = skill_path("unslop") / "scripts"
    results: list[ScanResult] = []

    for scanner in scanners:
        script = scripts / scanner
        if not script.is_file():
            results.append(
                ScanResult(scanner, violations=0, clean=True, error=f"missing script: {script}")
            )
            continue

        completed = subprocess.run(
            [sys.executable, str(script), str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

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
