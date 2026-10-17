"""Event bus and bubbles.

Every event is saved to the Event table, then sent to every connected
WebSocket. Event shapes are defined in docs/protocol.md; the builder functions
below create them, so each shape is written in exactly one place.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session

from mii.models import Event, Task, task_to_dict

log = logging.getLogger(__name__)


# --- Event builders (server -> client, see docs/protocol.md) ----------------


def agent_state(agent: str, state: str, building: str | None) -> dict:
    return {"type": "agent_state", "agent": agent, "state": state, "building": building}


def move(agent: str, from_building: str, to_building: str) -> dict:
    return {"type": "move", "agent": agent, "from": from_building, "to": to_building}


def bubble(agent: str, kind: str, bubble_id: str, text: str, options: list[str] | None = None) -> dict:
    event = {"type": "bubble", "agent": agent, "kind": kind, "id": bubble_id, "text": text}
    if options:
        event["options"] = options
    return event


def bubble_resolved(bubble_id: str) -> dict:
    return {"type": "bubble_resolved", "id": bubble_id}


def handoff(from_agent: str, to_agent: str, task: str, outcome: str) -> dict:
    return {"type": "handoff", "from": from_agent, "to": to_agent, "task": task, "outcome": outcome}


def mood(agent: str, mood: str) -> dict:
    return {"type": "mood", "agent": agent, "mood": mood}


def activity(agent: str, text: str) -> dict:
    return {"type": "activity", "agent": agent, "text": text}


def task(t: Task) -> dict:
    return {"type": "task", **task_to_dict(t)}


def error(message: str) -> dict:
    return {"type": "error", "message": message}


# --- Event bus -----------------------------------------------------------------


class EventBus:
    """Saves events to SQLite and broadcasts them to all connected clients.

    A "client" is anything with an async `send_json(dict)` method: a FastAPI
    WebSocket, or a fake one in tests.
    """

    def __init__(self, db_engine):
        self.db_engine = db_engine
        self.clients: set = set()

    def add_client(self, client) -> None:
        self.clients.add(client)

    def remove_client(self, client) -> None:
        self.clients.discard(client)

    async def emit(self, island_id: str | None, event: dict) -> None:
        event = {**event, "ts": datetime.now(timezone.utc).isoformat()}
        with Session(self.db_engine) as session:
            session.add(Event(island_id=island_id, type=event["type"], payload=event))
            session.commit()
        await self.broadcast(event)

    async def broadcast(self, event: dict) -> None:
        for client in list(self.clients):
            try:
                await client.send_json(event)
            except Exception:
                log.info("dropping disconnected client")
                self.remove_client(client)


# --- Bubbles ---------------------------------------------------------------------


@dataclass
class OpenBubble:
    id: str
    island_id: str
    agent: str
    kind: str
    text: str
    options: list[str] | None
    answer: asyncio.Future


class Bubbles:
    """Lets a resident ask you something and wait for the answer.

    `ask()` shows a bubble and waits until a client sends `answer_bubble`
    (which calls `answer()`), or until the timeout runs out. Open bubbles live
    in memory only.
    """

    def __init__(self, bus: EventBus):
        self.bus = bus
        self.open: dict[str, OpenBubble] = {}

    async def ask(
        self, island_id: str, agent: str, kind: str, text: str, timeout: float, options: list[str] | None = None
    ) -> str | None:
        """Show a bubble. Returns your answer, or None if nobody answered in time."""
        bubble_id = "b_" + uuid.uuid4().hex[:6]
        future = asyncio.get_running_loop().create_future()
        self.open[bubble_id] = OpenBubble(bubble_id, island_id, agent, kind, text, options, future)
        await self.bus.emit(island_id, bubble(agent, kind, bubble_id, text, options))
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            return None
        finally:
            del self.open[bubble_id]
            await self.bus.emit(island_id, bubble_resolved(bubble_id))

    def answer(self, bubble_id: str, response: str) -> bool:
        """Deliver an answer. Returns False if the bubble is unknown or already closed."""
        b = self.open.get(bubble_id)
        if b is None or b.answer.done():
            return False
        b.answer.set_result(response)
        return True

    def for_island(self, island_id: str) -> list[dict]:
        """Open bubbles, in the shape the snapshot uses."""
        return [
            {"id": b.id, "agent": b.agent, "kind": b.kind, "text": b.text, "options": b.options}
            for b in self.open.values()
            if b.island_id == island_id
        ]
