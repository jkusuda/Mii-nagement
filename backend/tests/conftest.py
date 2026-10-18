"""Shared test helpers.

Database tests use pytest-django. The engine talks to the database with
Django's *async* ORM, which runs queries in a helper thread, so those tests
need `transaction=True` (otherwise the helper thread can't see the test's rows).
"""

import asyncio
import subprocess
from pathlib import Path

import pytest
from django.db import transaction

from mii import events
from mii.agents import AgentJob, StepResult
from mii.engine import WorkflowEngine
from mii.island_map import SHAPES, build_roads, default_map
from mii.models import Building, Island, Resident, Road, Task
from mii.sandbox import Sandbox, Sandboxes
from mii.vocab import STEP_NAMES, STEP_ORDER

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
        self.workers: list[str] = []  # who ran each visit
        self.jobs: list = []  # the AgentJob of each visit (prompt inputs: messages, notes, neighbours)

    async def run(self, job) -> StepResult:
        self.visited.append(job.building.id)
        self.workers.append(job.resident.id)
        self.jobs.append(job)
        await asyncio.sleep(self.delay)
        planned = self.outcomes.get(job.building.type, [])
        return StepResult(planned.pop(0) if planned else "pass", "scripted")


class RecordingAsk:
    """A job.ask() that records bubbles and answers with the first option (or a fixed text)."""

    def __init__(self, answer: str | None = None):
        self.answer = answer
        self.asked: list[tuple[str, str, list[str] | None]] = []
        self.details: list[str | None] = []

    async def __call__(
        self, kind: str, text: str, options: list[str] | None = None, detail: str | None = None
    ) -> str | None:
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


def make_job(
    brain: str = "claude-code",
    workdir: Path | None = None,
    building_type: str = "lab",
    instructions: str = "",
    read_only: bool = False,
    model: str | None = None,
    ask=None,
    report=None,
    sandbox: Sandbox | None = None,
) -> AgentJob:
    """An unsaved job for runner tests (no database needed)."""
    return AgentJob(
        resident=Resident(id="r", island_id="sunny", name="Rae", job="coder", brain=brain, model=model),
        task=Task(id="t_test", island_id="sunny", title="Test task", branch="mii/t_test"),
        building=Building(
            id=building_type, island_id="sunny", type=building_type, name=building_type.title(), x=0, y=0
        ),
        instructions=instructions,
        ask=ask or RecordingAsk(),
        report=report or RecordingReport(),
        workdir=workdir,
        sandbox=sandbox,
        read_only=read_only,
    )


# --- The test island ("sunny"): the app ships no islands, the tests bring their own. ---

ISLAND_ID = "sunny"


def residents() -> list[Resident]:
    # One brain per vendor: a Gemini planner, a Claude Code coder and a Codex reviewer.
    return [
        Resident(
            id="jun",
            island_id=ISLAND_ID,
            name="Jun",
            job="planner",
            brain="gemini",
            bluntness=0.3,
            caution=0.7,
            chattiness=0.6,
            creativity=0.5,
            catchphrase="Let's map it out!",
            color="#F4A261",
            building_id="mailbox",
        ),
        Resident(
            id="carlos",
            island_id=ISLAND_ID,
            name="Carlos",
            job="coder",
            brain="claude-code",
            bluntness=0.5,
            caution=0.4,
            chattiness=0.3,
            creativity=0.8,
            catchphrase="Ship it... carefully!",
            color="#2A9D8F",
            building_id="workshop",
        ),
        Resident(
            id="sydney",
            island_id=ISLAND_ID,
            name="Sydney",
            job="reviewer",
            brain="codex",
            bluntness=0.8,
            caution=0.8,
            chattiness=0.4,
            creativity=0.3,
            catchphrase="Show me the tests.",
            color="#9B5DE5",
            building_id="courthouse",
        ),
    ]


def buildings() -> list[Building]:
    """The classic island's lots (islands.SHAPES), one resident each."""
    lots = SHAPES["classic"]["lots"]
    who = {"mailbox": "jun", "workshop": "carlos", "lab": "carlos", "courthouse": "sydney", "post_office": "jun"}
    return [
        Building(
            id=t,
            island_id=ISLAND_ID,
            type=t,
            name=STEP_NAMES[t],
            x=lots[t][0],
            y=lots[t][1],
            facing=lots[t][2],
            resident_id=who[t],
        )
        for t in STEP_ORDER
    ]


def roads() -> list[Road]:
    """The classic workflow; building ids equal their types on the test island."""
    return [
        Road(island_id=ISLAND_ID, from_building_id=a, to_building_id=b, condition=c)
        for a, b, c in build_roads(STEP_ORDER)
    ]


def make_island() -> str:
    """Create the test island with its residents, buildings and roads. Returns its id."""
    with transaction.atomic():
        Island.objects.create(id=ISLAND_ID, name="Sunny Isle", repo=None, map=default_map("classic"))
        # Residents point at buildings and buildings at residents: SQLite checks
        # foreign keys when the transaction commits, so the order doesn't matter.
        Resident.objects.bulk_create(residents())
        Building.objects.bulk_create(buildings())
        Road.objects.bulk_create(roads())
    return ISLAND_ID


@pytest.fixture
def island():
    """The test island, in the test database."""
    return make_island()


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
def fake_runtime(monkeypatch):
    """A Runtime stand-in behind views.get_runtime(): a scripted engine, no server."""
    from types import SimpleNamespace

    from mii import views

    workflow, client = make_workflow(ScriptedRunner())

    async def reload():
        return {"reloaded": True}

    runtime = SimpleNamespace(
        workflow=workflow,
        bubbles=workflow.bubbles,
        bus=workflow.bus,
        judge=workflow.judge,
        reload=reload,
        client=client,
    )
    monkeypatch.setattr(views, "get_runtime", lambda: runtime)
    return runtime


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    return Sandbox(make_git_repo(tmp_path / "repo"), tmp_path / "worktrees")


@pytest.fixture
def sandboxes(island, sandbox) -> Sandboxes:
    """Real-mode engine fixture: the test island owns the throwaway repo."""
    Island.objects.filter(pk=island).update(repo_path=str(sandbox.repo_path))
    boxes = Sandboxes(sandbox.worktree_root)
    return boxes
