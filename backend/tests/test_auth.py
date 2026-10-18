"""Auth0 login (auth.py): tokens, who sees which island, the 401/403 answers, the device flow.

A throwaway RSA key stands in for Auth0's: tokens are signed with it and `_signing_key`
returns its public half, so nothing reaches the network.
"""

import json
import time
from dataclasses import replace

import httpx
import jwt
import pytest
from asgiref.testing import ApplicationCommunicator
from conftest import db_test
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import AsyncClient

from mii import auth, consumers, islands, views
from mii.config import config
from mii.models import Island

DOMAIN = "mii.test.auth0.com"
AUDIENCE = "https://mii-nagement/api"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(sub: str = "auth0|alice", audience: str = AUDIENCE, expires_in: int = 3600) -> str:
    claims = {"sub": sub, "aud": audience, "iss": f"https://{DOMAIN}/", "exp": int(time.time()) + expires_in}
    return jwt.encode(claims, KEY, algorithm="RS256")


def bearer(sub: str = "auth0|alice") -> dict:
    return {"Authorization": f"Bearer {token(sub)}"}


@pytest.fixture
def login_on(monkeypatch):
    """Login switched on, with the test key as Auth0's."""
    on = replace(config, auth0_domain=DOMAIN, auth0_audience=AUDIENCE, auth0_client_id="client-1")
    monkeypatch.setattr(auth, "config", on)
    monkeypatch.setattr(auth, "_signing_key", lambda _token: KEY.public_key())
    return on


async def make_private(island_id: str = "sunny", owner: str = "auth0|alice", members: list | None = None) -> None:
    await Island.objects.filter(pk=island_id).aupdate(owner=owner, members=members or [])


# --- Tokens ----------------------------------------------------------------------------


def test_verify_accepts_a_good_token_and_refuses_bad_ones(login_on):
    assert auth.verify(token())["sub"] == "auth0|alice"
    with pytest.raises(auth.AuthError):
        auth.verify(token(expires_in=-60))  # expired
    with pytest.raises(auth.AuthError):
        auth.verify(token(audience="https://someone-else"))
    with pytest.raises(auth.AuthError):
        auth.verify("not-a-jwt")


def test_login_off_lets_everything_through():
    assert not auth.enabled()
    assert auth.user_from_query(b"island=sunny&token=garbage") is None  # not even looked at
    assert auth.can_see(Island(id="x", owner="auth0|alice"), None)


def test_can_see_public_owner_and_members(login_on):
    assert auth.can_see(Island(id="pub"), None)
    private = Island(id="priv", owner="auth0|alice", members=["auth0|bob"])
    assert auth.can_see(private, "auth0|alice") and auth.can_see(private, "auth0|bob")
    assert not auth.can_see(private, "auth0|eve") and not auth.can_see(private, None)


# --- HTTP ------------------------------------------------------------------------------


