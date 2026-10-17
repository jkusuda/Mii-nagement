"""Seed data + simulator.

    uv run python -m mii.seed            # create the seed island (if missing)
    uv run python -m mii.seed --reset    # wipe the database and re-seed (stop the server first)
    uv run python -m mii.seed --loop     # simulator: keep sending fake tasks to the running server

The IDs and lot positions here must match the "Seed island IDs" table in
docs/protocol.md, because the Godot client places buildings by these IDs.
"""

import argparse
import asyncio
import random

import httpx
from sqlmodel import Session, SQLModel

from mii.models import Building, Island, Resident, Road, db_engine, init_db

ISLAND_ID = "sunny"


def residents() -> list[Resident]:
    # One brain per vendor, for the demo: a Gemini planner, a Claude Code coder,
    # and a Codex reviewer. With MOCK_AGENTS=1 (the default) they all run on mock.
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
    #       id / type        name           x   y   resident
    rows = [
        ("mailbox",     "Mailbox",      4, 15, "jun"),
        ("workshop",    "Workshop",    12,  7, "carlos"),
        ("lab",         "Lab",         20, 15, "carlos"),
        ("courthouse",  "Courthouse",  28,  7, "sydney"),
        ("post_office", "Post Office", 35, 15, "jun"),
    ]
    return [
        Building(id=bid, island_id="sunny", type=bid, name=name, x=x, y=y, resident_id=who)
        for bid, name, x, y, who in rows
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
    return [Road(island_id="sunny", from_id=a, to_id=b, condition=c) for a, b, c in rows]


def seed(engine=db_engine, reset: bool = False) -> str:
    """Create the seed island if it doesn't exist. Returns the island id."""
    if reset:
        SQLModel.metadata.drop_all(engine)
    init_db(engine)
    with Session(engine) as session:
        if session.get(Island, ISLAND_ID) is None:
            session.add(Island(id=ISLAND_ID, name="Sunny Isle", repo=None))
            session.add_all(residents() + buildings() + roads())
            session.commit()
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the island, optionally run the simulator.")
    parser.add_argument("--reset", action="store_true", help="wipe the database first (stop the server first)")
    parser.add_argument("--loop", action="store_true", help="keep sending fake tasks to the server")
    parser.add_argument("--url", default="http://localhost:8000", help="server URL for --loop")
    parser.add_argument("--pause", type=float, default=3.0, help="seconds between tasks")
    args = parser.parse_args()

    island_id = seed(reset=args.reset)
    if args.loop:
        try:
            asyncio.run(simulate(args.url, island_id, args.pause))
        except KeyboardInterrupt:
            pass
        except httpx.ConnectError:
            print(f"could not reach {args.url}. Start the server first.")


if __name__ == "__main__":
    main()
