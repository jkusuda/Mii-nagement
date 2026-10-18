"""The island map: shapes, the build-mode rules, and the roads between steps.

Pure functions over plain data (no database), so models.py and the game-facing
code can use it freely. Island.map looks like:

    {"version": 1, "shape": "classic",
     "land": [GRID_H strings of GRID_W chars, "#" = land, "." = sea],
     "pond": {"x", "y", "w", "h"} in tiles, or None,
     "plants": None (the game arranges them) or [{"kind": "tree"|"bush", "x", "y", "rot", "scale"}],
     "season": "spring" | "summer" | "autumn" | "winter"}

A building stands on a LOT x LOT lot: Building.x / y is the lot's top-left tile,
Building.facing the way its door looks (0 south, 1 east, 2 north, 3 west). One
tile is 2 world units in the game, which draws the paths between the doors
(docs/protocol.md "Island map" has the rules; game/scenes/island/island_layout.gd
mirrors them).
"""

import math
from collections import deque
from itertools import pairwise

from mii.vocab import CHECK_STEPS, PLANS, REQUIRED_STEPS, SEASONS, STEP_NAMES, STEP_ORDER

GRID_W, GRID_H = 20, 14
LOT = 3
MAX_PLANTS = 150
MAX_POND = 6  # tiles per side


class IslandError(ValueError):
    """The request can't make or change an island. The message goes back as a 400 / `error` event."""


# Island shapes for the island creator: land rows, a pond, and a lot for every step.
SHAPES = {
    "classic": {
        "land": [
            "....................",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            "....................",
        ],
        "pond": {"x": 7, "y": 6, "w": 5, "h": 2},
        "lots": {
            "mailbox": (3, 2, 0),
            "workshop": (8, 2, 0),
            "lab": (13, 2, 0),
            "courthouse": (15, 8, 3),
            "post_office": (2, 8, 1),
        },
    },
    "round": {
        "land": [
            "....................",
            "......########......",
            "....############....",
            "...##############...",
            "..################..",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            "..################..",
            "...##############...",
            "....############....",
            "......########......",
            "....................",
        ],
        "pond": {"x": 9, "y": 6, "w": 3, "h": 2},
        "lots": {
            "mailbox": (5, 3, 0),
            "workshop": (8, 2, 0),
            "lab": (12, 3, 0),
            "courthouse": (12, 8, 2),
            "post_office": (5, 8, 2),
        },
    },
    "long": {
        "land": [
            "....................",
            "....................",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            "....................",
            "....................",
        ],
        "pond": {"x": 15, "y": 8, "w": 3, "h": 2},
        "lots": {
            "mailbox": (2, 3, 0),
            "workshop": (7, 3, 0),
            "lab": (12, 3, 0),
            "courthouse": (10, 8, 2),
            "post_office": (4, 8, 2),
        },
    },
    "l_shape": {
        "land": [
            "....................",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".#########..........",
            ".#########..........",
            ".#########..........",
            ".#########..........",
            ".#########..........",
            ".#########..........",
            "....................",
        ],
        "pond": {"x": 6, "y": 8, "w": 2, "h": 3},
        "lots": {
            "mailbox": (2, 2, 0),
            "workshop": (6, 2, 0),
            "lab": (10, 2, 0),
            "courthouse": (14, 2, 3),
            "post_office": (2, 8, 1),
        },
    },
    "twin": {
        "land": [
            "....................",
            ".########..########.",
            ".########..########.",
            ".########..########.",
            ".########..########.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".##################.",
            ".########..########.",
            ".########..########.",
            ".########..########.",
            ".########..########.",
            "....................",
        ],
        "pond": None,
        "lots": {
            "mailbox": (2, 2, 0),
            "workshop": (5, 2, 0),
            "lab": (12, 2, 0),
            "courthouse": (15, 2, 0),
            "post_office": (12, 8, 2),
        },
    },
}


# --- Steps and roads -----------------------------------------------------------------


