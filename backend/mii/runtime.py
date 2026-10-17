"""The one set of live objects per server process: sandbox, event bus, bubbles, engine.

Django views and the WebSocket consumer reach them through get_runtime().
asgi.py starts and stops the engine worker with the ASGI lifespan events.
"""

import asyncio
import logging

from asgiref.sync import sync_to_async
from django.core.management import call_command

from mii import events, islands, prefs, seed
from mii.agents import make_runner
from mii.config import config
from mii.engine import WorkflowEngine
from mii.judge import JevJudge, RuleJudge
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
        self.judge = RuleJudge()  # reload() swaps in JevJudge when a TypeSafe key is saved
        self.workflow = WorkflowEngine(
            self.bus, self.bubbles, make_runner(True), sandboxes=None, judge=self.judge,
            walk_seconds=config.walk_seconds, bubble_timeout=config.bubble_timeout,
            permission_timeout=config.permission_timeout, sleep_after=config.sleep_after,
            step_timeout=config.step_timeout, git_author=config.git_author,
        )
        self.worker: asyncio.Task | None = None

    async def start(self) -> None:
        """Create/upgrade the tables, seed the island if missing, apply settings, resume the queue, start the worker."""
        await sync_to_async(call_command)("migrate", interactive=False, verbosity=0)
        await sync_to_async(seed.seed)()
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
            openrouter_api_key=prefs.secret("openrouter_api_key"), openrouter_model=settings["openrouter_model"],
        )
        self.workflow.test_command = settings["test_command"]
        self.workflow.keep_worktrees = settings["keep_worktrees"]
        # The judge (moods, missing verdicts, flaky re-runs, tips, help): Jev whenever a key is set
        # (mock mode too, the calls cost a fraction of a cent), unless the `judge` setting is "off".
        key = await sync_to_async(prefs.secret)("typesafe_api_key")
        self.judge = JevJudge(key) if key and str(settings.get("judge") or "auto").lower() != "off" else RuleJudge()
        self.workflow.judge = self.judge
        return snap

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


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = Runtime()
    return _runtime
