"""Optional Auth0 login: who is calling, and which islands they may see.

Off unless AUTH0_DOMAIN, AUTH0_AUDIENCE and AUTH0_CLIENT_ID are all set; then every
request may carry `Authorization: Bearer <jwt>` (the WebSocket: `/ws?token=<jwt>`).

    verify(token)        the token's claims, checked against Auth0's public keys (RS256,
                         audience, issuer, expiry), or AuthError
    can_see(island, sub) public (no owner), or the owner, or a member
    island_guard         decorator for views with an `island_id`: 401 / 403 for private islands
    device_code(), poll_token()
                         the Device Authorization Flow, proxied so the game never talks to Auth0

With login off everything is allowed, exactly like before.
"""

import asyncio
import functools
from urllib.parse import parse_qs

import httpx
import jwt
from django.http import JsonResponse

from mii.config import config
from mii.models import Island

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
PENDING = {"authorization_pending", "slow_down"}  # keep polling

http: httpx.AsyncClient | None = None  # the Auth0 connection; tests put a MockTransport client here
_jwks: jwt.PyJWKClient | None = None


class AuthError(Exception):
    """A missing-but-required, invalid or expired token (401), or a refused device login."""


def enabled() -> bool:
    return bool(config.auth0_domain and config.auth0_audience and config.auth0_client_id)


def _base() -> str:
    return f"https://{config.auth0_domain}"


def _signing_key(token: str):
    """The Auth0 public key the token was signed with (fetched once, then cached). Sync (network)."""
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(f"{_base()}/.well-known/jwks.json")
    return _jwks.get_signing_key_from_jwt(token).key


def verify(token: str) -> dict:
    """The claims of a valid access token. Raises AuthError. Sync (the first call fetches the keys)."""
    try:
        return jwt.decode(
            token,
            _signing_key(token),
            algorithms=["RS256"],
            audience=config.auth0_audience,
            issuer=f"{_base()}/",
            options={"require": ["exp", "sub"]},
        )
    except (jwt.PyJWTError, ValueError) as e:
        raise AuthError(f"invalid token: {e}")


def _claims(token: str | None) -> dict | None:
    """None for no token (or login off); the claims for a valid one; AuthError otherwise."""
    if not enabled() or not token:
        return None
    return verify(token)


def user(request) -> dict | None:
    """The caller's claims from the Bearer header, or None. Raises AuthError on a bad token. Sync."""
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else None
    return _claims(token)


def user_from_query(query_string: bytes | str) -> dict | None:
    """Same, from the WebSocket's `?token=`. Sync."""
    if isinstance(query_string, bytes):
        query_string = query_string.decode()
    return _claims((parse_qs(query_string).get("token") or [""])[0])


def sub_of(claims: dict | None) -> str | None:
    return claims.get("sub") if claims else None


def can_see(island: Island, sub: str | None) -> bool:
    if not enabled() or not island.owner:
        return True
    return sub is not None and (sub == island.owner or sub in (island.members or []))


def _error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def island_guard(view):
    """For views whose URL has an `island_id`: 401 without a valid token, 403 for someone who
    is not the owner or a member. Public islands (no owner) and login off: always allowed."""

    @functools.wraps(view)
    async def guarded(request, island_id: str, *args, **kwargs):
        if enabled():
            denied = await check_island(request, island_id)
            if denied is not None:
                return denied
        return await view(request, island_id, *args, **kwargs)

    return guarded


async def check_island(request, island_id: str) -> JsonResponse | None:
    """The 401/403 answer for this caller and island, or None when they may go on."""
    island = await Island.objects.filter(pk=island_id).afirst()
    if island is None or can_see(island, None):
        return None  # a missing island is the view's 404; a public one is open
    try:
        sub = sub_of(await asyncio.to_thread(user, request))
    except AuthError as e:
        return _error(str(e), 401)
    if sub is None:
        return _error("log in to see this island", 401)
    return None if can_see(island, sub) else _error("this island is not yours", 403)


# --- Device Authorization Flow (POST /auth/device, POST /auth/token) -------------------


def _client() -> httpx.AsyncClient:
    global http
    if http is None:
        http = httpx.AsyncClient(timeout=10.0)
    return http


async def device_code() -> dict:
    """Start a login: the code the player types on their phone, and where."""
    response = await _client().post(
        f"{_base()}/oauth/device/code",
        data={
            "client_id": config.auth0_client_id,
            "scope": "openid profile email",
            "audience": config.auth0_audience,
        },
    )
    body = response.json()
    if response.status_code != 200:
        raise AuthError(body.get("error_description") or body.get("error") or "Auth0 refused the login")
    keys = ("user_code", "verification_uri_complete", "device_code", "interval", "expires_in")
    return {key: body.get(key) for key in keys}


async def poll_token(code: str) -> dict:
    """One poll: {"pending": true} until the player approves, then the token and who they are."""
    response = await _client().post(
        f"{_base()}/oauth/token",
        data={"grant_type": DEVICE_GRANT, "device_code": code, "client_id": config.auth0_client_id},
    )
    body = response.json()
    if body.get("error") in PENDING:
        return {"pending": True}
    if response.status_code != 200 or "access_token" not in body:
        raise AuthError(body.get("error_description") or body.get("error") or "Auth0 refused the login")
    profile = {}
    if body.get("id_token"):
        # Display only (the name in the top bar): the access token is what gets verified.
        profile = jwt.decode(body["id_token"], options={"verify_signature": False})
    return {
        "access_token": body["access_token"],
        "expires_in": body.get("expires_in"),
        "name": profile.get("name"),
        "email": profile.get("email"),
    }
