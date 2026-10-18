"""Gifts: extra MCP servers a resident brings to every ACP session (Resident.gifts holds the ids).

Two kinds:
  - built in: the GIFTS table below.
  - added from the gift shop: the game searches the official MCP Registry
    (GET /gifts/search) and adds a server (POST /gifts). We build its command from the
    registry entry ourselves, so the game only ever sends a registry name. Added gifts
    live in one `Setting` row; the values their servers need (often API keys) go to the
    OS keychain (prefs.gift_env), never the database.

Each gift is a stdio MCP server the agent starts; `npx -y` / `uvx` fetch it on first use.
It runs as the host, like the agent itself, so adding one is trusting that package.
"""

import asyncio
import re
import shutil
import subprocess
from urllib.parse import quote

import httpx
from acp.schema import EnvVariable, McpServerStdio

from mii import prefs
from mii.models import Resident, Setting

# Built-in gifts.
#   command    how to start it (the first word must be on PATH)
#   needs      another program that must be on PATH
#   needs_env  an environment variable / setting the server can't run without ({NAME} in command is filled in)
#   gh_token   put the user's `gh auth token` in this environment variable
#   key        the house key its tools need (models.DEFAULT_KEYS): with that key on "no" the gift
#              stays home, with "ask" each tool call shows a ✋ bubble. Added gifts use `tools`.
GIFTS = {
    "browser": {
        "label": "Browser",
        "command": ["npx", "-y", "@playwright/mcp@latest"],
        "key": "fetch",
        "hint": "a web browser the resident can drive (Playwright MCP)",
    },
    "github": {
        "label": "GitHub",
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "gh_token": "GITHUB_PERSONAL_ACCESS_TOKEN",
        "key": "fetch",
        "needs": "gh",
        "hint": "issues, PRs and repos through your `gh` login",
    },
    "docs": {
        "label": "Docs",
        "command": ["npx", "-y", "@upstash/context7-mcp"],
        "key": "fetch",
        "hint": "up-to-date library documentation (Context7)",
    },
    "database": {
        "label": "Database",
        "command": ["npx", "-y", "@bytebase/dbhub", "--dsn", "{DATABASE_URL}"],
        "needs_env": "DATABASE_URL",
        "key": "execute",
        "hint": "query the database at DATABASE_URL (save it in settings, e.g. postgres://… or sqlite:///path/to.db)",
    },
    "terminal": {
        "label": "Terminal",
        "command": ["npx", "-y", "mcp-server-commands"],
        "key": "execute",
        "hint": "an extra shell tool (the ACP agents have their own too)",
    },
}

ADDED_GIFT_KEY = "tools"  # the house key for gifts from the gift shop (we can't tell what they do)

REGISTRY = "https://registry.modelcontextprotocol.io/v0"
REGISTRY_TIMEOUT = 30.0  # searches often take 5-10 s
SEARCH_LIMIT = 30
CUSTOM = "custom_gifts"  # the Setting row: gift id -> spec (see _spec_from_registry)
# Package registries we can start without installing anything: npm through npx, PyPI through uvx.
RUNNERS = {"npm": "npx", "pypi": "uvx"}


class GiftError(ValueError):
    """Something the game can fix (a bad name, a registry entry we can't run). Goes back as a 400."""


# --- Which gifts exist, and which run here ------------------------------------------


def custom_gifts() -> dict[str, dict]:
    """Gifts added from the gift shop. Sync (database)."""
    row = Setting.objects.filter(pk=CUSTOM).first()
    return dict(row.value or {}) if row is not None else {}


def all_gifts() -> dict[str, dict]:
    """Built-in and added gifts, by id. Sync (database)."""
    return {**GIFTS, **custom_gifts()}


def available_gifts() -> dict[str, dict]:
    """Every gift and whether it can be handed over on this machine (the creator greys out the rest).
    Added gifts also say which values they need (`env`) and which are saved (`set`). Sync."""
    result = {}
    for gift, spec in all_gifts().items():
        missing = _missing(gift, spec)
        info = {"ok": missing is None, "label": spec["label"], "hint": missing or spec["hint"], "key": key_for(gift)}
        if spec.get("source"):
            info |= {
                "custom": True,
                "source": spec["source"],
                "env": [{**e, "set": bool(prefs.gift_env(gift, e["name"]))} for e in spec.get("env", [])],
            }
        result[gift] = info
    return result


