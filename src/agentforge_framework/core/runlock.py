"""One Run at a time, per checkout.

ADR-0026 decides that AgentForge does not prevent a *foreign* agent from writing
into a Run's checkout — the worktree that would have prevented it breaks the
Tester in a way that is worse than the hazard it fixes. What it does prevent is
the case where both writers are AgentForge: two Runs in one working tree, where
the second `git checkout -b` moves the branch out from under the first and every
file either of them wrote lands somewhere nobody chose.

The mechanism is an OS file lock rather than a pid recorded in a file. The
difference matters on exactly one path, which is the path this has to survive:
a Run killed hard — Ctrl+C twice, a closed terminal, a machine that lost power —
runs no cleanup, and a lock file holding a pid would still be sitting there
saying a Run is in progress. The kernel releases a file lock when the process
holding it dies, whatever killed it, so there is no staleness rule to guess at
and no pid to probe. Verified by invocation on Windows: a holder that exits via
`os._exit` leaves the lock free for the next acquirer.

`os.kill(pid, 0)` is the liveness check this would otherwise have used, and on
Windows it does not ask whether a process is alive — it terminates it. That is
the second reason the pid is metadata here and never a decision.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

# Windows locks a byte range from the file's current position; POSIX `flock`
# locks the file as a whole. Locking a single byte far past any content keeps
# the two behaviours compatible in the way that matters: the JSON at offset 0
# stays readable to the process being refused, which is what turns "someone else
# holds this" into a message naming who.
_LOCK_OFFSET = 1 << 30

if os.name == "nt":  # pragma: no cover - exercised on whichever OS runs the suite
    import msvcrt

    def _take(handle) -> None:
        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _drop(handle) -> None:
        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - exercised on whichever OS runs the suite
    import fcntl

    def _take(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _drop(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LockError(RuntimeError):
    """The checkout could not be held, and nothing has been spent."""


class RunInProgress(LockError):
    """Another Run holds this checkout."""


class LockUnavailable(LockError):
    """The lock could not be opened at all, which means the checkout is not one."""


def lock_path(git_dir: Path) -> Path:
    """Where the lock lives: inside `.git`, never in the working tree.

    A file in the working tree would be an untracked file, and ADR-0015 makes
    untracked files a decision the commit step has to take a position on. This
    one is not the Run's work, it is not the suite's leavings, and it should
    appear in nobody's `git status` or pull request. Inside `.git` it is scoped
    to the clone that is actually being written to, which is the thing being
    guarded, and it is disposed of by the same `rm -rf` that disposes of the
    clone.
    """
    return git_dir / "agentforge-run.lock"


@dataclass(frozen=True)
class Holder:
    """Who holds the lock, as recorded by the Run that took it."""

    issue: int | None
    pid: int
    started: float
    command: str

    def describe(self) -> str:
        elapsed = max(0, int(time.time() - self.started))
        minutes, seconds = divmod(elapsed, 60)
        ago = f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"
        what = f"`agentforge {self.command}`"
        if self.issue is not None:
            what += f" on issue #{self.issue}"
        return f"{what}, pid {self.pid}, started {ago} ago"


def _read_holder(path: Path) -> Holder | None:
    """The metadata the holder wrote, or None if it cannot be read.

    Unreadable is not an error worth raising. The lock has already answered the
    question that matters — somebody holds it — and this only decorates the
    refusal. A holder that was killed between creating the file and writing to
    it is the ordinary case for reading nothing.
    """
    try:
        recorded = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    try:
        return Holder(
            issue=recorded.get("issue"),
            pid=int(recorded["pid"]),
            started=float(recorded["started"]),
            command=str(recorded.get("command", "implement")),
        )
    except (KeyError, TypeError, ValueError):
        return None


class RunLock:
    """Held for the duration of a Run, released however the Run ends.

    Used as a context manager, so a Halt, a Suspend, a `RunFailed` and an
    unhandled exception all release it by the same path. A process that dies
    without unwinding releases it too, by the kernel rather than by this class.
    """

    def __init__(
        self,
        git_dir: Path,
        *,
        issue: int | None,
        command: str,
        tree: Path | None = None,
    ) -> None:
        self.path = lock_path(git_dir)
        # Named rather than derived: a linked worktree's git directory is
        # `<main>/.git/worktrees/<name>`, so walking up from it lands nowhere a
        # human would recognise.
        self.tree = tree if tree is not None else git_dir.parent
        self.issue = issue
        self.command = command
        self._handle = None

    def __enter__(self) -> Self:
        # The git directory is not created if it is missing. `open_repository`
        # has already established that this is a repository, so an absent one
        # means git answered with a path that is not there — and inventing a
        # `.git` to hold a lock would turn a broken checkout into a Run.
        try:
            # `os.open` rather than mode "a+b": append mode sets O_APPEND on
            # POSIX, where a write goes to the end of the file whatever the
            # seek said, and this file is rewritten from offset 0.
            handle = os.fdopen(os.open(self.path, os.O_RDWR | os.O_CREAT), "r+b")
        except OSError as exc:
            raise LockUnavailable(
                f"{self.path.parent} is not there, so the Run has nowhere to record that "
                "it holds this checkout. That directory is where git keeps this "
                "checkout's state; a repository missing it cannot be run against."
            ) from exc
        try:
            _take(handle)
        except OSError as exc:
            held = _read_holder(self.path)
            handle.close()
            whose = f" {held.describe()}" if held else ""
            raise RunInProgress(
                f"another AgentForge Run holds {self.tree}:{whose}. "
                "Two Runs in one working tree would each branch and commit over "
                "the other, so this one has not started and has spent nothing. "
                "Wait for it to finish, or run this Issue from a second clone."
            ) from exc

        # Written after the lock is taken, so what a refused Run reads is always
        # a holder that actually holds it.
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "issue": self.issue,
                    "pid": os.getpid(),
                    "started": time.time(),
                    "command": self.command,
                }
            ).encode("utf-8")
        )
        handle.flush()
        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            handle.truncate()
            handle.flush()
            _drop(handle)
        except OSError:
            # Closing the handle releases the lock regardless, which is the only
            # part that has to happen. A failure to blank the metadata leaves a
            # readable file describing a Run that has ended, and the next
            # acquirer overwrites it.
            pass
        finally:
            handle.close()
