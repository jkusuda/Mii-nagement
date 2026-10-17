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

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

load_dotenv(REPO_ROOT / ".env")


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
    mock_agents: bool  # True = every resident uses the mock brain, whatever its `brain` field says
    islands_root: Path | None  # where new island repos are created (None = Documents/Mii-nagement)
    worktree_root: Path  # where per-task worktrees of island repos go
    database_path: Path  # SQLite file

    # OpenRouter (the direct `openrouter` brain, no CLI needed)
    openrouter_api_key: str | None = None
    openrouter_default_model: str = "anthropic/claude-sonnet-4.5"

    # Real-mode plumbing
    test_command: str | None = None  # what the Lab runs in the worktree, e.g. "uv run pytest -q"
    keep_worktrees: bool = False  # keep a task's worktree after it finishes (debugging)
    git_author: str = "Mii-nagement <mii@localhost>"  # author of the Workshop's commits

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
        mock_agents=_flag("MOCK_AGENTS", "1"),
        islands_root=_optional_path("ISLANDS_ROOT"),
        worktree_root=_optional_path("WORKTREE_ROOT") or BACKEND_DIR / ".worktrees",
        database_path=_optional_path("DATABASE_PATH") or BACKEND_DIR / "mii.db",
        openrouter_api_key=_optional_str("OPENROUTER_API_KEY"),
        openrouter_default_model=_optional_str("OPENROUTER_MODEL") or "anthropic/claude-sonnet-4.5",
        test_command=_optional_str("TEST_COMMAND"),
        keep_worktrees=_flag("KEEP_WORKTREES", "0"),
        git_author=_optional_str("GIT_AUTHOR") or "Mii-nagement <mii@localhost>",
        walk_seconds=float(os.getenv("WALK_SECONDS", "2.0")),
        bubble_timeout=float(os.getenv("BUBBLE_TIMEOUT", "30")),
        permission_timeout=float(permission) if permission else None,
        sleep_after=float(os.getenv("SLEEP_AFTER", "45")),
        step_timeout=float(os.getenv("STEP_TIMEOUT", "600")),
    )


config = load_config()
