"""HTTP endpoints (plain async Django views, JSON in / JSON out):

    GET  /health                       is the server up? which mode?
    GET  /brains                       {brain: true/false} (what the game's creator greys out)
    GET  /brains/detail                per brain: installed, logged_in, api_key, hint
    GET  /gifts                        per gift (MCP server): ok, label, hint (what the creator greys out)
    GET  /models                       the OpenRouter model catalogue (needs OPENROUTER_API_KEY)
    GET  /islands                      every island + the default folder for new repositories
    POST /islands                      found an island {"name", "repo", "branch", "shape", "steps" | "plan", "season",
                                       "rules", "folder", "existing_repo"}
    GET  /github/repos?q=              the user's repos (via gh), for the island creator
    GET  /github/branches?repo=        a repo's branches (via gh)
    GET  /islands/{id}                 the island snapshot
    GET  /islands/{id}/tasks           the island's tasks with their steps (?status=done,failed&limit=50)
    POST /islands/{id}/tasks           {"title": "...", "description": "..."} or {"issue": 42} -> runs through the island
    GET  /islands/{id}/tasks/{task}    one task: steps (what each resident reported) + the events while it ran
    GET  /islands/{id}/insights        stats for the Insights screen (?range=today|week|month)
    GET  /islands/{id}/events          stored events, oldest first (?limit=200), for the News tab
    PUT  /islands/{id}/layout          build mode: {"buildings": [...], "roads": [...]} -> the saved layout,
                                       or {"map": {...}, "buildings": [{type, x, y, facing}]} -> the snapshot
    GET  /settings                     settings + which API keys are set (never the keys themselves)
    PUT  /settings                     {"settings": {...}, "secrets": {"openrouter_api_key": "sk-..."|null}}
    POST /replay/{task_id}?speed=4     replay a recorded task into the game
"""

import asyncio
import json

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from asgiref.sync import sync_to_async

from mii import events, github, islands, layout, prefs, residents, tasks
from mii.agents.acp import available_brains, available_gifts
from mii.agents.openrouter import list_models
from mii.insights import RANGES, island_insights
from mii.models import Event, Island, Step, Task, island_snapshot, step_to_dict, task_detail_dict, task_to_dict
from mii.replay import ReplayError, replay_task
from mii.runtime import get_runtime


def _error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


async def _island_or_none(island_id: str) -> Island | None:
    return await Island.objects.filter(pk=island_id).afirst()


@require_GET
async def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, "mock_agents": get_runtime().mock_agents})


def _brains() -> dict[str, dict]:
    """available_brains() with the API keys saved in Settings (keychain reads: run in a thread)."""
    return available_brains(prefs.secret("openrouter_api_key"), prefs.secrets_env())


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


@csrf_exempt
async def islands_view(request: HttpRequest) -> JsonResponse:
    """GET: every island (the Archipelago screen). POST: found an island (the island creator)."""
    if request.method == "GET":
        rows = [
            {"id": i.id, "name": i.name, "repo": i.repo, "path": i.repo_path, "branch": i.branch, "plan": i.plan,
             "created_at": i.created_at.isoformat()}
            async for i in Island.objects.order_by("name")
        ]
        return JsonResponse({"islands": rows, "default_folder": str(await asyncio.to_thread(islands.default_folder))})
    if request.method != "POST":
        return _error("GET or POST", 405)
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("body must be a JSON object", 400)
    try:
        island = await asyncio.to_thread(islands.create_island, body)
    except islands.IslandError as e:
        return _error(str(e), 400)
    return JsonResponse(await island_snapshot(island.id, []))


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
async def island_view(request: HttpRequest, island_id: str) -> JsonResponse:
    """GET: the island snapshot. DELETE: abandon the island (its git repository stays on disk)."""
    if request.method == "DELETE":
        try:
            kept = await islands.delete_island(island_id)
        except Island.DoesNotExist:
            return _error(f"no island {island_id!r}", 404)
        except islands.IslandError as e:
            return _error(str(e), 409)
        return JsonResponse({"deleted": island_id, "repo_kept": kept})
    if request.method != "GET":
        return _error("GET or DELETE", 405)
    try:
        snapshot = await island_snapshot(island_id, get_runtime().bubbles.for_island(island_id))
    except Island.DoesNotExist:
        return _error(f"no island {island_id!r}", 404)
    return JsonResponse(snapshot)