def key_for(gift: str) -> str:
    """The house key a gift's tools need."""
    return GIFTS.get(gift, {}).get("key", ADDED_GIFT_KEY)


def gift_in(title: str, gifts: list[str]) -> str | None:
    """Which of these gifts a tool call belongs to, from its title: Claude Code names MCP tools
    `mcp__<gift>__<tool>`, other agents put the server name in the title some other way."""
    for gift in gifts:
        if re.search(rf"(?<![a-z0-9]){re.escape(gift)}(?![a-z0-9])", title.lower()):
            return gift
    return None


def _missing(gift: str, spec: dict) -> str | None:
    """Why a gift can't be used right now, or None when it can."""
    for tool in (spec["command"][0], spec.get("needs")):
        if tool and shutil.which(tool) is None:
            return f"install `{tool}` first"
    if spec.get("needs_env") and not prefs.extra_env(spec["needs_env"]):
        return f"set {spec['needs_env']} in settings first"
    for e in spec.get("env", []):
        if e.get("required") and not e.get("default") and not prefs.gift_env(gift, e["name"]):
            return f"needs {e['name']}: add it in the gift shop"
    return None


def gift_servers(gifts: list[str]) -> tuple[list[McpServerStdio], list[str]]:
    """MCP servers for a resident's gifts, plus a note for each gift we had to skip. Sync (may run `gh`)."""
    table = GIFTS if all(g in GIFTS for g in gifts) else all_gifts()  # the database only for added gifts
    servers, skipped = [], []
    for gift in gifts:
        spec = table.get(gift)
        if spec is None:
            skipped.append(f"Unknown gift {gift!r}")
            continue
        missing = _missing(gift, spec)
        if missing:
            skipped.append(f"Left the {spec['label']} gift at home: {missing}")
            continue
        env, fill = [], {}
        if spec.get("needs_env"):
            fill[spec["needs_env"]] = prefs.extra_env(spec["needs_env"])
            env.append(EnvVariable(name=spec["needs_env"], value=fill[spec["needs_env"]]))
        for e in spec.get("env", []):
            value = prefs.gift_env(gift, e["name"]) or e.get("default")
            if value:
                env.append(EnvVariable(name=e["name"], value=value))
        if spec.get("gh_token"):
            token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
            if not token:
                skipped.append(f"Left the {spec['label']} gift at home: `gh auth login` first")
                continue
            env.append(EnvVariable(name=spec["gh_token"], value=token))
        command = [word.format(**fill) for word in spec["command"]]
        servers.append(McpServerStdio(name=gift, command=shutil.which(command[0]), args=command[1:], env=env))
    return servers, skipped


# --- The gift shop: the official MCP Registry ---------------------------------------


async def search(query: str) -> list[dict]:
    """Servers in the MCP Registry matching `query` that we can start here (an npm or PyPI
    stdio package). Each: name, label, description, package, runner, env, website, added."""
    params = {"search": query.strip()[:100], "version": "latest", "limit": SEARCH_LIMIT}
    data = await _registry("/servers", params)
    added = {spec["source"] for spec in (await _custom_async()).values()}
    results = []
    for entry in data.get("servers") or []:
        server = entry.get("server") or {}
        package = _runnable_package(server)
        if package is None:
            continue
        results.append(
            {
                "name": server["name"],
                "label": _label(server),
                "description": str(server.get("description") or "")[:300],
                "package": package["identifier"],
                "runner": RUNNERS[package["registryType"]],
                "env": _env_specs(package),
                "website": server.get("websiteUrl") or (server.get("repository") or {}).get("url") or "",
                "added": server["name"] in added,
            }
        )
    return results


async def add(name: str) -> tuple[str, dict]:
    """Add the registry server `name` as a gift. Returns (gift id, spec). Adding it again returns the one we have."""
    if not isinstance(name, str) or not name.strip():
        raise GiftError("name is the registry name of an MCP server")
    custom = await _custom_async()
    for gift, spec in custom.items():
        if spec["source"] == name:
            return gift, spec
    data = await _registry(f"/servers/{quote(name, safe='')}/versions/latest")
    server = data.get("server") or {}
    package = _runnable_package(server)
    if package is None:
        raise GiftError(f"{name} has no npm or PyPI package we can start here")
    gift = _new_id(server, set(GIFTS) | set(custom))
    custom[gift] = _spec_from_registry(server, package)
    await Setting.objects.aupdate_or_create(pk=CUSTOM, defaults={"value": custom})
    return gift, custom[gift]


