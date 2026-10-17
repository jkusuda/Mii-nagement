"""HTTP endpoints: plain async Django views, JSON in / JSON out.

The routes are listed in urls.py and documented in docs/http-api.md. Views only parse
the request and pick the status code; the work happens in the module each one calls
(islands, residents, tasks, layout, insights, prefs, replay, github).
"""

import asyncio
import json

import httpx
from asgiref.sync import sync_to_async
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from mii import auth, events, github, islands, layout, metrics, prefs, residents, tasks, vocab, weather
from mii.agents.acp import GIFTS, BrainError, available_brains, available_gifts, list_brain_models
from mii.agents.openrouter import list_models
from mii.auth import island_guard
from mii.config import config
from mii.insights import RANGES, island_insights
from mii.island_map import SHAPES
from mii.models import (
    DEFAULT_KEYS,
    Event,
    Island,
    Step,
    Task,
    island_snapshot,
    island_to_dict,
    task_detail_dict,
    task_to_dict,
)
from mii.personality import JOBS, TRAITS
from mii.replay import ReplayError, replay_task, task_window
from mii.runtime import get_runtime


def _error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _json_body(request: HttpRequest) -> dict | JsonResponse:
    """The request body as a dict, or the 400 to answer with."""
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("body is not valid JSON", 400)
    except RequestDataTooBig:
        return _error("the request is too big (attachments: at most 8 MB together)", 413)
    if not isinstance(body, dict):
        return _error("body must be a JSON object", 400)
    return body


def _int_param(request: HttpRequest, name: str, default: int, cap: int) -> int | JsonResponse:
    """A positive whole-number query parameter capped at `cap`, or the 400 to answer with."""
    try:
        return min(max(1, int(request.GET.get(name, str(default)))), cap)
    except ValueError:
        return _error(f"{name} must be a number", 400)


async def _island_or_404(island_id: str) -> Island | JsonResponse:
    island = await Island.objects.filter(pk=island_id).afirst()
    return island if island is not None else _error(f"no island {island_id!r}", 404)


async def _snapshot(island_id: str) -> dict:
    """The snapshot event's `island`, including `mock_agents`. Raises Island.DoesNotExist."""
    runtime = get_runtime()
    island = await island_snapshot(island_id, runtime.bubbles.for_island(island_id))
    return events.snapshot(island, runtime.mock_agents)["island"]


@require_GET
async def health(request: HttpRequest) -> JsonResponse:
    runtime = get_runtime()
    return JsonResponse(
        {
            "ok": True,
            "mock_agents": runtime.mock_agents,
            "judge": runtime.judge.name,
            "database": metrics.database(),  # sqlite | postgres | timescale
        }
    )


async def _caller(request: HttpRequest, required: bool = False) -> str | JsonResponse | None:
    """The logged-in user's id (auth.py), None for nobody (or login off), or the 401 to answer
    with: a bad token, or no token when `required` and login is on."""
    try:
        sub = auth.sub_of(await asyncio.to_thread(auth.user, request))
    except auth.AuthError as e:
        return _error(str(e), 401)
    if required and sub is None and auth.enabled():
        return _error("log in first", 401)
    return sub


def _brains() -> dict[str, dict]:
    """available_brains() with the API keys saved in Settings (keychain reads: run in a thread)."""
    return available_brains(prefs.secret("openrouter_api_key"), prefs.secrets_env())


@require_GET
async def meta(request: HttpRequest) -> JsonResponse:
    """Every list the game shows pickers for (vocab.py, plus jobs, traits, gifts and keys).

    Static. The game mirrors the same lists in scenes/protocol.gd and resident_data.gd.
    """
    return JsonResponse(
        {
            "jobs": list(JOBS),
            "job_buildings": vocab.JOB_BUILDINGS,
            "traits": {name: {"low": lo, "medium": mid, "high": hi} for name, (lo, mid, hi) in TRAITS.items()},
            "brains": list(vocab.BRAIN_NAMES),
            "gifts": {gift: spec["label"] for gift, spec in GIFTS.items()},
            "keys": {"names": list(DEFAULT_KEYS), "values": list(residents.KEY_VALUES), "defaults": DEFAULT_KEYS},
            "building_types": list(vocab.STEP_ORDER),
            "road_conditions": list(vocab.ROAD_CONDITIONS),
            "plans": {plan: {"steps": list(steps)} for plan, steps in vocab.PLANS.items()},
            "shapes": list(SHAPES),
            "rules": list(vocab.RULES),
            "bubble_kinds": list(vocab.BUBBLE_KINDS),
            "moods": list(vocab.MOODS),
            "states": list(vocab.STATES),
            "task_statuses": list(vocab.TASK_STATUSES),
            "weather": list(weather.KINDS),
        }
    )


