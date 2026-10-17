"""ASGI entry point. Run from backend/:

    uv run uvicorn mii.asgi:application --reload --port 8000

One process serves HTTP (Django views), the WebSocket (Channels consumer) and
runs the workflow engine (started by the `lifespan` handler below).
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mii.settings")
django_asgi_app = get_asgi_application()  # also calls django.setup(), so import the rest after this

from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import path

from mii.consumers import IslandConsumer
from mii.runtime import get_runtime


async def lifespan(scope, receive, send):
    """ASGI lifespan: start the engine worker with the server, stop it with the server."""
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            try:
                await get_runtime().start()
            except Exception as e:  # e.g. the database file isn't writable
                await send({"type": "lifespan.startup.failed", "message": str(e)})
                return
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await get_runtime().stop()
            await send({"type": "lifespan.shutdown.complete"})
            return


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter([path("ws", IslandConsumer.as_asgi())]),
        "lifespan": lifespan,
    }
)