async def set_env(gift: str, values: dict) -> None:
    """Save (or with null / "", forget) the values an added gift's server needs, in the keychain."""
    spec = (await _custom_async()).get(gift)
    if spec is None:
        raise GiftError(f"no added gift {gift!r}")
    names = {e["name"] for e in spec.get("env", [])}
    for env_name, value in values.items():
        if env_name not in names:
            raise GiftError(f"{spec['label']} doesn't use {env_name}")
        if value is not None and not isinstance(value, str):
            raise GiftError(f"{env_name} must be text or null")
    for env_name, value in values.items():
        await asyncio.to_thread(prefs.set_gift_env, gift, env_name, value)


async def remove(gift: str) -> list[str]:
    """Take an added gift off the shelf: forget its values and take it back from every
    resident. Returns the islands whose residents changed (they need a fresh snapshot)."""
    custom = await _custom_async()
    spec = custom.pop(gift, None)
    if spec is None:
        raise GiftError(f"no added gift {gift!r}" + (" (built-in gifts stay)" if gift in GIFTS else ""))
    await Setting.objects.aupdate_or_create(pk=CUSTOM, defaults={"value": custom})
    for e in spec.get("env", []):
        await asyncio.to_thread(prefs.set_gift_env, gift, e["name"], None)
    islands = set()
    async for resident in Resident.objects.all():
        if gift in (resident.gifts or []):
            resident.gifts = [g for g in resident.gifts if g != gift]
            await resident.asave(update_fields=["gifts"])
            islands.add(resident.island_id)
    return sorted(islands)


async def _registry(path: str, params: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=REGISTRY_TIMEOUT) as client:
            response = await client.get(REGISTRY + path, params=params)
    except httpx.HTTPError as e:
        raise GiftError(f"the MCP Registry is unreachable: {e}") from None
    if response.status_code == 404:
        raise GiftError("the MCP Registry doesn't know that server")
    if response.status_code >= 400:
        raise GiftError(f"the MCP Registry answered {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise GiftError("the MCP Registry sent something that isn't JSON") from None


def _runnable_package(server: dict) -> dict | None:
    """The first npm / PyPI package that speaks stdio and needs no arguments we can't fill in."""
    for package in server.get("packages") or []:
        if package.get("registryType") not in RUNNERS or not package.get("identifier"):
            continue
        if (package.get("transport") or {}).get("type", "stdio") != "stdio":
            continue
        args = (package.get("packageArguments") or []) + (package.get("runtimeArguments") or [])
        if any(a.get("isRequired") and not (a.get("value") or a.get("default")) for a in args):
            continue
        return package
    return None


def _spec_from_registry(server: dict, package: dict) -> dict:
    runner = RUNNERS[package["registryType"]]
    identifier, version = package["identifier"], package.get("version")
    if runner == "npx":
        command = ["npx", "-y", f"{identifier}@{version}" if version else identifier]
    else:
        command = ["uvx", f"{identifier}=={version}" if version else identifier]
    for arg in package.get("packageArguments") or []:
        value = arg.get("value") or arg.get("default")
        if not value:
            continue
        command += [arg["name"], str(value)] if arg.get("type") == "named" and arg.get("name") else [str(value)]
    return {
        "label": _label(server),
        "hint": str(server.get("description") or identifier)[:200],
        "command": command,
        "env": _env_specs(package),
        "source": server["name"],
    }


def _env_specs(package: dict) -> list[dict]:
    specs = []
    for e in package.get("environmentVariables") or []:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(e.get("name") or "")):
            continue
        specs.append(
            {
                "name": e["name"],
                "description": str(e.get("description") or "")[:200],
                "required": bool(e.get("isRequired")),
                "secret": bool(e.get("isSecret")),
                "default": str(e["default"]) if e.get("default") else None,
            }
        )
    return specs


def _label(server: dict) -> str:
    """A short tile label: the registry title, else the last part of its name."""
    title = server.get("title") or server["name"].split("/")[-1].replace("-", " ").replace("_", " ").title()
    return str(title)[:20]


def _new_id(server: dict, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", server["name"].split("/")[-1].lower()).strip("_")[:24] or "gift"
    gift, n = base, 2
    while gift in taken or gift == "search":  # /gifts/search is the gift shop's search
        gift, n = f"{base}_{n}", n + 1
    return gift


async def _custom_async() -> dict[str, dict]:
    row = await Setting.objects.filter(pk=CUSTOM).afirst()
    return dict(row.value or {}) if row is not None else {}