@require_GET
async def brains(request: HttpRequest) -> JsonResponse:
    return JsonResponse({brain: info["ok"] for brain, info in (await asyncio.to_thread(_brains)).items()})


@require_GET
async def brains_detail(request: HttpRequest) -> JsonResponse:
    return JsonResponse(await asyncio.to_thread(_brains))


@require_GET
async def gifts(request: HttpRequest) -> JsonResponse:
    return JsonResponse(await asyncio.to_thread(available_gifts))


@require_GET
async def models_list(request: HttpRequest) -> JsonResponse:
    api_key = await asyncio.to_thread(prefs.secret, "openrouter_api_key")
    if not api_key:
        return _error("no OpenRouter API key: save one with PUT /settings or set OPENROUTER_API_KEY", 503)
    try:
        models = await list_models(api_key)
    except Exception as e:
        return _error(f"could not fetch the OpenRouter catalogue: {e}", 502)
    return JsonResponse({"models": models})


@require_GET
async def brain_models(request: HttpRequest, brain: str) -> JsonResponse:
    """The models a brain offers here, for the model pickers. ACP brains are asked directly
    (a short-lived session, no prompt; cached 10 minutes, `?refresh=1` asks again);
    `openrouter` is its catalogue. {"models": [{id, name, ...}], "current": the default}."""
    if brain == "openrouter":
        response = await models_list(request)
        if response.status_code == 200:
            current = await sync_to_async(prefs.get)("openrouter_model")
            return JsonResponse({"models": json.loads(response.content)["models"], "current": current})
        return response
    if brain not in vocab.ACP_BRAINS:
        return _error(f"{brain} has no models to pick from" if brain in vocab.BRAIN_NAMES else "unknown brain", 404)
    try:
        return JsonResponse(await list_brain_models(brain, refresh=request.GET.get("refresh") == "1"))
    except BrainError as e:
        return _error(str(e), 503)


@csrf_exempt
async def islands_view(request: HttpRequest) -> JsonResponse:
    """GET: every island the caller may see (the Archipelago screen). POST: found an island (the
    island creator); with login on, you must be logged in and the island is yours."""
    if request.method == "GET":
        sub = await _caller(request)
        if isinstance(sub, JsonResponse):
            return sub
        # Public, owned or member-of, in Python: islands are few, and SQLite has no JSON-list "contains".
        visible = [island_to_dict(i) async for i in Island.objects.order_by("name") if auth.can_see(i, sub)]
        return JsonResponse(
            {"islands": visible, "default_folder": str(await asyncio.to_thread(islands.default_folder))}
        )
    if request.method != "POST":
        return _error("GET or POST", 405)
    sub = await _caller(request, required=True)
    if isinstance(sub, JsonResponse):
        return sub
    body = _json_body(request)
    if isinstance(body, JsonResponse):
        return body
    try:
        island = await asyncio.to_thread(islands.create_island, body, sub)
    except islands.IslandError as e:
        return _error(str(e), 400)
    return JsonResponse(await _snapshot(island.id))


@require_GET
async def github_repos(request: HttpRequest) -> JsonResponse:
    try:
        return JsonResponse({"repos": await asyncio.to_thread(github.list_repos, request.GET.get("q", ""))})
    except github.GitHubError as e:
        return _error(str(e), 503)


@require_GET
async def github_branches(request: HttpRequest) -> JsonResponse:
    repo = request.GET.get("repo", "")
    if not islands.REPO.match(repo):
        return _error("repo must look like owner/name", 400)
    try:
        return JsonResponse({"branches": await asyncio.to_thread(github.list_branches, repo)})
    except github.GitHubError as e:
        return _error(str(e), 503)


