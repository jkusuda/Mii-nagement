"""Safety: agents only work inside git worktrees of an island's repository.

Each task gets one worktree (and branch `mii/<task_id>`) under WORKTREE_ROOT.
`Sandboxes.for_island()` gives the engine the Sandbox for an island's repo.
The package carries that branch from resident to resident, so the reviewer
sees exactly what the coder wrote.

What is checked, and what isn't:
- Agents are only ever *started* inside a worktree we made (check_workdir).
- Every file an agent asks *us* to read or write is path-checked with
  is_inside() (the ACP fs methods and the OpenRouter brain's file tools).
- Shell commands an agent runs are **not** path-restricted: they're gated by a
  ✋ permission bubble instead, in both the ACP runner and the OpenRouter brain.
"""

import os
import re
import subprocess
from pathlib import Path


class SandboxError(Exception):
    pass


def is_inside(path: str | Path, root: str | Path) -> bool:
    """True if `path` is `root` or inside it (after resolving symlinks and '..')."""
    real_path, real_root = os.path.realpath(path), os.path.realpath(root)
    return os.path.commonpath([real_path, real_root]) == real_root


TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class Sandbox:
    """One island's repository, and the worktrees we cut from it."""

    def __init__(self, repo_path: Path | None, worktree_root: Path):
        if repo_path is None:
            raise SandboxError("this island has no repository yet")
        if not (repo_path / ".git").exists():
            raise SandboxError(f"the island's repository is not a git repo: {repo_path}")
        self.repo_path = repo_path.resolve()
        self.worktree_root = worktree_root.resolve()
        self.bases: dict[str, str] = {}  # task id -> the commit its branch started from

    def worktree_path(self, task_id: str) -> Path:
        if not TASK_ID_PATTERN.match(task_id):
            raise SandboxError(f"unsafe task id: {task_id!r}")
        return self.worktree_root / f"{self.repo_path.name}-{task_id}"  # one folder per island + task

    def create_worktree(self, task_id: str, branch: str | None = None) -> Path:
        """Create (or reuse) the worktree for a task and return its path.

        The task branch starts from the island's `branch` (a local branch, else
        `origin/<branch>` if it was fetched, else wherever HEAD is).
        """
        path = self.worktree_path(task_id)
        base = self.resolve_branch(branch)
        self.bases[task_id] = self._git(self.repo_path, "rev-parse", base).strip() or base
        if not path.exists():
            self.worktree_root.mkdir(parents=True, exist_ok=True)
            self._git(self.repo_path, "worktree", "add", "-b", f"mii/{task_id}", str(path), base)
        return path

    def resolve_branch(self, branch: str | None) -> str:
        """The ref to branch tasks from: `branch`, `origin/<branch>`, or HEAD when neither exists."""
        for candidate in ([branch, f"origin/{branch}"] if branch else []):
            ok = subprocess.run(["git", "-C", str(self.repo_path), "rev-parse", "--verify", "-q", candidate],
                                capture_output=True).returncode == 0
            if ok:
                return candidate
        return "HEAD"

    def remove_worktree(self, task_id: str) -> None:
        """Delete the worktree folder (the branch stays, so a pushed PR is unaffected)."""
        path = self.worktree_path(task_id)
        if path.exists():
            self._git(self.repo_path, "worktree", "remove", "--force", str(path))

    def check_workdir(self, workdir: Path) -> None:
        """Refuse to start an agent anywhere except inside a worktree we made."""
        if not is_inside(workdir, self.worktree_root) or workdir.resolve() == self.worktree_root:
            raise SandboxError(f"refusing to run an agent outside {self.worktree_root}: {workdir}")

    def commit_all(self, task_id: str, message: str, author: str = "Mii-nagement <mii@localhost>") -> bool:
        """Commit everything in the task's worktree. Returns False when there was nothing to commit."""
        path = self.worktree_path(task_id)
        self._git(path, "add", "-A")
        if subprocess.run(["git", "-C", str(path), "diff", "--cached", "--quiet"]).returncode == 0:
            return False  # nothing staged
        name, email = _split_author(author)
        self._git(path, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", message)
        return True

    def diff_stat(self, task_id: str) -> str:
        """What the task branch changed compared to where it started (for the reviewer)."""
        path = self.worktree_path(task_id)
        result = subprocess.run(
            ["git", "-C", str(path), "diff", "--stat", "HEAD~0", "--", "."], capture_output=True, text=True
        )
        committed = subprocess.run(
            ["git", "-C", str(path), "log", "--oneline", f"HEAD...{self._base(task_id)}"], capture_output=True, text=True
        )
        return (committed.stdout + result.stdout).strip() or "(no changes yet)"

    def _base(self, task_id: str) -> str:
        """The commit the task branch was created from (the main repo's HEAD if we don't know)."""
        if task_id in self.bases:
            return self.bases[task_id]
        result = subprocess.run(["git", "-C", str(self.repo_path), "rev-parse", "HEAD"], capture_output=True, text=True)
        return result.stdout.strip() or "HEAD"

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
        if result.returncode != 0:
            raise SandboxError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout


def _split_author(author: str) -> tuple[str, str]:
    """'Name <email>' -> ('Name', 'email')."""
    if "<" in author and author.endswith(">"):
        name, email = author[:-1].split("<", 1)
        return name.strip() or "Mii-nagement", email.strip()
    return author.strip() or "Mii-nagement", "mii@localhost"


class Sandboxes:
    """Sandbox per island (keyed by repo path). The engine holds one of these in real mode."""

    def __init__(self, worktree_root: Path):
        self.worktree_root = worktree_root
        self._by_path: dict[str, Sandbox] = {}

    def for_island(self, island) -> Sandbox:
        """`island` is an Island row (or anything with `repo_path`)."""
        key = island.repo_path or ""
        if key not in self._by_path:
            self._by_path[key] = Sandbox(Path(key) if key else None, self.worktree_root)
        return self._by_path[key]
