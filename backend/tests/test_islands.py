import json
import subprocess

import pytest
from sqlmodel import Session, select

from mii import github, islands, residents, seed
from mii.models import Building, Island, Road, island_snapshot, make_engine


@pytest.fixture
def db():
    engine = make_engine("sqlite://")
    seed.seed(engine)
    return engine


def test_found_classic_island_with_buildings_and_roads(db):
    with Session(db) as session:
        island = islands.create_island(session, {
            "name": "Fern Harbor", "repo": "your-org/fern-api", "branch": "develop",
            "rules": {"merge_pr": True, "shell": False},
        })
        assert (island.id, island.repo, island.branch, island.plan) == ("fern_harbor", "your-org/fern-api", "develop", "classic")
        assert island.rules == {"merge_pr": True, "install_packages": False, "shell": False}
        snap = island_snapshot(session, island.id, [])
        assert sorted(b["type"] for b in snap["buildings"]) == ["courthouse", "lab", "mailbox", "post_office", "workshop"]
        assert all(b["id"].startswith("fern_harbor_") for b in snap["buildings"])
        assert len(snap["roads"]) == 6 and snap["residents"] == []
        # Same name again gets its own id.
        assert islands.create_island(session, {"name": "Fern Harbor"}).id == "fern_harbor_2"


def test_quick_fixes_plan_is_smaller(db):
    with Session(db) as session:
        island = islands.create_island(session, {"name": "Quick", "plan": "quick_fixes"})
        types = {b.type for b in session.exec(select(Building).where(Building.island_id == island.id))}
        assert types == {"mailbox", "workshop", "post_office"}
        assert len(session.exec(select(Road).where(Road.island_id == island.id)).all()) == 2


@pytest.mark.parametrize("bad", [
    {"name": ""}, {"name": "x" * 31}, {"name": "A", "repo": "not a repo"},
    {"name": "A", "plan": "empty_lots"}, {"name": "A", "rules": {"merge_pr": "yes"}}, {"name": "A", "rules": {"fly": True}},
])
def test_found_rejects_bad_requests(db, bad):
    with Session(db) as session, pytest.raises(islands.IslandError):
        islands.create_island(session, bad)


def test_new_residents_move_into_their_islands_buildings(db):
    with Session(db) as session:
        island = islands.create_island(session, {"name": "Fern Harbor"})
        coder = residents.create_resident(session, island.id, {"name": "Pip", "job": "coder"})
        assert coder.building_id == "fern_harbor_workshop"
        works = {b.type: b.resident_id for b in session.exec(select(Building).where(Building.island_id == island.id))}
        assert works["workshop"] == coder.id and works["lab"] == coder.id and works["mailbox"] is None
        # A second coder doesn't take over.
        residents.create_resident(session, island.id, {"name": "Juniper", "job": "coder"})
        assert session.get(Building, "fern_harbor_workshop").resident_id == coder.id
        # A reviewer on an island without a courthouse still gets a home.
        quick = islands.create_island(session, {"name": "Quick", "plan": "quick_fixes"})
        assert residents.create_resident(session, quick.id, {"name": "Bea", "job": "reviewer"}).building_id is not None


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
