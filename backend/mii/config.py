"""Settings, loaded once from the .env file at the repo root."""

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


@dataclass(frozen=True)
class Settings:
    mock_agents: bool  # True = every resident uses the mock brain, whatever its `brain` field says
    sandbox_repo_path: Path | None  # the git repo agents work on (worktrees of it)
    worktree_root: Path
    database_url: str

    # Pacing (seconds)
    walk_seconds: float = 2.0  # how long a walk between buildings takes (lets the game animate)
    bubble_timeout: float = 30.0  # how long a resident waits for your answer
    sleep_after: float = 45.0  # idle this long -> residents fall asleep
    step_timeout: float = 600.0  # an agent taking longer than this gets stopped


def load_settings() -> Settings:
    # Agent API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY) aren't read
    # here: agent processes inherit them from the environment if they're set.
    return Settings(
        mock_agents=os.getenv("MOCK_AGENTS", "1").strip() != "0",
        sandbox_repo_path=_optional_path("SANDBOX_REPO_PATH"),
        worktree_root=_optional_path("WORKTREE_ROOT") or BACKEND_DIR / ".worktrees",
        database_url=os.getenv("DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'mii.db'}"),
    )


settings = load_settings()
