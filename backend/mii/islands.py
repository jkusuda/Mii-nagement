"""Founding islands (`POST /islands`), from the game's island creator.

A new island gets the buildings and roads of a town plan. It starts with no
residents: the creator makes them next (create_resident), and each one moves
into the buildings for their job (residents.py).
"""

import re

from sqlmodel import Session

from mii.models import Building, Island, Road

# plan -> (buildings [(type, name, x, y)], roads [(from type, to type, condition)])
TOWN_PLANS = {
    # The full workflow, like the seed island.
    "classic": (
        [("mailbox", "Mailbox", 4, 15), ("workshop", "Workshop", 12, 7), ("lab", "Lab", 20, 15),
         ("courthouse", "Courthouse", 28, 7), ("post_office", "Post Office", 35, 15)],
        [("mailbox", "workshop", "always"), ("workshop", "lab", "always"), ("lab", "courthouse", "pass"),
         ("lab", "workshop", "fail"), ("courthouse", "post_office", "pass"), ("courthouse", "workshop", "fail")],
    ),
    # Small chores, no tests or review stop.
    "quick_fixes": (
        [("mailbox", "Mailbox", 4, 15), ("workshop", "Workshop", 12, 7), ("post_office", "Post Office", 35, 15)],
        [("mailbox", "workshop", "always"), ("workshop", "post_office", "always")],
    ),
}
RULES = ("merge_pr", "install_packages", "shell")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_NAME = 30


class IslandError(ValueError):
    """The request can't make an island. The message goes back as a 400."""


def create_island(session: Session, data: dict) -> Island:
    name = str(data.get("name", "")).strip()
    if not 1 <= len(name) <= MAX_NAME:
        raise IslandError(f"name must be 1-{MAX_NAME} characters")
    repo = str(data.get("repo") or "").strip() or None
    if repo and not REPO.match(repo):
        raise IslandError(f"repo must look like owner/name, got {repo}")
    branch = str(data.get("branch") or "main").strip() or "main"
    plan = str(data.get("plan", "classic"))
    if plan not in TOWN_PLANS:
        raise IslandError(f"unknown town plan: {plan} (try {', '.join(TOWN_PLANS)})")
    rules = data.get("rules", {})
    if not isinstance(rules, dict) or any(k not in RULES or not isinstance(v, bool) for k, v in rules.items()):
        raise IslandError(f"rules must map {list(RULES)} to true/false")

    # A readable id from the name, made unique ("fern_harbor", "fern_harbor_2").
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "island"
    island_id, n = base, 1
    while session.get(Island, island_id) is not None:
        n += 1
        island_id = f"{base}_{n}"

    island = Island(id=island_id, name=name, repo=repo, branch=branch, plan=plan,
                    rules={rule: bool(rules.get(rule, False)) for rule in RULES})
    session.add(island)
    buildings, roads = TOWN_PLANS[plan]
    for btype, bname, x, y in buildings:  # building ids are unique across islands
        session.add(Building(id=f"{island_id}_{btype}", island_id=island_id, type=btype, name=bname, x=x, y=y))
    session.commit()  # buildings first: roads point at them
    for a, b, condition in roads:
        session.add(Road(island_id=island_id, from_id=f"{island_id}_{a}", to_id=f"{island_id}_{b}", condition=condition))
    session.commit()
    session.refresh(island)
    return island