@csrf_exempt
@island_guard
async def island_view(request: HttpRequest, island_id: str) -> JsonResponse:
    """GET: the island snapshot. PATCH: rename / link a repo / change branch or rules.
    DELETE: abandon the island (its git repository stays on disk). With login on or on the
    public demo, only the island's owner may: members and visitors of a public island get 403."""
    if request.method == "DELETE":
        if auth.enabled() or config.public_demo:
            sub = await _caller(request, required=True)
            if isinstance(sub, JsonResponse):
                return sub
            island = await Island.objects.filter(pk=island_id).afirst()
            if island is not None and (island.owner is None or island.owner != sub):
                return _error("only the island's owner can abandon it", 403)
        try:
            kept = await islands.delete_island(island_id)
        except Island.DoesNotExist:
            return _error(f"no island {island_id!r}", 404)
        except islands.IslandError as e:
            return _error(str(e), 409)
        return JsonResponse({"deleted": island_id, "repo_kept": kept})
    if request.method == "PATCH":
        sub = await _caller(request)
        if isinstance(sub, JsonResponse):
            return sub
        body = _json_body(request)
        if isinstance(body, JsonResponse):
            return body
        try:
            island = await asyncio.to_thread(islands.update_island, island_id, body, sub)
        except Island.DoesNotExist:
            return _error(f"no island {island_id!r}", 404)
        except islands.IslandError as e:
            return _error(str(e), 400)
        event = events.island(island)
        await get_runtime().bus.emit(island_id, event)  # every client redraws the top bar
        return JsonResponse(event["island"])
    if request.method != "GET":
        return _error("GET, PATCH or DELETE", 405)
    try:
        return JsonResponse(await _snapshot(island_id))
    except Island.DoesNotExist:
        return _error(f"no island {island_id!r}", 404)


@require_GET
@island_guard
async def island_issues(request: HttpRequest, island_id: str) -> JsonResponse:
    """Open issues on the island's GitHub repo, newest first, so a task can start from one."""
    island = await _island_or_404(island_id)
    if isinstance(island, JsonResponse):
        return island
    if not island.repo:
        return _error("this island has no GitHub repo (link one with PATCH /islands/{id})", 400)
    limit = _int_param(request, "limit", 50, 200)
    if isinstance(limit, JsonResponse):
        return limit
    try:
        return JsonResponse({"issues": await asyncio.to_thread(github.list_issues, island.repo, limit)})
    except github.GitHubError as e:
        return _error(str(e), 503)


@csrf_exempt
@island_guard
async def resident_view(request: HttpRequest, island_id: str, resident_id: str) -> JsonResponse:
    """DELETE: the resident moves away (same as the `delete_resident` message)."""
    if request.method != "DELETE":
        return _error("DELETE", 405)
    runtime = get_runtime()
    try:
        await residents.delete_resident(island_id, resident_id)
    except residents.ResidentError as e:
        return _error(str(e), 404 if "no resident" in str(e) else 409)
    await runtime.bus.emit(island_id, events.resident_removed(resident_id))
    return JsonResponse({"deleted": resident_id})


@csrf_exempt
@island_guard
async def tasks_view(request: HttpRequest, island_id: str) -> JsonResponse:
    """GET: the island's tasks, newest first. POST: send a task package to the mailbox.

    A posted task is queued and run by the engine worker: each building's resident
    does their step through their brain (Claude Code, Codex, Gemini over ACP, or
    OpenRouter; the mock brain while `mock_agents` is on). Follow it over the
    WebSocket, or poll GET /islands/{id}/tasks/{task_id}.
    """
    island = await _island_or_404(island_id)
    if isinstance(island, JsonResponse):
        return island
    if request.method == "GET":
        return await _list_tasks(request, island_id)
    if request.method != "POST":
        return _error("GET or POST", 405)
    body = _json_body(request)
    if isinstance(body, JsonResponse):
        return body
    try:
        task = await tasks.submit_task(get_runtime().workflow, island, body)
    except tasks.TaskError as e:
        return _error(str(e), e.status)
    return JsonResponse(task_to_dict(task))


