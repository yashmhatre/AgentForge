"""The vendored bundle must stay intact and the scanners must stay callable."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentbastion.core import skills


def test_all_five_third_party_skills_are_vendored():
    for name in ("grilling", "domain-modeling", "to-spec", "to-tickets", "unslop"):
        assert skills.skill_path(name).is_dir()


def test_the_bundle_says_which_skills_are_ours():
    """A refresh re-copies upstream over this directory. Anything of ours in it
    has to be findable in the manifest, or it goes in the next refresh.

    Read from `FIRST_PARTY` rather than `COMPOSED`: not everything we wrote is a
    composite, and a refresh does not care which is which."""
    manifest = (skills.SKILLS_ROOT / "MANIFEST.yaml").read_text(encoding="utf-8")

    assert "first_party:" in manifest
    for name in skills.FIRST_PARTY:
        assert name in manifest, f"{name} is ours and the manifest does not say so"
        assert skills.skill_path(name).is_dir()


def test_every_composite_is_one_of_ours():
    """`COMPOSED` is a subset of `FIRST_PARTY`, never the other way around."""
    assert set(skills.COMPOSED) <= set(skills.FIRST_PARTY)


def test_a_composite_expands_into_the_skills_it_composes():
    assert skills.expand(("grill-with-docs",)) == (
        "grill-with-docs",
        "grilling",
        "domain-modeling",
    )


def test_expansion_keeps_a_skill_once_however_it_was_asked_for():
    """A Role that declared a part directly as well gets it once, in the order
    the composite put it in."""
    assert skills.expand(("grill-with-docs", "grilling")) == (
        "grill-with-docs",
        "grilling",
        "domain-modeling",
    )


def test_a_plain_skill_expands_to_itself():
    assert skills.expand(("unslop",)) == ("unslop",)


def test_our_prose_skill_composes_nothing_and_travels_alone():
    """`write-plainly` restates no vendored text, so there is nothing to fan out
    to. A Fragment Provider gets the whole of it in one body."""
    assert skills.expand(("write-plainly",)) == ("write-plainly",)
    assert "write-plainly" not in skills.COMPOSED


def test_every_composite_names_skills_that_exist():
    """A composite pointing at a skill nobody ships would fail at delivery, on a
    Fragment Provider only, halfway through a Run."""
    for name, parts in skills.COMPOSED.items():
        assert skills.read_skill(name)
        for part in parts:
            assert skills.read_skill(part)


def test_unknown_skill_names_what_is_available():
    with pytest.raises(skills.SkillNotFound, match="grilling"):
        skills.skill_path("no-such-skill")


def test_skill_markdown_is_readable_for_fragment_delivery():
    assert "interview" in skills.read_skill("grilling").lower()


def test_unslop_scripts_travel_as_an_intact_unit():
    """The scanners import a sibling `_lang`, so a flattened copy breaks them."""
    scripts = skills.skill_path("unslop") / "scripts"
    assert (scripts / "_lang.py").is_file()
    for scanner in skills.UNSLOP_SCANNERS:
        assert (scripts / scanner).is_file()


def test_silhouette_fixture_stays_a_sibling_of_scripts():
    """silhouette_scan resolves ../evals/fixtures/... relative to scripts/."""
    unslop = skills.skill_path("unslop")
    assert (unslop / "evals" / "fixtures" / "silhouette" / "human_reference.json").is_file()


def test_clean_prose_passes_every_scanner(tmp_path):
    target = tmp_path / "clean.md"
    target.write_text(
        "The loader retries three times before giving up. Each retry waits twice as\n"
        "long as the last.\n\nTimeouts live in config.yaml. The default is thirty seconds.\n",
        encoding="utf-8",
    )

    report = skills.run_unslop(target)

    assert report.failed == []
    assert report.clean
    assert report.violations == 0


def test_the_prose_skill_survives_the_scanners_it_describes():
    """The skill tells the Reviewer what these three scripts count. If its own
    text cannot clear them, it is describing something else."""
    report = skills.run_unslop(skills.skill_path("write-plainly") / "SKILL.md")

    assert report.failed == []
    assert report.clean, "; ".join(skills.render_report(report))


def test_banned_phrases_are_caught_and_counted(tmp_path):
    target = tmp_path / "slop.md"
    target.write_text(
        "Here's the thing. It turns out this is not just a change, it's a shift.\n"
        "Let me be clear: we must leverage synergies. Full stop.\n",
        encoding="utf-8",
    )

    report = skills.run_unslop(target)

    assert report.failed == []
    assert not report.clean
    assert report.violations > 0

    banned = next(r for r in report.results if r.scanner == "banned_phrase_scan.py")
    assert banned.violations == len(banned.report["violations"])
    assert skills._describe(banned), "findings must carry detail for the reviewer retry loop"


def test_flag_style_reports_are_counted_not_silently_zeroed():
    """structure_scan and silhouette_scan use `flags`, not `total_violations`."""
    assert skills._count_violations({"total_violations": 3, "violations": []}) == 3
    assert skills._count_violations({"flags": [{"metric": "a"}, {"metric": "b"}]}) == 2
    assert skills._count_violations({"flagged": {"a": True, "b": False}}) == 1
    assert skills._count_violations({}) == 0


def test_missing_file_is_an_error_not_a_clean_pass(tmp_path):
    with pytest.raises(FileNotFoundError):
        skills.run_unslop(tmp_path / "nope.md")


def test_a_finding_with_no_suggestion_says_nothing_rather_than_None():
    """The scanners write an explicit null where they have no suggestion, so a
    `get` default never fires. The Reviewer hands these lines to a model as the
    thing to act on, and "- None" is worse than silence."""
    result = skills.ScanResult(
        scanner="banned_phrase_scan.py",
        violations=1,
        clean=False,
        report={"violations": [{"line_number": 5, "phrase": "in today's", "suggestion": None}]},
    )

    assert skills._describe(result) == ["line 5: \"in today's\""]


def test_a_report_renders_one_line_per_scanner_and_one_per_finding():
    report = skills.UnslopReport(
        path=Path("review.md"),
        results=[
            skills.ScanResult(
                scanner="banned_phrase_scan.py",
                violations=1,
                clean=False,
                report={
                    "violations": [
                        {"line_number": 4, "phrase": "delve into", "suggestion": "say what"}
                    ]
                },
            ),
            skills.ScanResult(scanner="structure_scan.py", violations=0, clean=True, report={}),
            skills.ScanResult(
                scanner="silhouette_scan.py", violations=0, clean=True, error="missing script"
            ),
        ],
    )

    assert skills.render_report(report) == [
        "- banned_phrase_scan.py: 1 finding(s)",
        "    - line 4: 'delve into' - say what",
        "- structure_scan.py: clean",
        "- silhouette_scan.py: could not run — missing script",
    ]
