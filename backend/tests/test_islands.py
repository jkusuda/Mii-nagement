import json
import subprocess
import sys
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from conftest import db_test, make_git_repo

from mii import github, islands, residents
from mii.models import Building, Island, Road, island_snapshot

pytestmark = db_test


def use_root(monkeypatch, root):
    """Point islands.py at another ISLANDS_ROOT (the environment wins over saved settings)."""
    if root is None:
        monkeypatch.delenv("ISLANDS_ROOT", raising=False)
    else:
        monkeypatch.setenv("ISLANDS_ROOT", str(root))


@pytest.fixture(autouse=True)
def repos_in_tmp(monkeypatch, tmp_path):
    """Never create island repos in the real Documents folder during tests."""
    use_root(monkeypatch, tmp_path / "root")
    return tmp_path / "root"


def test_default_folder_is_documents_or_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    use_root(monkeypatch, None)
    assert islands.default_folder() == tmp_path / "Mii-nagement"  # no Documents folder (Linux-style home)
    (tmp_path / "Documents").mkdir()
    assert islands.default_folder() == tmp_path / "Documents" / "Mii-nagement"
    use_root(monkeypatch, tmp_path / "elsewhere")
    assert islands.default_folder() == tmp_path / "elsewhere"


async def test_found_classic_island_with_buildings_roads_and_a_repo(island, repos_in_tmp):
    found = sync_to_async(islands.create_island)  # sync (git + ORM) -> run it off the event loop
    new = await found({"name": "Fern Harbor", "branch": "develop", "rules": {"merge_pr": True, "shell": False}})
    assert (new.id, new.repo, new.branch, new.plan) == ("fern_harbor", None, "develop", "classic")
    assert new.rules == {"merge_pr": True, "install_packages": False, "shell": False}
    path = Path(new.repo_path)
    assert path == (repos_in_tmp / "fern_harbor").resolve() and (path / ".git").is_dir()
    assert (path / "README.md").read_text().startswith("# Fern Harbor")
    log = subprocess.run(["git", "-C", str(path), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "New island" in log

    snap = await island_snapshot(new.id, [])
    assert sorted(b["type"] for b in snap["buildings"]) == ["courthouse", "lab", "mailbox", "post_office", "workshop"]
    assert all(b["id"].startswith("fern_harbor_") for b in snap["buildings"])
    assert len(snap["roads"]) == 6 and snap["residents"] == []
    assert snap["created_at"].endswith("+00:00")  # UTC, so the game can count days
    assert snap["branch"] == "develop" and snap["rules"]["merge_pr"] is True and snap["path"] == str(path)
    # Same name again gets its own id and folder.
    again = await found({"name": "Fern Harbor"})
    assert again.id == "fern_harbor_2" and Path(again.repo_path) != path


def test_quick_fixes_plan_is_smaller(island):
    new = islands.create_island({"name": "Quick", "plan": "quick_fixes"})
    assert {b.type for b in Building.objects.filter(island_id=new.id)} == {"mailbox", "workshop", "post_office"}
    assert Road.objects.filter(island_id=new.id).count() == 2


def test_found_in_a_chosen_folder_or_adopt_an_existing_repo(island, tmp_path):
    chosen = islands.create_island({"name": "Rocky Cove", "folder": tmp_path / "repos"})
    assert Path(chosen.repo_path) == (tmp_path / "repos" / "rocky_cove").resolve()
    repo = make_git_repo(tmp_path / "mine")
    adopted = islands.create_island({"name": "Mine", "existing_repo": repo})
    assert Path(adopted.repo_path) == repo.resolve()


@pytest.mark.parametrize("bad", [
    {"name": ""}, {"name": "x" * 31}, {"name": "A", "repo": "not a repo"},
    {"name": "A", "plan": "empty_lots"}, {"name": "A", "rules": {"merge_pr": "yes"}}, {"name": "A", "rules": {"fly": True}},
    {"name": "A", "existing_repo": "/nowhere/at/all"},
])
def test_found_rejects_bad_requests(island, bad):
    with pytest.raises(islands.IslandError):
        islands.create_island(bad)
    assert not Island.objects.filter(name="A").exists()  # nothing half-made


def test_found_refuses_a_non_empty_folder(island, tmp_path):
    (tmp_path / "busy" / "taken").mkdir(parents=True)
    (tmp_path / "busy" / "taken" / "file").write_text("x")
    with pytest.raises(islands.IslandError, match="not empty"):
        islands.create_island({"name": "Taken", "folder": tmp_path / "busy"})


async def test_new_residents_move_into_their_islands_buildings(island):
    found = sync_to_async(islands.create_island)
    new = await found({"name": "Fern Harbor"})
    coder = await residents.create_resident(new.id, {"name": "Pip", "job": "coder"})
    assert coder.building_id == "fern_harbor_workshop"
    works = {b.type: b.resident_id async for b in Building.objects.filter(island_id=new.id)}
    assert works["workshop"] == coder.id and works["lab"] == coder.id and works["mailbox"] is None
    # A second coder doesn't take over.
    await residents.create_resident(new.id, {"name": "Juniper", "job": "coder"})
    assert (await Building.objects.aget(pk="fern_harbor_workshop")).resident_id == coder.id
    # A reviewer on an island without a courthouse still gets a home.
    quick = await found({"name": "Quick", "plan": "quick_fixes"})
    assert (await residents.create_resident(quick.id, {"name": "Bea", "job": "reviewer"})).building_id is not None


def test_ensure_repo_gives_the_seed_island_a_repo(island, repos_in_tmp):
    sunny = Island.objects.get(pk="sunny")
    assert sunny.repo_path is None
    islands.ensure_repo(sunny)
    assert Path(sunny.repo_path) == (repos_in_tmp / "sunny").resolve()
    assert (Path(sunny.repo_path) / ".git").is_dir()
    islands.ensure_repo(sunny)  # idempotent


def test_list_repos_filters_and_reshapes(monkeypatch):
    out = json.dumps([
        {"nameWithOwner": "me/fern-api", "isPrivate": True, "updatedAt": "2026-10-16T00:00:00Z"},
        {"nameWithOwner": "me/other", "isPrivate": False, "updatedAt": "2026-10-15T00:00:00Z"},
    ])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    assert github.list_repos("FERN") == [{"repo": "me/fern-api", "private": True, "updated_at": "2026-10-16T00:00:00Z"}]


def test_gh_failure_is_a_github_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "not logged in"))
    with pytest.raises(github.GitHubError, match="not logged in"):
        github.list_branches("me/fern-api")