def validate_steps(steps: object) -> list[str]:
    """The steps an island may have, in STEP_ORDER. Raises IslandError."""
    if not isinstance(steps, list | tuple) or any(t not in STEP_ORDER for t in steps):
        raise IslandError(f"steps must be a list of {list(STEP_ORDER)}")
    if len(set(steps)) != len(steps):
        raise IslandError("each step can only be built once")
    missing = [t for t in REQUIRED_STEPS if t not in steps]
    if missing:
        raise IslandError(f"an island needs a {', '.join(STEP_NAMES[t] for t in missing)}")
    return [t for t in STEP_ORDER if t in steps]


def build_roads(types: object) -> list[tuple[str, str, str]]:
    """The roads (from type, to type, condition) for an island with these steps.

    Steps are chained in STEP_ORDER. A check step (lab, courthouse) sends a pass on
    to the next step and a fail back to the workshop; other steps always go on.
    """
    chosen = set(types)
    steps = [t for t in STEP_ORDER if t in chosen]
    roads = []
    for a, b in pairwise(steps):
        if a in CHECK_STEPS:
            roads += [(a, b, "pass"), (a, "workshop", "fail")]
        else:
            roads.append((a, b, "always"))
    return roads


def plan_name(types: object) -> str:
    """The town plan these steps match, or "custom"."""
    return next((plan for plan, steps in PLANS.items() if set(steps) == set(types)), "custom")


# --- Shapes ---------------------------------------------------------------------------


def door_tile(x: int, y: int, facing: int) -> tuple[int, int]:
    """The tile just outside a lot's door, where its path starts."""
    return [(x + 1, y + LOT), (x + LOT, y + 1), (x + 1, y - 1), (x - 1, y + 1)][facing]


def default_map(shape: str = "classic", season: str = "summer") -> dict:
    preset = SHAPES[shape]
    pond = dict(preset["pond"]) if preset["pond"] else None
    return {"version": 1, "shape": shape, "land": list(preset["land"]), "pond": pond, "plants": None, "season": season}


def default_buildings(shape: str, steps: object) -> list[dict]:
    """The shape's lots for these steps: [{"type", "x", "y", "facing"}] in STEP_ORDER."""
    lots = SHAPES[shape]["lots"]
    return [{"type": t, "x": lots[t][0], "y": lots[t][1], "facing": lots[t][2]} for t in STEP_ORDER if t in steps]


# --- Validation (build mode) --------------------------------------------------------


# JSON allows NaN and Infinity (Python's json does): refuse them, they'd break every client.
def _int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or int(value) != value:
        raise IslandError(f"{what} must be a whole number")
    return int(value)


def _number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise IslandError(f"{what} must be a number")
    return float(value)


