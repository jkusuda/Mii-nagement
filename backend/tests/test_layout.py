import pytest
from asgiref.sync import sync_to_async
from conftest import db_test

from mii import layout
from mii.engine import IslandGraph
from mii.models import Building, Resident, Road, Step, Task

pytestmark = db_test


def plan(*types, roads=(), **overrides):
    buildings = [
        {"id": t, "type": t, "name": t.title(), "x": i, "y": 0, "resident": overrides.get(t)}
        for i, t in enumerate(types)
    ]
    return {"buildings": buildings, "roads": [{"from": a, "to": b, "condition": c} for a, b, c in roads]}


async def test_layout_updates_removes_and_creates_buildings(island):
    await Step.objects.acreate(
        island_id="sunny",
        task_id="t",
        building_id="workshop",
        building_type="workshop",
        resident_id="carlos",
        brain="mock",
        outcome="pass",
    )
    data = plan(
        "mailbox",
        "workshop",
        "post_office",
        roads=[("mailbox", "workshop", "always"), ("workshop", "post_office", "always")],
        mailbox="jun",
        workshop="carlos",
        post_office="jun",
    )
    data["buildings"][1]["name"] = "Big Shed"
    data["buildings"].append({"type": "lab", "name": "Lab 2", "x": 9, "y": 9})  # no id: the server picks one

    saved = await sync_to_async(layout.save_layout)("sunny", *layout.clean_layout("sunny", data))

    assert [b["id"] for b in saved["buildings"]] == ["mailbox", "post_office", "sunny_lab_3", "workshop"]
    assert (await Building.objects.aget(pk="workshop")).name == "Big Shed"
    assert not await Building.objects.filter(pk="courthouse").aexists()
    assert await Road.objects.filter(island_id="sunny").acount() == 2
    assert await Step.objects.filter(building_id="workshop").aexists()  # history survives
    assert (await Resident.objects.aget(pk="sydney")).building_id == "mailbox"  # the courthouse is gone: rehomed
    graph = await IslandGraph.load("sunny")
    assert graph.next_building("workshop", "pass") == "post_office"


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"buildings": "x"}, "lists"),
        (plan("workshop"), "mailbox"),
        (plan("mailbox", "mailbox"), "unique"),
        (plan("mailbox", "castle"), "type"),
        (plan("mailbox", "lab", roads=[("mailbox", "nowhere", "always")]), "building ids"),
        (plan("mailbox", "lab", roads=[("mailbox", "lab", "maybe")]), "condition"),
        (plan("mailbox", "lab", roads=[("lab", "lab", "always")]), "same building"),
        ({"buildings": [{"type": "mailbox", "name": "x" * 31, "x": 0, "y": 0}]}, "name"),
        ({"buildings": [{"type": "mailbox", "x": "left", "y": 0}]}, "whole numbers"),
    ],
)
async def test_layout_rejects_bad_plans(island, bad, message):
    with pytest.raises(layout.LayoutError, match=message):
        layout.clean_layout("sunny", bad)


async def test_layout_rejects_unknown_residents_and_busy_buildings(island):
    with pytest.raises(layout.LayoutError, match="no resident"):
        await sync_to_async(layout.save_layout)(
            "sunny", *layout.clean_layout("sunny", plan("mailbox", mailbox="ghost"))
        )
    await Task.objects.acreate(id="busy", island_id="sunny", title="Busy", status="in_progress", building_id="lab")
    with pytest.raises(layout.LayoutError, match="package is at lab"):
        await sync_to_async(layout.save_layout)("sunny", *layout.clean_layout("sunny", plan("mailbox")))
    assert await Building.objects.filter(pk="lab").aexists()  # nothing changed
