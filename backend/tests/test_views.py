import base64
import json

from conftest import db_test
from django.test import AsyncClient

from mii import residents
from mii.models import Attachment, Island

pytestmark = db_test


async def test_health(fake_runtime):
    response = await AsyncClient().get("/health")
    assert response.status_code == 200 and response.json()["ok"] is True
    assert response.json()["judge"] == "rules"  # no TypeSafe key: the rule-based judge


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


async def test_create_task_with_attachments(island, fake_runtime):
    client = AsyncClient()

    async def post(attachments):
        body = {"title": "With files", "attachments": attachments}
        return await client.post("/islands/sunny/tasks", data=json.dumps(body), content_type="application/json")

    hi = base64.b64encode(b"hi").decode()
    ok = await post([{"name": "../../etc/notes.md", "content": hi}, {"name": "notes.md", "content": hi}])
    assert ok.status_code == 200
    rows = [(a.name, bytes(a.data)) async for a in Attachment.objects.filter(task_id=ok.json()["id"]).order_by("id")]
    assert rows == [("notes.md", b"hi"), ("notes-2.md", b"hi")]  # folders dropped, names kept apart

    assert (await post([{"name": "a.txt", "content": "not base64!"}])).status_code == 400
    assert (await post([{"name": "..", "content": hi}])).status_code == 400
    assert (await post("a.txt")).status_code == 400
    assert (await post([{"name": f"{i}.txt", "content": hi} for i in range(11)])).status_code == 400
    big = base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode()
    assert "too big" in (await post([{"name": "big.bin", "content": big}])).json()["error"]


async def test_list_and_get_tasks_with_steps_and_events(island, fake_runtime):
    client = AsyncClient()
    assert (await client.get("/islands/sunny/tasks")).json() == {"tasks": []}
    task = await fake_runtime.workflow.submit("sunny", "Hi", "do it")
    listed = (await client.get("/islands/sunny/tasks")).json()["tasks"]
    assert [t["id"] for t in listed] == [task.id] and listed[0]["description"] == "do it" and listed[0]["steps"] == []
    detail = (await client.get(f"/islands/sunny/tasks/{task.id}")).json()
    assert detail["status"] == "queued" and detail["events"] == []  # nothing happened yet
    assert detail["description"] == "do it" and detail["attachments"] == []

    await fake_runtime.workflow.run_task(task.id)
    detail = (await client.get(f"/islands/sunny/tasks/{task.id}")).json()
    assert detail["status"] == "done"
    assert [s["building_type"] for s in detail["steps"]] == ["mailbox", "workshop", "lab", "courthouse", "post_office"]
    assert detail["steps"][0] | {"outcome": "pass", "summary": "scripted", "resident": "jun"} == detail["steps"][0]
    assert detail["events"][0]["type"] == "task" and detail["events"][0]["status"] == "in_progress"
    assert detail["events"][-1]["type"] == "task" and detail["events"][-1]["status"] == "done"
    assert {"agent_state", "mood", "handoff"} <= {e["type"] for e in detail["events"]}
    assert (await client.get("/islands/sunny/tasks?status=queued,in_progress")).json()["tasks"] == []
    assert (await client.get("/islands/sunny/tasks?limit=x")).status_code == 400
    assert (await client.get("/islands/sunny/tasks/t_nope")).status_code == 404
    assert (await client.get("/islands/nowhere/tasks")).status_code == 404


async def test_cancel_and_delete_endpoints(island, fake_runtime):
    client = AsyncClient()
    task = await fake_runtime.workflow.submit("sunny", "Hi")
    assert (await client.post(f"/islands/sunny/tasks/{task.id}/cancel")).json()["status"] == "cancelled"
    assert (await client.post(f"/islands/sunny/tasks/{task.id}/cancel")).status_code == 409
    assert (await client.post("/islands/sunny/tasks/t_nope/cancel")).status_code == 404

    assert (await client.delete("/islands/sunny/residents/carlos")).status_code == 409  # works at the workshop
    mo = await residents.create_resident("sunny", {"name": "Mo", "job": "coder"})
    assert (await client.delete(f"/islands/sunny/residents/{mo.id}")).json() == {"deleted": mo.id}
    assert (await client.delete(f"/islands/sunny/residents/{mo.id}")).status_code == 404

    assert (await client.delete("/islands/sunny")).status_code == 409  # the last island stays
    await Island.objects.acreate(id="rock", name="Rocky Cove")
    assert (await client.delete("/islands/rock")).json() == {"deleted": "rock", "repo_kept": None}
    assert (await client.get("/islands/rock")).status_code == 404
    assert (await client.delete("/islands/rock")).status_code == 404


async def test_insights_and_replay_endpoints(island, fake_runtime):
    client = AsyncClient()
    data = (await client.get("/islands/sunny/insights")).json()
    assert {"island", "per_day", "tip", "residents", "brains", "buildings"} <= set(data) and data["range"] == "week"
    assert (await client.get("/islands/sunny/insights?range=today")).json()["range"] == "today"
    assert (await client.get("/islands/sunny/insights?range=year")).status_code == 400
    assert (await client.post("/replay/t_nope")).status_code == 404


