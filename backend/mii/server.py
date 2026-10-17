"""FastAPI app: the WebSocket the game connects to, plus a few HTTP endpoints.

Run from backend/:  uv run uvicorn mii.server:app --reload --port 8000

    WS   /ws?island=<id>               snapshot + live events (docs/protocol.md)
    GET  /health                       is the server up? which mode?
    GET  /brains                       which agent CLIs are installed
    GET  /islands                      every island: id, name, repo
    GET  /islands/{id}                 the island snapshot
    POST /islands/{id}/tasks           {"title": "...", "description": "..."} or {"issue": 42}
    GET  /islands/{id}/insights        stats for the Insights screen
    POST /replay/{task_id}             replay a recorded task into the game
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlmodel import Session, select

from mii import events, github, residents, seed
from mii.agents import make_runner
from mii.agents.acp import available_brains
from mii.config import settings
from mii.engine import WorkflowEngine
from mii.insights import island_insights
from mii.models import Island, db_engine, island_snapshot, task_to_dict
from mii.replay import replay_task
from mii.sandbox import Sandbox

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

# Mock mode never touches git. Real mode fails fast here if SANDBOX_REPO_PATH is wrong.
sandbox = None if settings.mock_agents else Sandbox(settings.sandbox_repo_path, settings.worktree_root)
bus = events.EventBus(db_engine)
bubbles = events.Bubbles(bus)
workflow = WorkflowEngine(
    db_engine, bus, bubbles, make_runner(settings.mock_agents, sandbox), sandbox=sandbox,
    walk_seconds=settings.walk_seconds, bubble_timeout=settings.bubble_timeout,
    sleep_after=settings.sleep_after, step_timeout=settings.step_timeout,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed.seed(db_engine)  # creates tables + the seed island if they're missing
    await workflow.recover()
    worker = asyncio.create_task(workflow.run_forever())
    log.info("Mii-nagement ready (%s agents)", "MOCK" if settings.mock_agents else "REAL")
    yield
    worker.cancel()


app = FastAPI(title="Mii-nagement", lifespan=lifespan)


def get_island_or_404(session: Session, island_id: str) -> Island:
    island = session.get(Island, island_id)
    if island is None:
        raise HTTPException(404, f"no island {island_id!r}")
    return island


# --- HTTP ---------------------------------------------------------------------------


class NewTask(BaseModel):
    title: str = ""
    description: str = ""
    issue: int | None = None  # a GitHub issue number on the island's repo


@app.get("/health")
def health() -> dict:
    return {"ok": True, "mock_agents": settings.mock_agents}


@app.get("/brains")
def brains() -> dict:
    return available_brains()


@app.get("/islands")
def list_islands() -> dict:
    with Session(db_engine) as session:
        islands = session.exec(select(Island).order_by(Island.name)).all()
        return {"islands": [{"id": i.id, "name": i.name, "repo": i.repo} for i in islands]}


@app.get("/islands/{island_id}")
def get_island(island_id: str) -> dict:
    with Session(db_engine) as session:
        get_island_or_404(session, island_id)
        return island_snapshot(session, island_id, bubbles.for_island(island_id))


@app.post("/islands/{island_id}/tasks")
async def create_task(island_id: str, body: NewTask) -> dict:
    with Session(db_engine) as session:
        island = get_island_or_404(session, island_id)
    title, description = body.title, body.description
    if body.issue is not None:
        if not island.repo:
            raise HTTPException(400, "this island has no GitHub repo")
        try:
            title, description = github.fetch_issue(island.repo, body.issue)
        except NotImplementedError as e:
            raise HTTPException(501, str(e))
    if not title:
        raise HTTPException(400, "give a title or an issue number")
    task = await workflow.submit(island_id, title, description, issue_number=body.issue)
    return task_to_dict(task)


@app.get("/islands/{island_id}/insights")
def get_insights(island_id: str) -> dict:
    with Session(db_engine) as session:
        get_island_or_404(session, island_id)
        return island_insights(session, island_id)


@app.post("/replay/{task_id}")
async def replay(task_id: str) -> dict:
    try:
        sent = await replay_task(bus, db_engine, task_id)
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    return {"replayed_events": sent}


# --- WebSocket ---------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, island: str | None = None):
    await ws.accept()
    with Session(db_engine) as session:
        first = session.exec(select(Island)).first()
        island_id = island or (first.id if first else "")
        try:
            snapshot = island_snapshot(session, island_id, bubbles.for_island(island_id))
        except KeyError:
            await ws.send_json(events.error(f"no island {island_id!r}"))
            await ws.close()
            return
    await ws.send_json({"type": "snapshot", "island": snapshot})

    bus.add_client(ws, island_id)  # only this island's events
    try:
        while True:
            await handle_client_message(ws, island_id, await ws.receive_text())
    except WebSocketDisconnect:
        pass
    finally:
        bus.remove_client(ws)


async def handle_client_message(ws: WebSocket, island_id: str, raw: str) -> None:
    """Client -> server messages (docs/protocol.md). Unknown types are ignored."""
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        await ws.send_json(events.error("message is not valid JSON"))
        return
    if not isinstance(message, dict):
        await ws.send_json(events.error("message must be a JSON object"))
        return
    kind = message.get("type")
    if kind == "answer_bubble":
        bubble_id = message.get("id", "")
        if not bubbles.answer(bubble_id, str(message.get("response", ""))):
            await ws.send_json(events.error(f"unknown bubble id: {bubble_id}"))
    elif kind in ("create_resident", "update_resident"):
        data = message.get("resident")
        if not isinstance(data, dict):
            await ws.send_json(events.error(f"{kind} needs a `resident` object"))
            return
        save = residents.create_resident if kind == "create_resident" else residents.update_resident
        try:
            with Session(db_engine) as session:
                resident = save(session, island_id, data)
                event = events.resident(resident)
        except residents.ResidentError as e:
            await ws.send_json(events.error(str(e)))
            return
        await bus.emit(island_id, event)  # every client, including this one
