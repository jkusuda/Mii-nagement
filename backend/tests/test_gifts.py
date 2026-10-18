"""Gifts (gifts.py): built-in MCP servers, the gift shop (the MCP Registry), and the `tools` house key."""

import asyncio
import json
import shutil

import pytest
from conftest import RecordingAsk, db_test, make_job
from django.test import AsyncClient

from mii import gifts, prefs, residents
from mii.agents.acp import KEY_FOR_KIND, _gifts, _gifts_left_home
from mii.agents.mock import MockRunner
from mii.gifts import GIFTS, available_gifts, gift_in, gift_servers, key_for
from mii.models import DEFAULT_KEYS, Resident, resident_to_dict

pytestmark = db_test

# One registry entry we can run (npm, stdio, an API key) and two we can't (remote only, a Docker image).
CONTEXT7 = {
    "name": "io.github.upstash/context7",
    "title": "Context7",
    "description": "Up-to-date code docs",
    "version": "4.4.2",
    "websiteUrl": "https://context7.com",
    "packages": [
        {
            "registryType": "npm",
            "identifier": "@upstash/context7-mcp",
            "version": "4.4.2",
            "transport": {"type": "stdio"},
            "environmentVariables": [{"name": "CONTEXT7_API_KEY", "isRequired": True, "isSecret": True}],
        }
    ],
}
REMOTE = {"name": "com.example/remote", "remotes": [{"type": "streamable-http", "url": "https://x"}]}
DOCKER = {"name": "com.example/docker", "packages": [{"registryType": "oci", "identifier": "x/y"}]}


@pytest.fixture
def registry(monkeypatch):
    """A fake MCP Registry, and a fake keychain for the values added gifts need."""
    calls = []

    async def fake(path: str, params: dict | None = None) -> dict:
        calls.append((path, params))
        if path == "/servers":
            return {"servers": [{"server": s} for s in (CONTEXT7, REMOTE, DOCKER)]}
        if path.endswith("context7/versions/latest"):
            return {"server": CONTEXT7}
        return {"server": REMOTE}

    store: dict = {}
    monkeypatch.setattr(gifts, "_registry", fake)
    monkeypatch.setattr(prefs, "gift_env", lambda gift, name: store.get((gift, name)))
    monkeypatch.setattr(prefs, "set_gift_env", lambda gift, name, value: store.__setitem__((gift, name), value))
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    return calls


def test_built_in_gifts_and_what_is_missing(monkeypatch):
    assert set(GIFTS) == {"browser", "github", "docs", "database", "terminal"}
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert all(not g["ok"] and "install" in g["hint"] for g in available_gifts().values())
    servers, skipped = gift_servers(["docs", "jetpack"])
    assert servers == [] and len(skipped) == 2 and "Docs" in skipped[0]
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(prefs, "extra_env", lambda name: None)
    assert available_gifts()["docs"]["ok"] is True
    assert "DATABASE_URL" in available_gifts()["database"]["hint"]
    [docs], _ = gift_servers(["docs"])
    assert docs.name == "docs" and docs.command == "/usr/bin/npx" and docs.args[-1] == "@upstash/context7-mcp"


