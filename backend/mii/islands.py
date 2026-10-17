"""Islands are repositories: founding an island creates (or adopts) a local git
repo and lays out its buildings and roads on an island shape.

The island's map (land tiles, pond, plants, season) is `Island.map`; see
"Island map" below. Build mode in the game saves it with set_map() (PUT
/islands/{id}/layout with a "map"; layout.py handles the plain buildings + roads
form of that request).

Where repos go, by default:
    macOS / Windows   ~/Documents/Mii-nagement/<island-id>
    Linux             ~/Mii-nagement/<island-id>
Override the root with ISLANDS_ROOT in .env, or pass `folder` when founding an
island. `existing_repo` adopts a repo you already have instead of creating one.

A new island starts with no residents: the creator makes them next
(create_resident), and each one moves into the buildings for their job.
"""

import re
import subprocess
from collections import deque
from pathlib import Path

from django.db import transaction

from mii import prefs
from mii.models import Building, Island, Resident, Road, Task
from mii.residents import JOB_BUILDINGS

# --- Workflow steps -------------------------------------------------------------
# Each building is one workflow step. An island picks which steps it has; the roads
# (the workflow graph the engine follows) always run in STEP_ORDER.
STEP_ORDER = ("mailbox", "workshop", "lab", "courthouse", "post_office")
REQUIRED_STEPS = ("mailbox", "workshop", "post_office")  # in, do the work, out
CHECK_STEPS = ("lab", "courthouse")  # pass -> the next step, fail -> back to the workshop
STEP_NAMES = {"mailbox": "Mailbox", "workshop": "Workshop", "lab": "Lab", "courthouse": "Courthouse",
              "post_office": "Post Office"}
# Town plans are just presets of steps (the island creator's quick picks).
PLANS = {
    "classic": STEP_ORDER,  # the full workflow, like the seed island
    "quick_fixes": ("mailbox", "workshop", "post_office"),  # small chores, no tests or review stop
}

RULES = ("merge_pr", "install_packages", "shell")  # house rules: True = ask me first (stored only for now)
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_NAME = 30


class IslandError(ValueError):
    """The request can't make an island. The message goes back as a 400."""


class MapBusy(IslandError):
    """The steps can't change right now (a task is on the road). Goes back as a 409."""


def build_roads(types) -> list[tuple[str, str, str]]:
    """The roads (from type, to type, condition) for an island with these steps.

    Steps are chained in STEP_ORDER. A check step (lab, courthouse) sends a pass on
    to the next step and a fail back to the workshop; other steps always go on.
    """
    chosen = set(types)
    steps = [t for t in STEP_ORDER if t in chosen]
    roads = []
    for a, b in zip(steps, steps[1:]):
        if a in CHECK_STEPS:
            roads += [(a, b, "pass"), (a, "workshop", "fail")]
        else:
            roads.append((a, b, "always"))
    return roads


# --- Island map -----------------------------------------------------------------
# An island is a GRID_W x GRID_H grid of tiles (one tile = 2 world units in the
# game). Island.map is:
#   {"version": 1, "shape": "classic",
#    "land": [GRID_H strings of GRID_W chars, "#" = land, "." = sea],
#    "pond": {"x", "y", "w", "h"} in tiles, or None,
#    "plants": None (the game arranges them) or [{"kind": "tree"|"bush", "x", "y", "rot", "scale"}],
#    "season": "spring" | "summer" | "autumn" | "winter"}
# A building stands on a LOT x LOT lot: Building.x / y is the lot's top-left tile,
# Building.facing the way its door looks (0 south, 1 east, 2 north, 3 west). The
# game draws the paths between the doors (docs/protocol.md has the rules).
GRID_W, GRID_H = 20, 14
LOT = 3
SEASONS = ("spring", "summer", "autumn", "winter")
MAX_PLANTS = 150
MAX_POND = 6  # tiles per side

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
        "lots": {"mailbox": (3, 2, 0), "workshop": (8, 2, 0), "lab": (13, 2, 0), "courthouse": (15, 8, 3),
                 "post_office": (2, 8, 1)},
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
        "lots": {"mailbox": (5, 3, 0), "workshop": (8, 2, 0), "lab": (12, 3, 0), "courthouse": (12, 8, 2),
                 "post_office": (5, 8, 2)},
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
        "lots": {"mailbox": (2, 3, 0), "workshop": (7, 3, 0), "lab": (12, 3, 0), "courthouse": (10, 8, 2),
                 "post_office": (4, 8, 2)},
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
        "lots": {"mailbox": (2, 2, 0), "workshop": (6, 2, 0), "lab": (10, 2, 0), "courthouse": (14, 2, 3),
                 "post_office": (2, 8, 1)},
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
        "lots": {"mailbox": (2, 2, 0), "workshop": (5, 2, 0), "lab": (12, 2, 0), "courthouse": (15, 2, 0),
                 "post_office": (12, 8, 2)},
    },
}


