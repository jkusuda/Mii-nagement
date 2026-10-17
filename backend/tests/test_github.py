import subprocess
from pathlib import Path

import pytest

from mii import github


def fake_run(calls: list, responses: dict):
    def run(args, cwd=None, capture_output=True, text=True, timeout=None, env=None):
        calls.append((args, cwd))
        code, out, err = responses.get(args[0] + " " + args[1], (0, "", ""))
        return subprocess.CompletedProcess(args, code, out, err)

    return run


def test_fetch_issue_parses_gh_json(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        github.subprocess, "run", fake_run(calls, {"gh issue": (0, '{"title": "Bug", "body": "It breaks"}', "")})
    )
    assert github.fetch_issue("octo/repo", 42) == ("Bug", "It breaks")
    assert calls[0][0] == ["gh", "issue", "view", "42", "--repo", "octo/repo", "--json", "title,body"]


def test_fetch_issue_raises_on_gh_failure(monkeypatch):
    monkeypatch.setattr(github.subprocess, "run", fake_run([], {"gh issue": (1, "", "not found")}))
    with pytest.raises(github.GitHubError, match="not found"):
        github.fetch_issue("octo/repo", 42)


def test_open_pull_request_pushes_then_creates(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        github.subprocess,
        "run",
        fake_run(calls, {"gh pr": (0, "Creating pull request\nhttps://github.com/octo/repo/pull/7\n", "")}),
    )
    url = github.open_pull_request("octo/repo", Path("/wt"), "mii/t_1", "Title", "Body")
    assert url == "https://github.com/octo/repo/pull/7"
    assert calls[0] == (["git", "push", "-u", "origin", "mii/t_1"], Path("/wt"))
    assert calls[1][0][:6] == ["gh", "pr", "create", "--repo", "octo/repo", "--head"]


def test_missing_gh_is_a_github_error(monkeypatch):
    def run(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(github.subprocess, "run", run)
    with pytest.raises(github.GitHubError, match="not installed"):
        github.fetch_issue("octo/repo", 1)