@db_test
async def test_island_list_shows_public_owned_and_member_islands(island, fake_runtime, login_on):
    await Island.objects.acreate(id="alices", name="Alice's", owner="auth0|alice")
    await Island.objects.acreate(id="shared", name="Shared", owner="auth0|carol", members=["auth0|alice"])
    await Island.objects.acreate(id="carols", name="Carol's", owner="auth0|carol")

    async def ids(**headers) -> set[str]:
        return {i["id"] for i in (await AsyncClient().get("/islands", headers=headers)).json()["islands"]}

    assert await ids() == {"sunny"}
    assert await ids(**bearer("auth0|alice")) == {"sunny", "alices", "shared"}
    assert await ids(**bearer("auth0|carol")) == {"sunny", "shared", "carols"}
    response = await AsyncClient().get("/islands", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


@db_test
async def test_found_an_island_needs_a_login_and_makes_it_yours(fake_runtime, login_on, monkeypatch, tmp_path):
    monkeypatch.setattr(islands, "default_folder", lambda: tmp_path)
    body = json.dumps({"name": "Alice Isle"})
    anonymous = await AsyncClient().post("/islands", data=body, content_type="application/json")
    assert anonymous.status_code == 401
    ok = await AsyncClient().post("/islands", data=body, content_type="application/json", headers=bearer())
    assert ok.status_code == 200
    assert ok.json()["owner"] == "auth0|alice" and ok.json()["members"] == []


@db_test
async def test_island_guard_401_and_403(island, fake_runtime, login_on):
    client = AsyncClient()
    assert (await client.get("/islands/sunny")).status_code == 200  # public
    await make_private(members=["auth0|bob"])
    assert (await client.get("/islands/sunny")).status_code == 401
    assert (await client.get("/islands/sunny/tasks", headers=bearer("auth0|eve"))).status_code == 403
    assert (await client.get("/islands/sunny/insights", headers=bearer("auth0|eve"))).status_code == 403
    assert (await client.get("/islands/sunny", headers=bearer("auth0|alice"))).status_code == 200
    assert (await client.get("/islands/sunny/tasks", headers=bearer("auth0|bob"))).status_code == 200


@db_test
async def test_only_the_owner_abandons_an_island(island, fake_runtime, login_on):
    client = AsyncClient()
    assert (await client.delete("/islands/sunny")).status_code == 401  # login on: who are you?
    assert (await client.delete("/islands/sunny", headers=bearer())).status_code == 403  # public: nobody's to drop
    await Island.objects.acreate(id="alices", name="Alice's", owner="auth0|alice", members=["auth0|bob"])
    assert (await client.delete("/islands/alices", headers=bearer("auth0|bob"))).status_code == 403  # a member
    assert (await client.delete("/islands/alices", headers=bearer("auth0|alice"))).status_code == 200


@db_test
async def test_only_the_owner_changes_members(island, fake_runtime, login_on):
    await make_private()
    client = AsyncClient()

    async def patch(sub: str, members) -> int:
        body = json.dumps({"members": members})
        response = await client.patch("/islands/sunny", data=body, content_type="application/json", headers=bearer(sub))
        return response.status_code

    assert await patch("auth0|alice", ["auth0|bob", " auth0|bob "]) == 200
    assert (await Island.objects.aget(pk="sunny")).members == ["auth0|bob"]
    assert await patch("auth0|bob", []) == 400  # a member, not the owner
    assert await patch("auth0|alice", "auth0|bob") == 400  # not a list


@db_test
async def test_settings_need_a_login(fake_runtime, login_on):
    response = await AsyncClient().put("/settings", data=json.dumps({}), content_type="application/json")
    assert response.status_code == 401
    assert (await AsyncClient().get("/auth/config")).json() == {"enabled": True}
    me = await AsyncClient().get("/auth/me", headers=bearer())
    assert me.status_code == 200 and me.json()["sub"] == "auth0|alice"


@db_test
async def test_server_side_lookups_need_a_login(fake_runtime, login_on, monkeypatch):
    # They use this machine's gh login and agent CLIs, so a visitor can't call them.
    monkeypatch.setattr(views.github, "list_repos", lambda q: [{"repo": "me/app"}])
    client = AsyncClient()
    for path in ("/github/repos", "/github/branches?repo=me/app", "/brains/nope/models"):
        assert (await client.get(path)).status_code == 401, path
    assert (await client.get("/github/repos", headers=bearer())).json() == {"repos": [{"repo": "me/app"}]}
    assert (await client.get("/brains/nope/models", headers=bearer())).status_code == 404  # no such brain


# --- WebSocket ---------------------------------------------------------------------------


async def connect(query: str) -> tuple[ApplicationCommunicator, dict]:
    """Open /ws?<query> (plain asgiref: channels.testing needs daphne). Returns the first message."""
    scope = {"type": "websocket", "path": "/ws", "query_string": query.encode(), "headers": [], "subprotocols": []}
    communicator = ApplicationCommunicator(consumers.IslandConsumer.as_asgi(), scope)
    await communicator.send_input({"type": "websocket.connect"})
    assert (await communicator.receive_output())["type"] == "websocket.accept"
    return communicator, json.loads((await communicator.receive_output())["text"])


@db_test
async def test_websocket_close_codes(island, fake_runtime, login_on, monkeypatch):
    monkeypatch.setattr(consumers, "get_runtime", lambda: fake_runtime)
    await make_private()

    communicator, first = await connect("island=sunny&token=garbage")
    assert first["type"] == "error"
    assert (await communicator.receive_output())["code"] == consumers.CLOSE_UNAUTHORIZED

    communicator, first = await connect(f"island=sunny&token={token('auth0|eve')}")
    assert first["type"] == "error"
    assert (await communicator.receive_output())["code"] == consumers.CLOSE_FORBIDDEN

    communicator, first = await connect(f"island=sunny&token={token()}")
    assert first["type"] == "snapshot"
    await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
    await communicator.wait()


@db_test
async def test_websocket_without_islands_stays_open(fake_runtime, monkeypatch):
    """A fresh install has no island: the socket stays open (the game is online) and sends nothing."""
    monkeypatch.setattr(consumers, "get_runtime", lambda: fake_runtime)
    for query in ("", "island=gone"):
        scope = {"type": "websocket", "path": "/ws", "query_string": query.encode(), "headers": [], "subprotocols": []}
        communicator = ApplicationCommunicator(consumers.IslandConsumer.as_asgi(), scope)
        await communicator.send_input({"type": "websocket.connect"})
        assert (await communicator.receive_output())["type"] == "websocket.accept"
        assert await communicator.receive_nothing()
        await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await communicator.wait()


# --- Device flow -------------------------------------------------------------------------


@db_test
async def test_device_flow_proxies_to_auth0(login_on, monkeypatch):
    seen: list[tuple[str, dict]] = []
    id_token = jwt.encode({"name": "Alice", "email": "alice@example.com"}, "x" * 32, algorithm="HS256")
    started = {"user_code": "ABCD-EFGH", "verification_uri_complete": "https://x/a", "device_code": "dc-1"}
    polls = iter(
        [
            httpx.Response(403, json={"error": "authorization_pending"}),
            httpx.Response(200, json={"access_token": "at-1", "expires_in": 86400, "id_token": id_token}),
            httpx.Response(403, json={"error": "expired_token", "error_description": "the code expired"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        seen.append((request.url.path, form))
        if request.url.path == "/oauth/device/code":
            return httpx.Response(200, json={**started, "interval": 5, "expires_in": 900})
        return next(polls)

    monkeypatch.setattr(auth, "http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    client = AsyncClient()

    started = (await client.post("/auth/device")).json()
    assert started["user_code"] == "ABCD-EFGH" and started["device_code"] == "dc-1"
    assert seen[0][1]["audience"] == AUDIENCE and seen[0][1]["client_id"] == "client-1"

    async def poll():
        body = json.dumps({"device_code": "dc-1"})
        return await client.post("/auth/token", data=body, content_type="application/json")

    assert (await poll()).json() == {"pending": True}
    done = (await poll()).json()
    assert done == {"access_token": "at-1", "expires_in": 86400, "name": "Alice", "email": "alice@example.com"}
    assert seen[-1][1]["grant_type"] == auth.DEVICE_GRANT
    assert (await poll()).status_code == 400  # expired: start again


@db_test
async def test_device_flow_is_404_when_login_is_off():
    assert (await AsyncClient().post("/auth/device")).status_code == 404
    assert (await AsyncClient().get("/auth/config")).json() == {"enabled": False}
