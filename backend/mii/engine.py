"""Workflow engine: moves task packages around the island.

The island's buildings and roads form a directed graph. A package starts at the
mailbox. At each building, that building's resident walks over, does the work
(through their brain's runner), and the outcome (pass/fail) picks the next road.
No road left = the task is finished. Event order: docs/protocol.md.

Database access is Django's async ORM (`aget`, `asave`, `async for`), because
the engine runs inside the ASGI server's event loop.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from mii import events, github
from mii.agents import AgentJob, StepResult
from mii.models import Building, Friendship, Island, Resident, Road, Step, Task, utcnow
from mii.sandbox import Sandbox, SandboxError, Sandboxes

log = logging.getLogger(__name__)

MAX_STEPS = 20  # safety net against packages bouncing forever
OUTPUT_LIMIT = 4000  # characters of command output we keep

# Mood a resident shows while a bubble of this kind is waiting.
BUBBLE_MOODS = {"confused": "confused", "struggling": "frustrated", "permission": "focused", "ready": "proud", "tired": "tired"}


# --- The island as a graph --------------------------------------------------------


class IslandGraph:
    def __init__(self, buildings: list[Building], roads: list[Road]):
        self.buildings = {b.id: b for b in buildings}
        self.roads = roads

    @classmethod
    async def load(cls, island_id: str) -> "IslandGraph":
        buildings = [b async for b in Building.objects.filter(island_id=island_id)]
        roads = [r async for r in Road.objects.filter(island_id=island_id).order_by("id")]
        return cls(buildings, roads)

    def start(self) -> str:
        """Packages arrive at the mailbox."""
        for b in self.buildings.values():
            if b.type == "mailbox":
                return b.id
        raise ValueError("this island has no mailbox")

    def next_building(self, current: str, outcome: str) -> str | None:
        """A road matching the outcome wins, then an 'always' road, else we're done."""
        outgoing = [r for r in self.roads if r.from_building_id == current]
        for road in outgoing:
            if road.condition == outcome:
                return road.to_building_id
        for road in outgoing:
            if road.condition == "always":
                return road.to_building_id
        return None


# --- Building handlers (one class per building type) ------------------------------
# A handler says what the resident should do there, and by default hands the work
# to the runner. Override `run` for steps that do more than talk to an agent.
# `engine` gives handlers the sandbox, the test command, and set_pr_url().

VERDICT_RULE = "End your reply with one line: `VERDICT: PASS` or `VERDICT: FAIL`."


class BuildingHandler:
    read_only = False  # True = the resident must not change files here

    def instructions(self, task: Task) -> str:
        raise NotImplementedError

    async def run(self, job: AgentJob, runner, engine: "WorkflowEngine") -> StepResult:
        return await runner.run(job)


class Mailbox(BuildingHandler):
    """The planner reads the request (a GitHub issue or a typed task) and writes a plan."""

    read_only = True

    def instructions(self, task: Task) -> str:
        return f"New request: {task.title}\n\n{task.description}\n\nWrite a short, numbered plan for the coder. Don't change files."


class Workshop(BuildingHandler):
    """The coder implements the plan in the task's worktree, then the engine commits it."""

    def instructions(self, task: Task) -> str:
        return (
            f"Implement: {task.title}\n\n{task.description}\n\nFollow the plan in the notes. "
            "If work was sent back, fix what was reported. Don't commit: your changes are committed for you when you're done."
        )

    async def run(self, job: AgentJob, runner, engine: "WorkflowEngine") -> StepResult:
        result = await runner.run(job)
        if job.sandbox and job.workdir:
            message = f"{job.task.title}\n\nStep by {job.resident.name} (attempt {job.attempt})."
            try:
                committed = await asyncio.to_thread(job.sandbox.commit_all, job.task.id, message, engine.git_author)
            except SandboxError as e:
                return StepResult("fail", f"{result.summary} Commit failed: {e}")
            result.summary += " Committed the changes." if committed else " No file changes to commit."
        return result


