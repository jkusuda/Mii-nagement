import asyncio
import random
import subprocess

import pytest
from sqlmodel import Session, select

from mii import events, seed
from mii.agents import StepResult
from mii.agents.mock import MockRunner
from mii.engine import IslandGraph, WorkflowEngine
from mii.models import Event, Step, make_engine
from mii.sandbox import Sandbox, SandboxError, is_inside


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


@pytest.fixture
def db():
    engine = make_engine("sqlite://")  # fresh in-memory database per test
    seed.seed(engine)
    return engine


def make_workflow(db, runner, **kwargs):
    bus = events.EventBus(db)
    client = FakeClient()
    bus.add_client(client)
    kwargs = {"walk_seconds": 0, "bubble_timeout": 0.05, **kwargs}
    return WorkflowEngine(db, bus, events.Bubbles(bus), runner, **kwargs), client


# --- Graph ----------------------------------------------------------------------


def test_graph_follows_roads_by_outcome(db):
    with Session(db) as session:
        graph = IslandGraph.load(session, "sunny")
    assert graph.start() == "mailbox"
    assert graph.next_building("mailbox", "pass") == "workshop"  # "always" road
    assert graph.next_building("lab", "pass") == "courthouse"
    assert graph.next_building("lab", "fail") == "workshop"
    assert graph.next_building("post_office", "pass") is None  # end of the line


# --- Engine -----------------------------------------------------------------------


async def test_task_bounces_back_on_fail_then_finishes(db):
    runner = ScriptedRunner(lab=["fail", "pass"])
    workflow, client = make_workflow(db, runner)
    task = await workflow.run_task((await workflow.submit("sunny", "Fix the thing")).id)

    assert task.status == "done"
    assert runner.visited == ["mailbox", "workshop", "lab", "workshop", "lab", "courthouse", "post_office"]
    handoffs = [(e["from"], e["to"], e["outcome"]) for e in client.of_type("handoff")]
    assert handoffs == [("jun", "carlos", "success"), ("carlos", "sydney", "success"), ("sydney", "jun", "success")]


async def test_package_that_never_passes_gives_up(db):
    workflow, _ = make_workflow(db, ScriptedRunner(lab=["fail"] * 100))
    task = await workflow.run_task((await workflow.submit("sunny", "Impossible bug")).id)
    assert task.status == "failed"


async def test_every_step_is_recorded_for_insights(db):
    workflow, _ = make_workflow(db, ScriptedRunner(courthouse=["fail", "pass"]))
    task = await workflow.run_task((await workflow.submit("sunny", "Fix the thing")).id)
    with Session(db) as session:
        steps = session.exec(select(Step).where(Step.task_id == task.id)).all()
    assert [s.building_type for s in steps][:5] == ["mailbox", "workshop", "lab", "courthouse", "workshop"]
    assert steps[3].outcome == "fail" and steps[3].brain == "codex"  # Sydney's brain
    assert all(s.finished_at is not None for s in steps)


async def test_slow_step_times_out_and_fails(db):
    workflow, _ = make_workflow(db, ScriptedRunner(delay=1.0), step_timeout=0.01)
    task = await workflow.run_task((await workflow.submit("sunny", "Slow one")).id)
    with Session(db) as session:
        first = session.exec(select(Step).where(Step.task_id == task.id)).first()
    assert first.timed_out and first.outcome == "fail"


async def test_every_event_is_stored_in_sqlite(db):
    workflow, client = make_workflow(db, ScriptedRunner())
    await workflow.run_task((await workflow.submit("sunny", "Fix the thing")).id)
    with Session(db) as session:
        assert len(session.exec(select(Event)).all()) == len(client.sent) > 0


async def test_mock_runner_end_to_end(db):
    runner = MockRunner(work_seconds=(0, 0), bubble_chance=1.0, rng=random.Random(7))
    workflow, client = make_workflow(db, runner)
    task = await workflow.run_task((await workflow.submit("sunny", "Mock task")).id)
    assert task.status in ("done", "failed")
    assert len(client.of_type("activity")) > 0
    assert len(client.of_type("bubble")) == len(client.of_type("bubble_resolved")) > 0


# --- Bubbles -------------------------------------------------------------------------


async def test_bubble_answer_reaches_the_waiting_resident(db):
    bus = events.EventBus(db)
    client = FakeClient()
    bus.add_client(client)
    bubbles = events.Bubbles(bus)

    asking = asyncio.create_task(bubbles.ask("sunny", "carlos", "permission", "Run npm?", 5, ["Allow", "Deny"]))
    await asyncio.sleep(0)  # let ask() run until it's waiting
    shown = client.of_type("bubble")[0]
    assert shown["options"] == ["Allow", "Deny"]

    assert bubbles.answer(shown["id"], "Allow") is True
    assert await asking == "Allow"
    assert bubbles.answer(shown["id"], "again") is False  # already closed


async def test_bubble_times_out_with_none(db):
    bubbles = events.Bubbles(events.EventBus(db))
    assert await bubbles.ask("sunny", "jun", "tired", "Zzz?", timeout=0.01) is None
    assert bubbles.open == {}


# --- Sandbox -------------------------------------------------------------------------


def test_is_inside(tmp_path):
    assert is_inside(tmp_path / "a" / "b.py", tmp_path)
    assert not is_inside(tmp_path / ".." / "elsewhere", tmp_path)
    assert not is_inside("/etc/passwd", tmp_path)


def test_worktrees_are_created_under_the_worktree_root(tmp_path):
    # A throwaway git repo inside pytest's temp folder.
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "init"], check=True)

    sandbox = Sandbox(repo, tmp_path / "worktrees")
    path = sandbox.create_worktree("t_abc123")
    assert path.exists() and is_inside(path, tmp_path / "worktrees")
    sandbox.check_workdir(path)  # doesn't raise
    with pytest.raises(SandboxError):
        sandbox.create_worktree("../../evil")
    with pytest.raises(SandboxError):
        sandbox.check_workdir(repo)
