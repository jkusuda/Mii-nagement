"""Island maps: workflow steps -> roads, shape presets, validation, build mode saves."""

import copy
import json
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async
from conftest import ScriptedRunner, db_test, make_workflow
from django.test import AsyncClient

from mii import islands, views
from mii.models import Building, Island, Resident, Road, Task

pytestmark = db_test

CLASSIC_ROADS = [
    ("mailbox", "workshop", "always"), ("workshop", "lab", "always"), ("lab", "courthouse", "pass"),
    ("lab", "workshop", "fail"), ("courthouse", "post_office", "pass"), ("courthouse", "workshop", "fail"),
]


def classic(steps=islands.STEP_ORDER):
    return islands.default_map("classic"), islands.default_buildings("classic", steps)


# --- Steps and roads ------------------------------------------------------------

def test_roads_for_all_steps_match_the_classic_plan():
    assert islands.build_roads(islands.STEP_ORDER) == CLASSIC_ROADS
    # The order the steps are given in doesn't matter: roads follow STEP_ORDER.
    assert islands.build_roads(reversed(islands.STEP_ORDER)) == CLASSIC_ROADS


def test_roads_for_other_step_sets():
    assert islands.build_roads(islands.PLANS["quick_fixes"]) == [
        ("mailbox", "workshop", "always"), ("workshop", "post_office", "always")]
    assert islands.build_roads(["mailbox", "workshop", "lab", "post_office"]) == [
        ("mailbox", "workshop", "always"), ("workshop", "lab", "always"),
        ("lab", "post_office", "pass"), ("lab", "workshop", "fail")]


def test_required_steps():
    with pytest.raises(islands.IslandError, match="Post Office"):
        islands.validate_steps(["mailbox", "workshop"])
    with pytest.raises(islands.IslandError, match="once"):
        islands.validate_steps(["mailbox", "workshop", "workshop", "post_office"])
    with pytest.raises(islands.IslandError):
        islands.validate_steps(["mailbox", "workshop", "post_office", "castle"])


# --- Shapes and validation ------------------------------------------------------

@pytest.mark.parametrize("shape", list(islands.SHAPES))
@pytest.mark.parametrize("plan", list(islands.PLANS))
def test_every_shape_preset_is_valid(shape, plan):
    layout, buildings = islands.validate_map(islands.default_map(shape), islands.default_buildings(shape, islands.PLANS[plan]))
    assert layout["shape"] == shape and [b["type"] for b in buildings] == list(islands.PLANS[plan])


def test_validation_rules():
    layout, buildings = classic()

    def rejects(layout, buildings, words):
        with pytest.raises(islands.IslandError, match=words):
            islands.validate_map(layout, buildings)

    bad = copy.deepcopy(layout)
    bad["land"][0] = "#" + bad["land"][0][1:]
    rejects(bad, buildings, "edge of the map")
    bad = copy.deepcopy(layout)
    bad["land"] = bad["land"][:-1]
    rejects(bad, buildings, "rows")
    split = [row[:10] + "." + row[11:] for row in layout["land"]]
    rejects({**layout, "land": split}, buildings, "one piece")
    rejects({**layout, "season": "monsoon"}, buildings, "season")
    rejects({**layout, "pond": {"x": 0, "y": 0, "w": 2, "h": 2}}, buildings, "pond must be on land")
    rejects({**layout, "plants": [{"kind": "cactus", "x": 1, "y": 1}]}, buildings, "tree or bush")

    moved = copy.deepcopy(buildings)
    moved[1]["x"] = moved[0]["x"] + 1  # workshop onto the mailbox
    rejects(layout, moved, "overlaps")
    moved = copy.deepcopy(buildings)
    moved[0]["x"] = 1  # mailbox lot touching the sea
    rejects(layout, moved, "land all round")
    onto_pond = copy.deepcopy(buildings)
    onto_pond[1].update(x=7, y=5)
    rejects(layout, onto_pond, "overlaps")
    walled = copy.deepcopy(buildings)
    walled[2].update(x=14, y=4, facing=2)  # the lab's door opens into the workshop's lot? no: into free land
    walled[0]["facing"] = 3  # mailbox door at (2, 3): free land, fine
    islands.validate_map(layout, walled)
    boxed = copy.deepcopy(buildings)
    boxed[0]["facing"] = 1  # mailbox door at (6, 3): the gap between mailbox and workshop lots
    islands.validate_map(layout, boxed)
    blocked = copy.deepcopy(buildings)
    blocked[1].update(x=6, y=2)  # workshop right next to the mailbox ...
    blocked[0]["facing"] = 1     # ... so the mailbox's east door opens into it
    rejects(layout, blocked, "door is blocked")


def test_validation_keeps_explicit_plants_and_cleans_them():
    layout, buildings = classic()
    layout["plants"] = [{"kind": "tree", "x": 2, "y": 2.5, "rot": 400, "scale": 1.2}]
    clean, _ = islands.validate_map(layout, buildings)
    assert clean["plants"] == [{"kind": "tree", "x": 2.0, "y": 2.5, "rot": 40.0, "scale": 1.2}]


