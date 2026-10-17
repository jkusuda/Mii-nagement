"""Build mode: change an island's buildings and roads (`set_layout`, docs/protocol.md).

The game sends the whole layout (every building and road). Buildings keep
their ids, so a building that's already there is updated in place (its resident
and its Step history survive); buildings left out are removed; new ones are
created. Roads are replaced. The engine loads the graph when a task starts, so
a change applies to the next package.
"""

import re

from django.db import transaction

from mii.engine import HANDLERS
from mii.models import Building, Resident, Road, Task, building_to_dict, road_to_dict

CONDITIONS = ("always", "pass", "fail")
MAX_NAME = 30
BUILDING_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class LayoutError(ValueError):
    """The layout can't be used. The message goes back as an `error` event (or a 400)."""


def clean_layout(island_id: str, data: dict) -> tuple[list[dict], list[dict]]:
    """Check a layout and return (buildings, roads) as plain dicts. Raises LayoutError."""
    buildings = data.get("buildings")
    roads = data.get("roads", [])
    if not isinstance(buildings, list) or not isinstance(roads, list):
        raise LayoutError("`buildings` and `roads` must be lists")
    clean_buildings: list[dict] = []
    for i, b in enumerate(buildings):
        if not isinstance(b, dict) or str(b.get("type", "")) not in HANDLERS:
            raise LayoutError(f"building {i}: type must be one of {list(HANDLERS)}")
        btype = str(b["type"])
        bid = str(b.get("id") or f"{island_id}_{btype}_{i}")
        if not BUILDING_ID.match(bid):
            raise LayoutError(f"building {i}: bad id {bid!r}")
        name = str(b.get("name") or btype.replace("_", " ").title()).strip()
        if not 1 <= len(name) <= MAX_NAME:
            raise LayoutError(f"building {bid}: name must be 1-{MAX_NAME} characters")
        try:
            x, y = int(b.get("x", 0)), int(b.get("y", 0))
        except (TypeError, ValueError):
            raise LayoutError(f"building {bid}: x and y must be whole numbers") from None
        resident = b.get("resident")
        clean_buildings.append({"id": bid, "type": btype, "name": name, "x": x, "y": y,
                                "resident": str(resident) if resident else None})
    ids = [b["id"] for b in clean_buildings]
    if len(set(ids)) != len(ids):
        raise LayoutError("building ids must be unique")
    if sum(b["type"] == "mailbox" for b in clean_buildings) != 1:
        raise LayoutError("an island needs exactly one mailbox (packages arrive there)")

    clean_roads: list[dict] = []
    for i, r in enumerate(roads):
        if not isinstance(r, dict):
            raise LayoutError(f"road {i}: must be an object")
        a, b, condition = str(r.get("from", "")), str(r.get("to", "")), str(r.get("condition", "always"))
        if a not in ids or b not in ids:
            raise LayoutError(f"road {i}: from/to must be building ids on this island")
        if condition not in CONDITIONS:
            raise LayoutError(f"road {i}: condition must be one of {list(CONDITIONS)}")
        if a == b:
            raise LayoutError(f"road {i}: a road can't lead back to the same building")
        clean_roads.append({"from": a, "to": b, "condition": condition})
    return clean_buildings, clean_roads


def save_layout(island_id: str, buildings: list[dict], roads: list[dict]) -> dict:
    """Apply a cleaned layout. Sync (one transaction): async callers use sync_to_async."""
    with transaction.atomic():
        residents = set(Resident.objects.filter(island_id=island_id).values_list("id", flat=True))
        for b in buildings:
            if b["resident"] and b["resident"] not in residents:
                raise LayoutError(f"building {b['id']}: no resident {b['resident']!r} on this island")
        keep = {b["id"] for b in buildings}
        busy = Task.objects.filter(island_id=island_id, status="in_progress", building_id__isnull=False)
        for task in busy.exclude(building_id__in=keep):
            raise LayoutError(f"a package is at {task.building_id} right now; wait for it to move on")
        # Residents live where they work: anyone whose home is being removed moves to the mailbox.
        mailbox = next(b["id"] for b in buildings if b["type"] == "mailbox")
        homeless = list(Resident.objects.filter(island_id=island_id).exclude(building_id__in=keep)
                        .exclude(building_id=None).values_list("id", flat=True))
        Building.objects.filter(island_id=island_id).exclude(id__in=keep).delete()  # roads cascade
        for b in buildings:
            Building.objects.update_or_create(
                id=b["id"], defaults={"island_id": island_id, "type": b["type"], "name": b["name"],
                                      "x": b["x"], "y": b["y"], "resident_id": b["resident"]},
            )
        Road.objects.filter(island_id=island_id).delete()
        Road.objects.bulk_create([
            Road(island_id=island_id, from_building_id=r["from"], to_building_id=r["to"], condition=r["condition"])
            for r in roads
        ])
        Resident.objects.filter(id__in=homeless).update(building_id=mailbox)
        return {
            "buildings": [building_to_dict(b) for b in Building.objects.filter(island_id=island_id).order_by("id")],
            "roads": [road_to_dict(r) for r in Road.objects.filter(island_id=island_id).order_by("id")],
        }


def assign_buildings(island_id: str, resident_id: str, building_ids: list) -> list[str]:
    """Make a resident the worker at these buildings (the creator's "works at"). Sync. Returns the ids."""
    if not isinstance(building_ids, list):
        raise LayoutError("buildings must be a list of building ids")
    wanted = [str(b) for b in building_ids]
    known = {b.id for b in Building.objects.filter(island_id=island_id)}
    unknown = [b for b in wanted if b not in known]
    if unknown:
        raise LayoutError(f"no building {unknown[0]!r} on this island")
    Building.objects.filter(island_id=island_id, id__in=wanted).update(resident_id=resident_id)
    return wanted
