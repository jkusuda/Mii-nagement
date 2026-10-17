"""GitHub in and out, through the user's logged-in `gh` CLI (no token setup needed).

    issue in:  gh issue view <number> --repo <owner/name> --json title,body
    PR out:    git push -u origin mii/<task_id>   (from the task's worktree)
               gh pr create --repo <owner/name> --head mii/<task_id> --title ... --body ...
"""

from pathlib import Path


class GitHubError(Exception):
    pass


def fetch_issue(repo: str, number: int) -> tuple[str, str]:
    """Return (title, body) of an issue. Used by POST /islands/{id}/tasks {"issue": 42}."""
    # TODO(mvp): run the `gh issue view` command above with subprocess and parse its JSON.
    raise NotImplementedError("GitHub issues aren't wired up yet (mii/github.py)")


def open_pull_request(repo: str, worktree: Path, branch: str, title: str, body: str) -> str:
    """Push the task branch and open a PR. Returns the PR URL. Called by the post office."""
    # TODO(mvp): run the `git push` and `gh pr create` commands above; return the URL gh prints.
    raise NotImplementedError("Pull requests aren't wired up yet (mii/github.py)")
