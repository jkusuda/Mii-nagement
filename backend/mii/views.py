"""HTTP endpoints (plain async Django views, JSON in / JSON out):

    GET  /health                       is the server up? which mode?
    GET  /brains                       {brain: true/false} (what the game's creator greys out)
    GET  /brains/detail                per brain: installed, logged_in, api_key, hint
    GET  /models                       the OpenRouter model catalogue (needs OPENROUTER_API_KEY)
    GET  /islands                      every island + the default folder for new repositories
    POST /islands                      found an island {"name", "repo", "branch", "shape", "steps" | "plan", "season",
                                       "rules", "folder", "existing_repo"}
    GET  /github/repos?q=              the user's repos (via gh), for the island creator
    GET  /github/branches?repo=        a repo's branches (via gh)
    GET  /islands/{id}                 the island snapshot
    PUT  /islands/{id}/layout          build mode's save {"layout": {...}, "buildings": [{"type", "x", "y", "facing"}]}
    POST /islands/{id}/tasks           {"title": "...", "description": "..."} or {"issue": 42}
    GET  /islands/{id}/insights        stats for the Insights screen
    GET  /settings                     settings + which API keys are set (never the keys themselves)
    PUT  /settings                     {"settings": {...}, "secrets": {"openrouter_api_key": "sk-..."|null}}
    POST /replay/{task_id}?speed=4     replay a recorded task into the game
"""

import asyncio
import json

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from mii import github, islands, prefs
from mii.agents.acp import available_brains
from mii.agents.openrouter import list_models
from mii.insights import island_insights
from mii.models import Island, island_snapshot, task_to_dict
from mii.replay import ReplayError, replay_task
from mii.runtime import get_runtime


def _error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


async def _island_or_none(island_id: str) -> Island | None:
    return await Island.objects.filter(pk=island_id).afirst()


@require_GET
async def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, "mock_agents": get_runtime().mock_agents})


@require_GET
async def brains(request: HttpRequest) -> JsonResponse:
    return JsonResponse({brain: info["ok"] for brain, info in available_brains(prefs.secret("openrouter_api_key")).items()})


@require_GET
async def brains_detail(request: HttpRequest) -> JsonResponse:
    return JsonResponse(available_brains(prefs.secret("openrouter_api_key")))


@require_GET
async def models_list(request: HttpRequest) -> JsonResponse:
    api_key = prefs.secret("openrouter_api_key")
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


@require_GET
async def get_island(request: HttpRequest, island_id: str) -> JsonResponse:
    try:
        snapshot = await island_snapshot(island_id, get_runtime().bubbles.for_island(island_id))
    except Island.DoesNotExist:
        return _error(f"no island {island_id!r}", 404)
    return JsonResponse(snapshot)


@csrf_exempt
async def put_layout(request: HttpRequest, island_id: str) -> JsonResponse:
    """Build mode's save. Answers with the new snapshot and sends it to everyone watching."""
    if request.method != "PUT":
        return _error("PUT", 405)
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("body must be a JSON object", 400)
    try:
        await asyncio.to_thread(islands.set_layout, island_id, body)
    except Island.DoesNotExist:
        return _error(f"no island {island_id!r}", 404)
    except islands.LayoutBusy as e:
        return _error(str(e), 409)
    except islands.IslandError as e:
        return _error(str(e), 400)
    runtime = get_runtime()
    snapshot = await island_snapshot(island_id, runtime.bubbles.for_island(island_id))
    await runtime.bus.broadcast({"type": "snapshot", "island": snapshot}, island_id)
    return JsonResponse(snapshot)


@csrf_exempt
@require_POST
async def create_task(request: HttpRequest, island_id: str) -> JsonResponse:
    island = await _island_or_none(island_id)
    if island is None:
        return _error(f"no island {island_id!r}", 404)
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("body is not valid JSON", 400)
    title = str(body.get("title") or "")
    description = str(body.get("description") or "")
    issue = body.get("issue")
    if issue is not None:
        if not island.repo:
            return _error("this island has no GitHub repo", 400)
        try:
            title, description = await asyncio.to_thread(github.fetch_issue, island.repo, int(issue))
        except (ValueError, TypeError):
            return _error("issue must be a number", 400)
        except github.GitHubError as e:
            return _error(str(e), 502)
    if not title:
        return _error("give a title or an issue number", 400)
    task = await get_runtime().workflow.submit(island_id, title, description, issue_number=issue)
    return JsonResponse(task_to_dict(task))


@require_GET
async def get_insights(request: HttpRequest, island_id: str) -> JsonResponse:
    if await _island_or_none(island_id) is None:
        return _error(f"no island {island_id!r}", 404)
    return JsonResponse(await island_insights(island_id))


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
