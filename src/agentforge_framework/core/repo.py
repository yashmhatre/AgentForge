"""Git, through the Command Runner.

ADR-0002 makes a git repository with a GitHub remote a hard precondition, and
user story 11 asks that a repository missing either be refused clearly and
immediately rather than halfway through a paid Run. Both checks live here, and
so does the branch-and-commit work that turns an Agent's edits into something
`gh pr create` can point at.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .process import CommandRunner, MissingBinary, require


class PreconditionFailed(RuntimeError):
    """The working directory cannot host a Run, and nothing has been spent."""


def branch_for_issue(issue: int) -> str:
    """Branch names derive from the Issue number.

    The link between a Run, its Issue, and its pull request is then recoverable
    from any one of the three.
    """
    return f"agentforge/issue-{issue}"


@dataclass(frozen=True)
class Repository:
    """A git working tree AgentForge is allowed to act on."""

    runner: CommandRunner
    root: Path

    def _git(self, *args: str, check: bool = True):
        result = self.runner.run(("git", *args), cwd=self.root)
        if check:
            result.check()
        return result

    @property
    def current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    @property
    def remote_url(self) -> str:
        return self._git("remote", "get-url", "origin", check=False).stdout.strip()

    def is_dirty(self) -> bool:
        return bool(self._git("status", "--porcelain").stdout.strip())

    def working_tree(self) -> tuple[tuple[str, str], ...]:
        """Every entry `git status --porcelain` reports, as (status code, path).

        The code is kept rather than discarded because `??` is the one
        distinction `commit_declared` turns on: git already knows about
        everything else, and a file git has never seen is the only kind a Role's
        commands can invent.

        `--untracked-files=all` because the default collapses a wholly new
        directory to `src/newpkg/` and names none of its files. `commit_declared`
        matches paths exactly, so a collapsed entry would drop a new package the
        Plan asked for along with the `__pycache__` beside it.
        """
        lines = self._git("status", "--porcelain", "--untracked-files=all").stdout.splitlines()
        return tuple(
            (line[:2], line[3:].strip().strip('"')) for line in lines if line.strip()
        )

    def changed_files(self) -> tuple[str, ...]:
        """Paths touched in the working tree, staged or not."""
        return tuple(path for _, path in self.working_tree())

    def create_branch(self, name: str) -> None:
        """Switch to `name`, creating it. An Agent never edits on the base branch."""
        existing = self._git("rev-parse", "--verify", "--quiet", name, check=False)
        if existing.ok:
            self._git("checkout", name)
        else:
            self._git("checkout", "-b", name)

    def commit_declared(self, message: str, declared: Iterable[str]) -> tuple[str, ...]:
        """Commit the Run's work and nothing its commands left lying around.

        Returns the paths committed, empty when there was nothing to commit.

        `declared` is every path the Run said it would touch — the frozen Plan's
        files, and what each Agent reported changing. It gates untracked files
        only. See ADR-0015: a file git already tracks is committed however it
        changed, because refusing an edit to a file the Plan forgot to name
        would drop an Agent's work silently; a file git has never seen is
        committed only when the Run named it, because `--allow-commands` means
        a suite can invent one and `__pycache__` is not the Run's work.

        Paths match exactly. A declared directory does not admit what is under
        it, which is the whole point: a Plan naming `src/` would otherwise
        re-admit `src/__pycache__/loader.pyc`.
        """
        allowed = {_normalize(path) for path in declared}
        staging = [
            path for code, path in self.working_tree() if code != "??" or path in allowed
        ]
        if not staging:
            return ()
        self._git("add", "--", *staging)
        self._git("commit", "-m", message)
        return tuple(staging)

    def carries_work_against(self, base: str) -> bool:
        """Whether this branch holds commits `base` does not.

        Which is what "something to open a pull request for" means. Asked of git
        rather than of a Role's account of what it changed, because that account
        is exactly what a Run cannot take on trust — and because the work may
        have been committed by an earlier invocation of this Run, or by the human
        whose diff a `review` Workflow was pointed at.

        A git that cannot answer — an unfetched base, a shallow clone — answers
        yes. Refusing to open a pull request because a ref was missing is the
        worse of the two mistakes.
        """
        counted = self._git("rev-list", "--count", f"{base}..HEAD", check=False)
        if not counted.ok:
            return True
        return counted.stdout.strip() not in ("", "0")

    def push(self, branch: str) -> None:
        self._git("push", "--set-upstream", "origin", branch)


def _normalize(path: str) -> str:
    r"""A declared path in the spelling `git status` uses.

    A Plan is written by a model and an Agent Result by another one, so the same
    file arrives as `src/loader.py`, `./src/loader.py`, or — on Windows —
    `src\loader.py`. Git answers in one of those three and a comparison that
    took the other two literally would quietly commit nothing.
    """
    return path.strip().replace("\\", "/").removeprefix("./").strip("/")


def open_repository(runner: CommandRunner, cwd: Path | str) -> Repository:
    """Resolve the working directory to a repository, or refuse with a reason."""
    try:
        require(runner, "git", "AgentForge drives git rather than reimplementing it.")
    except MissingBinary as exc:
        raise PreconditionFailed(str(exc)) from exc

    top = runner.run(("git", "rev-parse", "--show-toplevel"), cwd=cwd)
    if not top.ok:
        raise PreconditionFailed(
            f"{Path(cwd).resolve()} is not inside a git repository. "
            "ADR-0002 makes a repository with a GitHub remote a precondition for every Run."
        )

    repo = Repository(runner=runner, root=Path(top.stdout.strip() or str(cwd)))
    remote = repo.remote_url
    if not remote:
        raise PreconditionFailed(
            f"{repo.root} has no `origin` remote. AgentForge hands off through GitHub "
            "issues (ADR-0002), so a remote is required before a Run can start."
        )
    if "github" not in remote.lower():
        raise PreconditionFailed(
            f"`origin` points at {remote}, which is not GitHub. ADR-0002 supports GitHub only; "
            "no other tracker is implemented."
        )
    return repo
