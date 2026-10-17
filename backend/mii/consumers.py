"""The WebSocket the game connects to: ws://localhost:8000/ws[?island=<id>][&token=<jwt>]

Channels concept: a *consumer* is to a WebSocket what a view is to an HTTP
request. One instance per connection. We don't use a channel layer (no Redis):
the EventBus keeps the connected consumers in memory and calls send_json().
"""

import asyncio
import contextlib
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from mii import auth, events, islands, layout, residents, talk, tasks
from mii.models import Island, island_layout, island_snapshot
from mii.runtime import Runtime, get_runtime

_talks: set[asyncio.Task] = set()  # running `talk` answers (kept so they aren't garbage collected)


async def handle_message(
    runtime: Runtime, island_id: str, message: object, send=None, sub: str | None = None
) -> dict | None:
    """Client -> server messages (docs/protocol.md). Unknown types are ignored.

    Returns an `error` event for the sender only, or None. Anything every client
    should see goes through runtime.bus. A message's optional `request_id` is
    echoed on the error, so the client can tell which command was refused.
    `send(event)` reaches the sender alone (a `talk` answers that way, later).
    """
    if not isinstance(message, dict):
        return events.error("message must be a JSON object")
    reply = await _handle(runtime, island_id, message, send, sub)
    if reply is not None and message.get("request_id") is not None:
        reply["request_id"] = str(message["request_id"])
    return reply


async def _handle(runtime: Runtime, island_id: str, message: dict, send=None, sub: str | None = None) -> dict | None:
    kind = message.get("type")
    if kind in ("talk", "talk_open"):
        if send is None:
            return events.error(f"{kind} needs a WebSocket connection")
        answer = talk.talk if kind == "talk" else talk.open_conversation
        job = asyncio.create_task(answer(runtime, island_id, message, send))  # a real brain takes a while
        _talks.add(job)
        job.add_done_callback(_talks.discard)
    elif kind == "talk_close":
        await talk.close_conversation(runtime, str(message.get("id", "")))
    elif kind == "answer_bubble":
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
        if "buildings" in data:  # who works where changed too
            await runtime.bus.emit(island_id, events.layout(**await island_layout(island_id)))
    elif kind == "delete_resident":
        resident_id = str(message.get("id", ""))
        try:
            await residents.delete_resident(island_id, resident_id)
        except residents.ResidentError as e:
            return events.error(str(e))
        await runtime.bus.emit(island_id, events.resident_removed(resident_id))
    elif kind == "create_task":
        try:
            island = await Island.objects.aget(pk=island_id)
            await tasks.submit_task(runtime.workflow, island, message.get("task"))  # emits the `task` event
        except Island.DoesNotExist:
            return events.error(f"no island {island_id!r}")
        except tasks.TaskError as e:
            return events.error(str(e))
    elif kind == "cancel_task":
        task_id = str(message.get("id", ""))
        try:
            await runtime.workflow.cancel(task_id)  # emits `task` with status cancelled
        except KeyError:
            return events.error(f"unknown task id: {task_id}")
        except ValueError as e:
            return events.error(str(e))
    elif kind == "set_layout":
        try:
            buildings, roads = layout.clean_layout(island_id, message)
            saved = await sync_to_async(layout.save_layout)(island_id, buildings, roads)
        except layout.LayoutError as e:
            return events.error(str(e))
        await runtime.bus.emit(island_id, events.layout(**saved))
    elif kind == "update_island":
        data = message.get("island")
        if not isinstance(data, dict):
            return events.error("update_island needs an `island` object")
        try:
            island = await asyncio.to_thread(islands.update_island, island_id, data, sub)
        except Island.DoesNotExist:
            return events.error(f"no island {island_id!r}")
        except islands.IslandError as e:
            return events.error(str(e))
        await runtime.bus.emit(island_id, events.island(island))
    return None


CLOSE_UNAUTHORIZED = 4401  # login on and the token is invalid or expired: log in again
CLOSE_FORBIDDEN = 4403  # a private island you are not the owner or a member of


class IslandConsumer(AsyncJsonWebsocketConsumer):
    island_id = ""
    sub: str | None = None  # the logged-in user (auth.py), None without login

    async def connect(self) -> None:
        # Read the token before accepting; the close codes below only reach the game after accept().
        query_string = self.scope.get("query_string", b"")
        token_error = None
        try:
            self.sub = auth.sub_of(await asyncio.to_thread(auth.user_from_query, query_string))
        except auth.AuthError as e:
            token_error = str(e)
        await self.accept()
        if token_error:
            await self.send_json(events.error(token_error))
            await self.close(code=CLOSE_UNAUTHORIZED)
            return
        runtime = get_runtime()
        query = parse_qs(query_string.decode())
        island_id = (query.get("island") or [""])[0]
        if not island_id:
            first = await Island.objects.order_by("id").afirst()
            island_id = first.id if first else ""
        island = await Island.objects.filter(pk=island_id).afirst()
        if island is not None and not auth.can_see(island, self.sub):
            await self.send_json(events.error("this island is not yours"))
            await self.close(code=CLOSE_FORBIDDEN)
            return
        if island is None and not await Island.objects.aexists():
            return  # a fresh install: stay connected with no island until the game founds one
        try:
            snapshot = await island_snapshot(island_id, runtime.bubbles.for_island(island_id))
        except Island.DoesNotExist:
            await self.send_json(events.error(f"no island {island_id!r}"))
            await self.close()
            return
        self.island_id = island_id
        await self.send_json(events.snapshot(snapshot, runtime.mock_agents))
        runtime.bus.add_client(self, island_id)  # only this island's events

    async def disconnect(self, code: int) -> None:
        runtime = get_runtime()
        runtime.bus.remove_client(self)
        await talk.close_all(runtime, self._send_quietly)  # residents it was talking to can go back to work

    async def receive_json(self, message: object, **kwargs: object) -> None:
        reply = await handle_message(get_runtime(), self.island_id, message, self._send_quietly, self.sub)
        if reply is not None:
            await self.send_json(reply)

    async def _send_quietly(self, event: dict) -> None:
        """Send to this client; it may have disconnected while a resident was thinking."""
        with contextlib.suppress(Exception):
            await self.send_json(event)

    async def decode_json(self, text_data: str) -> object:
        try:
            return await super().decode_json(text_data)
        except ValueError:
            await self.send_json(events.error("message is not valid JSON"))
            return {}
