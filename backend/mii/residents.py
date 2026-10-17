"""Creating and editing residents (`create_resident` / `update_resident`).

The resident creator in the game sends these over the WebSocket. A new resident
moves into their job's buildings (mailbox / workshop / courthouse) and takes
over the ones nobody works at yet. `buildings` (a list of building ids) makes
them the worker at those buildings outright, taking over from whoever was there:
the engine always uses the resident each building points at (Building.resident).
"""

import re
import uuid

from asgiref.sync import sync_to_async
from django.db.models import Q

from mii.agents.acp import GIFTS
from mii.layout import LayoutError, assign_buildings
from mii.models import DEFAULT_KEYS, Building, Friendship, Resident
from mii.personality import JOBS
from mii.vocab import BRAIN_NAMES, JOB_BUILDINGS

SLIDERS = ("bluntness", "caution", "chattiness", "creativity")
KEY_VALUES = ("yes", "ask", "no")
MAX_RESIDENT_NAME = 20
MAX_APPEARANCE_KEYS = 24
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ResidentError(ValueError):
    """The client sent something we can't use. The message goes back as an `error` event."""


def clean_fields(data: dict, partial: bool) -> dict:
    """Check the editable fields and return only those that were sent.

    partial=False (create) requires a name. Sliders are clamped to 0-1.
    """
    out: dict = {}
    if "name" in data or not partial:
        name = str(data.get("name", "")).strip()
        if not 1 <= len(name) <= MAX_RESIDENT_NAME:
            raise ResidentError(f"name must be 1-{MAX_RESIDENT_NAME} characters")
        out["name"] = name
    if "job" in data:
        if str(data["job"]) not in JOBS:
            raise ResidentError(f"unknown job: {data['job']}")
        out["job"] = data["job"]
    if "brain" in data:
        if str(data["brain"]) not in BRAIN_NAMES:
            raise ResidentError(f"unknown brain: {data['brain']}")
        out["brain"] = data["brain"]
    if "model" in data:
        model = str(data["model"] or "").strip()
        out["model"] = model or None  # blank = the brain's default
    if "catchphrase" in data:
        out["catchphrase"] = str(data["catchphrase"]).strip()[:60]
    if "color" in data:
        if not HEX_COLOR.match(str(data["color"])):
            raise ResidentError(f"color must look like #RRGGBB, got {data['color']}")
        out["color"] = str(data["color"]).upper()
    for slider in SLIDERS:
        if slider in data:
            try:
                out[slider] = min(max(float(data[slider]), 0.0), 1.0)
            except (TypeError, ValueError):
                raise ResidentError(f"{slider} must be a number") from None
    if "keys" in data:
        keys = data["keys"]
        if not isinstance(keys, dict) or any(k not in DEFAULT_KEYS or v not in KEY_VALUES for k, v in keys.items()):
            raise ResidentError(f"keys must map {list(DEFAULT_KEYS)} to one of {list(KEY_VALUES)}")
        out["keys"] = {**DEFAULT_KEYS, **keys}
    if "master_key" in data:
        if not isinstance(data["master_key"], bool):
            raise ResidentError("master_key must be true or false")
        out["master_key"] = data["master_key"]
    if "gifts" in data:
        gifts = data["gifts"]
        if not isinstance(gifts, list) or any(g not in GIFTS for g in gifts):
            raise ResidentError(f"gifts must be a list of {list(GIFTS)}")
        out["gifts"] = list(dict.fromkeys(gifts))  # drop duplicates, keep order
    if "appearance" in data:
        out["appearance"] = clean_appearance(data["appearance"])
    return out


def clean_appearance(look) -> dict:
    """The Mii's look. The game owns its meaning, so we only check it's small and flat:
    a dict of short keys to numbers, booleans, or short strings (colours like #RRGGBB)."""
    if not isinstance(look, dict) or len(look) > MAX_APPEARANCE_KEYS:
        raise ResidentError(f"appearance must be an object with at most {MAX_APPEARANCE_KEYS} fields")
    for key, value in look.items():
        ok = isinstance(value, (bool, int, float)) or (isinstance(value, str) and len(value) <= 20)
        if len(key) > 20 or not ok:
            raise ResidentError(f"appearance.{key[:20]} must be a number, true/false, or a short string")
    return dict(look)


async def _work_at(island_id: str, resident: Resident, data: dict) -> None:
    if "buildings" not in data:
        return
    try:
        await sync_to_async(assign_buildings)(island_id, resident.id, data["buildings"])
    except LayoutError as e:
        raise ResidentError(str(e)) from None


async def create_resident(island_id: str, data: dict) -> Resident:
    fields = clean_fields(data, partial=False)
    job = fields.setdefault("job", "coder")
    slug = re.sub(r"[^a-z0-9]+", "_", fields["name"].lower()).strip("_") or "resident"
    buildings = [b async for b in Building.objects.filter(island_id=island_id).order_by("id")]
    mine = [b for t in JOB_BUILDINGS[job] for b in buildings if b.type == t]
    home = mine[0] if mine else (buildings[0] if buildings else None)
    resident = await Resident.objects.acreate(
        id=f"{slug}_{uuid.uuid4().hex[:4]}", island_id=island_id, building_id=home.id if home else None, **fields
    )
    for b in mine:
        if b.resident_id is None:
            b.resident_id = resident.id
            await b.asave(update_fields=["resident_id"])
    await _work_at(island_id, resident, data)
    return resident


async def update_resident(island_id: str, data: dict) -> Resident:
    resident = await Resident.objects.filter(pk=str(data.get("id", "")), island_id=island_id).afirst()
    if resident is None:
        raise ResidentError(f"unknown resident id: {data.get('id')}")
    changes = clean_fields(data, partial=True)
    for field, value in changes.items():
        setattr(resident, field, value)
    if changes:
        await resident.asave(update_fields=list(changes))
    await _work_at(island_id, resident, data)
    return resident


async def delete_resident(island_id: str, resident_id: str) -> Resident:
    """The resident moves away. Raises ResidentError if unknown, or if a building still counts on them."""
    resident = await Resident.objects.filter(pk=resident_id, island_id=island_id).afirst()
    if resident is None:
        raise ResidentError(f"no resident {resident_id!r} on this island")
    if resident.state in ("working", "walking"):
        raise ResidentError(f"{resident.name} is carrying a package; wait or cancel the task first")
    where = [b.name async for b in Building.objects.filter(island_id=island_id, resident_id=resident_id)]
    if where:
        raise ResidentError(f"{resident.name} runs the {where[0]}; give it to someone else first (their 'Works at')")
    await Friendship.objects.filter(island_id=island_id).filter(Q(a=resident_id) | Q(b=resident_id)).adelete()
    await resident.adelete()  # tasks they carried keep their history (agent becomes null), Step rows stay
    return resident
