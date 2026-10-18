"""GitHub in and out, through the user's logged-in `gh` CLI (no token setup needed).

    repos:     gh api user/repos?affiliation=owner,collaborator,organization_member  (island creator)
    branches:  gh api repos/<owner/name>/branches --jq '.[].name'                (island creator)
    issues:    gh issue list --repo <owner/name> --state open --json number,title,labels,updatedAt  (pick a task)
    issue in:  gh issue view <number> --repo <owner/name> --json title,body
    PR out:    git push -u origin mii/<task_id>   (from the task's worktree)
               gh pr create --repo <owner/name> --head mii/<task_id> --base <island branch> --title ... --body ...
    PR state:  gh pr view <url> --json state,mergedAt   (insights.py: "PRs merged")
    merge:     gh pr merge <url> --squash   (or --merge / --rebase, whichever the repo allows)

Everything here blocks on subprocesses; async callers wrap it in asyncio.to_thread.
"""

import json
import os
import subprocess
from pathlib import Path

# `gh` as a command list, so tests can swap in a stand-in (tests/fake_gh.py).
GH = ["gh"]
INSTALL_HINT = "the GitHub CLI (gh) is not installed: get it from https://cli.github.com"


class GitHubError(Exception):
    """gh (or git) could not do it. The message is shown to the user."""


def _run(args: list[str], cwd: Path | None = None, timeout: float = 120) -> str:
    # GIT_TERMINAL_PROMPT=0: never hang waiting for a password nobody can type.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        raise GitHubError(INSTALL_HINT if args[0] == GH[0] else f"`{args[0]}` is not installed")
    except subprocess.TimeoutExpired:
        raise GitHubError(f"`{' '.join(args[:2])}` timed out")
    if result.returncode != 0:
        raise GitHubError(f"`{' '.join(args[:3])}` failed: {(result.stderr.strip() or result.stdout.strip())[-300:]}")
    return result.stdout


def _json(raw: str) -> dict | list:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise GitHubError("gh returned something that isn't JSON")


def ensure_logged_in() -> None:
    """gh is installed and logged in (it clones and, later, opens the PRs)."""
    try:
        status = subprocess.run([*GH, "auth", "status"], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise GitHubError(f"{INSTALL_HINT}, then run `gh auth login`")
    except subprocess.TimeoutExpired:
        raise GitHubError("`gh auth status` timed out")
    if status.returncode != 0:
        raise GitHubError("gh is not logged in to GitHub: run `gh auth login` in a terminal, then try again")


def clone(repo: str, path: Path, timeout: float) -> None:
    """`gh repo clone owner/name <path>` (islands' repos.clone_repo)."""
    _run([*GH, "repo", "clone", repo, str(path)], timeout=timeout)


def list_repos(query: str = "", limit: int = 100) -> list[dict]:
    """Repos the logged-in user owns, contributes to or reaches through an org, newest first, filtered by `query`."""
    # `gh repo list` only shows the user's own repos; the API lets us add collaborator and org repos.
    affiliation = "affiliation=owner,collaborator,organization_member"
    endpoint = f"user/repos?{affiliation}&sort=updated&direction=desc&per_page={min(limit, 100)}"
    repos = _json(_run([*GH, "api", endpoint]))
    query = query.lower().strip()
    return [
        {"repo": r["full_name"], "private": r["private"], "updated_at": r["updated_at"]}
        for r in repos
        if query in r["full_name"].lower()
    ]


def list_branches(repo: str) -> list[str]:
    """Branch names of `owner/name`, its default branch first (the island creator picks that one)."""
    raw = _run([*GH, "api", "--paginate", f"repos/{repo}/branches", "--jq", ".[].name"])
    names = [line for line in raw.splitlines() if line]
    default = _run([*GH, "api", f"repos/{repo}", "--jq", ".default_branch"]).strip()
    return [default, *[n for n in names if n != default]] if default else names


def list_issues(repo: str, limit: int = 50) -> list[dict]:
    """Open issues of `owner/name`, newest first: number, title, labels, updated_at (GET /islands/{id}/issues)."""
    raw = _run(
        [
            *GH,
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,labels,updatedAt",
        ]
    )
    try:
        issues = json.loads(raw)
    except json.JSONDecodeError:
        raise GitHubError("gh returned something that isn't JSON")
    return [
        {
            "number": i["number"],
            "title": i["title"],
            "labels": [label["name"] for label in i.get("labels", [])],
            "updated_at": i.get("updatedAt"),
        }
        for i in issues
    ]


def fetch_issue(repo: str, number: int) -> tuple[str, str]:
    """Return (title, body) of an issue. Used by POST /islands/{id}/tasks {"issue": 42}."""
    data = _json(_run([*GH, "issue", "view", str(number), "--repo", repo, "--json", "title,body"]))
    return data.get("title", f"Issue #{number}"), data.get("body") or ""


def open_pull_request(repo: str, worktree: Path, branch: str, title: str, body: str, base: str | None = None) -> str:
    """Push the task branch and open a PR against `base` (the island's branch). Returns the PR URL."""
    _run(["git", "push", "-u", "origin", branch], cwd=worktree)
    args = [*GH, "pr", "create", "--repo", repo, "--head", branch, "--title", title, "--body", body]
    if base:
        args += ["--base", base]
    out = _run(args, cwd=worktree)
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if not lines or not lines[-1].startswith("http"):
        raise GitHubError(f"gh did not print a PR URL: {out.strip()[:200]}")
    return lines[-1]


MERGE_METHODS = ("--squash", "--merge", "--rebase")


def merge_pull_request(pr_url: str) -> str:
    """Merge a PR you approved. Tries squash, then a merge commit, then rebase, since
    repositories can switch any of them off. Returns the method that worked."""
    errors = []
    for method in MERGE_METHODS:
        try:
            _run([*GH, "pr", "merge", pr_url, method])
            return method.lstrip("-")
        except GitHubError as e:
            if "not allowed" not in str(e).lower():
                raise  # not about the method (checks failing, a conflict, no permission): say so
            errors.append(str(e))
    raise GitHubError(f"this repository allows none of {', '.join(MERGE_METHODS)}: {errors[-1]}")


def pr_merged_at(pr_url: str) -> str | None:
    """When the PR was merged (ISO-8601), or None while it's open or closed unmerged."""
    data = _json(_run([*GH, "pr", "view", pr_url, "--json", "state,mergedAt"]))
    return data.get("mergedAt") or None
