"""Workflow engine: moves task packages around the island.

The island's buildings and roads form a directed graph. A package starts at the
mailbox. At each building, that building's resident walks over, does the work
(through their brain's runner), and the outcome (pass/fail) picks the next road.
No road left = the task is finished. Event order: docs/protocol.md.
"""

import asyncio
import logging
import uuid
from functools import partial

from sqlmodel import Session, select

from mii import events
from mii.agents import AgentJob, StepResult
from mii.models import Building, Resident, Road, Step, Task, utcnow
from mii.sandbox import Sandbox

log = logging.getLogger(__name__)

MAX_STEPS = 20  # safety net against packages bouncing forever

# Mood a resident shows while a bubble of this kind is waiting.
BUBBLE_MOODS = {"confused": "confused", "struggling": "frustrated", "permission": "focused", "ready": "proud", "tired": "tired"}


# --- The island as a graph --------------------------------------------------------


class IslandGraph:
    def __init__(self, buildings: list[Building], roads: list[Road]):
        self.buildings = {b.id: b for b in buildings}
        self.roads = roads

    @classmethod
    def load(cls, session: Session, island_id: str) -> "IslandGraph":
        buildings = session.exec(select(Building).where(Building.island_id == island_id)).all()
        roads = session.exec(select(Road).where(Road.island_id == island_id)).all()
        return cls(list(buildings), list(roads))

    def start(self) -> str:
        """Packages arrive at the mailbox."""
        for b in self.buildings.values():
            if b.type == "mailbox":
                return b.id
        raise ValueError("this island has no mailbox")

    def next_building(self, current: str, outcome: str) -> str | None:
        """A road matching the outcome wins, then an 'always' road, else we're done."""
        outgoing = [r for r in self.roads if r.from_id == current]
        for road in outgoing:
            if road.condition == outcome:
                return road.to_id
        for road in outgoing:
            if road.condition == "always":
                return road.to_id
        return None


# --- Building handlers (one class per building type) ------------------------------
# A handler says what the resident should do there, and by default hands the work
# to the runner. Override `run` for steps that don't need an agent.

VERDICT_RULE = "End your reply with one line: `VERDICT: PASS` or `VERDICT: FAIL`."


class BuildingHandler:
    def instructions(self, task: Task) -> str:
        raise NotImplementedError

    async def run(self, job: AgentJob, runner) -> StepResult:
        return await runner.run(job)


class Mailbox(BuildingHandler):
    """The planner reads the request (a GitHub issue or a typed task) and writes a plan."""

    def instructions(self, task: Task) -> str:
        return f"New request: {task.title}\n\n{task.description}\n\nWrite a short, numbered plan for the coder. Don't change files."


class Workshop(BuildingHandler):
    """The coder implements the plan in the task's worktree."""

    # TODO(mvp): commit the coder's changes on the task branch after this step.
    def instructions(self, task: Task) -> str:
        return f"Implement: {task.title}\n\n{task.description}\n\nFollow the plan in the notes. If work was sent back, fix what was reported."


class Lab(BuildingHandler):
    """Tests run here. Pass goes on to review; fail goes back to the workshop."""

    # TODO(mvp): run the repo's test command here (override `run`) and pass/fail on the exit code.
    def instructions(self, task: Task) -> str:
        return f"Run the project's tests for: {task.title}\n\n{VERDICT_RULE}"


class Courthouse(BuildingHandler):
    """The reviewer judges the diff. Pass goes to the post office; fail goes back."""

    def instructions(self, task: Task) -> str:
        return f"Review the changes on this branch for: {task.title}. If you fail it, say exactly what to fix.\n\n{VERDICT_RULE}"


class PostOffice(BuildingHandler):
    """Finished work leaves the island as a pull request."""

    # TODO(mvp): after the agent writes the description, call github.open_pull_request()
    # and save the URL on task.pr_url.
    def instructions(self, task: Task) -> str:
        return f"The change for '{task.title}' passed review. Write a pull request title and description."


HANDLERS: dict[str, BuildingHandler] = {
    "mailbox": Mailbox(),
    "workshop": Workshop(),
    "lab": Lab(),
    "courthouse": Courthouse(),
    "post_office": PostOffice(),
}


