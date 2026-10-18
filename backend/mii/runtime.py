"""The one set of live objects per server process: sandbox, event bus, bubbles, engine.

Django views and the WebSocket consumer reach them through get_runtime().
asgi.py starts and stops the engine worker with the ASGI lifespan events.
"""

import asyncio
import logging

from asgiref.sync import sync_to_async
from django.core.management import call_command

from mii import events, islands, metrics, prefs
from mii.agents import make_runner
from mii.config import config
from mii.engine import WorkflowEngine
from mii.hooks import SpectatorHook
from mii.judge import GEMINI_MODEL, GeminiJudge, JevJudge, RuleJudge
from mii.models import Island
from mii.sandbox import Sandboxes

log = logging.getLogger(__name__)


class Runtime:
    """Built from defaults; start() and reload() apply the saved settings (prefs.py)."""

    def __init__(self):
        self.mock_agents = config.mock_agents
        self.sandboxes = None
        self.bus = events.EventBus()
        self.bubbles = events.Bubbles(self.bus)
        self.judge = RuleJudge()  # reload() swaps in Jev or Gemini (the `judge` setting)
        self.spectator = SpectatorHook()  # off until reload() gives it a `spectator_url`
        self.bus.add_hook(self.spectator)
        self.workflow = WorkflowEngine(
            self.bus,
            self.bubbles,
            make_runner(True),
            sandboxes=None,
            judge=self.judge,
            walk_seconds=config.walk_seconds,
            bubble_timeout=config.bubble_timeout,
            permission_timeout=config.permission_timeout,
            sleep_after=config.sleep_after,
            step_timeout=config.step_timeout,
            git_author=config.git_author,
        )
        self.worker: asyncio.Task | None = None

    async def start(self) -> None:
        """Create/upgrade the tables, apply settings, resume the queue, start the worker."""
        await sync_to_async(call_command)("migrate", interactive=False, verbosity=0)
        await sync_to_async(metrics.detect)()  # sqlite / postgres / timescale, for /health and Insights
        await self.reload()
        await sync_to_async(self._ensure_island_repos)()
        await self.workflow.recover()
        self.worker = asyncio.create_task(self.workflow.run_forever())
        log.info("Mii-nagement ready (%s agents)", "MOCK" if self.mock_agents else "REAL")

    async def reload(self) -> dict:
        """Re-read settings + secrets and apply them. Takes effect from the next task. Returns the snapshot."""
        snap = await sync_to_async(prefs.snapshot)()
        settings = snap["settings"]
        self.mock_agents = settings["mock_agents"]
        # Mock mode never cuts worktrees; real mode works in worktrees of each island's repo.
        self.sandboxes = None if self.mock_agents else Sandboxes(config.worktree_root)
        self.workflow.sandboxes = self.sandboxes
        self.workflow.runner = make_runner(
            self.mock_agents,
            openrouter_api_key=await sync_to_async(prefs.secret)("openrouter_api_key"),  # keychain: off the loop
            openrouter_model=settings["openrouter_model"],
        )
        self.workflow.test_command = settings["test_command"]
        self.workflow.keep_worktrees = settings["keep_worktrees"]
        self.workflow.parallel = settings["parallel_packages"]
        self.workflow.master_key = settings["master_key"]
        # The judge (moods, missing verdicts, flaky re-runs, tips, help), in mock mode too.
        await self.judge.aclose()
        self.judge = await self._pick_judge(str(settings.get("judge") or "auto").lower(), settings["judge_model"])
        self.workflow.judge = self.judge
        # The spectator feed (hooks.py): a None url switches it off.
        self.spectator.configure(settings["spectator_url"], await sync_to_async(prefs.secret)("spectator_secret"))
        return snap

    @staticmethod
    async def _pick_judge(choice: str, model: str | None) -> RuleJudge:
        """off = rules; jev / gemini = that one when its key is set; auto = Jev, else Gemini, else rules."""
        if choice == "off":
            return RuleJudge()
        jev_key = await sync_to_async(prefs.secret)("typesafe_api_key")
        gemini_key = await sync_to_async(prefs.secret)("gemini_api_key")
        if choice in ("auto", "jev") and jev_key:
            return JevJudge(jev_key)
        if choice in ("auto", "gemini") and gemini_key:
            return GeminiJudge(gemini_key, model or GEMINI_MODEL)
        return RuleJudge()

    @staticmethod
    def _ensure_island_repos() -> None:
        """Every island is a repository: give any island without one a fresh repo in the default folder."""
        for island in Island.objects.all():
            try:
                islands.ensure_repo(island)
            except islands.IslandError as e:
                log.warning("island %s has no repository: %s", island.id, e)

    async def stop(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.worker = None
        await self.spectator.aclose()
        await self.judge.aclose()


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = Runtime()
    return _runtime
