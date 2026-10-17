"""The WebSocket the game connects to: ws://localhost:8000/ws[?island=<id>]

Channels concept: a *consumer* is to a WebSocket what a view is to an HTTP
request. One instance per connection. We don't use a channel layer (no Redis):
the EventBus keeps the connected consumers in memory and calls send_json().
"""

from urllib.parse import parse_qs

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from mii import events, residents
from mii.models import Island, island_snapshot
from mii.runtime import get_runtime


async def handle_message(runtime, island_id: str, message) -> dict | None:
    """Client -> server messages (docs/protocol.md). Unknown types are ignored.

    Returns an `error` event for the sender only, or None. Anything every client
    should see goes through runtime.bus.
    """
    if not isinstance(message, dict):
        return events.error("message must be a JSON object")
    kind = message.get("type")
    if kind == "answer_bubble":
        bubble_id = str(message.get("id", ""))
        if not runtime.bubbles.answer(bubble_id, str(message.get("response", ""))):
            return events.error(f"unknown bubble id: {bubble_id}")
    elif kind in ("create_resident", "update_resident"):
        data = message.get("resident")
        if not isinstance(data, dict):
            return events.error(f"{kind} needs a `resident` object")
        save = residents.create_resident if kind == "create_resident" else residents.update_resident
        try:
            resident = await save(island_id, data)
        except residents.ResidentError as e:
            return events.error(str(e))
        await runtime.bus.emit(island_id, events.resident(resident))  # every client, including the sender
    return None


class IslandConsumer(AsyncJsonWebsocketConsumer):
    island_id = ""

    async def connect(self) -> None:
        await self.accept()
        runtime = get_runtime()
        query = parse_qs(self.scope.get("query_string", b"").decode())
        island_id = (query.get("island") or [""])[0]
        if not island_id:
            first = await Island.objects.order_by("id").afirst()
            island_id = first.id if first else ""
        try:
            snapshot = await island_snapshot(island_id, runtime.bubbles.for_island(island_id))
        except Island.DoesNotExist:
            await self.send_json(events.error(f"no island {island_id!r}"))
            await self.close()
            return
        self.island_id = island_id
        await self.send_json({"type": "snapshot", "island": snapshot})
        runtime.bus.add_client(self, island_id)  # only this island's events

    async def disconnect(self, code) -> None:
        get_runtime().bus.remove_client(self)

    async def receive_json(self, message, **kwargs) -> None:
        reply = await handle_message(get_runtime(), self.island_id, message)
        if reply is not None:
            await self.send_json(reply)

    async def decode_json(self, text_data):
        try:
            return await super().decode_json(text_data)
        except ValueError:
            await self.send_json(events.error("message is not valid JSON"))
            return {}
