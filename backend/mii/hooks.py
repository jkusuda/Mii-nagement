"""The spectator feed: copies a few island events to an outside web page (spectator/).

Outbound only, and off unless the `spectator_url` setting is set. EventBus.emit() calls
the hook after saving each event; the hook only drops the event in a small queue, and one
background task POSTs it with `Authorization: Bearer <spectator_secret>`. A slow or dead
feed fills the queue and events are dropped, so it can never slow the engine down.
The secret is PRIVATE_ENV (prefs.py): it never reaches agent processes.
"""

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

KINDS = {"task", "handoff", "bubble", "bubble_resolved", "chat", "weather", "mood"}  # what spectators see
QUEUE_SIZE = 200
TIMEOUT = 3.0  # seconds per POST


class SpectatorHook:
    def __init__(self, url: str | None = None, secret: str | None = None, http: httpx.AsyncClient | None = None):
        self.url = url
        self.secret = secret
        self.http = http  # tests inject a MockTransport client; made on first use otherwise
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.worker: asyncio.Task | None = None
        self._warned = False

    def configure(self, url: str | None, secret: str | None) -> None:
        """New settings (runtime.reload). A None url switches the feed off."""
        self.url, self.secret = url or None, secret

    def __call__(self, island_id: str | None, event: dict) -> None:
        """EventBus hook: synchronous, never blocks, never raises."""
        if not self.url or event.get("type") not in KINDS:
            return
        try:
            self.queue.put_nowait({"island": island_id, **event})
        except asyncio.QueueFull:
            log.debug("spectator queue full; dropping a %s event", event.get("type"))
            return
        if self.worker is None or self.worker.done():
            self.worker = asyncio.get_running_loop().create_task(self._work())

    async def _work(self) -> None:
        while True:
            item = await self.queue.get()
            if self.url:  # switched off while it waited: drop it
                await self._post(item)
            self.queue.task_done()

    async def _post(self, item: dict) -> None:
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=TIMEOUT)
        headers = {"Authorization": f"Bearer {self.secret}"} if self.secret else {}
        try:
            response = await self.http.post(self.url, json=item, headers=headers, timeout=TIMEOUT)
            response.raise_for_status()
            self._warned = False
        except httpx.HTTPError as e:
            if not self._warned:  # once, not on every event
                log.warning("the spectator feed is unavailable (%s); dropping events until it is back", e)
                self._warned = True

    async def aclose(self) -> None:
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        if self.http is not None:
            await self.http.aclose()
            self.http = None
