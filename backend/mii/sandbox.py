"""Safety: agents only work inside git worktrees of SANDBOX_REPO_PATH.

Each task gets one worktree (and branch `mii/<task_id>`) under WORKTREE_ROOT.
The package carries that branch from resident to resident, so the reviewer
sees exactly what the coder wrote.

The ACP runner also uses is_inside() to check every file an agent asks us to
read or write (see agents/acp.py).
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
    def __init__(self, repo_path: Path | None, worktree_root: Path):
        if repo_path is None:
            raise SandboxError("SANDBOX_REPO_PATH is not set (needed when MOCK_AGENTS=0)")
        if not (repo_path / ".git").exists():
            raise SandboxError(f"SANDBOX_REPO_PATH is not a git repo: {repo_path}")
        self.repo_path = repo_path.resolve()
        self.worktree_root = worktree_root.resolve()

    def worktree_path(self, task_id: str) -> Path:
        if not TASK_ID_PATTERN.match(task_id):
            raise SandboxError(f"unsafe task id: {task_id!r}")
        return self.worktree_root / f"{self.repo_path.name}-{task_id}"

    def create_worktree(self, task_id: str) -> Path:
        """Create (or reuse) the worktree for a task and return its path."""
        path = self.worktree_path(task_id)
        if not path.exists():
            self.worktree_root.mkdir(parents=True, exist_ok=True)
            self._git("worktree", "add", "-b", f"mii/{task_id}", str(path), "HEAD")
        return path

    def remove_worktree(self, task_id: str) -> None:
        # TODO(mvp): call this once the PR is opened (or the task fails).
        self._git("worktree", "remove", "--force", str(self.worktree_path(task_id)))

    def check_workdir(self, workdir: Path) -> None:
        """Refuse to start an agent anywhere except inside a worktree we made."""
        if not is_inside(workdir, self.worktree_root) or workdir.resolve() == self.worktree_root:
            raise SandboxError(f"refusing to run an agent outside {self.worktree_root}: {workdir}")

    def _git(self, *args: str) -> None:
        result = subprocess.run(["git", "-C", str(self.repo_path), *args], capture_output=True, text=True)
        if result.returncode != 0:
            raise SandboxError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
