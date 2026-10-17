"""Seed data + simulator (used by `manage.py seed`).

    uv run python manage.py seed            # create the seed island (if missing)
    uv run python manage.py seed --reset    # wipe the database and re-seed (stop the server first)
    uv run python manage.py seed --loop     # simulator: keep sending fake tasks to the running server

The IDs and lot positions here must match the "Seed island IDs" table in
docs/protocol.md, because the Godot client places buildings by these IDs.
"""

import asyncio
import random

import httpx
from django.db import transaction

from mii.islands import SHAPES, STEP_NAMES, STEP_ORDER, default_layout
from mii.models import Building, Event, Island, Resident, Road, Step, Task

ISLAND_ID = "sunny"


def residents() -> list[Resident]:
    # One brain per vendor, for the demo: a Gemini planner, a Claude Code coder,
    # and a Codex reviewer. With MOCK_AGENTS=1 (the default) they all run on mock.
    # A resident on the `openrouter` brain (any model, no CLI) is added later by the resident creator.
    return [
        Resident(
            id="jun", island_id="sunny", name="Jun", job="planner", brain="gemini",
            bluntness=0.3, caution=0.7, chattiness=0.6, creativity=0.5,
            catchphrase="Let's map it out!", color="#F4A261", building_id="mailbox",
        ),
        Resident(
            id="carlos", island_id="sunny", name="Carlos", job="coder", brain="claude-code",
            bluntness=0.5, caution=0.4, chattiness=0.3, creativity=0.8,
            catchphrase="Ship it... carefully!", color="#2A9D8F", building_id="workshop",
        ),
        Resident(
            id="sydney", island_id="sunny", name="Sydney", job="reviewer", brain="codex",
            bluntness=0.8, caution=0.8, chattiness=0.4, creativity=0.3,
            catchphrase="Show me the tests.", color="#9B5DE5", building_id="courthouse",
        ),
    ]


def buildings() -> list[Building]:
    """The classic island's lots (islands.SHAPES), one resident each."""
    lots = SHAPES["classic"]["lots"]
    who = {"mailbox": "jun", "workshop": "carlos", "lab": "carlos", "courthouse": "sydney", "post_office": "jun"}
    return [
        Building(id=t, island_id="sunny", type=t, name=STEP_NAMES[t], x=lots[t][0], y=lots[t][1], facing=lots[t][2],
                 resident_id=who[t])
        for t in STEP_ORDER
    ]


def roads() -> list[Road]:
    rows = [
        ("mailbox", "workshop", "always"),
        ("workshop", "lab", "always"),
        ("lab", "courthouse", "pass"),
        ("lab", "workshop", "fail"),
        ("courthouse", "post_office", "pass"),
        ("courthouse", "workshop", "fail"),
    ]
    return [Road(island_id="sunny", from_building_id=a, to_building_id=b, condition=c) for a, b, c in rows]


def seed(reset: bool = False) -> str:
    """Create the seed island if it doesn't exist. Returns the island id. (Sync: wrap with sync_to_async.)"""
    with transaction.atomic():
        if reset:
            for model in (Event, Step, Task, Road, Building, Resident, Island):
                model.objects.all().delete()
        if not Island.objects.filter(pk=ISLAND_ID).exists():
            Island.objects.create(id=ISLAND_ID, name="Sunny Isle", repo=None, layout=default_layout("classic"))
            # Residents point at buildings and buildings at residents: SQLite checks
            # foreign keys when the transaction commits, so the order doesn't matter.
            Resident.objects.bulk_create(residents())
            Building.objects.bulk_create(buildings())
            Road.objects.bulk_create(roads())
            print(f"seeded island {ISLAND_ID!r}")
    return ISLAND_ID


# --- Simulator ------------------------------------------------------------------

FAKE_TASKS = [
    ("Fix typo in README", "The install section says 'pyhton'."),
    ("Add a /health endpoint", "Return {ok: true} so the load balancer is happy."),
    ("Make the login button bigger", "Users on phones keep missing it."),
    ("Cache the weather API", "We call it on every page load."),
    ("Write tests for the date parser", "It breaks on leap years."),
    ("Rename `utils2.py`", "Nobody knows what's in it."),
    ("Dark mode for settings page", "Everything else is already dark."),
    ("Remove unused dependencies", "The lockfile is 4,000 lines long."),
]


async def simulate(base_url: str, island_id: str, pause: float) -> None:
    """Send one fake task, wait until it's finished, pause, repeat."""
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as http:
        while True:
            title, description = random.choice(FAKE_TASKS)
            response = await http.post(f"/islands/{island_id}/tasks", json={"title": title, "description": description})
            response.raise_for_status()
            task_id = response.json()["id"]
            print(f"sent {task_id}: {title}")

            # The snapshot lists only active tasks, so wait until ours drops off.
            while True:
                await asyncio.sleep(2)
                island = (await http.get(f"/islands/{island_id}")).json()
                if all(t["id"] != task_id for t in island["tasks"]):
                    break
            print(f"finished {task_id}")
            await asyncio.sleep(pause)
