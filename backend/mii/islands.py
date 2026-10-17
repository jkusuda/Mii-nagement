"""Islands are repositories: founding an island creates (or adopts) a local git
repo and lays out the buildings and roads of a town plan.

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
from pathlib import Path

from django.db import transaction

from mii import prefs
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
RULES = ("merge_pr", "install_packages", "shell")  # house rules: True = ask me first (stored only for now)
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_NAME = 30


class IslandError(ValueError):
    """The request can't make an island. The message goes back as a 400."""


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
      plan ("classic" | "quick_fixes"), rules ({rule: bool}),
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
    plan = str(data.get("plan", "classic"))
    if plan not in TOWN_PLANS:
        raise IslandError(f"unknown town plan: {plan} (try {', '.join(TOWN_PLANS)})")
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
            id=island_id, name=name, repo=repo, repo_path=str(path), branch=branch, plan=plan,
            rules={rule: bool(rules.get(rule, False)) for rule in RULES},
        )
        buildings, roads = TOWN_PLANS[plan]
        for btype, bname, x, y in buildings:  # building ids are unique across islands
            Building.objects.create(id=f"{island_id}_{btype}", island_id=island_id, type=btype, name=bname, x=x, y=y)
        for a, b, condition in roads:
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