# --- The engine ---------------------------------------------------------------------


class WorkflowEngine:
    """Runs task packages through the island, one task at a time."""

    def __init__(
        self,
        db_engine,
        bus: events.EventBus,
        bubbles: events.Bubbles,
        runner,
        *,
        sandbox: Sandbox | None = None,  # None in mock mode: no git at all
        walk_seconds: float = 2.0,
        bubble_timeout: float = 30.0,
        sleep_after: float = 45.0,
        step_timeout: float = 600.0,
    ):
        self.db_engine = db_engine
        self.bus = bus
        self.bubbles = bubbles
        self.runner = runner
        self.sandbox = sandbox
        self.walk_seconds = walk_seconds
        self.bubble_timeout = bubble_timeout
        self.sleep_after = sleep_after
        self.step_timeout = step_timeout
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    # --- Public API -----------------------------------------------------------

    async def submit(self, island_id: str, title: str, description: str = "", issue_number: int | None = None) -> Task:
        """Add a task to the queue. It starts at the island's mailbox."""
        task = Task(id="t_" + uuid.uuid4().hex[:6], island_id=island_id, title=title,
                    description=description, issue_number=issue_number)
        self._save(task)
        await self._emit(task, events.task(task))
        await self.queue.put(task.id)
        return task

    async def run_forever(self) -> None:
        """Worker loop: take tasks off the queue and run them."""
        while True:
            try:
                task_id = await asyncio.wait_for(self.queue.get(), timeout=self.sleep_after)
            except TimeoutError:
                await self._put_idle_residents_to_sleep()
                continue
            try:
                await self.run_task(task_id)
            except Exception:
                log.exception("task %s crashed", task_id)
                task = self._update(Task, task_id, status="failed")
                await self._emit(task, events.task(task))

    async def recover(self) -> None:
        """After a restart: re-queue waiting tasks, fail half-done ones, wake everyone."""
        with Session(self.db_engine) as session:
            for t in session.exec(select(Task).where(Task.status.in_(["queued", "in_progress"]))).all():
                if t.status == "in_progress":
                    t.status = "failed"
                else:
                    self.queue.put_nowait(t.id)
            for r in session.exec(select(Resident)).all():
                r.state = "idle"
            session.commit()

    async def run_task(self, task_id: str) -> Task:
        task = self._update(Task, task_id, status="in_progress")
        await self._emit(task, events.task(task))

        with Session(self.db_engine) as session:
            graph = IslandGraph.load(session, task.island_id)
        workdir = self.sandbox.create_worktree(task.id) if self.sandbox else None
        if workdir:
            task = self._update(Task, task.id, branch=f"mii/{task.id}")

        history: list[str] = []  # notes passed along with the package
        visits: dict[str, int] = {}
        previous: Resident | None = None
        outcome = "pass"
        building_id: str | None = graph.start()

        while building_id is not None and len(history) < MAX_STEPS:
            building = graph.buildings[building_id]
            resident = self._get(Resident, building.resident_id)

            # 1. Hand the package over, if someone else takes it.
            if previous and previous.id != resident.id:
                await self._set_state(previous, "idle")
                result_word = "success" if outcome == "pass" else "fail"
                await self._emit(task, events.handoff(previous.id, resident.id, task.id, result_word))

            # 2. Walk over, then 3. work.
            resident = await self._walk(resident, building_id)
            resident = await self._set_state(resident, "working")
            resident = await self._set_mood(resident, "focused")
            task = self._update(Task, task.id, building_id=building_id, agent_id=resident.id)
            await self._emit(task, events.task(task))

            visits[building_id] = visits.get(building_id, 0) + 1
            result, step = await self._run_step(task, building, resident, visits[building_id], history, workdir)
            history.append(f"{resident.name} at the {building.name}: {result.outcome.upper()}. {result.summary}")

            # 4. React, then follow the road that matches the outcome.
            resident = await self._set_mood(resident, "happy" if result.outcome == "pass" else "frustrated")
            previous, outcome = resident, result.outcome
            building_id = graph.next_building(building_id, outcome)

        gave_up = building_id is not None  # hit MAX_STEPS
        task = self._update(Task, task.id, status="failed" if gave_up or outcome == "fail" else "done")
        await self._emit(task, events.task(task))
        if previous:
            await self._set_mood(previous, "proud" if task.status == "done" else "frustrated")
            await self._set_state(previous, "idle")
        return task

    # --- One step ------------------------------------------------------------------

    async def _run_step(self, task, building, resident, attempt, history, workdir) -> tuple[StepResult, Step]:
        """Run one building visit with a timeout, and record it as a Step row."""
        step = self._save(Step(island_id=task.island_id, task_id=task.id, building_id=building.id,
                               building_type=building.type, resident_id=resident.id, brain=resident.brain))
        bubbles_raised = 0

        async def ask(kind: str, text: str, options: list[str] | None = None) -> str | None:
            nonlocal bubbles_raised
            bubbles_raised += 1
            await self._set_mood(resident, BUBBLE_MOODS.get(kind, "confused"))
            answer = await self.bubbles.ask(task.island_id, resident.id, kind, text, self.bubble_timeout, options)
            await self._set_mood(resident, "focused")
            return answer

        handler = HANDLERS[building.type]
        job = AgentJob(
            resident=resident, task=task, building=building, instructions=handler.instructions(task),
            ask=ask, report=partial(self._report, task, resident), attempt=attempt,
            history=list(history), workdir=workdir,
        )
        timed_out = False
        try:
            result = await asyncio.wait_for(handler.run(job, self.runner), timeout=self.step_timeout)
        except TimeoutError:
            timed_out = True
            result = StepResult("fail", f"{resident.name} ran out of time.")
            await self._set_mood(resident, "tired")

        step = self._update(Step, step.id, outcome=result.outcome, timed_out=timed_out,
                            bubbles_raised=bubbles_raised, finished_at=utcnow())
        return result, step

    # --- Helpers -------------------------------------------------------------------

    async def _emit(self, task: Task, event: dict) -> None:
        await self.bus.emit(task.island_id, event)

    async def _report(self, task: Task, resident: Resident, text: str) -> None:
        await self._emit(task, events.activity(resident.id, text))

    async def _walk(self, resident: Resident, to_building: str) -> Resident:
        if resident.building_id == to_building:
            return resident
        if resident.building_id is not None:
            await self.bus.emit(resident.island_id, events.move(resident.id, resident.building_id, to_building))
        resident = self._update(Resident, resident.id, state="walking", building_id=to_building)
        await self.bus.emit(resident.island_id, events.agent_state(resident.id, "walking", to_building))
        await asyncio.sleep(self.walk_seconds)  # give the game time to animate
        return resident

    async def _set_state(self, resident: Resident, state: str) -> Resident:
        resident = self._update(Resident, resident.id, state=state)
        await self.bus.emit(resident.island_id, events.agent_state(resident.id, state, resident.building_id))
        return resident

    async def _set_mood(self, resident: Resident, mood: str) -> Resident:
        resident = self._update(Resident, resident.id, mood=mood)
        await self.bus.emit(resident.island_id, events.mood(resident.id, mood))
        return resident

    async def _put_idle_residents_to_sleep(self) -> None:
        with Session(self.db_engine) as session:
            idle = session.exec(select(Resident).where(Resident.state == "idle")).all()
        for resident in idle:
            await self._set_state(resident, "sleeping")
            await self._set_mood(resident, "tired")

    def _save(self, row):
        with Session(self.db_engine, expire_on_commit=False) as session:
            session.add(row)
            session.commit()
        return row

    def _get(self, model, row_id):
        with Session(self.db_engine, expire_on_commit=False) as session:
            row = session.get(model, row_id)
        if row is None:
            raise KeyError(f"{model.__name__} {row_id} not found")
        return row

    def _update(self, model, row_id, **changes):
        """Change some fields of one row and return the updated row."""
        with Session(self.db_engine, expire_on_commit=False) as session:
            row = session.get(model, row_id)
            if row is None:
                raise KeyError(f"{model.__name__} {row_id} not found")
            for name, value in changes.items():
                setattr(row, name, value)
            session.commit()
        return row
