"""The one set of live objects per server process: sandbox, event bus, bubbles, engine.

Django views and the WebSocket consumer reach them through get_runtime().
asgi.py starts and stops the engine worker with the ASGI lifespan events.
"""

import asyncio
import logging

from asgiref.sync import sync_to_async
from django.core.management import call_command

from mii import events, islands, seed
from mii.agents import make_runner
from mii.config import config
from mii.engine import WorkflowEngine
from mii.models import Island
from mii.sandbox import Sandboxes

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self):
        # Mock mode never cuts worktrees; real mode works in worktrees of each island's repo.
        self.sandboxes = None if config.mock_agents else Sandboxes(config.worktree_root)
        self.bus = events.EventBus()
        self.bubbles = events.Bubbles(self.bus)
        runner = make_runner(
            config.mock_agents,
            openrouter_api_key=config.openrouter_api_key, openrouter_model=config.openrouter_default_model,
        )
        self.workflow = WorkflowEngine(
            self.bus, self.bubbles, runner, sandboxes=self.sandboxes,
            walk_seconds=config.walk_seconds, bubble_timeout=config.bubble_timeout,
            permission_timeout=config.permission_timeout, sleep_after=config.sleep_after,
            step_timeout=config.step_timeout, test_command=config.test_command,
            keep_worktrees=config.keep_worktrees, git_author=config.git_author,
        )
        self.worker: asyncio.Task | None = None

    async def start(self) -> None:
        """Create/upgrade the tables, seed the island if missing, resume the queue, start the worker."""
        await sync_to_async(call_command)("migrate", interactive=False, verbosity=0)
        await sync_to_async(seed.seed)()
        await sync_to_async(self._ensure_island_repos)()
        await self.workflow.recover()
        self.worker = asyncio.create_task(self.workflow.run_forever())
        log.info("Mii-nagement ready (%s agents)", "MOCK" if config.mock_agents else "REAL")

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
