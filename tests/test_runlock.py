"""One Run at a time, per checkout (ADR-0026).

These tests take real locks on real files, because the property being claimed is
an operating system's and not a data structure's. The one that matters most
spawns a process and kills it without letting it clean up: that is the path a
lock file holding a pid cannot survive, and it is the reason this is a file lock
instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from agentforge_framework.core.runlock import (
    LockUnavailable,
    RunInProgress,
    RunLock,
    lock_path,
)


def a_checkout(tmp_path: Path, name: str = "pipelines") -> Path:
    """A git directory, which is all the lock needs to exist."""
    git_dir = tmp_path / name / ".git"
    git_dir.mkdir(parents=True)
    return git_dir


def test_a_second_run_on_one_checkout_is_refused_before_it_spends_anything(tmp_path):
    git_dir = a_checkout(tmp_path)

    with (
        RunLock(git_dir, issue=12, command="implement 12"),
        pytest.raises(RunInProgress) as refused,
        RunLock(git_dir, issue=13, command="implement 13"),
    ):
        pytest.fail("the second Run took a lock the first one holds")

    assert "another AgentForge Run holds" in str(refused.value)


def test_the_refusal_names_the_run_that_is_already_going(tmp_path):
    """A message that says only "locked" sends the human looking for a file.

    Naming the Issue and the pid tells them whether the other Run is one they
    started and forgot, or a scheduled one they should wait for.
    """
    git_dir = a_checkout(tmp_path)

    with (
        RunLock(git_dir, issue=12, command="implement 12"),
        pytest.raises(RunInProgress) as refused,
        RunLock(git_dir, issue=13, command="implement 13"),
    ):
        pass

    message = str(refused.value)
    assert "issue #12" in message
    assert f"pid {os.getpid()}" in message


def test_the_lock_is_released_when_the_run_ends(tmp_path):
    git_dir = a_checkout(tmp_path)

    with RunLock(git_dir, issue=12, command="implement 12"):
        pass

    with RunLock(git_dir, issue=13, command="implement 13"):
        pass


def test_a_run_that_raises_still_releases_the_checkout(tmp_path):
    """Halt, Suspend, `RunFailed`, and an exception nobody expected all leave
    the `with` block, which is why the lock is one rather than a `finally`."""
    git_dir = a_checkout(tmp_path)

    with (
        pytest.raises(ZeroDivisionError),
        RunLock(git_dir, issue=12, command="implement 12"),
    ):
        raise ZeroDivisionError("a Role did something regrettable")

    with RunLock(git_dir, issue=13, command="implement 13"):
        pass


def test_a_run_killed_without_cleanup_does_not_leave_the_checkout_held(tmp_path):
    """The property the whole design rests on, proved by killing a process.

    A pid written into a file survives the process that wrote it, so a Run
    killed with the terminal window would lock the checkout until a human
    deleted the file and guessed whether that was safe. An OS file lock is
    released by the kernel when the holder dies, whatever killed it. `os._exit`
    here stands for Ctrl+C twice, a closed terminal, and a lost machine.
    """
    git_dir = a_checkout(tmp_path)
    script = textwrap.dedent(
        f"""
        import os
        from pathlib import Path
        from agentforge_framework.core.runlock import RunLock
        held = RunLock(Path({str(git_dir)!r}), issue=12, command="implement 12").__enter__()
        print("held", flush=True)
        os._exit(1)
        """
    )
    environment = dict(os.environ)
    package_parent = Path(__file__).resolve().parents[1] / "src"
    if package_parent.is_dir():
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            f"{package_parent}{os.pathsep}{existing}" if existing else str(package_parent)
        )

    killed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=environment,
        check=False,  # it exits 1 on purpose; that is the point of the test
    )

    assert "held" in killed.stdout, killed.stderr
    assert killed.returncode == 1
    with RunLock(git_dir, issue=13, command="implement 13"):
        pass


def test_the_lock_lives_in_the_git_directory_and_never_in_the_working_tree(tmp_path):
    """An untracked file in the tree is a decision ADR-0015 has to take a
    position on. This one is not the Run's work, not the suite's leavings, and
    belongs in nobody's `git status` or pull request."""
    git_dir = a_checkout(tmp_path)

    with RunLock(git_dir, issue=12, command="implement 12"):
        assert lock_path(git_dir).exists()

    tree = git_dir.parent
    assert [path.name for path in tree.iterdir()] == [".git"]


def test_two_checkouts_of_one_repository_do_not_queue_behind_each_other(tmp_path):
    """Two clones, or a repository and a linked worktree, are two working trees.
    Neither can branch over the other, so neither has anything to wait for."""
    one = a_checkout(tmp_path, "pipelines")
    another = a_checkout(tmp_path, "pipelines-second-clone")

    with (
        RunLock(one, issue=12, command="implement 12"),
        RunLock(another, issue=13, command="implement 13"),
    ):
        pass


def test_a_checkout_with_no_git_directory_is_refused_rather_than_given_one(tmp_path):
    """Creating the missing directory would turn a broken checkout into a Run."""
    absent = tmp_path / "not-a-repository" / ".git"

    with (
        pytest.raises(LockUnavailable, match="is not there"),
        RunLock(absent, issue=12, command="implement 12"),
    ):
        pass

    assert not absent.exists()


def test_a_lock_whose_metadata_cannot_be_read_still_refuses(tmp_path):
    """The lock has already answered the question that matters. Metadata only
    decorates the refusal, so garbage in the file costs a clause, not the
    guarantee."""
    git_dir = a_checkout(tmp_path)

    with RunLock(git_dir, issue=12, command="implement 12"):
        lock_path(git_dir).write_bytes(b"\xff\xfe not json at all")
        with (
            pytest.raises(RunInProgress) as refused,
            RunLock(git_dir, issue=13, command="implement 13"),
        ):
            pass

    assert "another AgentForge Run holds" in str(refused.value)
    assert "pid" not in str(refused.value)
