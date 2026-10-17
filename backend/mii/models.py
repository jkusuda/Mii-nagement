"""Database tables and setup.

SQLModel = SQLAlchemy + Pydantic. Each class with `table=True` is one SQLite
table, and each instance is one row. Table models don't validate values, so
personality.py clamps the sliders when it reads them.

No migrations: after changing a table, run `uv run python -m mii.seed --reset`.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, Column
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine, select

from mii.config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    """ISO-8601 in UTC. SQLite hands datetimes back without a timezone; they are UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# --- Tables -----------------------------------------------------------------

# House keys: ACP tool kind group -> "yes" | "ask" | "no" (docs/protocol.md).
DEFAULT_KEYS = {"read": "yes", "edit": "ask", "execute": "ask", "fetch": "no"}


class Island(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    repo: str | None = None  # GitHub "owner/name"
    branch: str = "main"  # residents branch off from here
    plan: str = "classic"  # town plan it was founded with (islands.py TOWN_PLANS)
    # House rules from the island creator: True = ask me before doing this.
    # Stored only for now (merge_pr, install_packages, shell).
    rules: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)  # "Day 1" in the game


class Resident(SQLModel, table=True):
    id: str = Field(primary_key=True)
    island_id: str = Field(foreign_key="island.id", index=True)
    name: str
    job: str  # planner | coder | reviewer

    # Personality sliders, each 0.0-1.0 (see personality.py)
    bluntness: float = 0.5
    caution: float = 0.5
    chattiness: float = 0.5
    creativity: float = 0.5

    brain: str = "mock"  # mock | claude-code | codex | gemini (see agents/acp.py BRAINS)
    model: str | None = None  # optional model override; None = the agent's default
    catchphrase: str = ""
    color: str = "#FFFFFF"

    # Set in the resident creator. Stored only for now (see docs/mii-customization.md):
    # keys = what the agent may do without asking, gifts = extra MCP server ids.
    keys: dict = Field(default_factory=lambda: dict(DEFAULT_KEYS), sa_column=Column(JSON))
    gifts: list = Field(default_factory=list, sa_column=Column(JSON))

    # Live state, shown in the game
    state: str = "idle"  # idle | walking | working | sleeping
    building_id: str | None = None
    mood: str = "happy"


class Building(SQLModel, table=True):
    id: str = Field(primary_key=True)
    island_id: str = Field(foreign_key="island.id", index=True)
    type: str  # mailbox | workshop | lab | courthouse | post_office
    name: str
    x: int  # lot position on the island grid
    y: int
    resident_id: str | None = Field(default=None, foreign_key="resident.id")


class Road(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    island_id: str = Field(foreign_key="island.id", index=True)
    from_id: str = Field(foreign_key="building.id")
    to_id: str = Field(foreign_key="building.id")
    condition: str = "always"  # always | pass | fail


class Task(SQLModel, table=True):
    id: str = Field(primary_key=True)
    island_id: str = Field(foreign_key="island.id", index=True)
    title: str
    description: str = ""
    status: str = "queued"  # queued | in_progress | done | failed
    building_id: str | None = None  # where the package is now
    agent_id: str | None = None  # who is carrying it
    issue_number: int | None = None  # the GitHub issue it came from
    branch: str | None = None  # mii/<task_id>, in the task's worktree
    pr_url: str | None = None  # set by the post office
    created_at: datetime = Field(default_factory=utcnow)


class Step(SQLModel, table=True):
    """One building visit by one resident. The source data for insights.py."""

    id: int | None = Field(default=None, primary_key=True)
    island_id: str = Field(foreign_key="island.id", index=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    building_id: str
    building_type: str
    resident_id: str
    brain: str
    outcome: str = ""  # pass | fail
    timed_out: bool = False
    bubbles_raised: int = 0
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class Event(SQLModel, table=True):
    """Every event sent to clients, kept for history and replay.py."""

    id: int | None = Field(default=None, primary_key=True)
    island_id: str | None = Field(default=None, index=True)
    type: str = Field(index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


# --- Database setup ---------------------------------------------------------


def make_engine(url: str):
    if url == "sqlite://":  # in-memory database, used by tests
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, connect_args={"check_same_thread": False})


db_engine = make_engine(settings.database_url)


def init_db(engine=db_engine) -> None:
    """Create any missing tables."""
    SQLModel.metadata.create_all(engine)


# --- Converting rows to protocol dicts (see docs/protocol.md) ---------------


def resident_to_dict(r: Resident) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "job": r.job,
        "brain": r.brain,
        "model": r.model,
        "color": r.color,
        "catchphrase": r.catchphrase,
        "bluntness": r.bluntness,
        "caution": r.caution,
        "chattiness": r.chattiness,
        "creativity": r.creativity,
        "keys": r.keys,
        "gifts": r.gifts,
        "state": r.state,
        "building": r.building_id,
        "mood": r.mood,
    }


def task_to_dict(t: Task) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "status": t.status,
        "building": t.building_id,
        "agent": t.agent_id,
        "pr_url": t.pr_url,
    }


def island_snapshot(session: Session, island_id: str, open_bubbles: list[dict]) -> dict:
    """Full island state for the `snapshot` event."""
    island = session.get(Island, island_id)
    if island is None:
        raise KeyError(island_id)

    def rows(model):
        return session.exec(select(model).where(model.island_id == island_id)).all()

    return {
        "id": island.id,
        "name": island.name,
        "repo": island.repo,
        "branch": island.branch,
        "plan": island.plan,
        "rules": island.rules,
        "created_at": iso_utc(island.created_at),
        "buildings": [
            {"id": b.id, "type": b.type, "name": b.name, "x": b.x, "y": b.y, "resident": b.resident_id}
            for b in rows(Building)
        ],
        "roads": [{"from": r.from_id, "to": r.to_id, "condition": r.condition} for r in rows(Road)],
        "residents": [resident_to_dict(r) for r in rows(Resident)],
        "bubbles": open_bubbles,
        "tasks": [task_to_dict(t) for t in rows(Task) if t.status in ("queued", "in_progress")],
    }
