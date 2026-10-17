"""The spectator feed (hooks.py): selected events go out with the bearer secret, never blocking."""

import asyncio
import json
import logging

import httpx
from conftest import db_test

from mii import events
from mii.hooks import QUEUE_SIZE, SpectatorHook

pytestmark = db_test

URL = "https://feed.example/ingest"


def hook_and_requests(status: int = 200) -> tuple[SpectatorHook, list[dict]]:
    """A SpectatorHook posting to a fake feed. Returns (hook, the requests the feed got)."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"auth": request.headers.get("authorization"), "body": json.loads(request.content)})
        return httpx.Response(status)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SpectatorHook(URL, "s3cret", http=http), seen


async def drain(hook: SpectatorHook) -> None:
    await asyncio.wait_for(hook.queue.join(), 2)


async def test_a_task_event_is_posted_with_the_secret_and_the_island(island):
    hook, seen = hook_and_requests()
    bus = events.EventBus()
    bus.add_hook(hook)
    await bus.emit("sunny", {"type": "task", "task": {"id": "t_1"}})
    await bus.emit("sunny", {"type": "thinking", "resident_id": "carlos"})
    await bus.emit("sunny", {"type": "activity", "resident_id": "carlos"})
    await drain(hook)
    assert len(seen) == 1
    assert seen[0]["auth"] == "Bearer s3cret"
    assert seen[0]["body"]["island"] == "sunny" and seen[0]["body"]["type"] == "task"
    await hook.aclose()


async def test_a_failing_feed_warns_once_and_keeps_posting(island, caplog):
    hook, seen = hook_and_requests(status=500)
    with caplog.at_level(logging.WARNING, logger="mii.hooks"):
        hook("sunny", {"type": "chat"})
        hook("sunny", {"type": "mood"})
        await drain(hook)
    assert len(seen) == 2  # the second event was still sent
    assert sum(r.levelno == logging.WARNING for r in caplog.records) == 1
    await hook.aclose()


async def test_overflow_drops_without_raising(island):
    hook, seen = hook_and_requests()
    for i in range(QUEUE_SIZE + 50):  # no await in between: the worker can't drain yet
        hook("sunny", {"type": "chat", "n": i})
    assert hook.queue.qsize() == QUEUE_SIZE
    await drain(hook)
    assert len(seen) == QUEUE_SIZE
    await hook.aclose()


async def test_configure_none_switches_it_off(island):
    hook, seen = hook_and_requests()
    hook.configure(None, "s3cret")
    hook("sunny", {"type": "task"})
    assert hook.queue.empty() and hook.worker is None
    hook.configure(URL, "s3cret")
    hook("sunny", {"type": "task"})
    await drain(hook)
    assert len(seen) == 1
    await hook.aclose()