async def test_events_history_endpoint(island, fake_runtime):
    client = AsyncClient()
    assert (await client.get("/islands/sunny/events")).json() == {"events": []}
    task = await fake_runtime.workflow.submit("sunny", "Hi")
    await fake_runtime.workflow.run_task(task.id)
    history = (await client.get("/islands/sunny/events")).json()["events"]
    assert (
        history[0]["type"] == "task"
        and history[0]["status"] == "queued"
        and history[0]["event_id"] < history[1]["event_id"]
    )
    assert history[-1]["status"] == "done" and all("ts" in e for e in history)
    last_two = (await client.get("/islands/sunny/events?limit=2")).json()["events"]
    assert last_two == history[-2:]
    assert (await client.get("/islands/sunny/events?limit=x")).status_code == 400
    assert (await client.get("/islands/nowhere/events")).status_code == 404


async def test_gifts_endpoint():
    data = (await AsyncClient().get("/gifts")).json()
    assert set(data) == {"browser", "github", "docs", "database", "terminal", "godot", "video"}
    assert {"ok", "label", "hint"} <= set(data["docs"]) and isinstance(data["docs"]["ok"], bool)


async def test_layout_endpoint(island, fake_runtime):
    client = AsyncClient()
    body = {
        "buildings": [
            {"id": "mailbox", "type": "mailbox", "name": "Mailbox", "x": 1, "y": 1, "resident": "jun"},
            {"id": "workshop", "type": "workshop", "name": "Shed", "x": 5, "y": 5, "resident": "carlos"},
        ],
        "roads": [{"from": "mailbox", "to": "workshop", "condition": "always"}],
    }
    saved = await client.put("/islands/sunny/layout", data=json.dumps(body), content_type="application/json")
    assert saved.status_code == 200, saved.content
    assert [b["id"] for b in saved.json()["buildings"]] == ["mailbox", "workshop"] and len(saved.json()["roads"]) == 1
    assert (await client.get("/islands/sunny")).json()["buildings"][1]["name"] == "Shed"
    bad = await client.put("/islands/sunny/layout", data=json.dumps({"buildings": []}), content_type="application/json")
    assert bad.status_code == 400 and "mailbox" in bad.json()["error"]
    assert (await client.post("/islands/sunny/layout")).status_code == 405


async def test_list_and_found_islands(island, fake_runtime, tmp_path, monkeypatch):
    monkeypatch.setenv("ISLANDS_ROOT", str(tmp_path / "root"))
    client = AsyncClient()
    listing = (await client.get("/islands")).json()
    assert [i["id"] for i in listing["islands"]] == ["sunny"] and listing["default_folder"].endswith("root")
    assert listing["islands"][0]["created_at"].endswith("+00:00")

    founded = await client.post(
        "/islands", data=json.dumps({"name": "Rocky Cove", "plan": "quick_fixes"}), content_type="application/json"
    )
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


async def test_settings_endpoint_saves_and_hides_secrets(fake_runtime, monkeypatch):
    import keyring

    from mii import prefs

    store: dict = {}
    monkeypatch.setattr(keyring, "get_password", lambda service, name: store.get(name))
    monkeypatch.setattr(keyring, "set_password", lambda service, name, value: store.__setitem__(name, value))
    monkeypatch.setattr(keyring, "delete_password", lambda service, name: store.pop(name))
    for env_name in prefs.SECRETS.values():
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("TEST_COMMAND", raising=False)
    client = AsyncClient()

    before = (await client.get("/settings")).json()
    assert before["secrets"]["openrouter_api_key"] is False

    saved = await client.put(
        "/settings",
        data=json.dumps({"settings": {"test_command": "make test"}, "secrets": {"openrouter_api_key": "sk-or-1"}}),
        content_type="application/json",
    )
    assert saved.status_code == 200 and saved.json() == {"reloaded": True}  # the runtime re-read its settings
    after = (await client.get("/settings")).json()
    assert after["settings"]["test_command"] == "make test" and after["secrets"]["openrouter_api_key"] is True
    assert "sk-or-1" not in json.dumps(after)

    bad = await client.put(
        "/settings", data=json.dumps({"settings": {"mock_agents": "no"}}), content_type="application/json"
    )
    assert bad.status_code == 400
    assert (await client.post("/settings")).status_code == 405


async def test_meta_lists_the_vocabulary():
    data = (await AsyncClient().get("/meta")).json()
    assert data["jobs"] == ["planner", "coder", "reviewer"]
    assert (
        set(data["traits"]) == {"bluntness", "caution", "chattiness", "creativity"}
        and data["traits"]["caution"]["high"]
    )
    assert "openrouter" in data["brains"] and "mock" in data["brains"]
    assert set(data["gifts"]) == {"browser", "github", "docs", "database", "terminal", "godot", "video"}
    assert data["keys"]["values"] == ["yes", "ask", "no"] and data["keys"]["defaults"]["read"] == "yes"
    assert len(data["plans"]["classic"]["steps"]) == 5 and data["building_types"] == [
        "mailbox",
        "workshop",
        "lab",
        "courthouse",
        "post_office",
    ]
    assert data["bubble_kinds"] == ["confused", "struggling", "permission", "ready", "tired"]
    assert data["weather"] == ["sunny", "cloudy", "rainy", "night"]