class Lab(BuildingHandler):
    """Tests run here. Pass goes on to review; fail goes back to the workshop.

    With TEST_COMMAND set (real mode) the engine runs the command itself and the
    exit code decides. Otherwise the resident is asked to run the tests.
    """

    def instructions(self, task: Task) -> str:
        return f"Run the project's tests for: {task.title}\n\n{VERDICT_RULE}"

    async def run(self, job: AgentJob, runner, engine: "WorkflowEngine") -> StepResult:
        if not (engine.test_command and job.workdir):
            return await runner.run(job)
        await job.report(f"Running: {engine.test_command}")
        code, output = await run_shell(engine.test_command, job.workdir, timeout=engine.step_timeout)
        tail = "\n".join(output.splitlines()[-20:])
        if code == 0:
            return StepResult("pass", f"Tests passed.\n{tail}".strip())
        return StepResult("fail", f"Tests failed (exit {code}).\n{tail}".strip())


class Courthouse(BuildingHandler):
    """The reviewer judges the diff. Pass goes to the post office; fail goes back.

    Real mode, on an island with a GitHub repo: once the reviewer passes it, you
    get a ✅ Ready bubble (unless the island's `merge_pr` house rule is off, which
    approves it for you). "Approve & merge" has the Post Office merge the PR it
    opens; "Send back: <note>" fails the step, so the package goes back to the
    Workshop with your note. No answer: the PR is opened but left for you.
    """

    read_only = True
    APPROVE, SEND_BACK = "Approve & merge", "Send back"

    def instructions(self, task: Task) -> str:
        return f"Review the changes on this branch for: {task.title}. If you fail it, say exactly what to fix.\n\n{VERDICT_RULE}"

    async def run(self, job: AgentJob, runner, engine: "WorkflowEngine") -> StepResult:
        if not (job.sandbox and job.workdir):
            return await runner.run(job)
        stat = await asyncio.to_thread(job.sandbox.diff_stat, job.task.id)
        job.instructions = f"{job.instructions}\n\nWhat changed on this branch (git log + diff --stat):\n{stat}"
        result = await runner.run(job)
        island = await Island.objects.aget(pk=job.task.island_id)
        if result.outcome != "pass" or not island.repo:
            return result  # nothing to merge without a GitHub repo
        if not job.rules.get("merge_pr"):
            engine._approved.add(job.task.id)  # house rule: merging needs no OK
            return result

        verdict = " ".join(result.summary.split())
        verdict = verdict if len(verdict) <= 220 else verdict[:220].rsplit(" ", 1)[0] + "…"
        answer = await job.ask("ready", verdict or "All clear! It looks good to me.",
                               [self.APPROVE, self.SEND_BACK], "\n".join(stat.splitlines()[-12:]))
        if answer and answer.startswith(self.SEND_BACK):
            note = answer[len(self.SEND_BACK):].lstrip(" :").strip()
            return StepResult("fail", f"The island owner sent it back{': ' + note if note else '.'}")
        if answer:
            engine._approved.add(job.task.id)
            result.summary += " Approved: the Post Office will merge it."
        else:
            result.summary += " Nobody approved it in time, so the PR will stay open."
        return result


class PostOffice(BuildingHandler):
    """Finished work leaves the island as a pull request."""

    read_only = True

    def instructions(self, task: Task) -> str:
        return (
            f"The change for '{task.title}' passed review. Write a pull request title and description. "
            "Put the title on the first line, then a blank line, then the description."
        )

    async def run(self, job: AgentJob, runner, engine: "WorkflowEngine") -> StepResult:
        result = await runner.run(job)
        if result.outcome != "pass" or not (job.sandbox and job.workdir and job.task.branch):
            return result
        island = await Island.objects.aget(pk=job.task.island_id)
        if not island.repo:
            result.summary += " No GitHub repo linked to this island, so no PR was opened."
            return result
        title, body = split_pr_text(result.summary, fallback_title=job.task.title)
        await job.report("Opening the pull request")
        try:
            url = await asyncio.to_thread(
                github.open_pull_request, island.repo, job.workdir, job.task.branch, title, body, island.branch
            )
        except github.GitHubError as e:
            return StepResult("fail", f"Could not open the PR: {e}")
        task = await engine.set_pr_url(job.task, url)
        if job.task.id not in engine._approved:
            return StepResult("pass", f"Opened {url}")
        await job.report("Merging the pull request")
        try:
            method = await asyncio.to_thread(github.merge_pull_request, url)
        except github.GitHubError as e:
            return StepResult("pass", f"Opened {url}, but couldn't merge it: {e}")
        task.merged_at = utcnow()
        await task.asave(update_fields=["merged_at"])
        return StepResult("pass", f"Opened and merged {url} ({method}).")


