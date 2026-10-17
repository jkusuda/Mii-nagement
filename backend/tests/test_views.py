import json
from types import SimpleNamespace

import pytest
from conftest import ScriptedRunner, db_test, make_workflow
from django.test import AsyncClient

from mii import views

pytestmark = db_test


@pytest.fixture
def fake_runtime(monkeypatch):
    workflow, _ = make_workflow(ScriptedRunner())
    runtime = SimpleNamespace(workflow=workflow, bubbles=workflow.bubbles, bus=workflow.bus)
    monkeypatch.setattr(views, "get_runtime", lambda: runtime)
    return runtime


async def test_health():
    response = await AsyncClient().get("/health")
    assert response.status_code == 200 and response.json()["ok"] is True


async def test_brains_lists_every_brain():
    data = (await AsyncClient().get("/brains")).json()
    assert {"mock", "claude-code", "codex", "gemini", "openrouter"} <= set(data)
    assert data["mock"] is True and all(isinstance(v, bool) for v in data.values())
    detail = (await AsyncClient().get("/brains/detail")).json()
    assert detail["codex"]["hint"]


async def test_island_snapshot_and_404(island, fake_runtime):
    client = AsyncClient()
    data = (await client.get("/islands/sunny")).json()
    assert data["name"] == "Sunny Isle" and len(data["buildings"]) == 5
    assert (await client.get("/islands/nowhere")).status_code == 404


async def test_create_task_validates_and_queues(island, fake_runtime):
    client = AsyncClient()
    bad = await client.post("/islands/sunny/tasks", data=json.dumps({}), content_type="application/json")
    assert bad.status_code == 400
    issue = await client.post("/islands/sunny/tasks", data=json.dumps({"issue": 3}), content_type="application/json")
    assert issue.status_code == 400  # the seed island has no repo
    ok = await client.post("/islands/sunny/tasks", data=json.dumps({"title": "Hi"}), content_type="application/json")
    assert ok.status_code == 200 and ok.json()["status"] == "queued"
    assert fake_runtime.workflow.queue.qsize() == 1


async def test_insights_and_replay_endpoints(island, fake_runtime):
    client = AsyncClient()
    assert set((await client.get("/islands/sunny/insights")).json()) == {"island", "residents", "brains", "buildings"}
    assert (await client.post("/replay/t_nope")).status_code == 404


async def test_list_and_found_islands(island, fake_runtime, tmp_path, monkeypatch):
    import dataclasses

    from mii import islands
    from mii.config import config

    monkeypatch.setattr(islands, "config", dataclasses.replace(config, islands_root=tmp_path / "root"))
    client = AsyncClient()
    listing = (await client.get("/islands")).json()
    assert [i["id"] for i in listing["islands"]] == ["sunny"] and listing["default_folder"].endswith("root")
    assert listing["islands"][0]["created_at"].endswith("+00:00")

    founded = await client.post("/islands", data=json.dumps({"name": "Rocky Cove", "plan": "quick_fixes"}),
                                content_type="application/json")
    assert founded.status_code == 200, founded.content
    snap = founded.json()
    assert snap["id"] == "rocky_cove" and snap["path"] == str((tmp_path / "root" / "rocky_cove").resolve())
    assert len(snap["buildings"]) == 3 and snap["plan"] == "quick_fixes"
    assert (await client.get("/islands/rocky_cove")).json()["path"] == snap["path"]

    bad = await client.post("/islands", data=json.dumps({"name": ""}), content_type="application/json")
    assert bad.status_code == 400


async def test_github_endpoints_report_gh_problems(monkeypatch):
    from mii import github

    def boom(*a, **k):
        raise github.GitHubError("not logged in")

    monkeypatch.setattr(github, "list_repos", boom)
    monkeypatch.setattr(github, "list_branches", boom)
    client = AsyncClient()
    assert (await client.get("/github/repos?q=x")).status_code == 503
    assert (await client.get("/github/branches?repo=me/x")).status_code == 503
    assert (await client.get("/github/branches?repo=nope")).status_code == 400