def validate_map(island_map: object, buildings: object) -> tuple[dict, list[dict]]:
    """Check a map and its buildings; return clean copies. Raises IslandError.

    Rules: the land is one piece with sea all round the grid's edge; the pond sits on
    land; every lot and the ring of tiles around it is land; lots don't overlap each
    other or the pond; every door opens onto free land, and all doors can reach each
    other over free land (so the game can draw a path between them).
    """
    if not isinstance(island_map, dict):
        raise IslandError("map must be an object")
    land = island_map.get("land")
    if not (
        isinstance(land, list)
        and len(land) == GRID_H
        and all(isinstance(row, str) and len(row) == GRID_W and set(row) <= {"#", "."} for row in land)
    ):
        raise IslandError(f"land must be {GRID_H} rows of {GRID_W} '#' / '.' characters")

    tiles = {(c, r) for r in range(GRID_H) for c in range(GRID_W) if land[r][c] == "#"}
    if not tiles:
        raise IslandError("an island needs some land")
    if any(c in (0, GRID_W - 1) or r in (0, GRID_H - 1) for c, r in tiles):
        raise IslandError("keep the edge of the map as sea, so the island has a beach")
    if len(_reachable(next(iter(tiles)), tiles)) != len(tiles):
        raise IslandError("the land must be one piece")

    pond = island_map.get("pond")
    pond_tiles: set[tuple[int, int]] = set()
    if pond is not None:
        if not isinstance(pond, dict):
            raise IslandError("pond must be an object or null")
        x, y, w, h = (_int(pond.get(k), f"pond {k}") for k in ("x", "y", "w", "h"))
        if not (1 <= w <= MAX_POND and 1 <= h <= MAX_POND):
            raise IslandError(f"the pond can be 1-{MAX_POND} tiles on each side")
        pond = {"x": x, "y": y, "w": w, "h": h}
        pond_tiles = {(c, r) for c in range(x, x + w) for r in range(y, y + h)}
        if not pond_tiles <= tiles:
            raise IslandError("the pond must be on land")

    season = island_map.get("season", "summer")
    if season not in SEASONS:
        raise IslandError(f"season must be one of {', '.join(SEASONS)}")

    plants = island_map.get("plants")
    if plants is not None:
        plants = _clean_plants(plants)

    if not isinstance(buildings, list):
        raise IslandError("buildings must be a list")
    types = validate_steps([b.get("type") if isinstance(b, dict) else None for b in buildings])
    by_type = {b["type"]: b for b in buildings}
    blocked = set(pond_tiles)
    clean_buildings = []
    for t in types:
        b = by_type[t]
        x, y, facing = _int(b.get("x"), f"{t} x"), _int(b.get("y"), f"{t} y"), _int(b.get("facing", 0), f"{t} facing")
        if not 0 <= facing <= 3:
            raise IslandError("facing must be 0-3")
        lot = {(c, r) for c in range(x, x + LOT) for r in range(y, y + LOT)}
        ring = {(c, r) for c in range(x - 1, x + LOT + 1) for r in range(y - 1, y + LOT + 1)}
        if not ring <= tiles:
            raise IslandError(f"the {STEP_NAMES[t]} needs land all round it")
        if lot & blocked:
            raise IslandError(f"the {STEP_NAMES[t]} overlaps something")
        blocked |= lot
        clean_buildings.append({"type": t, "x": x, "y": y, "facing": facing})

    doors = {b["type"]: door_tile(b["x"], b["y"], b["facing"]) for b in clean_buildings}
    free = tiles - blocked
    for t, door in doors.items():
        if door not in free:
            raise IslandError(f"the {STEP_NAMES[t]}'s door is blocked")
    reach = _reachable(doors["mailbox"], free)
    for t, door in doors.items():
        if door not in reach:
            raise IslandError(f"no path can reach the {STEP_NAMES[t]}")

    shape = str(island_map.get("shape") or "custom")[:20]
    clean_map = {"version": 1, "shape": shape, "land": list(land), "pond": pond, "plants": plants, "season": season}
    return clean_map, clean_buildings


def _clean_plants(plants: object) -> list[dict]:
    if not isinstance(plants, list) or len(plants) > MAX_PLANTS:
        raise IslandError(f"plants must be null or a list of up to {MAX_PLANTS}")
    clean = []
    for p in plants:
        if not isinstance(p, dict) or p.get("kind") not in ("tree", "bush"):
            raise IslandError("each plant needs a kind: tree or bush")
        px, py = _number(p.get("x"), "plant x"), _number(p.get("y"), "plant y")
        if not (0 <= px <= GRID_W and 0 <= py <= GRID_H):
            raise IslandError("plants must be on the map")
        scale = _number(p.get("scale", 1.0), "plant scale")
        if not 0.5 <= scale <= 2.0:
            raise IslandError("plant scale must be 0.5-2")
        rot = _number(p.get("rot", 0.0), "plant rot") % 360
        clean.append({"kind": p["kind"], "x": px, "y": py, "rot": rot, "scale": scale})
    return clean


def _reachable(start: tuple[int, int], tiles: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Every tile in `tiles` connected to `start` (4 neighbours, like the game's paths)."""
    seen, queue = {start}, deque([start])
    while queue:
        c, r = queue.popleft()
        for n in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
            if n in tiles and n not in seen:
                seen.add(n)
                queue.append(n)
    return seen
