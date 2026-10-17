import json
import subprocess
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
    new = await found({
        "name": "Fern Harbor", "repo": "your-org/fern-api", "branch": "develop",
        "rules": {"merge_pr": True, "shell": False},
    })
    assert (new.id, new.repo, new.branch, new.plan) == ("fern_harbor", "your-org/fern-api", "develop", "classic")
    assert new.rules == {"merge_pr": True, "install_packages": False, "shell": False}
    path = Path(new.repo_path)
    assert path == (repos_in_tmp / "fern_harbor").resolve() and (path / ".git").is_dir()
    assert (path / "README.md").read_text().startswith("# Fern Harbor")
    log = subprocess.run(["git", "-C", str(path), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "New island" in log
    origin = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True, text=True).stdout
    assert origin.strip() == "https://github.com/your-org/fern-api.git"

    snap = await island_snapshot(new.id, [])
    assert sorted(b["type"] for b in snap["buildings"]) == ["courthouse", "lab", "mailbox", "post_office", "workshop"]
    assert all(b["id"].startswith("fern_harbor_") for b in snap["buildings"])
    assert len(snap["roads"]) == 6 and snap["residents"] == []
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
