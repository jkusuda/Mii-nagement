"""Shared test helpers.

Database tests use pytest-django. The engine talks to the database with
Django's *async* ORM, which runs queries in a helper thread, so those tests
need `transaction=True` (otherwise the helper thread can't see the test's rows).
"""

import asyncio
import subprocess
from pathlib import Path

import pytest

from mii import events, seed
from mii.agents import AgentJob, StepResult
from mii.engine import WorkflowEngine
from mii.models import Building, Resident, Task
from mii.models import Island
from mii.sandbox import Sandbox, Sandboxes

db_test = pytest.mark.django_db(transaction=True)


class FakeClient:
    """Stands in for a WebSocket and records everything sent to it."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, event: dict) -> None:
        self.sent.append(event)

    def of_type(self, event_type: str) -> list[dict]:
        return [e for e in self.sent if e["type"] == event_type]


class ScriptedRunner:
    """Returns pre-set outcomes per building type, and records where it was used."""

    def __init__(self, delay: float = 0.0, **outcomes: list[str]):
        self.delay = delay
        self.outcomes = outcomes
        self.visited: list[str] = []

    async def run(self, job) -> StepResult:
        self.visited.append(job.building.id)
        await asyncio.sleep(self.delay)
        planned = self.outcomes.get(job.building.type, [])
        return StepResult(planned.pop(0) if planned else "pass", "scripted")


class RecordingAsk:
    """A job.ask() that records bubbles and answers with the first option (or a fixed text)."""

    def __init__(self, answer: str | None = None):
        self.answer = answer
        self.asked: list[tuple[str, str, list[str] | None]] = []
        self.details: list[str | None] = []

    async def __call__(self, kind: str, text: str, options: list[str] | None = None, detail: str | None = None) -> str | None:
        self.asked.append((kind, text, options))
        self.details.append(detail)
        if self.answer is not None:
            return self.answer
        return options[0] if options else "ok"


class RecordingReport:
    def __init__(self):
        self.lines: list[str] = []

    async def __call__(self, text: str) -> None:
        self.lines.append(text)


def make_job(brain: str = "mock", workdir: Path | None = None, building_type: str = "lab",
             instructions: str = "", read_only: bool = False, model: str | None = None,
             ask=None, report=None, sandbox: Sandbox | None = None) -> AgentJob:
    """An unsaved job for runner tests (no database needed)."""
    return AgentJob(
        resident=Resident(id="r", island_id="sunny", name="Rae", job="coder", brain=brain, model=model),
        task=Task(id="t_test", island_id="sunny", title="Test task", branch="mii/t_test"),
        building=Building(id=building_type, island_id="sunny", type=building_type, name=building_type.title(), x=0, y=0),
        instructions=instructions, ask=ask or RecordingAsk(), report=report or RecordingReport(),
        workdir=workdir, sandbox=sandbox, read_only=read_only,
    )


@pytest.fixture
def island():
    """The seed island, in the test database."""
    return seed.seed()


def make_workflow(runner, **kwargs) -> tuple[WorkflowEngine, FakeClient]:
    bus = events.EventBus()
    client = FakeClient()
    bus.add_client(client)
    kwargs = {"walk_seconds": 0, "bubble_timeout": 0.05, **kwargs}
    return WorkflowEngine(bus, events.Bubbles(bus), runner, **kwargs), client


def make_git_repo(path: Path) -> Path:
    """A throwaway git repo with one committed file."""
    path.mkdir(parents=True, exist_ok=True)
    git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    (path / "hello.py").write_text("print('hello')\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    return path


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    return Sandbox(make_git_repo(tmp_path / "repo"), tmp_path / "worktrees")


@pytest.fixture
def sandboxes(island, sandbox) -> Sandboxes:
    """Real-mode engine fixture: the seed island owns the throwaway repo."""
    Island.objects.filter(pk=island).update(repo_path=str(sandbox.repo_path))
    boxes = Sandboxes(sandbox.worktree_root)
    return boxes
