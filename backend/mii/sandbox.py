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

import asyncio
import logging
import os
import re
import signal
import subprocess
from contextlib import suppress
from pathlib import Path

from mii.config import PRIVATE_ENV
from mii.vocab import ATTACHMENTS_DIR, DEFAULT_GIT_AUTHOR

log = logging.getLogger(__name__)

OUTPUT_LIMIT = 4000  # characters of command output we keep


class SandboxError(Exception):
    """A path or worktree check failed, or git refused. The message is shown to the user."""


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
        `origin/<branch>` if it was fetched, else wherever HEAD is). A task resumed after a
        restart keeps its branch, and its work, even if the folder was removed.
        """
        path = self.worktree_path(task_id)
        base = self.resolve_branch(branch)
        task_branch = f"mii/{task_id}"
        resumed = self._ok(self.repo_path, "rev-parse", "--verify", "-q", task_branch)
        # A resumed branch started where it meets the base, not at the base's newest commit.
        start = self._git(self.repo_path, "merge-base", base, task_branch, check=False) if resumed else ""
        self.bases[task_id] = start.strip() or self._git(self.repo_path, "rev-parse", base).strip() or base
        if not path.exists():
            self.worktree_root.mkdir(parents=True, exist_ok=True)
            self._git(self.repo_path, "worktree", "prune")  # forget a folder deleted by hand
            elsewhere = self._checked_out_at(task_branch) if resumed else None
            if elsewhere is not None:
                # Made under another WORKTREE_ROOT: move it here, uncommitted work and all.
                # git won't check a branch out twice.
                self._git(self.repo_path, "worktree", "move", str(elsewhere), str(path))
            elif resumed:
                self._git(self.repo_path, "worktree", "add", str(path), task_branch)
            else:
                self._git(self.repo_path, "worktree", "add", "-b", task_branch, str(path), base)
        return path

    def _checked_out_at(self, branch: str) -> Path | None:
        """The worktree (not the island's own folder) that has `branch` checked out, or None."""
        folder = None
        for line in self._git(self.repo_path, "worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                folder = Path(line.removeprefix("worktree "))
            elif line == f"branch refs/heads/{branch}" and folder is not None and folder.resolve() != self.repo_path:
                return folder
        return None

    def resolve_branch(self, branch: str | None) -> str:
        """The ref to branch tasks from: `branch`, `origin/<branch>`, or HEAD when neither exists."""
        for candidate in [branch, f"origin/{branch}"] if branch else []:
            if self._ok(self.repo_path, "rev-parse", "--verify", "-q", candidate):
                return candidate
        return "HEAD"

    def remove_worktree(self, task_id: str, delete_branch: bool = False) -> None:
        """Delete the worktree folder. The branch stays (so a pushed PR is unaffected) unless
        `delete_branch`: a throwaway worktree, like a talk's (talk.py), leaves nothing behind."""
        path = self.worktree_path(task_id)
        if path.exists():
            self._git(self.repo_path, "worktree", "remove", "--force", str(path))
        if delete_branch:
            self._git(self.repo_path, "branch", "-D", f"mii/{task_id}", check=False)
            self.bases.pop(task_id, None)

    def add_attachments(self, task_id: str, files: list[tuple[str, bytes]]) -> None:
        """Write a task's attached files (plain names, see tasks.safe_file_name) into
        <worktree>/.mii-attachments/, refusing any path that would land outside it."""
        worktree = self.worktree_path(task_id)
        folder = worktree / ATTACHMENTS_DIR
        if folder.is_symlink() or not is_inside(folder, worktree):  # the repo could hold a symlink there
            raise SandboxError(f"{ATTACHMENTS_DIR} in the worktree is not a plain folder")
        folder.mkdir(exist_ok=True)
        (folder / ".gitignore").write_text("*\n")
        for name, data in files:
            target = inside(folder, name)
            if target.parent.resolve() != folder.resolve():
                raise SandboxError(f"attachment name {name!r} is not a plain file name")
            target.write_bytes(data)

    def check_workdir(self, workdir: Path) -> None:
        """Refuse to start an agent anywhere except inside a worktree we made."""
        if not is_inside(workdir, self.worktree_root) or workdir.resolve() == self.worktree_root:
            raise SandboxError(f"refusing to run an agent outside {self.worktree_root}: {workdir}")

    def commit_all(self, task_id: str, message: str, author: str = DEFAULT_GIT_AUTHOR) -> bool:
        """Commit everything in the task's worktree. Returns False when there was nothing to commit."""
        path = self.worktree_path(task_id)
        self._git(path, "add", "-A")
        if self._ok(path, "diff", "--cached", "--quiet"):
            return False  # nothing staged
        name, email = split_author(author)
        self._git(path, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", message)
        return True

    def merge_into_checkout(self, task_id: str, author: str = DEFAULT_GIT_AUTHOR) -> tuple[str, list[str]]:
        """Merge the task branch into whatever branch the island's own folder has checked out.

        Returns (that branch, the files that conflict). On a conflict the merge is aborted,
        so the folder is as it was. Raises SandboxError when the merge can't start (no branch
        checked out, uncommitted changes, a file in the way)."""
        branch = self._git(self.repo_path, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip()
        if not branch:
            raise SandboxError(f"{self.repo_path} has no branch checked out")
        if self._git(self.repo_path, "status", "--porcelain", "--untracked-files=no").strip():
            raise SandboxError(f"{self.repo_path} has uncommitted changes: commit or stash them first")
        return branch, self._merge(self.repo_path, f"mii/{task_id}", author)

    def prepare_conflict_branch(self, task_id: str, other_id: str, base: str, author: str = DEFAULT_GIT_AUTHOR) -> None:
        """Start `task_id`'s branch from `base` with `other_id`'s branch merged in, conflict markers
        and all, so the residents who pick it up only have to fix the marked files. (The engine
        later reuses the branch, see create_worktree.)"""
        path = self.create_worktree(task_id, base)
        try:
            if self._merge(path, f"mii/{other_id}", author, abort=False):
                self.commit_all(task_id, f"Merge mii/{other_id} (conflicts to fix)", author)
        finally:
            self.remove_worktree(task_id)

    def _merge(self, cwd: Path, branch: str, author: str, abort: bool = True) -> list[str]:
        """`git merge` `branch` in `cwd`: [] when it merged, else the conflicting files (the
        merge is aborted unless not `abort`)."""
        name, email = split_author(author)
        # Your own git name signs the merge commit; Mii-nagement's only if git has none.
        who = (
            []
            if self._git(cwd, "config", "user.email", check=False).strip()
            else ["-c", f"user.name={name}", "-c", f"user.email={email}"]
        )
        result = subprocess.run(
            ["git", "-C", str(cwd), *who, "merge", "--no-ff", "--no-edit", branch], capture_output=True, text=True
        )
        if result.returncode == 0:
            return []
        conflicts = self._git(cwd, "diff", "--name-only", "--diff-filter=U", check=False).split()
        if not conflicts:
            self._git(cwd, "merge", "--abort", check=False)
            raise SandboxError(f"git merge {branch} failed: {(result.stderr or result.stdout).strip()}")
        if abort:
            self._git(cwd, "merge", "--abort")
        return conflicts

    def diff_stat(self, task_id: str) -> str:
        """What the task branch changed compared to where it started (for the reviewer)."""
        path = self.worktree_path(task_id)
        uncommitted = self._git(path, "diff", "--stat", "HEAD~0", "--", ".", check=False)
        committed = self._git(path, "log", "--oneline", f"HEAD...{self._base(task_id)}", check=False)
        return (committed + uncommitted).strip() or "(no changes yet)"

    def _base(self, task_id: str) -> str:
        """The commit the task branch was created from (the main repo's HEAD if we don't know)."""
        if task_id in self.bases:
            return self.bases[task_id]
        return self._git(self.repo_path, "rev-parse", "HEAD", check=False).strip() or "HEAD"

    @staticmethod
    def _git(cwd: Path, *args: str, check: bool = True) -> str:
        """Run git in `cwd` and return its stdout. With `check`, a non-zero exit is a SandboxError."""
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
        if check and result.returncode != 0:
            raise SandboxError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    @staticmethod
    def _ok(cwd: Path, *args: str) -> bool:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True).returncode == 0


def split_author(author: str) -> tuple[str, str]:
    """'Name <email>' -> ('Name', 'email')."""
    if "<" in author and author.endswith(">"):
        name, email = author[:-1].split("<", 1)
        return name.strip() or "Mii-nagement", email.strip()
    return author.strip() or "Mii-nagement", "mii@localhost"


def require_worktree(job) -> str | None:
    """Why a real brain can't run this job (no worktree, or one we didn't make), or None when it can.

    `job` is an agents.AgentJob; every runner that starts a process calls this first.
    """
    if job.sandbox is None or job.workdir is None:
        return f"The {job.resident.brain} brain needs a worktree (an island repository)."
    try:
        job.sandbox.check_workdir(job.workdir)
    except SandboxError as e:
        return str(e)
    return None


def inside(root: Path, path: str) -> Path:
    """`path` (relative to `root`, or absolute) as a Path inside `root`. Raises SandboxError otherwise."""
    full = Path(path) if Path(path).is_absolute() else root / path
    if not is_inside(full, root):
        raise SandboxError(f"{path} is outside the worktree")
    return full


async def run_shell(command: str, cwd: Path, timeout: float, limit: int = OUTPUT_LIMIT) -> tuple[int, str]:
    """Run a shell command in `cwd`. Returns (exit code, combined output capped to `limit` chars).

    The command runs code the agents wrote, so it gets the user's environment without the
    backend's own keys (PRIVATE_ENV), in its own process group so a timeout ends everything
    it started (`npm test` -> node -> its workers), not just the shell.
    """
    env = {k: v for k, v in os.environ.items() if k not in PRIVATE_ENV}
    group = {} if os.name == "nt" else {"start_new_session": True}
    proc = await asyncio.create_subprocess_shell(
        command, cwd=str(cwd), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **group
    )
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        await _kill_group(proc)
        return 124, "(timed out)"
    except asyncio.CancelledError:  # the step timed out or the task was cancelled: no orphans
        await _kill_group(proc)
        raise
    output = raw.decode(errors="replace")
    if len(output) > limit:
        output = "…" + output[-limit:]
    return proc.returncode or 0, output


async def _kill_group(proc: asyncio.subprocess.Process) -> None:
    with suppress(ProcessLookupError, PermissionError):
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    with suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=5)


class Sandboxes:
    """Sandbox per island (keyed by repo path). The engine holds one of these in real mode."""

    def __init__(self, worktree_root: Path):
        self.worktree_root = worktree_root
        self._by_path: dict[str, Sandbox] = {}
        project = next((p for p in Path(worktree_root).resolve().parents if (p / ".git").exists()), None)
        if project is not None:  # agents would also read that project's CLAUDE.md / AGENTS.md (config.py)
            log.warning("WORKTREE_ROOT %s is inside the git repository %s: move it out", worktree_root, project)

    def for_island(self, island: object) -> Sandbox:
        """`island` is an Island row (or anything with `repo_path`)."""
        key = island.repo_path or ""
        if key not in self._by_path:
            self._by_path[key] = Sandbox(Path(key) if key else None, self.worktree_root)
        return self._by_path[key]
