"""Creating and editing residents (`create_resident` / `update_resident`).

The resident creator in the game sends these. New residents are placeholders
for now: they're saved and shown in the game, but the workflow engine keeps
using the residents assigned to buildings (Building.resident_id).
"""

import re
import uuid

from sqlmodel import Session

from mii.agents.acp import BRAINS
from mii.models import DEFAULT_KEYS, Resident
from mii.personality import JOBS

# Where a new resident lives, by job.
JOB_HOMES = {"planner": "mailbox", "coder": "workshop", "reviewer": "courthouse"}
SLIDERS = ("bluntness", "caution", "chattiness", "creativity")
KEY_VALUES = ("yes", "ask", "no")
GIFTS = ("browser", "github", "docs", "database", "terminal")  # TODO(mvp): an MCP server table, like BRAINS
MAX_NAME = 20
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
        if not 1 <= len(name) <= MAX_NAME:
            raise ResidentError(f"name must be 1-{MAX_NAME} characters")
        out["name"] = name
    if "job" in data:
        if str(data["job"]) not in JOBS:
            raise ResidentError(f"unknown job: {data['job']}")
        out["job"] = data["job"]
    if "brain" in data:
        if str(data["brain"]) != "mock" and str(data["brain"]) not in BRAINS:
            raise ResidentError(f"unknown brain: {data['brain']}")
        out["brain"] = data["brain"]
    if "model" in data:
        model = str(data["model"] or "").strip()
        out["model"] = model or None  # blank = the agent's default
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
    if "gifts" in data:
        gifts = data["gifts"]
        if not isinstance(gifts, list) or any(g not in GIFTS for g in gifts):
            raise ResidentError(f"gifts must be a list of {list(GIFTS)}")
        out["gifts"] = list(dict.fromkeys(gifts))  # drop duplicates, keep order
    return out


def create_resident(session: Session, island_id: str, data: dict) -> Resident:
    fields = clean_fields(data, partial=False)
    job = fields.setdefault("job", "coder")
    slug = re.sub(r"[^a-z0-9]+", "_", fields["name"].lower()).strip("_") or "resident"
    resident = Resident(
        id=f"{slug}_{uuid.uuid4().hex[:4]}",
        island_id=island_id,
        building_id=JOB_HOMES[job],
        **fields,
    )
    session.add(resident)
    session.commit()
    session.refresh(resident)
    return resident


def update_resident(session: Session, island_id: str, data: dict) -> Resident:
    resident = session.get(Resident, str(data.get("id", "")))
    if resident is None or resident.island_id != island_id:
        raise ResidentError(f"unknown resident id: {data.get('id')}")
    for field, value in clean_fields(data, partial=True).items():
        setattr(resident, field, value)
    session.add(resident)
    session.commit()
    session.refresh(resident)
    return resident
