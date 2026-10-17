"""GitHub in and out, through the user's logged-in `gh` CLI (no token setup needed).

    repos:     gh repo list --limit 100 --json nameWithOwner,isPrivate,updatedAt   (island creator)
    branches:  gh api repos/<owner/name>/branches --jq '.[].name'                (island creator)
    issue in:  gh issue view <number> --repo <owner/name> --json title,body
    PR out:    git push -u origin mii/<task_id>   (from the task's worktree)
               gh pr create --repo <owner/name> --head mii/<task_id> --title ... --body ...
"""

import json
import subprocess
from pathlib import Path


class GitHubError(Exception):
    pass


def _gh(*args: str) -> str:
    """Run the `gh` CLI and return what it printed. GitHubError if it's missing or fails."""
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        raise GitHubError("the GitHub CLI (gh) isn't installed") from None
    except subprocess.TimeoutExpired:
        raise GitHubError("gh took too long to answer") from None
    if done.returncode != 0:
        raise GitHubError(done.stderr.strip() or "gh failed")
    return done.stdout


def list_repos(query: str = "", limit: int = 100) -> list[dict]:
    """Repos the logged-in user can see, newest first, filtered by `query`."""
    repos = json.loads(_gh("repo", "list", "--limit", str(limit), "--json", "nameWithOwner,isPrivate,updatedAt"))
    query = query.lower().strip()
    return [
        {"repo": r["nameWithOwner"], "private": r["isPrivate"], "updated_at": r["updatedAt"]}
        for r in repos
        if query in r["nameWithOwner"].lower()
    ]


def list_branches(repo: str) -> list[str]:
    """Branch names of `owner/name`."""
    return [line for line in _gh("api", f"repos/{repo}/branches", "--jq", ".[].name").splitlines() if line]


def fetch_issue(repo: str, number: int) -> tuple[str, str]:
    """Return (title, body) of an issue. Used by POST /islands/{id}/tasks {"issue": 42}."""
    # TODO(mvp): run the `gh issue view` command above with subprocess and parse its JSON.
    raise NotImplementedError("GitHub issues aren't wired up yet (mii/github.py)")


def open_pull_request(repo: str, worktree: Path, branch: str, title: str, body: str) -> str:
    """Push the task branch and open a PR. Returns the PR URL. Called by the post office."""
    # TODO(mvp): run the `git push` and `gh pr create` commands above; return the URL gh prints.
    raise NotImplementedError("Pull requests aren't wired up yet (mii/github.py)")
