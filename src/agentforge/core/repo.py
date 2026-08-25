"""Git, through the Command Runner.

ADR-0002 makes a git repository with a GitHub remote a hard precondition, and
user story 11 asks that a repository missing either be refused clearly and
immediately rather than halfway through a paid Run. Both checks live here, and
so does the branch-and-commit work that turns an Agent's edits into something
`gh pr create` can point at.
"""

from __future__ import annotations

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

    def changed_files(self) -> tuple[str, ...]:
        """Paths touched in the working tree, staged or not."""
        lines = self._git("status", "--porcelain").stdout.splitlines()
        return tuple(line[3:].strip().strip('"') for line in lines if line.strip())

    def create_branch(self, name: str) -> None:
        """Switch to `name`, creating it. An Agent never edits on the base branch."""
        existing = self._git("rev-parse", "--verify", "--quiet", name, check=False)
        if existing.ok:
            self._git("checkout", name)
        else:
            self._git("checkout", "-b", name)

    def commit_all(self, message: str) -> bool:
        """Stage and commit everything. False when there was nothing to commit."""
        if not self.is_dirty():
            return False
        self._git("add", "-A")
        self._git("commit", "-m", message)
        return True

    def push(self, branch: str) -> None:
        self._git("push", "--set-upstream", "origin", branch)


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
