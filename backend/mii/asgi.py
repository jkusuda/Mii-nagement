"""ASGI entry point. Run from backend/:

    uv run uvicorn mii.asgi:application --reload --port 8000

One process serves HTTP (Django views), the WebSocket (Channels consumer) and
runs the workflow engine (started by the `lifespan` handler below).
"""

import ipaddress
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mii.settings")
django_asgi_app = get_asgi_application()  # also calls django.setup(), so import the rest after this

from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import path

from mii.config import config
from mii.consumers import IslandConsumer
from mii.runtime import get_runtime


def _header(scope: dict, name: bytes) -> str:
    return next((value.decode("latin-1") for key, value in scope.get("headers", []) if key == name), "")


def trusted(scope: dict) -> bool:
    """Is this the game, curl or another program, and not a web page?

    Agents run shell commands, so a page the user happens to visit must never drive the
    backend. Browsers send `Origin` on every WebSocket and every cross-site POST; the game
    never does. The Host check stops DNS rebinding (a page whose own name points here):
    only localhost, IP addresses and the names in config.hosts are served.
    """
    if _header(scope, b"origin"):
        return False
    host = _header(scope, b"host").lower()
    host = host[1 : host.find("]")] if host.startswith("[") else host.split(":")[0]
    if host in ("", "localhost", *config.hosts):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def browsers_refused(app):
    """Answer 403 to HTTP and WebSocket requests that aren't trusted()."""

    async def guarded(scope, receive, send):
        if scope["type"] == "http" and not trusted(scope):
            await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"Mii-nagement only talks to the game and API clients."})
            return
        if scope["type"] == "websocket" and not trusted(scope):
            await send({"type": "websocket.close", "code": 4403})  # before accept: the client gets a 403
            return
        await app(scope, receive, send)

    return guarded


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


application = browsers_refused(
    ProtocolTypeRouter(
        {
            "http": django_asgi_app,
            "websocket": URLRouter([path("ws", IslandConsumer.as_asgi())]),
            "lifespan": lifespan,
        }
    )
)