async def _list_tasks(request: HttpRequest, island_id: str) -> JsonResponse:
    statuses = [s for s in request.GET.get("status", "").split(",") if s]
    limit = _int_param(request, "limit", 50, 2000)
    if isinstance(limit, JsonResponse):
        return limit
    rows = Task.objects.filter(island_id=island_id).order_by("-created_at", "-id")
    if statuses:
        rows = rows.filter(status__in=statuses)
    tasks = [t async for t in rows[:limit]]
    steps = await _steps_by_task([t.id for t in tasks])
    return JsonResponse({"tasks": [task_detail_dict(t, steps.get(t.id, [])) for t in tasks]})


@require_GET
@island_guard
async def get_task(request: HttpRequest, island_id: str, task_id: str) -> JsonResponse:
    task = await Task.objects.filter(pk=task_id, island_id=island_id).afirst()
    if task is None:
        return _error(f"no task {task_id!r} on island {island_id!r}", 404)
    steps = await _steps_by_task([task.id])
    return JsonResponse({**task_detail_dict(task, steps.get(task.id, [])), "events": await _task_events(task)})


@csrf_exempt
@require_POST
@island_guard
async def cancel_task(request: HttpRequest, island_id: str, task_id: str) -> JsonResponse:
    """Cancel a queued task, or stop the running one. Replies with the task (now `cancelled`)."""
    if await Task.objects.filter(pk=task_id, island_id=island_id).afirst() is None:
        return _error(f"no task {task_id!r} on island {island_id!r}", 404)
    try:
        task = await get_runtime().workflow.cancel(task_id)
    except ValueError as e:
        return _error(str(e), 409)
    return JsonResponse(task_to_dict(task))


async def _steps_by_task(task_ids: list[str]) -> dict[str, list[Step]]:
    """task id -> its Step rows, oldest first."""
    steps: dict[str, list[Step]] = {}
    async for s in Step.objects.filter(task_id__in=task_ids).order_by("id"):
        steps.setdefault(s.task_id, []).append(s)
    return steps


async def _task_events(task: Task, limit: int = 200) -> list[dict]:
    """The island's stored events while this task ran (replay.task_window), the newest `limit`,
    oldest first, each the stored payload plus `event_id`: the shape of GET /islands/{id}/events."""
    window = await task_window(task.id, running_too=True)
    if window is None:
        return []  # still queued
    newest = [{"event_id": e.id, **e.payload} async for e in window.order_by("-id")[:limit]]
    return newest[::-1]


@require_GET
@island_guard
async def get_insights(request: HttpRequest, island_id: str) -> JsonResponse:
    island = await _island_or_404(island_id)
    if isinstance(island, JsonResponse):
        return island
    range_name = request.GET.get("range", "week")
    if range_name not in RANGES:
        return _error(f"range must be one of {list(RANGES)}", 400)
    return JsonResponse(await island_insights(island_id, range_name, get_runtime().judge))


@require_GET
@island_guard
async def get_events(request: HttpRequest, island_id: str) -> JsonResponse:
    """The newest `limit` stored events, oldest first: the stored payload plus `event_id`."""
    island = await _island_or_404(island_id)
    if isinstance(island, JsonResponse):
        return island
    limit = _int_param(request, "limit", 200, 2000)
    if isinstance(limit, JsonResponse):
        return limit
    newest = [
        {"event_id": e.id, **e.payload} async for e in Event.objects.filter(island_id=island_id).order_by("-id")[:limit]
    ]
    return JsonResponse({"events": newest[::-1]})


@csrf_exempt
@island_guard
async def layout_view(request: HttpRequest, island_id: str) -> JsonResponse:
    """PUT: build mode. Two forms (docs/protocol.md):

    - {"map": {...}, "buildings": [{type, x, y, facing}]}: the game's build mode.
      The island's map (land, pond, plants, season) and where each workflow step
      stands; the roads follow the steps (islands.set_map). Answers with the
      snapshot and sends it to every client.
    - {"buildings": [...], "roads": [...]}: the whole workflow graph, by hand
      (layout.py). Answers with the saved layout and sends a `layout` event.
    """
    if request.method != "PUT":
        return _error("PUT", 405)
    island = await _island_or_404(island_id)
    if isinstance(island, JsonResponse):
        return island
    body = _json_body(request)
    if isinstance(body, JsonResponse):
        return body
    if "map" in body:
        try:
            await asyncio.to_thread(islands.set_map, island_id, body)
        except islands.MapBusy as e:
            return _error(str(e), 409)
        except islands.IslandError as e:
            return _error(str(e), 400)
        runtime = get_runtime()
        snapshot = await _snapshot(island_id)
        await runtime.bus.emit(island_id, events.snapshot(snapshot, runtime.mock_agents))  # every client redraws
        return JsonResponse(snapshot)
    try:
        buildings, roads = layout.clean_layout(island_id, body)
        saved = await sync_to_async(layout.save_layout)(island_id, buildings, roads)
    except layout.LayoutError as e:
        return _error(str(e), 400)
    await get_runtime().bus.emit(island_id, events.layout(**saved))
    return JsonResponse(saved)