# --- Founding with a shape and steps --------------------------------------------

async def test_found_island_with_shape_and_steps(island, tmp_path, monkeypatch):
    monkeypatch.setenv("ISLANDS_ROOT", str(tmp_path))
    new = await sync_to_async(islands.create_island)(
        {"name": "Twin Peaks", "shape": "twin", "steps": ["post_office", "mailbox", "workshop", "lab"], "season": "winter"})
    assert new.plan == "custom" and new.map["shape"] == "twin" and new.map["season"] == "winter"
    types = await sync_to_async(lambda: sorted(Building.objects.filter(island=new).values_list("type", flat=True)))()
    assert types == ["lab", "mailbox", "post_office", "workshop"]
    roads = await sync_to_async(lambda: [(r.from_building.type, r.to_building.type, r.condition)
                                         for r in Road.objects.filter(island=new).order_by("id")])()
    assert roads == islands.build_roads(types)

    quick = await sync_to_async(islands.create_island)({"name": "Quick", "plan": "quick_fixes"})
    assert quick.plan == "quick_fixes" and quick.map["shape"] == "classic"
    with pytest.raises(islands.IslandError, match="shape"):
        await sync_to_async(islands.create_island)({"name": "Nope", "shape": "square"})


# --- Build mode saves -----------------------------------------------------------

def _body(**changes):
    layout, buildings = classic()
    return {"map": {**layout, **changes.pop("map", {})}, "buildings": changes.pop("buildings", buildings)}


async def test_set_layout_moves_buildings_and_keeps_ids(island):
    body = _body(map={"season": "autumn"})
    body["buildings"][1].update(x=8, y=9, facing=2)  # the workshop moves to the south
    await sync_to_async(islands.set_map)("sunny", body)
    workshop = await Building.objects.aget(pk="workshop")
    assert (workshop.x, workshop.y, workshop.facing) == (8, 9, 2)
    sunny = await Island.objects.aget(pk="sunny")
    assert sunny.map["season"] == "autumn" and sunny.plan == "classic"
    assert await Road.objects.filter(island_id="sunny").acount() == 6  # same steps: roads untouched


async def test_set_layout_adds_and_removes_steps(island):
    layout, buildings = classic(islands.PLANS["quick_fixes"])
    await sync_to_async(islands.set_map)("sunny", {"map": layout, "buildings": buildings})
    assert sorted([b.type async for b in Building.objects.filter(island_id="sunny")]) == ["mailbox", "post_office", "workshop"]
    roads = [(r.from_building_id, r.to_building_id, r.condition) async for r in Road.objects.filter(island_id="sunny").order_by("id")]
    assert roads == [("mailbox", "workshop", "always"), ("workshop", "post_office", "always")]
    sydney = await Resident.objects.aget(pk="sydney")  # the reviewer lost the courthouse: no other home
    assert sydney.building_id is None
    assert (await Island.objects.aget(pk="sunny")).plan == "quick_fixes"

    # Bring the courthouse back: the reviewer gets it again.
    layout, buildings = classic()
    await sync_to_async(islands.set_map)("sunny", {"map": layout, "buildings": buildings})
    courthouse = await Building.objects.aget(island_id="sunny", type="courthouse")
    assert courthouse.resident_id == "sydney"
    assert (await Resident.objects.aget(pk="sydney")).building_id == courthouse.id
    assert await Road.objects.filter(island_id="sunny").acount() == 6


async def test_set_layout_refuses_new_steps_while_a_task_runs(island):
    await Task.objects.acreate(id="t1", island_id="sunny", title="busy", status="in_progress")
    layout, buildings = classic(islands.PLANS["quick_fixes"])
    with pytest.raises(islands.MapBusy):
        await sync_to_async(islands.set_map)("sunny", {"map": layout, "buildings": buildings})
    # Moving things around is still fine.
    await sync_to_async(islands.set_map)("sunny", _body(map={"season": "spring"}))


# --- The endpoint ---------------------------------------------------------------

@pytest.fixture
def fake_runtime(monkeypatch):
    workflow, client = make_workflow(ScriptedRunner())
    runtime = SimpleNamespace(workflow=workflow, bubbles=workflow.bubbles, bus=workflow.bus, mock_agents=True)
    monkeypatch.setattr(views, "get_runtime", lambda: runtime)
    return client


async def test_put_map_endpoint(island, fake_runtime):
    client = AsyncClient()
    body = _body(map={"season": "winter"})
    response = await client.put("/islands/sunny/layout", json.dumps(body), content_type="application/json")
    assert response.status_code == 200
    data = response.json()
    assert data["map"]["season"] == "winter" and all("facing" in b for b in data["buildings"])

    bad = _body(buildings=[])
    response = await client.put("/islands/sunny/layout", json.dumps(bad), content_type="application/json")
    assert response.status_code == 400 and "needs a" in response.json()["error"]
    response = await client.put("/islands/nowhere/layout", json.dumps(body), content_type="application/json")
    assert response.status_code == 404
    assert (await client.get("/islands/sunny/layout")).status_code == 405