def test_a_needed_setting_reaches_the_server(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(prefs, "extra_env", lambda name: "sqlite:///x.db" if name == "DATABASE_URL" else None)
    [db], skipped = gift_servers(["database"])
    assert skipped == [] and db.args[-1] == "sqlite:///x.db"
    assert [(e.name, e.value) for e in db.env] == [("DATABASE_URL", "sqlite:///x.db")]


async def test_the_gift_shop_finds_adds_and_removes(island, registry):
    found = await gifts.search("docs")
    assert [s["name"] for s in found] == ["io.github.upstash/context7"]  # remote-only and Docker are left out
    assert found[0]["runner"] == "npx" and found[0]["env"][0]["required"] and not found[0]["added"]

    gift, spec = await gifts.add("io.github.upstash/context7")
    assert gift == "context7" and spec["command"] == ["npx", "-y", "@upstash/context7-mcp@4.4.2"]
    assert (await gifts.add("io.github.upstash/context7"))[0] == "context7"  # added once
    assert (await gifts.search("docs"))[0]["added"]
    with pytest.raises(gifts.GiftError):
        await gifts.add("com.example/remote")

    info = (await asyncio.to_thread(available_gifts))["context7"]
    assert not info["ok"] and "CONTEXT7_API_KEY" in info["hint"] and info["custom"]
    with pytest.raises(gifts.GiftError):
        await gifts.set_env("context7", {"OTHER": "x"})
    await gifts.set_env("context7", {"CONTEXT7_API_KEY": "k"})
    info = (await asyncio.to_thread(available_gifts))["context7"]
    assert info["ok"] and info["env"][0]["set"]
    [server], _ = await asyncio.to_thread(gift_servers, ["context7"])
    assert server.args[-1] == "@upstash/context7-mcp@4.4.2" and server.env[0].value == "k"

    # Residents can be given it, and lose it when it goes.
    jun = await residents.update_resident("sunny", {"id": "jun", "gifts": ["context7", "docs"]})
    assert jun.gifts == ["context7", "docs"]
    assert await gifts.remove("context7") == ["sunny"]
    assert (await Resident.objects.aget(pk="jun")).gifts == ["docs"]
    with pytest.raises(residents.ResidentError):
        await residents.update_resident("sunny", {"id": "jun", "gifts": ["context7"]})
    with pytest.raises(gifts.GiftError):
        await gifts.remove("docs")  # built-in gifts stay


async def test_gift_endpoints(island, registry):
    client = AsyncClient()
    assert (await client.get("/gifts/search")).status_code == 400
    found = (await client.get("/gifts/search?q=docs")).json()["servers"]
    assert found[0]["label"] == "Context7"
    added = await client.post("/gifts", json.dumps({"name": found[0]["name"]}), content_type="application/json")
    assert added.status_code == 200 and added.json()["id"] == "context7" and "context7" in added.json()["gifts"]
    put = await client.put("/gifts/context7", json.dumps({"env": {"CONTEXT7_API_KEY": "k"}}), "application/json")
    assert put.json()["context7"]["ok"]
    assert (await client.put("/gifts/context7", json.dumps({"env": 1}), "application/json")).status_code == 400
    assert "context7" not in (await client.delete("/gifts/context7")).json()
    assert (await client.delete("/gifts/context7")).status_code == 400


def test_each_gift_follows_a_house_key():
    assert KEY_FOR_KIND["other"] == "tools" and DEFAULT_KEYS["tools"] == "ask"
    assert [key_for(g) for g in ("browser", "github", "docs", "database", "terminal", "context7")] == [
        "fetch",
        "fetch",
        "fetch",
        "execute",
        "execute",
        "tools",  # added from the gift shop
    ]
    assert gift_in("mcp__browser__browser_navigate", ["docs", "browser"]) == "browser"
    assert gift_in("query-docs (docs MCP Server)", ["docs"]) == "docs"
    assert gift_in("mcp__docsearch__x", ["docs"]) is None


def test_a_gift_stays_home_when_its_key_says_no():
    job = make_job("claude-code")
    job.resident.gifts = ["browser", "terminal"]
    job.resident.keys = {**DEFAULT_KEYS, "fetch": "no", "execute": "ask"}
    assert _gifts(job) == ("terminal",)
    assert "Browser gift at home" in _gifts_left_home(job)[0]
    job.resident.keys["master"] = "yes"  # the master key opens every door
    assert _gifts(job) == ("browser", "terminal")


def test_old_residents_get_every_house_key():
    keys = resident_to_dict(Resident(id="old", name="Old", keys={"read": "no"}))["keys"]
    assert keys == {**DEFAULT_KEYS, "read": "no"}


async def test_mock_residents_use_gifts_as_their_house_key_says():
    runner = MockRunner(work_seconds=(0, 0), bubble_chance=0)
    runner.GIFT_CHANCE = 1.0
    for key, asked, note in (("yes", 0, "Used my Docs"), ("no", 0, "Left my Docs"), ("ask", 1, "Used my Docs")):
        job = make_job("mock", ask=RecordingAsk())
        job.resident.gifts, job.resident.keys = ["docs"], {**DEFAULT_KEYS, "fetch": key}  # Docs uses the internet
        result = await runner.run(job)
        assert len(job.ask.asked) == asked and note in result.summary


async def test_mock_permission_bubbles_follow_the_house_keys():
    runner = MockRunner(work_seconds=(0, 0), bubble_chance=1.0)
    runner.rng.choice = lambda items: "permission" if "permission" in items else items[-1]  # always: delete a file
    job = make_job("mock", building_type="workshop", ask=RecordingAsk())
    job.resident.keys = {**DEFAULT_KEYS, "edit": "yes"}
    assert "house key let me" in (await runner.run(job)).summary and job.ask.asked == []
    job.resident.keys = {**DEFAULT_KEYS, "master": "yes", "edit": "no"}
    assert "house key let me" in (await runner.run(job)).summary  # the master key opens every door
    job.resident.keys = DEFAULT_KEYS
    await runner.run(job)
    assert job.ask.asked[0][0] == "permission"