# --- An island on code you already have -------------------------------------------

def git(path, *args):
    return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def fake_github(monkeypatch, tmp_path):
    """A pretend GitHub: bare repos under tmp_path/github, cloned by tests/fake_gh.py."""
    root = tmp_path / "github"
    monkeypatch.setattr(islands, "GH", [sys.executable, str(Path(__file__).parent / "fake_gh.py")])
    monkeypatch.setenv("FAKE_GH_ROOT", str(root))
    return root


def hosted_repo(github_root: Path, repo: str, tmp_path: Path) -> Path:
    """Push a small project (main + a develop branch) to github_root/<repo>.git."""
    work = make_git_repo(tmp_path / "work" / repo)
    git(work, "branch", "-M", "main")
    git(work, "checkout", "-q", "-b", "develop")
    (work / "feature.py").write_text("x = 1\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "develop work")
    git(work, "checkout", "-q", "main")
    bare = github_root / f"{repo}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return bare


def test_found_from_github_clones_the_real_code(island, fake_github, tmp_path, repos_in_tmp):
    bare = hosted_repo(fake_github, "your-org/fern-api", tmp_path)
    new = islands.create_island({"name": "Fern Harbor", "repo": "your-org/fern-api", "branch": "develop"})
    path = Path(new.repo_path)
    assert path == (repos_in_tmp / "fern_harbor").resolve()
    assert (new.repo, new.branch) == ("your-org/fern-api", "develop")
    assert (path / "hello.py").exists() and (path / "feature.py").exists()  # the real project, on develop
    assert not (path / "README.md").exists()
    assert git(path, "rev-parse", "HEAD") == git(bare, "rev-parse", "develop")  # same history as GitHub

    # A task's worktree can push its branch back (the Post Office's first step).
    from mii.sandbox import Sandbox
    work = Sandbox(path, tmp_path / "worktrees").create_worktree("t1", new.branch)
    (work / "fix.py").write_text("fixed = True\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "fix")
    git(work, "push", "-q", "-u", "origin", "mii/t1")
    assert git(bare, "rev-parse", "mii/t1^") == git(bare, "rev-parse", "develop")


def test_found_from_github_defaults_to_its_default_branch(island, fake_github, tmp_path):
    hosted_repo(fake_github, "me/app", tmp_path)
    new = islands.create_island({"name": "App", "repo": "me/app"})
    assert new.branch == "main" and (Path(new.repo_path) / "hello.py").exists()


def test_found_from_an_empty_github_repo_starts_it(island, fake_github, tmp_path):
    subprocess.run(["git", "init", "-q", "--bare", str(fake_github / "me" / "empty.git")], check=True)
    new = islands.create_island({"name": "Empty", "repo": "me/empty", "branch": "trunk"})
    path = Path(new.repo_path)
    assert new.branch == "trunk" and git(path, "symbolic-ref", "--short", "HEAD") == "trunk"
    assert (path / "README.md").read_text().startswith("# Empty")


@pytest.mark.parametrize("setup, request_extra, match", [
    ("missing", {}, "Could not resolve"),
    ("logged_out", {}, "gh auth login"),
    ("no_gh", {}, "not installed"),
    ("hosted", {"branch": "nope"}, "no branch nope"),
])
def test_clone_errors_are_clear_and_leave_nothing(island, fake_github, tmp_path, repos_in_tmp, monkeypatch,
                                                   setup, request_extra, match):
    if setup == "hosted":
        hosted_repo(fake_github, "me/app", tmp_path)
    if setup == "logged_out":
        monkeypatch.setenv("FAKE_GH_LOGGED_OUT", "1")
    if setup == "no_gh":
        monkeypatch.setattr(islands, "GH", ["no-such-gh-command-here"])
    with pytest.raises(islands.IslandError, match=match):
        islands.create_island({"name": "App", "repo": "me/app", **request_extra})
    assert not Island.objects.filter(name="App").exists()
    assert not (repos_in_tmp / "app").exists()  # no half-made clone to trip over next time


def test_clone_refuses_a_non_empty_folder(island, fake_github, tmp_path, repos_in_tmp):
    hosted_repo(fake_github, "me/app", tmp_path)
    (repos_in_tmp / "app").mkdir(parents=True)
    (repos_in_tmp / "app" / "notes.txt").write_text("mine")
    with pytest.raises(islands.IslandError, match="not empty"):
        islands.create_island({"name": "App", "repo": "me/app"})
    assert (repos_in_tmp / "app" / "notes.txt").read_text() == "mine"  # left alone


@pytest.mark.parametrize("url", ["https://github.com/me/app.git", "git@github.com:me/app.git",
                                 "ssh://git@github.com/me/app", "https://github.com/me/app"])
def test_adopt_reads_owner_and_name_from_origin(island, tmp_path, url):
    repo = make_git_repo(tmp_path / "mine")
    git(repo, "checkout", "-q", "-b", "work")
    git(repo, "remote", "add", "origin", url)
    adopted = islands.create_island({"name": "Mine", "existing_repo": str(repo)})
    assert (adopted.repo, adopted.branch, Path(adopted.repo_path)) == ("me/app", "work", repo.resolve())


def test_adopt_without_github_origin(island, tmp_path):
    repo = make_git_repo(tmp_path / "local")
    adopted = islands.create_island({"name": "Local", "existing_repo": str(repo)})
    assert adopted.repo is None  # no PRs, but residents still work on the code
    git(repo, "remote", "add", "origin", "https://gitlab.com/me/app.git")
    assert islands.create_island({"name": "Lab", "existing_repo": str(repo)}).repo is None
    linked = make_git_repo(tmp_path / "linked")
    assert islands.create_island({"name": "Linked", "existing_repo": str(linked), "repo": "me/app"}).repo == "me/app"
    assert git(linked, "remote", "get-url", "origin") == "https://github.com/me/app.git"


def test_adopt_errors(island, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(islands.IslandError, match="not a git repository"):
        islands.create_island({"name": "A", "existing_repo": str(plain)})
    subprocess.run(["git", "init", "-q", str(tmp_path / "fresh")], check=True)
    with pytest.raises(islands.IslandError, match="no commits"):
        islands.create_island({"name": "A", "existing_repo": str(tmp_path / "fresh")})
    repo = make_git_repo(tmp_path / "other")
    git(repo, "remote", "add", "origin", "https://github.com/me/other.git")
    with pytest.raises(islands.IslandError, match="pushes to me/other"):
        islands.create_island({"name": "A", "existing_repo": str(repo), "repo": "me/app"})
    with pytest.raises(islands.IslandError, match="no branch"):
        islands.create_island({"name": "A", "existing_repo": str(repo), "branch": "nope"})
    assert not Island.objects.filter(name="A").exists()
