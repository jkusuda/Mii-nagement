"""Event bus and bubbles.

Every event is saved to the Event table, then sent to every connected
WebSocket. Event shapes are defined in docs/protocol.md; the builder functions
below create them, so each shape is written in exactly one place.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from mii import vocab
from mii.models import (
    Event,
    Friendship,
    Island,
    Resident,
    Task,
    friendship_to_dict,
    island_to_dict,
    resident_to_dict,
    task_to_dict,
)

log = logging.getLogger(__name__)


# --- Event builders (server -> client, see docs/protocol.md) ----------------


def agent_state(agent: str, state: str, building: str | None) -> dict:
    return {"type": "agent_state", "agent": agent, "state": state, "building": building}


def move(agent: str, from_building: str, to_building: str) -> dict:
    return {"type": "move", "agent": agent, "from": from_building, "to": to_building}


def bubble(
    agent: str,
    kind: str,
    bubble_id: str,
    text: str,
    options: list[str] | None = None,
    task: str | None = None,
    detail: str | None = None,
) -> dict:
    event = {"type": "bubble", "agent": agent, "kind": kind, "id": bubble_id, "text": text}
    if options:
        event["options"] = options
    if task:
        event["task"] = task
    if detail:
        event["detail"] = detail
    return event


def bubble_resolved(bubble_id: str, answered: bool = False, response: str | None = None) -> dict:
    """`answered` is False when the bubble timed out. insights.py counts answers from these."""
    return {"type": "bubble_resolved", "id": bubble_id, "answered": answered, "response": response}


def handoff(from_agent: str, to_agent: str, task: str, outcome: str) -> dict:
    return {"type": "handoff", "from": from_agent, "to": to_agent, "task": task, "outcome": outcome}


def mood(agent: str, mood: str) -> dict:
    return {"type": "mood", "agent": agent, "mood": mood}


def activity(agent: str, text: str) -> dict:
    return {"type": "activity", "agent": agent, "text": text}


def thinking(agent: str, task: str, kind: str, text: str, tool: str = "", call: str = "", detail: str = "") -> dict:
    """`tool`, `call` and `detail` only go with kind "tool", and only when set (protocol.md)."""
    event = {"type": "thinking", "agent": agent, "task": task, "kind": kind, "text": text}
    for key, value in (("tool", tool), ("call", call), ("detail", detail[: vocab.THINKING_DETAIL_MAX])):
        if value:
            event[key] = value
    return event


def task(t: Task) -> dict:
    return {"type": "task", **task_to_dict(t)}


def resident(r: Resident) -> dict:
    return {"type": "resident", "resident": resident_to_dict(r)}


def resident_removed(resident_id: str) -> dict:
    return {"type": "resident_removed", "id": resident_id}


def friendship(f: Friendship) -> dict:
    return {"type": "friendship", **friendship_to_dict(f)}


def notebook(agent: str, value: float) -> dict:
    return {"type": "notebook", "agent": agent, "value": value}


def layout(buildings: list[dict], roads: list[dict]) -> dict:
    return {"type": "layout", "buildings": buildings, "roads": roads}


def island(i: Island) -> dict:
    """The island's header changed (name, repo, branch, rules): same fields as the top of the snapshot."""
    return {"type": "island", "island": island_to_dict(i)}


def chat(from_agent: str, to_agent: str, task: str, text: str) -> dict:
    """One resident said something to another: a request for help, the helper's reply, or a `SAY TO` note."""
    return {"type": "chat", "from": from_agent, "to": to_agent, "task": task, "text": text}


def weather(kind: str) -> dict:
    return {"type": "weather", "weather": kind}


def snapshot(island: dict, mock_agents: bool) -> dict:
    """The full island (models.island_snapshot) plus whether every resident runs on the mock brain."""
    return {"type": "snapshot", "island": {**island, "mock_agents": mock_agents}}


def error(message: str) -> dict:
    """consumers.handle_message adds the client's `request_id`, when the message had one."""
    return {"type": "error", "message": message}


# --- Event bus -----------------------------------------------------------------


class EventBus:
    """Saves events to SQLite and broadcasts them to the clients watching that island.

    A "client" is anything with an async `send_json(dict)` method: the Channels
    WebSocket consumer, or a fake one in tests. Each client watches one island,
    or every island (`island_id=None`, what tests use).
    """

    def __init__(self):
        self.clients: dict = {}  # client -> island id it watches (None = all)
        self.hooks: list = []  # fn(island_id, event), called after each saved event (hooks.py)

    def add_hook(self, fn) -> None:
        """`fn` must be quick and synchronous (enqueue only): it runs inside emit()."""
        self.hooks.append(fn)

    def add_client(self, client, island_id: str | None = None) -> None:
        self.clients[client] = island_id

    def remove_client(self, client) -> None:
        self.clients.pop(client, None)

    async def emit(self, island_id: str | None, event: dict) -> None:
        event = {**event, "ts": datetime.now(UTC).isoformat()}
        await Event.objects.acreate(island_id=island_id, type=event["type"], payload=event)
        for hook in self.hooks:
            try:
                hook(island_id, event)
            except Exception:
                log.exception("event hook failed")
        await self.broadcast(event, island_id)

    async def broadcast(self, event: dict, island_id: str | None = None) -> None:
        """Send to every client watching `island_id` (None = send to everyone)."""
        for client, watching in list(self.clients.items()):
            if island_id is not None and watching is not None and watching != island_id:
                continue
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
    task: str | None = None  # the task package the resident is working on
    detail: str | None = None  # e.g. the exact command a permission bubble asks about
    asked_at: str = ""  # ISO-8601 UTC, so a client that connects later can still say "2m ago"


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
        self,
        island_id: str,
        agent: str,
        kind: str,
        text: str,
        timeout: float,
        options: list[str] | None = None,
        task: str | None = None,
        detail: str | None = None,
    ) -> str | None:
        """Show a bubble. Returns your answer, or None if nobody answered in time."""
        bubble_id = "b_" + uuid.uuid4().hex[:6]
        future = asyncio.get_running_loop().create_future()
        asked_at = datetime.now(UTC).isoformat()
        self.open[bubble_id] = OpenBubble(
            bubble_id, island_id, agent, kind, text, options, future, task, detail, asked_at
        )
        await self.bus.emit(island_id, bubble(agent, kind, bubble_id, text, options, task, detail))
        response: str | None = None
        try:
            response = await asyncio.wait_for(future, timeout)
            return response
        except TimeoutError:
            return None
        finally:
            del self.open[bubble_id]
            await self.bus.emit(island_id, bubble_resolved(bubble_id, response is not None, response))

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
            {
                "id": b.id,
                "agent": b.agent,
                "kind": b.kind,
                "text": b.text,
                "options": b.options,
                "task": b.task,
                "detail": b.detail,
                "asked_at": b.asked_at,
            }
            for b in self.open.values()
            if b.island_id == island_id
        ]