def door_tile(x: int, y: int, facing: int) -> tuple[int, int]:
    """The tile just outside a lot's door, where its path starts."""
    return [(x + 1, y + LOT), (x + LOT, y + 1), (x + 1, y - 1), (x - 1, y + 1)][facing]


def default_map(shape: str = "classic", season: str = "summer") -> dict:
    preset = SHAPES[shape]
    pond = dict(preset["pond"]) if preset["pond"] else None
    return {"version": 1, "shape": shape, "land": list(preset["land"]), "pond": pond, "plants": None, "season": season}


def default_buildings(shape: str, steps) -> list[dict]:
    """The shape's lots for these steps: [{"type", "x", "y", "facing"}] in STEP_ORDER."""
    lots = SHAPES[shape]["lots"]
    return [{"type": t, "x": lots[t][0], "y": lots[t][1], "facing": lots[t][2]} for t in STEP_ORDER if t in steps]


def validate_steps(steps) -> list[str]:
    if not isinstance(steps, (list, tuple)) or any(t not in STEP_ORDER for t in steps):
        raise IslandError(f"steps must be a list of {list(STEP_ORDER)}")
    if len(set(steps)) != len(steps):
        raise IslandError("each step can only be built once")
    missing = [t for t in REQUIRED_STEPS if t not in steps]
    if missing:
        raise IslandError(f"an island needs a {', '.join(STEP_NAMES[t] for t in missing)}")
    return [t for t in STEP_ORDER if t in steps]


