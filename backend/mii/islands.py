"""Islands: found, change and abandon them (every island is a git repository).

    create_island()   POST /islands: validate, get the repository (repos.py), lay out the town plan
    set_map()         PUT /islands/{id}/layout with a "map": build mode's save
    update_island()   rename, link a repo, change the branch or house rules
    delete_island()   abandon the island (rows go, the repository stays on disk)
    ensure_repo()     give an old island without a repository one (server startup)

Where repos go by default: ~/Documents/Mii-nagement/<island-id> (macOS, Windows) or
~/Mii-nagement/<island-id> (Linux); ISLANDS_ROOT or the `folder` field override it.
Map rules and shapes live in island_map.py, git in repos.py. The functions that
touch git are sync: async callers use asyncio.to_thread.

A new island starts with no residents: the creator makes them next
(create_resident), and each one moves into the buildings for their job.
"""

import re
from pathlib import Path

from django.db import transaction

from mii import prefs, repos
from mii.config import config
from mii.island_map import (
    SHAPES,
    IslandError,
    build_roads,
    default_buildings,
    default_map,
    plan_name,
    validate_map,
    validate_steps,
)
from mii.models import Building, Island, Resident, Road, Task
from mii.vocab import JOB_BUILDINGS, PLANS, RULES, SEASONS, STEP_NAMES, STEP_ORDER  # noqa: F401  (STEP_ORDER: for the tests)

REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_ISLAND_NAME = 30


class MapBusy(IslandError):
    """The steps can't change right now (a task is on the road). Goes back as a 409."""


# --- Founding -----------------------------------------------------------------------


def default_folder() -> Path:
    """The islands_root setting (or ISLANDS_ROOT), else Documents/Mii-nagement if there is a
    Documents folder, else ~/Mii-nagement. Sync (reads the database)."""
    root = prefs.get("islands_root")
    if root:
        return Path(root).resolve()
    documents = Path.home() / "Documents"
    return (documents if documents.is_dir() else Path.home()) / "Mii-nagement"


def unique_island_id(name: str, folder: Path | None = None) -> str:
    """A readable id from the name, made unique ("fern_harbor", "fern_harbor_2"). With a
    `folder`, also skip ids whose repository is still there (from an abandoned island)."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "island"
    island_id, n = base, 1
    while Island.objects.filter(pk=island_id).exists() or (folder is not None and (folder / island_id).exists()):
        n += 1
        island_id = f"{base}_{n}"
    return island_id


def create_island(data: dict, owner: str | None = None) -> Island:
    """Found an island: validate, create its repository, lay out the town plan. Sync.

    Fields: name (required), repo ("owner/name" on GitHub), branch (default: the repo's
    own, else main), shape (a SHAPES key), steps (see STEP_ORDER) or plan (a PLANS key),
    season, rules ({rule: bool}), test_command (what the Lab runs; "" = the setting),
    folder (where the repo goes; default: default_folder()),
    existing_repo (adopt this git folder instead of creating or cloning one).
    `owner`: the logged-in founder's id (auth.py); None makes a public island.
    """
    if config.public_demo and (data.get("folder") or data.get("existing_repo")):
        raise IslandError("the public demo picks where islands live: leave out folder and existing_repo")
    name = _clean_name(data.get("name"))
    repo = _clean_repo(data.get("repo"))
    branch = str(data.get("branch") or "").strip() or None
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
    rules = _clean_rules(data.get("rules", {}))
    test_command = _clean_test_command(data.get("test_command"))

    folder = Path(str(data["folder"])).expanduser().resolve() if data.get("folder") else default_folder()
    # Locally, founding an island again under its old name picks its repository back up. On the
    # public demo that repository may be someone else's abandoned island: start a new one.
    island_id = unique_island_id(name, folder if config.public_demo else None)
    if data.get("existing_repo"):
        path, repo, branch = repos.adopt_repo(Path(str(data["existing_repo"])).expanduser().resolve(), repo, branch)
    elif repo:
        path, branch = repos.clone_repo(repo, folder / island_id, branch, name)  # slow: off the event loop
    else:
        path = repos.init_repo(folder, island_id, name, branch or "main")
        branch = repos.current_branch(path) or "main"  # that, or the branch of a repo already there

    with transaction.atomic():
        island = Island.objects.create(
            id=island_id,
            name=name,
            repo=repo,
            repo_path=str(path),
            branch=branch,
            plan=plan_name(steps),
            rules={rule: bool(rules.get(rule, False)) for rule in RULES},
            test_command=test_command,
            map=default_map(shape, season),
            owner=owner,
        )
        _create_buildings(island_id, default_buildings(shape, steps))
    return island


def _create_buildings(island_id: str, specs: list[dict]) -> None:
    """Buildings and their roads for the steps in `specs` (building ids are unique across islands)."""
    for b in specs:
        Building.objects.create(
            id=f"{island_id}_{b['type']}",
            island_id=island_id,
            type=b["type"],
            name=STEP_NAMES[b["type"]],
            x=b["x"],
            y=b["y"],
            facing=b["facing"],
        )
    _create_roads(island_id, [b["type"] for b in specs])


def _create_roads(island_id: str, types: list[str]) -> None:
    ids = {b.type: b.id for b in Building.objects.filter(island_id=island_id)}
    for a, b, condition in build_roads(types):
        Road.objects.create(island_id=island_id, from_building_id=ids[a], to_building_id=ids[b], condition=condition)


# --- Build mode ---------------------------------------------------------------------


def set_map(island_id: str, data: dict) -> Island:
    """Build mode's save: a new map and building lots (and maybe other steps). Sync.

    `data` is {"map": {...}, "buildings": [{"type", "x", "y", "facing"}]}. Raises
    Island.DoesNotExist, IslandError, or MapBusy when the steps would change while a
    task is running. Building ids are kept, so residents, tasks and history stay
    attached; a new step gets a resident of the right job, and residents of a removed
    step move home.
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
                b = Building(
                    id=_new_building_id(island_id, spec["type"]),
                    island_id=island_id,
                    type=spec["type"],
                    name=STEP_NAMES[spec["type"]],
                    resident_id=_staff(island_id, spec["type"]),
                )
            b.x, b.y, b.facing = spec["x"], spec["y"], spec["facing"]
            b.save()
        if steps_changed:
            Road.objects.filter(island_id=island_id).delete()
            _create_roads(island_id, new_types)
            _rehome_residents(island_id)
        island.map = island_map
        island.plan = plan_name(new_types)
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


