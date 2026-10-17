"""Tiny WebSocket client: prints every event from the backend (`manage.py watch`)."""

import asyncio
import json

import websockets

URL = "ws://localhost:8000/ws"


async def watch(url: str) -> None:
    async with websockets.connect(url) as ws:
        print(f"connected to {url}  (Ctrl+C to stop)\n")
        async for raw in ws:
            event = json.loads(raw)
            if event["type"] == "snapshot":
                island = event["island"]
                print(
                    f"snapshot  {island['name']}: {len(island['buildings'])} buildings, "
                    f"{len(island['residents'])} residents, {len(island['bubbles'])} open bubbles"
                )
            else:
                fields = "  ".join(f"{k}={v}" for k, v in event.items() if k not in ("type", "ts"))
                print(f"{event['type']:<16} {fields}")


def main(url: str = URL) -> None:
    try:
        asyncio.run(watch(url))
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"could not connect: {e}. Is the server running?")
