"""Tiny WebSocket client: prints every event from the backend.

Run (with the server running):  uv run python -m mii.watch
"""

import asyncio
import json
import sys

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


if __name__ == "__main__":
    try:
        asyncio.run(watch(sys.argv[1] if len(sys.argv) > 1 else URL))
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"could not connect: {e}. Is the server running?")
