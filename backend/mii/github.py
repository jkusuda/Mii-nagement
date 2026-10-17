"""GitHub in and out, through the user's logged-in `gh` CLI (no token setup needed).

    repos:     gh repo list --limit 100 --json nameWithOwner,isPrivate,updatedAt   (island creator)
    branches:  gh api repos/<owner/name>/branches --jq '.[].name'                (island creator)
    issue in:  gh issue view <number> --repo <owner/name> --json title,body
    PR out:    git push -u origin mii/<task_id>   (from the task's worktree)
               gh pr create --repo <owner/name> --head mii/<task_id> --title ... --body ...

Both functions block on subprocesses; async callers wrap them in asyncio.to_thread.
"""

import json
import subprocess
from pathlib import Path


class GitHubError(Exception):
    pass


def _run(args: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise GitHubError(f"`{args[0]}` is not installed")
    except subprocess.TimeoutExpired:
        raise GitHubError(f"`{' '.join(args[:2])}` timed out")
    if result.returncode != 0:
        raise GitHubError(f"`{' '.join(args[:3])}` failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def list_repos(query: str = "", limit: int = 100) -> list[dict]:
    """Repos the logged-in user can see, newest first, filtered by `query`."""
    raw = _run(["gh", "repo", "list", "--limit", str(limit), "--json", "nameWithOwner,isPrivate,updatedAt"])
    try:
        repos = json.loads(raw)
    except json.JSONDecodeError:
        raise GitHubError("gh returned something that isn't JSON")
    query = query.lower().strip()
    return [
        {"repo": r["nameWithOwner"], "private": r["isPrivate"], "updated_at": r["updatedAt"]}
        for r in repos
        if query in r["nameWithOwner"].lower()
    ]


def list_branches(repo: str) -> list[str]:
    """Branch names of `owner/name`."""
    return [line for line in _run(["gh", "api", f"repos/{repo}/branches", "--jq", ".[].name"]).splitlines() if line]


def fetch_issue(repo: str, number: int) -> tuple[str, str]:
    """Return (title, body) of an issue. Used by POST /islands/{id}/tasks {"issue": 42}."""
    raw = _run(["gh", "issue", "view", str(number), "--repo", repo, "--json", "title,body"])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise GitHubError("gh returned something that isn't JSON")
    return data.get("title", f"Issue #{number}"), data.get("body") or ""


def open_pull_request(repo: str, worktree: Path, branch: str, title: str, body: str) -> str:
    """Push the task branch and open a PR. Returns the PR URL. Called by the post office."""
    _run(["git", "push", "-u", "origin", branch], cwd=worktree)
    out = _run(["gh", "pr", "create", "--repo", repo, "--head", branch, "--title", title, "--body", body], cwd=worktree)
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if not lines or not lines[-1].startswith("http"):
        raise GitHubError(f"gh did not print a PR URL: {out.strip()[:200]}")
    return lines[-1]
