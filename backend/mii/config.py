"""Developer config, loaded once from the optional .env file at the repo root.

Nothing here is required: every value has a default, and users change the
important ones at runtime through PUT /settings (prefs.py: database + OS
keychain). Values set in the environment win over saved settings. Django's own
settings live in settings.py and read from here.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from mii.vocab import DEFAULT_GIT_AUTHOR

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

load_dotenv(REPO_ROOT / ".env")

DEV_SECRET_KEY = "mii-nagement-local-dev-key"


def _optional_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser().resolve() if value else None


def _optional_str(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip() not in ("0", "", "false", "no")


@dataclass(frozen=True)
class Config:
    """What the process needs before the database is up. Everything the user can change at
    runtime (mock mode, islands folder, test command, keys...) is read by prefs.py instead."""

    mock_agents: bool  # the starting value; prefs.get("mock_agents") is the live one
    worktree_root: Path  # where per-task worktrees of island repos go
    database_path: Path  # SQLite file
    git_author: str = DEFAULT_GIT_AUTHOR  # author of the Workshop's commits

    # Hosting (the hosted demo sets these; local runs keep the defaults)
    debug: bool = True  # DJANGO_DEBUG
    secret_key: str = DEV_SECRET_KEY  # DJANGO_SECRET_KEY
    public_demo: bool = False  # PUBLIC_DEMO: no PUT /settings, no repo paths on POST /islands
    database_url: str | None = None  # MII_DATABASE_URL: Postgres/TimescaleDB instead of SQLite (settings.py)

    # Auth0 login (auth.py): off unless all three are set
    auth0_domain: str | None = None
    auth0_audience: str | None = None
    auth0_client_id: str | None = None

    # Pacing (seconds)
    walk_seconds: float = 2.0  # how long a walk between buildings takes (lets the game animate)
    bubble_timeout: float = 30.0  # how long a resident waits for your answer
    permission_timeout: float | None = None  # same, for ✋ permission bubbles (None = bubble_timeout)
    sleep_after: float = 45.0  # idle this long -> residents fall asleep
    step_timeout: float = 600.0  # an agent taking longer than this gets stopped


def load_config() -> Config:
    # Agent API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY) aren't read
    # here: agent processes inherit them from the environment if they're set.
    permission = _optional_str("PERMISSION_TIMEOUT")
    return Config(
        mock_agents=_flag("MOCK_AGENTS", "0"),
        worktree_root=_optional_path("WORKTREE_ROOT") or BACKEND_DIR / ".worktrees",
        database_path=_optional_path("DATABASE_PATH") or BACKEND_DIR / "mii.db",
        git_author=_optional_str("GIT_AUTHOR") or DEFAULT_GIT_AUTHOR,
        walk_seconds=float(os.getenv("WALK_SECONDS", "2.0")),
        bubble_timeout=float(os.getenv("BUBBLE_TIMEOUT", "30")),
        permission_timeout=float(permission) if permission else None,
        sleep_after=float(os.getenv("SLEEP_AFTER", "45")),
        step_timeout=float(os.getenv("STEP_TIMEOUT", "600")),
        debug=_flag("DJANGO_DEBUG", "1"),
        secret_key=_optional_str("DJANGO_SECRET_KEY") or DEV_SECRET_KEY,
        public_demo=_flag("PUBLIC_DEMO", "0"),
        database_url=_optional_str("MII_DATABASE_URL"),
        auth0_domain=_optional_str("AUTH0_DOMAIN"),
        auth0_audience=_optional_str("AUTH0_AUDIENCE"),
        auth0_client_id=_optional_str("AUTH0_CLIENT_ID"),
    )


config = load_config()