@csrf_exempt
@require_POST
async def replay(request: HttpRequest, task_id: str) -> JsonResponse:
    try:
        speed = float(request.GET.get("speed", "1"))
    except ValueError:
        return _error("speed must be a number", 400)
    task = await Task.objects.filter(pk=task_id).afirst()
    if task is not None and auth.enabled():
        denied = await auth.check_island(request, task.island_id)
        if denied is not None:
            return denied
    try:
        sent = await replay_task(get_runtime().bus, task_id, speed=speed)
    except ReplayError as e:
        return _error(str(e), 404)
    return JsonResponse({"replayed_events": sent})


@csrf_exempt
async def settings_view(request: HttpRequest) -> JsonResponse:
    """GET: effective settings and which secrets are set. PUT: save settings and/or secrets.

    Secret values are written to the OS keychain and never returned. A value set
    in the environment (.env) wins and is listed in `from_env`.
    """
    if request.method == "PUT":
        if config.public_demo:
            return _error("settings are locked on the public demo", 403)
        sub = await _caller(request, required=True)
        if isinstance(sub, JsonResponse):
            return sub
        body = _json_body(request)
        if isinstance(body, JsonResponse):
            return body
        settings = body.get("settings") or {}
        secrets = body.get("secrets") or {}
        if not isinstance(settings, dict) or not isinstance(secrets, dict):
            return _error("`settings` and `secrets` must be objects", 400)
        try:
            await asyncio.to_thread(prefs.set_many, settings)
            for name, value in secrets.items():
                if value is not None and not isinstance(value, str):
                    return _error(f"{name} must be text or null", 400)
                await asyncio.to_thread(prefs.set_secret, name, value)
        except prefs.PrefsError as e:
            return _error(str(e), 400)
        return JsonResponse(await get_runtime().reload())
    if request.method != "GET":
        return _error("GET or PUT", 405)
    return JsonResponse(await asyncio.to_thread(prefs.snapshot))


# --- Login (auth.py): the Device Authorization Flow, proxied to Auth0 --------------------


@require_GET
async def auth_config(request: HttpRequest) -> JsonResponse:
    """Whether the game should show "Log in"."""
    return JsonResponse({"enabled": auth.enabled()})


@require_GET
async def auth_me(request: HttpRequest) -> JsonResponse:
    try:
        claims = await asyncio.to_thread(auth.user, request)
    except auth.AuthError as e:
        return _error(str(e), 401)
    return JsonResponse(claims) if claims else _error("not logged in", 401)


@csrf_exempt
@require_POST
async def auth_device(request: HttpRequest) -> JsonResponse:
    """Start a login: {user_code, verification_uri_complete, device_code, interval, expires_in}."""
    if not auth.enabled():
        return _error("login is off on this server", 404)
    try:
        return JsonResponse(await auth.device_code())
    except (auth.AuthError, httpx.HTTPError, ValueError) as e:
        return _error(f"could not start the login: {e}", 502)


@csrf_exempt
@require_POST
async def auth_token(request: HttpRequest) -> JsonResponse:
    """Poll a login with {device_code}: {pending: true}, or {access_token, expires_in, name, email}."""
    if not auth.enabled():
        return _error("login is off on this server", 404)
    body = _json_body(request)
    if isinstance(body, JsonResponse):
        return body
    code = body.get("device_code")
    if not isinstance(code, str) or not code:
        return _error("send the device_code from POST /auth/device", 400)
    try:
        return JsonResponse(await auth.poll_token(code))
    except auth.AuthError as e:
        return _error(str(e), 400)  # expired_token, access_denied: start again
    except (httpx.HTTPError, ValueError) as e:
        return _error(f"could not reach Auth0: {e}", 502)