HANDLERS: dict[str, BuildingHandler] = {
    "mailbox": Mailbox(),
    "workshop": Workshop(),
    "lab": Lab(),
    "courthouse": Courthouse(),
    "post_office": PostOffice(),
}


def split_pr_text(text: str, fallback_title: str) -> tuple[str, str]:
    """First non-empty line = title (minus '#'/'Title:' decorations), the rest = body."""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return fallback_title, ""
    title = lines[0].strip().lstrip("#").strip()
    if title.lower().startswith("title:"):
        title = title[6:].strip()
    body = "\n".join(lines[1:]).strip()
    if body.lower().startswith("description:"):
        body = body[12:].strip()
    return (title or fallback_title)[:200], body


async def run_shell(command: str, cwd: Path, timeout: float) -> tuple[int, str]:
    """Run a shell command in `cwd`. Returns (exit code, combined output, capped)."""
    proc = await asyncio.create_subprocess_shell(
        command, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return 124, "(timed out)"
    output = raw.decode(errors="replace")
    if len(output) > OUTPUT_LIMIT:
        output = "…" + output[-OUTPUT_LIMIT:]
    return proc.returncode or 0, output


# --- The engine ---------------------------------------------------------------------


@dataclass
class Lane:
    """One island's line of packages. Islands work in parallel; within an island,
    one package at a time (each building has one resident)."""

    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    worker: asyncio.Task | None = None
    current: asyncio.Task | None = None  # the task run in progress
    current_id: str | None = None


class WorkflowEngine:
    """Runs task packages through the islands: one at a time per island, islands in parallel.

    submit() puts a task id in `queue` (the inbox); run_forever() hands each one to
    its island's Lane, whose worker runs that island's packages in order.
    """

    def __init__(
        self,
        bus: events.EventBus,
        bubbles: events.Bubbles,
        runner,
        *,
        sandboxes: Sandboxes | None = None,  # None in mock mode: no git at all
        walk_seconds: float = 2.0,
        bubble_timeout: float = 30.0,
        permission_timeout: float | None = None,  # None = bubble_timeout
        sleep_after: float = 45.0,
        step_timeout: float = 600.0,
        test_command: str | None = None,
        keep_worktrees: bool = False,
        git_author: str = "Mii-nagement <mii@localhost>",
    ):
        self.bus = bus
        self.bubbles = bubbles
        self.runner = runner
        self.sandboxes = sandboxes
        self.walk_seconds = walk_seconds
        self.bubble_timeout = bubble_timeout
        self.permission_timeout = permission_timeout or bubble_timeout
        self.sleep_after = sleep_after
        self.step_timeout = step_timeout
        self.test_command = test_command
        self.keep_worktrees = keep_worktrees
        self.git_author = git_author
        self.queue: asyncio.Queue[str] = asyncio.Queue()  # the inbox: every island's new task ids
        self.lanes: dict[str, Lane] = {}  # island id -> its line of packages (run_forever)
        self._cancelling: set[str] = set()  # task ids that cancel() is stopping
        self._approved: set[str] = set()  # task ids you approved at the Courthouse (merge at the Post Office)

    # --- Public API -----------------------------------------------------------

    async def submit(self, island_id: str, title: str, description: str = "", issue_number: int | None = None) -> Task:
        """Add a task to the queue. It starts at the island's mailbox."""
        task = await Task.objects.acreate(
            id="t_" + uuid.uuid4().hex[:6], island_id=island_id, title=title,
            description=description, issue_number=issue_number,
        )
        await self._emit(task, events.task(task))
        await self.queue.put(task.id)
        return task

    async def cancel(self, task_id: str) -> Task:
        """Cancel a queued task, or stop the one running (its resident drops the package).

        Raises KeyError for an unknown task and ValueError for one that already finished.
        A running task is stopped through asyncio cancellation, which also ends the
        agent process (see agents/acp.py); run_task then marks it `cancelled`.
        """
        task = await self._get(Task, task_id)
        if task.status == "queued":
            task = await self._update(Task, task_id, status="cancelled", finished_at=utcnow())
            await self._emit(task, events.task(task))  # run_forever skips it when it comes off the queue
            return task
        lane = self.lanes.get(task.island_id)
        if task.status == "in_progress" and lane and lane.current_id == task_id and not lane.current.done():
            self._cancelling.add(task_id)
            lane.current.cancel()
            await asyncio.wait([lane.current])
            return await self._get(Task, task_id)
        raise ValueError(f"task {task_id} is already {task.status}")

    def busy_islands(self) -> set[str]:
        """Islands with a package being worked on right now."""
        return {island for island, lane in self.lanes.items() if lane.current_id}

    async def run_forever(self) -> None:
        """Dispatcher: hand each new task to its island's lane (starting the lane's worker)."""
        try:
            while True:
                try:
                    task_id = await asyncio.wait_for(self.queue.get(), timeout=self.sleep_after)
                except TimeoutError:
                    await self._put_idle_residents_to_sleep()
                    continue
                task = await Task.objects.filter(pk=task_id, status="queued").afirst()
                if task is None:
                    continue  # cancelled while waiting, or its island was deleted
                lane = self.lanes.setdefault(task.island_id, Lane())
                lane.queue.put_nowait(task_id)
                if lane.worker is None or lane.worker.done():
                    lane.worker = asyncio.create_task(self._work_lane(lane))
        finally:  # the server is shutting down: stop every island's worker too
            workers = [lane.worker for lane in self.lanes.values() if lane.worker]
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def _work_lane(self, lane: Lane) -> None:
        """One island's worker: run its packages in order, until its queue is empty."""
        while not lane.queue.empty():
            task_id = lane.queue.get_nowait()
            if not await Task.objects.filter(pk=task_id, status="queued").aexists():
                continue  # cancelled while waiting in line
            lane.current_id = task_id
            lane.current = asyncio.create_task(self.run_task(task_id))
            try:
                await lane.current
            except asyncio.CancelledError:
                if task_id not in self._cancelling:
                    raise  # the server is shutting down
            except Exception:
                log.exception("task %s crashed", task_id)
                task = await self._update(Task, task_id, status="failed", finished_at=utcnow())
                await self._emit(task, events.task(task))
            finally:
                lane.current = lane.current_id = None
                self._cancelling.discard(task_id)
                self._approved.discard(task_id)

    async def recover(self) -> None:
        """After a restart: re-queue waiting tasks, fail half-done ones, wake everyone."""
        async for t in Task.objects.filter(status__in=["queued", "in_progress"]):
            if t.status == "in_progress":
                t.status, t.finished_at = "failed", utcnow()
                await t.asave(update_fields=["status", "finished_at"])
            else:
                self.queue.put_nowait(t.id)
        await Resident.objects.all().aupdate(state="idle")

    async def set_pr_url(self, task: Task, url: str) -> Task:
        task = await self._update(Task, task.id, pr_url=url)
        await self._emit(task, events.task(task))
        return task

    async def run_task(self, task_id: str) -> Task:
        task = await self._update(Task, task_id, status="in_progress")
        await self._emit(task, events.task(task))
        sandbox = await self._sandbox_for(task)
        try:
            return await self._run_task(task, sandbox)
        except asyncio.CancelledError:
            if task_id not in self._cancelling:
                raise  # the server is shutting down, not a cancel request
            return await self._drop_package(task_id)
        finally:
            if sandbox and not self.keep_worktrees:
                try:
                    await asyncio.to_thread(sandbox.remove_worktree, task.id)
                except SandboxError as e:
                    log.warning("could not remove worktree for %s: %s", task.id, e)

    async def _drop_package(self, task_id: str) -> Task:
        """cancel() stopped this task mid-step: the resident puts the package down."""
        task = await self._update(Task, task_id, status="cancelled", finished_at=utcnow())
        if task.agent_id:
            resident = await self._get(Resident, task.agent_id)
            await self._set_mood(resident, "confused")
            await self._set_state(resident, "idle")
        await self._emit(task, events.task(task))
        return task

    async def _sandbox_for(self, task: Task) -> Sandbox | None:
        """The island's repository sandbox (real mode only). A missing repo fails the task loudly."""
        if self.sandboxes is None:
            return None
        island = await Island.objects.aget(pk=task.island_id)
        return self.sandboxes.for_island(island)  # raises SandboxError -> run_forever marks the task failed

    async def _run_task(self, task: Task, sandbox: Sandbox | None) -> Task:
        island = await Island.objects.aget(pk=task.island_id)
        graph = await IslandGraph.load(task.island_id)
        # The task branch starts from the island's branch (`main` unless the creator chose another).
        workdir = await asyncio.to_thread(sandbox.create_worktree, task.id, island.branch) if sandbox else None
        if workdir:
            task = await self._update(Task, task.id, branch=f"mii/{task.id}")

        history: list[str] = []  # notes passed along with the package
        visits: dict[str, int] = {}
        previous: Resident | None = None
        outcome = "pass"
        building_id: str | None = graph.start()

        while building_id is not None and len(history) < MAX_STEPS:
            building = graph.buildings[building_id]
            resident = await self._get(Resident, building.resident_id)

            # 1. Hand the package over, if someone else takes it.
            if previous and previous.id != resident.id:
                await self._set_state(previous, "idle")
                result_word = "success" if outcome == "pass" else "fail"
                await self._emit(task, events.handoff(previous.id, resident.id, task.id, result_word))
                await self._befriend(task.island_id, previous.id, resident.id)

            # 2. Walk over, then 3. work.
            resident = await self._walk(resident, building_id)
            resident = await self._set_state(resident, "working")
            resident = await self._set_mood(resident, "focused")
            task = await self._update(Task, task.id, building_id=building_id, agent_id=resident.id)
            await self._emit(task, events.task(task))

            visits[building_id] = visits.get(building_id, 0) + 1
            result, step = await self._run_step(
                task, building, resident, visits[building_id], history, workdir, sandbox, rules=island.rules
            )
            history.append(f"{resident.name} at the {building.name}: {result.outcome.upper()}. {result.summary}")
            task = await self._get(Task, task.id)  # a handler may have set pr_url

            # 4. React, then follow the road that matches the outcome.
            resident = await self._set_mood(resident, "happy" if result.outcome == "pass" else "frustrated")
            previous, outcome = resident, result.outcome
            building_id = graph.next_building(building_id, outcome)

        gave_up = building_id is not None  # hit MAX_STEPS
        task = await self._update(
            Task, task.id, status="failed" if gave_up or outcome == "fail" else "done", finished_at=utcnow()
        )
        if previous:
            await self._set_mood(previous, "proud" if task.status == "done" else "frustrated")
            await self._set_state(previous, "idle")
        await self._emit(task, events.task(task))  # last event of the task (replay.py relies on this)
        return task

    # --- One step ------------------------------------------------------------------

    async def _run_step(self, task, building, resident, attempt, history, workdir, sandbox=None, rules=None) -> tuple[StepResult, Step]:
        """Run one building visit with a timeout, and record it as a Step row."""
        step = await Step.objects.acreate(
            island_id=task.island_id, task_id=task.id, building_id=building.id,
            building_type=building.type, resident_id=resident.id, brain=resident.brain,
        )
        bubbles_raised = 0

        async def ask(kind: str, text: str, options: list[str] | None = None, detail: str | None = None) -> str | None:
            nonlocal bubbles_raised
            bubbles_raised += 1
            # Permission and review bubbles are you approving something: they can wait longer.
            timeout = self.permission_timeout if kind in ("permission", "ready") else self.bubble_timeout
            await self._set_mood(resident, BUBBLE_MOODS.get(kind, "confused"))
            answer = await self.bubbles.ask(task.island_id, resident.id, kind, text, timeout, options, task.id, detail)
            await self._set_mood(resident, "focused")
            return answer

        handler = HANDLERS[building.type]
        job = AgentJob(
            resident=resident, task=task, building=building, instructions=handler.instructions(task),
            ask=ask, report=partial(self._report, task, resident), attempt=attempt,
            history=list(history), workdir=workdir, sandbox=sandbox, read_only=handler.read_only,
            rules=rules or {}, notebook=partial(self._set_notebook, resident),
            think=partial(self._think, task, resident),
        )
        timed_out = False
        try:
            result = await asyncio.wait_for(handler.run(job, self.runner, self), timeout=self.step_timeout)
        except TimeoutError:
            timed_out = True
            result = StepResult("fail", f"{resident.name} ran out of time.")
            await self._set_mood(resident, "tired")

        step = await self._update(Step, step.id, outcome=result.outcome, summary=result.summary, timed_out=timed_out,
                                  bubbles_raised=bubbles_raised, finished_at=utcnow())
        return result, step

    # --- Helpers -------------------------------------------------------------------

    async def _emit(self, task: Task, event: dict) -> None:
        await self.bus.emit(task.island_id, event)

    async def _befriend(self, island_id: str, one: str, other: str) -> None:
        """Every handoff brings the two residents a little closer (the friendship meter)."""
        a, b = sorted((one, other))
        friendship, _ = await Friendship.objects.aget_or_create(island_id=island_id, a=a, b=b)
        friendship.handoffs += 1
        friendship.updated_at = utcnow()
        await friendship.asave(update_fields=["handoffs", "updated_at"])
        await self.bus.emit(island_id, events.friendship(friendship))

    async def _think(self, task: Task, resident: Resident, kind: str, text: str) -> None:
        await self._emit(task, events.thinking(resident.id, task.id, kind, text))

    async def _report(self, task: Task, resident: Resident, text: str) -> None:
        await self._emit(task, events.activity(resident.id, text))

    async def _set_notebook(self, resident: Resident, value: float) -> None:
        """How full the resident's context window is (0-1), from the agent's usage updates."""
        value = round(min(max(float(value), 0.0), 1.0), 3)
        await Resident.objects.filter(pk=resident.id).aupdate(notebook=value)
        await self.bus.emit(resident.island_id, events.notebook(resident.id, value))

    async def _walk(self, resident: Resident, to_building: str) -> Resident:
        if resident.building_id == to_building:
            return resident
        if resident.building_id is not None:
            await self.bus.emit(resident.island_id, events.move(resident.id, resident.building_id, to_building))
        resident = await self._update(Resident, resident.id, state="walking", building_id=to_building)
        await self.bus.emit(resident.island_id, events.agent_state(resident.id, "walking", to_building))
        await asyncio.sleep(self.walk_seconds)  # give the game time to animate
        return resident

    async def _set_state(self, resident: Resident, state: str) -> Resident:
        resident = await self._update(Resident, resident.id, state=state)
        await self.bus.emit(resident.island_id, events.agent_state(resident.id, state, resident.building_id))
        return resident

    async def _set_mood(self, resident: Resident, mood: str) -> Resident:
        resident = await self._update(Resident, resident.id, mood=mood)
        await self.bus.emit(resident.island_id, events.mood(resident.id, mood))
        return resident

    async def _put_idle_residents_to_sleep(self) -> None:
        """Nap time on quiet islands (an island with a package in progress stays awake)."""
        idle = [r async for r in Resident.objects.filter(state="idle").exclude(island_id__in=self.busy_islands())]
        for resident in idle:
            await self._set_state(resident, "sleeping")
            await self._set_mood(resident, "tired")

    async def _get(self, model, row_id):
        try:
            return await model.objects.aget(pk=row_id)
        except model.DoesNotExist:
            raise KeyError(f"{model.__name__} {row_id} not found")

    async def _update(self, model, row_id, **changes):
        """Change some fields of one row and return the updated row."""
        row = await self._get(model, row_id)
        for name, value in changes.items():
            setattr(row, name, value)
        await row.asave(update_fields=list(changes))
        return row