def _int(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise IslandError(f"{what} must be a whole number")
    return int(value)


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IslandError(f"{what} must be a number")
    return float(value)


def validate_map(island_map, buildings) -> tuple[dict, list[dict]]:
    """Check a map and its buildings (see "Island map" above); return clean copies.

    Rules: the land is one piece with sea all round the grid's edge; the pond sits on
    land; every lot and the ring of tiles around it is land; lots don't overlap each
    other or the pond; every door opens onto free land, and all doors can reach each
    other over free land (so the game can draw a path between them).
    """
    if not isinstance(island_map, dict):
        raise IslandError("map must be an object")
    land = island_map.get("land")
    if not (isinstance(land, list) and len(land) == GRID_H
            and all(isinstance(row, str) and len(row) == GRID_W and set(row) <= {"#", "."} for row in land)):
        raise IslandError(f"land must be {GRID_H} rows of {GRID_W} '#' / '.' characters")

    def is_land(c: int, r: int) -> bool:
        return 0 <= c < GRID_W and 0 <= r < GRID_H and land[r][c] == "#"

    tiles = {(c, r) for r in range(GRID_H) for c in range(GRID_W) if is_land(c, r)}
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
            clean.append({"kind": p["kind"], "x": px, "y": py, "rot": _number(p.get("rot", 0.0), "plant rot") % 360,
                          "scale": scale})
        plants = clean

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
    return ({"version": 1, "shape": shape, "land": list(land), "pond": pond, "plants": plants, "season": season},
            clean_buildings)


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


def set_map(island_id: str, data: dict) -> Island:
    """Build mode's save: a new map and building lots (and maybe other steps).

    `data` is {"map": {...}, "buildings": [{"type", "x", "y", "facing"}]}.
    Sync (ORM), so async callers use asyncio.to_thread. Raises Island.DoesNotExist,
    IslandError, or MapBusy when the steps would change while a task is running.
    Building ids are kept, so residents, tasks, and history stay attached; a new step
    gets a resident of the right job, and residents of a removed step move home.
    """
    island = Island.objects.get(pk=island_id)
    island_map, buildings = validate_map(data.get("map"), data.get("buildings"))
    with transaction.atomic():
        current = {b.type: b for b in Building.objects.filter(island_id=island_id)}
        new_types = [b["type"] for b in buildings]
        steps_changed = set(new_types) != set(current)
        if steps_changed and Task.objects.filter(island_id=island_id, status="in_progress").exists():
            raise MapBusy("a task is on the road: add or remove buildings once it's done")
        for t, b in current.items():
            if t not in new_types:
                b.delete()  # its roads go with it; residents and tasks there lose their building
        for spec in buildings:
            b = current.get(spec["type"])
            if b is None:
                b = Building(id=_new_building_id(island_id, spec["type"]), island_id=island_id, type=spec["type"],
                             name=STEP_NAMES[spec["type"]], resident_id=_staff(island_id, spec["type"]))
            b.x, b.y, b.facing = spec["x"], spec["y"], spec["facing"]
            b.save()
        if steps_changed:
            Road.objects.filter(island_id=island_id).delete()
            ids = {b.type: b.id for b in Building.objects.filter(island_id=island_id)}
            for a, b, condition in build_roads(new_types):
                Road.objects.create(island_id=island_id, from_building_id=ids[a], to_building_id=ids[b], condition=condition)
            _rehome_residents(island_id)
        island.map = island_map
        island.plan = _plan_name(new_types)
        island.save(update_fields=["map", "plan"])
    return island


def _new_building_id(island_id: str, btype: str) -> str:
    building_id, n = f"{island_id}_{btype}", 1
    while Building.objects.filter(pk=building_id).exists():
        n += 1
        building_id = f"{island_id}_{btype}_{n}"
    return building_id


def _staff(island_id: str, btype: str) -> str | None:
    """A resident whose job works in this building type, if the island has one."""
    jobs = [job for job, types in JOB_BUILDINGS.items() if btype in types]
    resident = Resident.objects.filter(island_id=island_id, job__in=jobs).order_by("id").first()
    return resident.id if resident else None


def _rehome_residents(island_id: str) -> None:
    """Residents left without a building (theirs was removed) go to their job's home."""
    buildings = list(Building.objects.filter(island_id=island_id).order_by("id"))
    for r in Resident.objects.filter(island_id=island_id, building__isnull=True):
        mine = [b for t in JOB_BUILDINGS.get(r.job, []) for b in buildings if b.type == t]
        if mine:
            r.building_id = mine[0].id
            r.save(update_fields=["building"])


def _plan_name(types) -> str:
    """The town plan these steps match, or "custom"."""
    return next((plan for plan, steps in PLANS.items() if set(steps) == set(types)), "custom")


def default_folder() -> Path:
    """The islands_root setting (or ISLANDS_ROOT), else Documents/Mii-nagement if there is a
    Documents folder, else ~/Mii-nagement. Sync (reads the database)."""
    root = prefs.get("islands_root")
    if root:
        return Path(root).resolve()
    documents = Path.home() / "Documents"
    return (documents if documents.is_dir() else Path.home()) / "Mii-nagement"


def unique_island_id(name: str) -> str:
    """A readable id from the name, made unique ("fern_harbor", "fern_harbor_2")."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "island"
    island_id, n = base, 1
    while Island.objects.filter(pk=island_id).exists():
        n += 1
        island_id = f"{base}_{n}"
    return island_id


def create_island(data: dict) -> Island:
    """Found an island: validate, create its repository, lay out the town plan.

    Sync (git + filesystem): async callers use asyncio.to_thread. Fields:
      name (required), repo ("owner/name" on GitHub), branch (default main),
      shape (a SHAPES key, default classic), steps (which buildings, see STEP_ORDER)
      or plan ("classic" | "quick_fixes", a preset of steps), season, rules ({rule: bool}),
      folder (directory to create the repo in; default: default_folder()),
      existing_repo (adopt this git repo instead of creating one).
    """
    name = str(data.get("name", "")).strip()
    if not 1 <= len(name) <= MAX_NAME:
        raise IslandError(f"name must be 1-{MAX_NAME} characters")
    repo = str(data.get("repo") or "").strip() or None
    if repo and not REPO.match(repo):
        raise IslandError(f"repo must look like owner/name, got {repo}")
    branch = str(data.get("branch") or "main").strip() or "main"
    if data.get("steps") is not None:
        steps = validate_steps(data["steps"])
    else:
        plan = str(data.get("plan", "classic"))
        if plan not in PLANS:
            raise IslandError(f"unknown town plan: {plan} (try {', '.join(PLANS)})")
        steps = list(PLANS[plan])
    shape = str(data.get("shape") or "classic")
    if shape not in SHAPES:
        raise IslandError(f"unknown island shape: {shape} (try {', '.join(SHAPES)})")
    season = str(data.get("season") or "summer")
    if season not in SEASONS:
        raise IslandError(f"season must be one of {', '.join(SEASONS)}")
    rules = data.get("rules", {})
    if not isinstance(rules, dict) or any(k not in RULES or not isinstance(v, bool) for k, v in rules.items()):
        raise IslandError(f"rules must map {list(RULES)} to true/false")

    with transaction.atomic():
        island_id = unique_island_id(name)
        if data.get("existing_repo"):
            path = Path(str(data["existing_repo"])).expanduser().resolve()
            if not (path / ".git").is_dir():
                raise IslandError(f"{path} is not a git repository")
        else:
            folder = data.get("folder")
            path = init_repo(Path(str(folder)).expanduser().resolve() if folder else default_folder(), island_id, name)
        if repo:
            link_remote(path, repo)  # so the post office can push branches and open PRs
        island = Island.objects.create(
            id=island_id, name=name, repo=repo, repo_path=str(path), branch=branch, plan=_plan_name(steps),
            rules={rule: bool(rules.get(rule, False)) for rule in RULES}, map=default_map(shape, season),
        )
        for b in default_buildings(shape, steps):  # building ids are unique across islands
            Building.objects.create(id=f"{island_id}_{b['type']}", island_id=island_id, type=b["type"],
                                    name=STEP_NAMES[b["type"]], x=b["x"], y=b["y"], facing=b["facing"])
        for a, b, condition in build_roads(steps):
            Road.objects.create(island_id=island_id, from_building_id=f"{island_id}_{a}",
                                to_building_id=f"{island_id}_{b}", condition=condition)
    return island


def init_repo(folder: Path, island_id: str, name: str) -> Path:
    """Create <folder>/<island_id> as a git repo with one commit (or adopt it if it already is one)."""
    path = folder / island_id
    if (path / ".git").is_dir():
        return path
    if path.exists() and any(path.iterdir()):
        raise IslandError(f"{path} exists and is not empty")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise IslandError(f"cannot create {path}: {e.strerror}")
    (path / "README.md").write_text(f"# {name}\n\nThis repository is the island \"{name}\" in Mii-nagement.\n")
    _git(path, "init", "-q")
    _git(path, "add", "README.md")
    _git(path, "-c", "user.name=Mii-nagement", "-c", "user.email=mii@localhost", "commit", "-q", "-m", "New island")
    return path


def link_remote(path: Path, repo: str) -> None:
    """Point the repo's `origin` at github.com/<owner/name> (only if it has no origin yet)."""
    has_origin = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True).returncode == 0
    if not has_origin:
        _git(path, "remote", "add", "origin", f"https://github.com/{repo}.git")


def ensure_repo(island: Island) -> Island:
    """Give an island without a repository one, in the default folder. Sync."""
    if island.repo_path and (Path(island.repo_path) / ".git").is_dir():
        return island
    island.repo_path = str(init_repo(default_folder(), island.id, island.name))
    island.save(update_fields=["repo_path"])
    return island


def _git(cwd: Path, *args: str) -> None:
    try:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise IslandError("git is not installed")
    if result.returncode != 0:
        raise IslandError(f"git {args[0]} failed: {result.stderr.strip()}")


async def delete_island(island_id: str) -> str | None:
    """Abandon an island: its rows go, its git repository stays on disk (returned, for the message).

    Raises Island.DoesNotExist, or IslandError while a task is running there or
    when it is the last island (the game needs one to connect to).
    """
    island = await Island.objects.aget(pk=island_id)
    if await Task.objects.filter(island_id=island_id, status="in_progress").aexists():
        raise IslandError("a task is running on this island; cancel it first")
    if await Island.objects.acount() == 1:
        raise IslandError("this is the last island; found another one first")
    await island.adelete()  # residents, buildings, roads, tasks, friendships cascade; Step/Event rows stay
    return island.repo_path
