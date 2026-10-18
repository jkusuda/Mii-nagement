"""Preferences: user settings in the database, API keys in the OS keychain.

Three layers, first one wins:
  1. the environment (a root .env, for developers): MOCK_AGENTS, OPENROUTER_API_KEY, ...
  2. what the user saved through PUT /settings: the `Setting` table for ordinary
     values, the OS keychain (macOS Keychain, Windows Credential Manager, Linux
     Secret Service) for API keys. No keychain available = an owner-only file.
  3. built-in defaults.

Secrets never go into the database and are never sent back to the game, only
"set" / "not set".
"""

import json
import logging
import os
import stat
from contextlib import suppress
from pathlib import Path

import keyring
import keyring.errors

from mii.config import BACKEND_DIR
from mii.models import Setting

log = logging.getLogger(__name__)

SERVICE = "mii-nagement"  # keychain service name
SECRETS_FILE = BACKEND_DIR / ".secrets.json"  # fallback when there is no keychain (gitignored)

# setting -> (environment variable, default, type)
SETTINGS = {
    "mock_agents": ("MOCK_AGENTS", False, bool),
    "islands_root": ("ISLANDS_ROOT", None, "path"),
    "test_command": ("TEST_COMMAND", None, str),
    "openrouter_model": ("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5", str),
    "keep_worktrees": ("KEEP_WORKTREES", False, bool),
    "database_url": ("DATABASE_URL", None, str),  # for the `database` gift (agents/acp.py GIFTS)
    "godot_path": ("GODOT_PATH", None, str),  # for the `godot` gift: the Godot 4 executable
    "judge": ("JUDGE", "auto", str),  # auto | off | jev | gemini (judge.py, runtime.reload)
    "judge_model": ("JUDGE_MODEL", "gemini-2.5-flash", str),  # the Gemini judge's model
    "spectator_url": ("SPECTATOR_URL", None, str),  # the spectator feed's ingest URL (hooks.py); None = off
    "parallel_packages": ("PARALLEL_PACKAGES", 3, int),  # packages one island works on at once (engine.py)
    "master_key": ("MASTER_KEY", False, bool),  # residents never ask: every bubble answers itself (engine.py)
}
# Settings with a fixed list of values.
CHOICES = {"judge": ("auto", "off", "jev", "gemini")}
MAX_PARALLEL = 8  # parallel_packages is kept between 1 and this (each real package runs its own agents)

# secret -> environment variable (what the agent processes expect)
SECRETS = {
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "typesafe_api_key": "TYPESAFE_API_KEY",  # the judge (judge.py); never handed to agents
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",  # the Gemini CLI brain and the Gemini judge: shared on purpose
    "spectator_secret": "SPECTATOR_SECRET",  # the spectator feed's bearer secret (hooks.py)
}
# Keys the backend uses itself. They never reach agent processes (secrets_env, agents/acp.py).
PRIVATE_ENV = {"OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "SPECTATOR_SECRET"}


class PrefsError(ValueError):
    pass


# --- Settings (database) -----------------------------------------------------------


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value is not None and value.strip() != "" else None


def _parse(value, kind):
    if value is None:
        return None
    if kind is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in ("0", "", "false", "no", "off")
    if kind == "path":
        return str(Path(str(value)).expanduser())
    if kind is int:
        try:
            return min(max(int(value), 1), MAX_PARALLEL)
        except (TypeError, ValueError):
            return 1
    return str(value)


def get(key: str):
    """One setting: environment, then the database, then the default. Sync (database)."""
    env_name, default, kind = SETTINGS[key]
    if _env(env_name) is not None:
        return _parse(_env(env_name), kind)
    row = Setting.objects.filter(pk=key).first()
    return _parse(row.value, kind) if row is not None else default


def extra_env(env_name: str) -> str | None:
    """A value an MCP server needs, by its environment variable name: the environment, else the matching setting."""
    if _env(env_name) is not None:
        return _env(env_name)
    for key, (name, _, _) in SETTINGS.items():
        if name == env_name:
            return get(key)
    return None


def set_many(values: dict) -> None:
    """Save settings. Unknown keys and wrong types are refused (nothing saved). Sync."""
    clean: dict = {}
    for key, value in values.items():
        if key not in SETTINGS:
            raise PrefsError(f"unknown setting: {key}")
        kind = SETTINGS[key][2]
        if value is None:
            clean[key] = None
        elif kind is bool:
            if not isinstance(value, bool):
                raise PrefsError(f"{key} must be true or false")
            clean[key] = value
        elif kind is int:
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PARALLEL:
                raise PrefsError(f"{key} must be a whole number from 1 to {MAX_PARALLEL}")
            clean[key] = value
        else:
            if not isinstance(value, str):
                raise PrefsError(f"{key} must be text")
            clean[key] = value.strip() or None
            if key in CHOICES and clean[key] is not None and clean[key].lower() not in CHOICES[key]:
                raise PrefsError(f"{key} must be one of {', '.join(CHOICES[key])}")
    for key, value in clean.items():
        Setting.objects.update_or_create(pk=key, defaults={"value": value})


def snapshot() -> dict:
    """Every setting's effective value, which ones the environment locks, and which secrets are set. Sync."""
    return {
        "settings": {key: get(key) for key in SETTINGS},
        "from_env": [key for key, (env_name, _, _) in SETTINGS.items() if _env(env_name) is not None]
        + [name for name, env_name in SECRETS.items() if _env(env_name) is not None],
        "secrets": {name: secret(name) is not None for name in SECRETS},
    }


# --- Secrets (keychain) ------------------------------------------------------------


def secret(name: str) -> str | None:
    """An API key: environment first, then the keychain (or the fallback file). No database."""
    if name not in SECRETS:
        raise PrefsError(f"unknown secret: {name}")
    if _env(SECRETS[name]) is not None:
        return _env(SECRETS[name])
    try:
        value = keyring.get_password(SERVICE, name)
    except keyring.errors.KeyringError:
        value = _file_secrets().get(name)
    return value or None


def set_secret(name: str, value: str | None) -> None:
    """Store (or with None, delete) an API key in the keychain."""
    if name not in SECRETS:
        raise PrefsError(f"unknown secret: {name}")
    value = (value or "").strip() or None
    try:
        if value is None:
            with suppress(keyring.errors.PasswordDeleteError):
                keyring.delete_password(SERVICE, name)
        else:
            keyring.set_password(SERVICE, name, value)
    except keyring.errors.KeyringError:
        log.warning("no OS keychain available; keeping %s in %s", name, SECRETS_FILE)
        secrets = _file_secrets()
        if value is None:
            secrets.pop(name, None)
        else:
            secrets[name] = value
        _write_file_secrets(secrets)


def secrets_env() -> dict[str, str]:
    """The stored vendor keys as environment variables for agent processes (PRIVATE_ENV keys stay with us)."""
    env = {}
    for name, env_name in SECRETS.items():
        if env_name in PRIVATE_ENV:
            continue
        value = secret(name)
        if value:
            env[env_name] = value
    return env


def _file_secrets() -> dict:
    try:
        return json.loads(SECRETS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _write_file_secrets(secrets: dict) -> None:
    SECRETS_FILE.write_text(json.dumps(secrets))
    with suppress(OSError):
        SECRETS_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # owner only
