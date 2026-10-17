import subprocess
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from conftest import ScriptedRunner, db_test, make_git_repo, make_workflow
from django.test import AsyncClient

from mii import github, sync
from mii.models import Island, Task
from mii.sandbox import Sandbox, SandboxError

pytestmark = db_test


def git(path: Path, *args: str) -> str:
    cmd = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def commit(path: Path, name: str, text: str, message: str = "change") -> None:
    (path / name).write_text(text)
    git(path, "add", name)
    git(path, "commit", "-q", "-m", message)


@pytest.fixture
def hosted(tmp_path, island):
    """GitHub stand-in: a bare `origin`, the island's clone of it, and a teammate's clone."""
    seed = make_git_repo(tmp_path / "seed")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    local, mate = tmp_path / "local", tmp_path / "mate"
    for path in (local, mate):
        subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True)
    branch = git(local, "symbolic-ref", "--short", "HEAD")
    Island.objects.filter(pk=island).update(repo_path=str(local), branch=branch, repo="me/sunny")
    return local, mate, branch


async def get(island_id: str = "sunny"):
    return await AsyncClient().get(f"/islands/{island_id}/sync")


async def post(island_id: str = "sunny"):
    return await AsyncClient().post(f"/islands/{island_id}/sync", data=b"", content_type="application/json")


async def test_no_repository_or_no_origin(island, fake_runtime, tmp_path):
    assert (await get()).status_code == 400  # the seed island has no repo in tests
    assert (await get("nowhere")).status_code == 404
    repo = make_git_repo(tmp_path / "solo")
    await Island.objects.filter(pk=island).aupdate(repo_path=str(repo), branch=git(repo, "branch", "--show-current"))
    assert (await get()).json()["remote"] is False
    assert (await post()).status_code == 400


async def test_pull_fast_forwards_the_island_branch(hosted, fake_runtime):
    local, mate, branch = hosted
    assert (await get()).json() == {"branch": branch, "remote": True, "behind": 0, "ahead": 0}
    await sync_to_async(commit)(mate, "new.py", "x = 1\n")
    git(mate, "push", "-q", "origin", branch)
    assert (await get()).json()["behind"] == 1
    data = (await post()).json()
    assert data["pulled"] == 1 and data["behind"] == 0 and data["conflicts"] == []
    assert "Pulled 1 commit" in data["message"]
    assert (local / "new.py").exists()


async def test_pull_moves_a_branch_that_is_not_checked_out(hosted, fake_runtime):
    local, mate, branch = hosted
    git(local, "checkout", "-q", "-b", "elsewhere")
    commit(mate, "new.py", "x = 1\n")
    git(mate, "push", "-q", "origin", branch)
    assert (await post()).json()["pulled"] == 1
    assert git(local, "rev-parse", branch) == git(mate, "rev-parse", "HEAD")


async def test_push_when_only_ahead(hosted, fake_runtime):
    local, mate, _ = hosted
    commit(local, "mine.py", "y = 2\n")
    data = (await post()).json()
    assert data["pushed"] == 1 and data["ahead"] == 0
    git(mate, "pull", "-q")
    assert (mate / "mine.py").exists()


async def test_diverged_or_dirty_changes_nothing(hosted, fake_runtime):
    local, mate, branch = hosted
    commit(mate, "theirs.py", "a = 1\n")
    git(mate, "push", "-q", "origin", branch)
    (local / "hello.py").write_text("edited, not committed\n")
    dirty = await post()
    assert dirty.status_code == 409 and "uncommitted" in dirty.json()["error"]
    git(local, "checkout", "-q", "--", "hello.py")
    commit(local, "mine.py", "b = 2\n")
    diverged = await post()
    assert diverged.status_code == 409 and "by hand" in diverged.json()["error"]
    assert not (local / "theirs.py").exists()


def open_pr(local: Path, task_id: str, name: str, text: str) -> str:
    """A finished task's branch, pushed like the Post Office does, that writes `name`."""
    branch = f"mii/{task_id}"
    git(local, "checkout", "-q", "-b", branch)
    commit(local, name, text)
    git(local, "push", "-q", "origin", branch)
    git(local, "checkout", "-q", "-")
    return branch


async def test_a_conflicting_open_pr_gets_one_fix_package(hosted, monkeypatch):
    local, mate, branch = hosted
    pr_branch = await sync_to_async(open_pr)(local, "t_pr", "hello.py", "print('from the PR')\n")
    await Task.objects.acreate(
        id="t_pr", island_id="sunny", title="Say hi", status="done", branch=pr_branch, pr_url="https://gh/pr/1"
    )
    clean = await sync_to_async(open_pr)(local, "t_ok", "other.py", "z = 3\n")
    await Task.objects.acreate(
        id="t_ok", island_id="sunny", title="Add", status="done", branch=clean, pr_url="https://gh/pr/2"
    )
    commit(mate, "hello.py", "print('from main')\n")
    git(mate, "push", "-q", "origin", branch)
    monkeypatch.setattr(github, "pr_state", lambda url: "OPEN")
    workflow, _ = make_workflow(ScriptedRunner())
    island = await Island.objects.aget(pk="sunny")

    data = await sync.sync(island, workflow)
    assert data["pulled"] == 1 and len(data["conflicts"]) == 1  # t_ok still merges cleanly
    found = data["conflicts"][0]
    assert found["task"] == "t_pr" and found["files"] == ["hello.py"]
    fix = await Task.objects.aget(pk=found["fix_task"])
    assert fix.target_branch == pr_branch and fix.pr_url is None and fix.status == "queued"
    assert "hello.py" in fix.description and "conflict" in data["message"]
    assert (await sync.sync(island, workflow))["conflicts"] == []  # the fix is already on its way

    await Task.objects.filter(pk=fix.id).aupdate(status="done")
    monkeypatch.setattr(github, "pr_state", lambda url: "MERGED")
    assert (await sync.sync(island, workflow))["conflicts"] == []  # merged on GitHub meanwhile


def test_merge_in_leaves_conflicts_and_commit_all_concludes_the_merge(tmp_path):
    repo = make_git_repo(tmp_path / "repo")
    main = git(repo, "branch", "--show-current")
    git(repo, "checkout", "-q", "-b", "mii/t_pr")
    commit(repo, "hello.py", "print('pr')\n")
    git(repo, "checkout", "-q", main)
    commit(repo, "hello.py", "print('main')\n")
    box = Sandbox(repo, tmp_path / "worktrees")

    path = box.create_worktree("t_fix", "mii/t_pr")
    assert box.merge_in("t_fix", main) == ["hello.py"]
    assert "<<<<<<<" in (path / "hello.py").read_text()
    assert box.merge_in("t_fix", main) == ["hello.py"]  # resumed: the merge in progress is kept
    with pytest.raises(SandboxError, match="conflict markers"):  # markers left in: no commit
        box.commit_all("t_fix", "Fix conflicts")
    assert box.conflict_markers("t_fix") == ["hello.py"]
    (path / "hello.py").write_text("print('pr and main')\n")
    assert box.conflict_markers("t_fix") == []
    assert box.commit_all("t_fix", "Fix conflicts") is True
    assert len(git(path, "log", "-1", "--format=%P").split()) == 2  # a merge commit

    git(repo, "checkout", "-q", "-b", "side", main)
    commit(repo, "other.py", "z = 3\n")
    git(repo, "checkout", "-q", main)
    clean = box.create_worktree("t_clean", main)
    assert box.merge_in("t_clean", "side") == []  # merges cleanly: already committed
    assert (clean / "other.py").exists() and git(clean, "status", "--porcelain") == ""
