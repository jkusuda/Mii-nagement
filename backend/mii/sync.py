"""Keep an island's repository in step with GitHub: the game's Sync button.

    status   GET /islands/{id}/sync:  git fetch origin, then how far the island's branch is
             behind / ahead of origin/<branch>
    sync     POST /islands/{id}/sync: fetch, fast-forward the branch (pull), push local commits
             when it is only ahead, then look for open PRs (finished tasks with a `pr_url`)
             that no longer merge into the branch. Each one gets a fix package
             (Task.target_branch): it starts from the PR branch with the island's branch
             merged in, and the Post Office pushes the fix back onto that PR.

Nothing is ever forced: uncommitted changes on the branch or a diverged branch is a SyncError
(409) and nothing changes. Git and `gh` run in a thread (asyncio.to_thread).
"""

import asyncio
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from mii import github, repos
from mii.models import Island, Task

if TYPE_CHECKING:
    from mii.engine import WorkflowEngine

GIT_TIMEOUT = 120  # seconds; a fetch of a big repo over a slow line
MAX_PRS = 20  # open PRs checked for conflicts per sync, newest first


class SyncError(Exception):
    """The sync can't go ahead. `status` is the HTTP status a view should answer with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


async def status(island: Island) -> dict:
    """{branch, remote, behind, ahead} after a fetch. Raises SyncError."""
    path, branch = _repo(island)
    return await asyncio.to_thread(_status, path, branch)


async def sync(island: Island, workflow: "WorkflowEngine") -> dict:
    """Pull, push, and send a fix package for every open PR that now conflicts. Raises SyncError."""
    path, branch = _repo(island)
    pulled, pushed = await asyncio.to_thread(_pull, path, branch)
    fixes = await _fix_conflicts(island, path, branch, workflow)
    result = {**await asyncio.to_thread(_counts_dict, path, branch), "pulled": pulled, "pushed": pushed}
    return {**result, "conflicts": fixes, "message": _message(branch, pulled, pushed, fixes)}


def _repo(island: Island) -> tuple[Path, str]:
    if not island.repo_path or not (Path(island.repo_path) / ".git").exists():
        raise SyncError("this island has no git repository", 400)
    path = Path(island.repo_path)
    return path, island.branch or repos.current_branch(path) or "main"


def _status(path: Path, branch: str) -> dict:
    if not _has_remote(path):
        return {"branch": branch, "remote": False, "behind": 0, "ahead": 0}
    _out(path, "fetch", "--quiet", "origin")
    return _counts_dict(path, branch)


def _counts_dict(path: Path, branch: str) -> dict:
    if not _has_remote(path):
        return {"branch": branch, "remote": False, "behind": 0, "ahead": 0}
    ahead, behind = _counts(path, branch)
    return {"branch": branch, "remote": True, "behind": behind, "ahead": ahead}


def _counts(path: Path, branch: str) -> tuple[int, int]:
    """(ahead, behind) of the local branch against origin/<branch>, as of the last fetch."""
    local, remote = _has_ref(path, f"refs/heads/{branch}"), _has_ref(path, f"refs/remotes/origin/{branch}")
    if local and remote:
        ahead, behind = _out(path, "rev-list", "--left-right", "--count", f"{branch}...origin/{branch}").split()
        return int(ahead), int(behind)
    if local:  # never pushed: every commit is ahead
        return int(_out(path, "rev-list", "--count", branch)), 0
    return 0, 0  # only on GitHub: tasks already branch from origin/<branch>


def _pull(path: Path, branch: str) -> tuple[int, int]:
    """Fetch, fast-forward the branch, push it when it is only ahead. Returns (pulled, pushed)."""
    if not _has_remote(path):
        raise SyncError("this island's repository has no `origin` to sync with", 400)
    _out(path, "fetch", "--quiet", "origin")
    ahead, behind = _counts(path, branch)
    if ahead and behind:
        raise SyncError(
            f"{branch} has {ahead} local commit(s) and {behind} new one(s) on GitHub: merge them by hand first"
        )
    if behind:
        if repos.current_branch(path) == branch:
            if _out(path, "status", "--porcelain", "--untracked-files=no"):
                raise SyncError(f"{path} has uncommitted changes on {branch}: commit or stash them, then sync")
            _out(path, "merge", "--ff-only", "--quiet", f"origin/{branch}")
        else:  # not checked out: move the branch without touching any files
            _out(path, "fetch", "--quiet", ".", f"origin/{branch}:{branch}")
    if ahead:
        _out(path, "push", "--quiet", "origin", f"refs/heads/{branch}:refs/heads/{branch}")
    return behind, ahead


async def _fix_conflicts(island: Island, path: Path, branch: str, workflow: "WorkflowEngine") -> list[dict]:
    """A fix package for every open PR of this island that no longer merges into `branch`."""
    prs = (
        Task.objects.filter(island_id=island.id, status="done", merged_at__isnull=True, target_branch__isnull=True)
        .exclude(pr_url__isnull=True)
        .exclude(pr_url="")
        .exclude(branch__isnull=True)
        .order_by("-created_at")[:MAX_PRS]
    )
    fixes = []
    async for pr in prs:
        if await Task.objects.filter(target_branch=pr.branch, status__in=["queued", "in_progress"]).aexists():
            continue  # a fix is already on its way
        files = await asyncio.to_thread(_conflicts, path, branch, pr.branch)
        if not files:
            continue
        try:
            if await asyncio.to_thread(github.pr_state, pr.pr_url) != "OPEN":
                continue  # merged or closed on GitHub meanwhile
        except github.GitHubError:
            continue  # can't tell: better no package than one for a closed PR
        fix = await workflow.submit(
            island.id,
            f"Fix merge conflicts: {pr.title}"[:500],
            _fix_description(pr, branch, files),
            target_branch=pr.branch,
        )
        fixes.append({"task": pr.id, "branch": pr.branch, "pr_url": pr.pr_url, "files": files, "fix_task": fix.id})
    return fixes


def _conflicts(path: Path, base: str, branch: str) -> list[str]:
    """Files that conflict when `base` is merged into the PR branch (empty: merges cleanly, or
    git can't tell: `merge-tree --write-tree` needs git 2.38). The local PR branch is first
    fast-forwarded to GitHub's, so the fix starts from what the PR holds now."""
    if _has_ref(path, f"refs/remotes/origin/{branch}"):
        _git(path, "fetch", "--quiet", ".", f"origin/{branch}:{branch}")  # fails harmlessly if diverged
    if not _has_ref(path, f"refs/heads/{branch}"):
        return []
    base_ref = base if _has_ref(path, f"refs/heads/{base}") else f"origin/{base}"
    result = _git(path, "merge-tree", "--write-tree", "--name-only", "--no-messages", base_ref, branch)
    if result.returncode != 1:
        return []
    return list(dict.fromkeys(line.strip() for line in result.stdout.splitlines()[1:] if line.strip()))


def _fix_description(pr: Task, base: str, files: list[str]) -> str:
    listed = "\n".join(f"- {name}" for name in files)
    return (
        f"The pull request {pr.pr_url} (branch {pr.branch}) no longer merges into {base}. "
        f"{base} is already merged into your branch, with conflict markers left in:\n{listed}\n\n"
        "Resolve every conflict so both sides' changes keep working, and remove all the markers. "
        f"The fix is pushed onto {pr.branch}, updating that pull request."
    )


def _message(branch: str, pulled: int, pushed: int, fixes: list[dict]) -> str:
    parts = []
    if pulled:
        parts.append(f"Pulled {pulled} commit{'s' * (pulled != 1)} into {branch}.")
    if pushed:
        parts.append(f"Pushed {pushed} commit{'s' * (pushed != 1)} from {branch}.")
    if not parts:
        parts.append(f"{branch} is up to date.")
    if fixes:
        n = len(fixes)
        parts.append(f"{n} PR{'s' * (n != 1)} conflict with {branch}: a fix package is on its way.")
    return " ".join(parts)


def _has_remote(path: Path) -> bool:
    return _git(path, "remote", "get-url", "origin").returncode == 0


def _has_ref(path: Path, ref: str) -> bool:
    return _git(path, "rev-parse", "--verify", "--quiet", ref).returncode == 0


def _out(path: Path, *args: str) -> str:
    """git's stdout, stripped; a failure is a SyncError (502: git or GitHub said no)."""
    result = _git(path, *args)
    if result.returncode != 0:
        raise SyncError(f"git {args[0]} failed: {(result.stderr.strip() or result.stdout.strip())[-300:]}", 502)
    return result.stdout.strip()


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    # GIT_TERMINAL_PROMPT=0: never hang waiting for a password nobody can type.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env
        )
    except FileNotFoundError:
        raise SyncError("git is not installed", 502)
    except subprocess.TimeoutExpired:
        raise SyncError(f"git {args[0]} timed out", 502)