@csrf_exempt
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
async def tasks_view(request: HttpRequest, island_id: str) -> JsonResponse:
    """GET: the island's tasks, newest first. POST: send a task package to the mailbox.

    A posted task is queued and run by the engine worker: each building's resident
    does their step through their brain (Claude Code, Codex, Gemini over ACP, or
    OpenRouter; the mock brain while `mock_agents` is on). Follow it over the
    WebSocket, or poll GET /islands/{id}/tasks/{task_id}.
    """
    island = await _island_or_none(island_id)
    if island is None:
        return _error(f"no island {island_id!r}", 404)
    if request.method == "GET":
        return await _list_tasks(request, island_id)
    if request.method != "POST":
        return _error("GET or POST", 405)
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("body is not valid JSON", 400)
    try:
        task = await tasks.submit_task(get_runtime().workflow, island, body)
    except tasks.TaskError as e:
        return _error(str(e), e.status)
    return JsonResponse(task_to_dict(task))


async def _list_tasks(request: HttpRequest, island_id: str) -> JsonResponse:
    statuses = [s for s in request.GET.get("status", "").split(",") if s]
    try:
        limit = max(1, int(request.GET.get("limit", "50")))
    except ValueError:
        return _error("limit must be a number", 400)
    rows = Task.objects.filter(island_id=island_id).order_by("-created_at", "-id")
    if statuses:
        rows = rows.filter(status__in=statuses)
    tasks = [t async for t in rows[:limit]]
    steps = await _steps_by_task([t.id for t in tasks])
    return JsonResponse({"tasks": [task_detail_dict(t, steps.get(t.id, [])) for t in tasks]})


@require_GET
async def get_task(request: HttpRequest, island_id: str, task_id: str) -> JsonResponse:
    task = await Task.objects.filter(pk=task_id, island_id=island_id).afirst()
    if task is None:
        return _error(f"no task {task_id!r} on island {island_id!r}", 404)
    steps = await _steps_by_task([task.id])
    return JsonResponse({**task_detail_dict(task, steps.get(task.id, [])), "events": await _task_events(task)})


@csrf_exempt
@require_POST
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
    """The island's stored events while this task ran: activity lines, bubbles, moods, handoffs.

    Events aren't tagged with a task, but the engine runs one task at a time, so
    everything between the task's `in_progress` event and its `done`/`failed`
    event belongs to it. Same shape as the planned GET /islands/{id}/events:
    the stored payload plus `event_id`, oldest first, the newest `limit` rows.
    """
    marks = Event.objects.filter(island_id=task.island_id, type="task", payload__id=task.id).order_by("id")
    first = await marks.filter(payload__status="in_progress").afirst()
    if first is None:
        return []  # still queued
    rows = Event.objects.filter(island_id=task.island_id, id__gte=first.id)
    last = await marks.filter(payload__status__in=["done", "failed"]).alast()
    if last is not None:
        rows = rows.filter(id__lte=last.id)
    newest = [{"event_id": e.id, **e.payload} async for e in rows.order_by("-id")[:limit]]
    return newest[::-1]


@require_GET
async def get_insights(request: HttpRequest, island_id: str) -> JsonResponse:
    if await _island_or_none(island_id) is None:
        return _error(f"no island {island_id!r}", 404)
    range_name = request.GET.get("range", "week")
    if range_name not in RANGES:
        return _error(f"range must be one of {list(RANGES)}", 400)
    return JsonResponse(await island_insights(island_id, range_name))


@require_GET
async def get_events(request: HttpRequest, island_id: str) -> JsonResponse:
    """The newest `limit` stored events, oldest first: the stored payload plus `event_id`."""
    if await _island_or_none(island_id) is None:
        return _error(f"no island {island_id!r}", 404)
    try:
        limit = min(max(1, int(request.GET.get("limit", "200"))), 2000)
    except ValueError:
        return _error("limit must be a number", 400)
    newest = [{"event_id": e.id, **e.payload} async for e in Event.objects.filter(island_id=island_id).order_by("-id")[:limit]]
    return JsonResponse({"events": newest[::-1]})


@csrf_exempt
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
    if await _island_or_none(island_id) is None:
        return _error(f"no island {island_id!r}", 404)
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("body must be a JSON object", 400)
    if "map" in body:
        try:
            await asyncio.to_thread(islands.set_map, island_id, body)
        except islands.MapBusy as e:
            return _error(str(e), 409)
        except islands.IslandError as e:
            return _error(str(e), 400)
        runtime = get_runtime()
        snapshot = await island_snapshot(island_id, runtime.bubbles.for_island(island_id))
        await runtime.bus.broadcast({"type": "snapshot", "island": snapshot}, island_id)
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
        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return _error("body is not valid JSON", 400)
        if not isinstance(body, dict):
            return _error("body must be a JSON object", 400)
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
