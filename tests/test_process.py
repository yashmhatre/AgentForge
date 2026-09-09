"""The Command Runner port, where an OS failure becomes something readable.

Everything external is a subprocess behind this one seam, so a failure it
misnames is a failure every caller misreports.
"""

from __future__ import annotations

import subprocess

import pytest

from agentforge_framework.core.process import (
    CommandLineTooLong,
    MissingBinary,
    SubprocessRunner,
)


def _raises(exc: Exception, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(subprocess, "run", boom)


def test_an_over_long_command_line_says_so_rather_than_blaming_the_binary(monkeypatch):
    """#128: Windows reports an over-long command line as `FileNotFoundError`
    with `winerror` 206, and reading that as a missing binary sent whoever hit
    it to check a PATH that `preflight` had already proved was fine."""
    too_long = FileNotFoundError(2, "The filename or extension is too long")
    too_long.winerror = 206
    _raises(too_long, monkeypatch)

    with pytest.raises(CommandLineTooLong) as caught:
        SubprocessRunner().run(("gh", "issue", "create", "--body", "x" * 40000))

    assert caught.value.binary == "gh"
    assert caught.value.length > 32767
    assert "not a missing binary" in str(caught.value)


def test_a_genuinely_absent_binary_still_reports_as_one(monkeypatch):
    _raises(FileNotFoundError(2, "No such file or directory"), monkeypatch)

    with pytest.raises(MissingBinary) as caught:
        SubprocessRunner().run(("gh", "issue", "list"))

    assert caught.value.binary == "gh"
    assert "not installed or not on PATH" in str(caught.value)


def test_the_two_are_not_the_same_exception():
    """A caller that means to catch one must not silently catch the other."""
    assert not issubclass(CommandLineTooLong, MissingBinary)
    assert not issubclass(MissingBinary, CommandLineTooLong)