async def test_snapshot_has_server_time_weather_and_mode(island, fake_runtime):
    data = (await AsyncClient().get("/islands/sunny")).json()
    assert data["server_time"].endswith("+00:00") and data["weather"] == "sunny" and data["mock_agents"] is True
    assert data["residents"][0]["activity"] == "" and data["residents"][0]["appearance"] == {}


async def test_patch_island_links_repo_and_broadcasts(island, fake_runtime, sandbox):
    import subprocess

    from conftest import FakeClient

    await Island.objects.filter(pk="sunny").aupdate(repo_path=str(sandbox.repo_path))
    watcher = FakeClient()
    fake_runtime.bus.add_client(watcher, "sunny")
    client = AsyncClient()

    changed = await client.patch(
        "/islands/sunny",
        data=json.dumps({"name": "Sunnier Isle", "repo": "me/sunny", "rules": {"shell": True}}),
        content_type="application/json",
    )
    assert changed.status_code == 200, changed.content
    assert (
        changed.json()["name"] == "Sunnier Isle"
        and changed.json()["repo"] == "me/sunny"
        and changed.json()["rules"]["shell"] is True
    )
    assert watcher.of_type("island")[0]["island"]["repo"] == "me/sunny"
    snap = (await client.get("/islands/sunny")).json()
    assert snap["name"] == "Sunnier Isle" and snap["repo"] == "me/sunny" and snap["branch"] == "main"
    origin = subprocess.run(
        ["git", "-C", str(sandbox.repo_path), "remote", "get-url", "origin"], capture_output=True, text=True
    )
    assert origin.stdout.strip() == "https://github.com/me/sunny.git"

    assert (
        await client.patch("/islands/sunny", data=json.dumps({"repo": "nope"}), content_type="application/json")
    ).status_code == 400
    assert (
        await client.patch("/islands/sunny", data=json.dumps({}), content_type="application/json")
    ).status_code == 400
    assert (
        await client.patch("/islands/nowhere", data=json.dumps({"name": "x"}), content_type="application/json")
    ).status_code == 404


async def test_patch_island_refuses_a_branch_the_repo_lacks(island, fake_runtime, sandboxes):
    from mii.models import Island as IslandModel
    from mii.repos import current_branch

    client = AsyncClient()
    assert (
        await client.patch("/islands/sunny", data=json.dumps({"branch": "develpo"}), content_type="application/json")
    ).status_code == 400
    have = current_branch(__import__("pathlib").Path((await IslandModel.objects.aget(pk="sunny")).repo_path))
    ok = await client.patch("/islands/sunny", data=json.dumps({"branch": have}), content_type="application/json")
    assert ok.status_code == 200 and ok.json()["branch"] == have


async def test_island_issues_endpoint(island, fake_runtime, monkeypatch):
    from mii import github

    client = AsyncClient()
    assert (await client.get("/islands/sunny/issues")).status_code == 400  # no repo linked
    await Island.objects.filter(pk="sunny").aupdate(repo="me/sunny")
    monkeypatch.setattr(
        github,
        "list_issues",
        lambda repo, limit: [
            {"number": 7, "title": "Typo", "labels": ["bug"], "updated_at": None, "repo": repo, "limit": limit}
        ],
    )
    data = (await client.get("/islands/sunny/issues?limit=5")).json()
    assert data["issues"][0]["number"] == 7 and data["issues"][0]["limit"] == 5
    assert (await client.get("/islands/sunny/issues?limit=x")).status_code == 400
    assert (await client.get("/islands/nowhere/issues")).status_code == 404

    def boom(repo, limit):
        raise github.GitHubError("not logged in")

    monkeypatch.setattr(github, "list_issues", boom)
    assert (await client.get("/islands/sunny/issues")).status_code == 503


async def test_brain_models(fake_runtime, monkeypatch):
    from mii import views
    from mii.agents.acp import BrainError

    async def fake_list(brain, refresh=False):
        if brain == "gemini":
            raise BrainError("gemini isn't logged in: run `gemini`")
        return {"models": [{"id": "opus", "name": "Opus", "description": None}], "current": "default"}

    monkeypatch.setattr(views, "list_brain_models", fake_list)
    client = AsyncClient()
    ok = await client.get("/brains/claude-code/models")
    assert ok.status_code == 200 and ok.json()["models"][0]["id"] == "opus" and ok.json()["current"] == "default"
    assert (await client.get("/brains/gemini/models")).status_code == 503
    assert (await client.get("/brains/mock/models")).status_code == 404
    assert (await client.get("/brains/nope/models")).status_code == 404
