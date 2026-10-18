"""Island repositories on disk: create, clone or adopt the git repo an island lives in.

Three ways to get one (islands.create_island picks):
    new     init_repo(): a fresh repo with a README commit
    clone   clone_repo(): `gh repo clone owner/name` with the user's gh login, on the island's branch
    adopt   adopt_repo(): a git folder that's already on this computer, as is

Everything here is sync (git subprocesses); async callers use asyncio.to_thread.
Errors are IslandError, so the island creator can show the message.
"""

import re
import shutil
import subprocess
from pathlib import Path

from mii import github
from mii.island_map import IslandError
from mii.sandbox import split_author
from mii.vocab import DEFAULT_GIT_AUTHOR

CLONE_TIMEOUT = 15 * 60  # seconds; big repos take a while
GITHUB_URL = re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def init_repo(folder: Path, island_id: str, name: str, branch: str = "main") -> Path:
    """Create <folder>/<island_id> as a git repo with one commit on `branch` (or adopt it if it already is one)."""
    path = folder / island_id
    if (path / ".git").is_dir():
        return path
    if path.exists() and any(path.iterdir()):
        raise IslandError(f"{path} exists and is not empty")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise IslandError(f"cannot create {path}: {e.strerror}")
    _git(path, "init", "-q")
    _git(path, "symbolic-ref", "HEAD", f"refs/heads/{branch}")  # not git's own default (often master)
    _first_commit(path, name)
    return path


def clone_repo(repo: str, path: Path, branch: str | None, name: str) -> tuple[Path, str]:
    """Clone github.com/<repo> into `path` and check out `branch` (None: the repo's default branch).

    Returns (path, branch). An empty GitHub repo gets the island's first commit (a README)
    on `branch`. On failure the half-made folder is removed again.
    """
    if path.exists() and any(path.iterdir()):
        raise IslandError(
            f"{path} exists and is not empty: pick another folder, or use it as a folder on this computer"
        )
    try:
        github.ensure_logged_in()
    except github.GitHubError as e:
        raise IslandError(str(e))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise IslandError(f"cannot create {path.parent}: {e.strerror}")
    created = not path.exists()
    try:
        try:
            github.clone(repo, path, timeout=CLONE_TIMEOUT)
        except github.GitHubError as e:
            raise IslandError(str(e))
        if _has_commits(path):
            if branch and branch != current_branch(path):
                if not _ok(path, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"):
                    raise IslandError(f"{repo} has no branch {branch}")
                _git(path, "checkout", "-q", "-B", branch, f"origin/{branch}")
        else:  # an empty repo on GitHub: start it like a new island
            branch = branch or "main"
            _git(path, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
            _first_commit(path, name)
        return path, branch or current_branch(path) or "main"
    except BaseException:
        if created:
            shutil.rmtree(path, ignore_errors=True)
        raise


def adopt_repo(path: Path, repo: str | None, branch: str | None) -> tuple[Path, str | None, str]:
    """Use a git folder that's already on this computer. Returns (path, repo, branch): `repo`
    comes from its GitHub origin when not given, `branch` defaults to the checked-out one."""
    if not path.is_dir():
        raise IslandError(f"{path} is not a folder")
    if not (path / ".git").exists():  # a folder (or a worktree's .git file)
        raise IslandError(f"{path} is not a git repository (run `git init` there first)")
    if not _has_commits(path):
        raise IslandError(
            f"{path} has no commits yet: commit something first, so residents have a branch to start from"
        )
    origin = github_repo_of(path)
    if repo and origin and origin.lower() != repo.lower():
        raise IslandError(f"{path} pushes to {origin}, not {repo}")
    if repo and not origin:
        link_remote(path, repo)
    branch = branch or current_branch(path) or "main"
    if not has_branch(path, branch):
        raise IslandError(f"{path} has no branch {branch}")
    return path, repo or origin, branch


def github_repo_of(path: Path) -> str | None:
    """ "owner/name" of the repo's `origin`, if it is on GitHub."""
    result = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True, text=True)
    match = GITHUB_URL.search(result.stdout.strip()) if result.returncode == 0 else None
    return match.group(1) if match else None


def current_branch(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True
    )
    return result.stdout.strip() or None


def link_remote(path: Path, repo: str) -> None:
    """Point the repo's `origin` at github.com/<owner/name>."""
    url = f"https://github.com/{repo}.git"
    if not _ok(path, "remote", "get-url", "origin"):
        _git(path, "remote", "add", "origin", url)
    elif github_repo_of(path) != repo:
        _git(path, "remote", "set-url", "origin", url)


def has_branch(path: Path, branch: str) -> bool:
    """A local branch, or one fetched from origin."""
    return _ok(path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") or _ok(
        path, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"
    )


def is_git_repo(path: str | None) -> bool:
    return bool(path) and (Path(path) / ".git").is_dir()


def _has_commits(path: Path) -> bool:
    return _ok(path, "rev-parse", "--verify", "--quiet", "HEAD")


def _ok(path: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True).returncode == 0


def _first_commit(path: Path, name: str) -> None:
    (path / "README.md").write_text(f'# {name}\n\nThis repository is the island "{name}" in Mii-nagement.\n')
    author, email = split_author(DEFAULT_GIT_AUTHOR)
    _git(path, "add", "README.md")
    _git(path, "-c", f"user.name={author}", "-c", f"user.email={email}", "commit", "-q", "-m", "New island")


def _git(cwd: Path, *args: str) -> None:
    try:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise IslandError("git is not installed")
    if result.returncode != 0:
        raise IslandError(f"git {args[0]} failed: {result.stderr.strip()}")