# --- After founding -----------------------------------------------------------------


def update_island(island_id: str, data: dict, sub: str | None = None) -> Island:
    """Rename an island, or link / change its GitHub repo, branch, house rules, test command or members. Sync (git).

    Only the fields sent change. Linking a repo also points the repository's `origin` at
    it, so the post office can push. `members` (a list of user ids) is the owner's to change:
    `sub` is the caller (auth.py). Raises Island.DoesNotExist or IslandError.
    """
    island = Island.objects.get(pk=island_id)
    changes: list[str] = []
    if "name" in data:
        island.name, changes = _clean_name(data.get("name")), [*changes, "name"]
    if "repo" in data:
        island.repo, changes = _clean_repo(data.get("repo")), [*changes, "repo"]
    if "branch" in data:
        branch = str(data.get("branch") or "main").strip() or "main"
        if repos.is_git_repo(island.repo_path) and not repos.has_branch(Path(island.repo_path), branch):
            raise IslandError(f"{island.repo_path} has no branch {branch}")
        island.branch, changes = branch, [*changes, "branch"]
    if "rules" in data:
        island.rules, changes = {**island.rules, **_clean_rules(data.get("rules"))}, [*changes, "rules"]
    if "test_command" in data:
        island.test_command, changes = _clean_test_command(data.get("test_command")), [*changes, "test_command"]
    if "members" in data:
        if not island.owner:
            raise IslandError("a public island has no members")
        if sub != island.owner:
            raise IslandError("only the island's owner can change its members")
        members = data.get("members")
        if not isinstance(members, list) or not all(isinstance(m, str) and m.strip() for m in members):
            raise IslandError("members must be a list of user ids")
        island.members, changes = sorted({m.strip() for m in members}), [*changes, "members"]
    if not changes:
        raise IslandError("nothing to change: send name, repo, branch, rules, test_command, or members")
    if island.repo and "repo" in changes and repos.is_git_repo(island.repo_path):
        repos.link_remote(Path(island.repo_path), island.repo)
    island.save(update_fields=changes)
    return island


def ensure_repo(island: Island) -> Island:
    """Give an island without a repository one, in the default folder. Sync."""
    if repos.is_git_repo(island.repo_path):
        return island
    island.repo_path = str(repos.init_repo(default_folder(), island.id, island.name))
    island.save(update_fields=["repo_path"])
    return island


async def delete_island(island_id: str) -> str | None:
    """Abandon an island: its rows go, its git repository stays on disk (returned, for the message).

    Raises Island.DoesNotExist, or IslandError while a task is running there.
    """
    island = await Island.objects.aget(pk=island_id)
    if await Task.objects.filter(island_id=island_id, status="in_progress").aexists():
        raise IslandError("a task is running on this island; cancel it first")
    await island.adelete()  # residents, buildings, roads, tasks, friendships cascade; Step/Event rows stay
    return island.repo_path


# --- Field checks ---------------------------------------------------------------------


def _clean_name(value: object) -> str:
    name = str(value or "").strip()
    if not 1 <= len(name) <= MAX_ISLAND_NAME:
        raise IslandError(f"name must be 1-{MAX_ISLAND_NAME} characters")
    return name


def _clean_repo(value: object) -> str | None:
    repo = str(value or "").strip() or None
    if repo and not REPO.match(repo):
        raise IslandError(f"repo must look like owner/name, got {repo}")
    return repo


def _clean_test_command(value: object) -> str:
    command = str(value or "").strip()
    if len(command) > 500 or "\n" in command:
        raise IslandError("test_command must be one line of at most 500 characters")
    return command


def _clean_rules(value: object) -> dict:
    if not isinstance(value, dict) or any(k not in RULES or not isinstance(v, bool) for k, v in value.items()):
        raise IslandError(f"rules must map {list(RULES)} to true/false")
    return value
