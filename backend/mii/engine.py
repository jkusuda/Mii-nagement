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
import re
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from django.db.models import Q

from mii import events, github, metrics, weather
from mii.agents import AgentJob, AgentRunner, StepResult, end_task
from mii.judge import HelpRequest, RuleJudge
from mii.models import Attachment, Building, Event, Friendship, Island, Resident, Road, Step, Task, utcnow
from mii.sandbox import Sandbox, SandboxError, Sandboxes, run_shell
from mii.vocab import CHECK_STEPS, DEFAULT_GIT_AUTHOR, SEND_BACK

log = logging.getLogger(__name__)

MAX_STEPS = 20  # safety net against packages bouncing forever
MAX_VISITS = 3  # visits to one building per task; one more and the task fails ("gave up at the Workshop: ...")
FLAKY_RERUN_AT = 0.7  # the judge's "this failure looks flaky" probability that earns the tests one more run
MAX_HELPS = 2  # help visits per task (a stuck resident gets a neighbour's hand this many times)
RERUN_MIN_LEFT = 10.0  # seconds of step budget a flaky re-run needs before it is worth starting
NOTES_CHARS = 1500  # a resident's diary (Resident.notes) keeps about this much; the oldest lines go first
CHAT_CHARS = 200  # how much of a helper's report is said out loud (the `chat` back)
PRETEST_RETRIES = 2  # the Workshop's own test run may send the work back to the coder this many times per visit
TEST_TAIL_LINES = 20  # how much of the test output goes into reports and prompts
TALK_TURN = "talk_turn"  # Event.type of a line said in a talk (talk.py); the owner's lines steer the package
DIRECTION_LINES = 10  # the owner's newest talk lines about a package that later steps read

# Mood a resident shows while a bubble of this kind is waiting.
BUBBLE_MOODS = {
    "confused": "confused",
    "struggling": "frustrated",
    "permission": "focused",
    "ready": "proud",
    "tired": "tired",
}
# What the master key says to a question bubble (a free-text one, with no options).
MASTER_KEY_ANSWER = "No need to ask: use your best judgement and carry on."
# Permission buttons that refuse; the master key picks the first button that isn't one.
REFUSALS = ("not now", "never", "deny", "reject", "no")


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
# to the runner. Override `run` for steps that do more than talk to an agent; `engine`
# gives them the test command, the git author, the step timeout, the judge, set_pr_url()
# and approve() / approved(). Every prompt ends with the VERDICT rule (agents.build_prompt).


class BuildingHandler:
    read_only = False  # True = the resident must not change files here
    role = "You work here."  # what this building is for, in a line (talk.py's chat prompt)

    def instructions(self, task: Task) -> str:
        raise NotImplementedError

    async def run(self, job: AgentJob, runner: AgentRunner, engine: "WorkflowEngine") -> StepResult:
        return await runner.run(job)


class Mailbox(BuildingHandler):
    """The planner reads the request (a GitHub issue or a typed task) and writes a plan."""

    read_only = True
    role = "This is where requests arrive. You read them and turn them into short, numbered plans."

    def instructions(self, task: Task) -> str:
        return (
            f"New request: {task.title}\n\n{task.description}\n\n"
            "Write a short, numbered plan for the coder. Don't change files."
        )


class Workshop(BuildingHandler):
    """The coder implements the plan in the task's worktree, then the engine commits it.

    With TEST_COMMAND set, the engine then runs the tests itself before the handoff. A
    failure goes straight back to the coder in this same step (PRETEST_RETRIES times at
    most), so a red test doesn't cost a whole Workshop -> Lab -> Workshop trip. The last
    attempt is left for the Lab to test.
    """

    role = "This is where the code gets written. You implement plans and fix what was sent back."

    def instructions(self, task: Task) -> str:
        return (
            f"Implement: {task.title}\n\n{task.description}\n\nFollow the plan in the notes. "
            "If work was sent back, fix what was reported. "
            "Don't commit: your changes are committed for you when you're done."
        )

    async def run(self, job: AgentJob, runner: AgentRunner, engine: "WorkflowEngine") -> StepResult:
        result = await self._code(job, runner, engine)
        asked, failing = job.instructions, None
        for retry in range(1, PRETEST_RETRIES + 1):
            if result.outcome != "pass" or not (engine.test_command and job.workdir):
                break
            await job.report(f"Checking before the handoff: {engine.test_command}")
            code, output = await run_shell(engine.test_command, job.workdir, timeout=engine.step_timeout)
            if code == 0:
                result.summary += " The tests pass."
                break
            failing = _tail(output) or f"exit {code}"
            await job.report(f"The tests fail (exit {code}); back to the code ({retry}/{PRETEST_RETRIES})")
            job.instructions = (
                f"Your change is committed, but the tests fail (exit {code}). Fix that before you hand it on:\n"
                f"{failing}\n\n{asked}"
            )
            result = await self._code(job, runner, engine)
        job.instructions = asked
        result.test_output = failing  # the last failure seen here: the engine compares it with the Lab's
        return result

    async def _code(self, job: AgentJob, runner: AgentRunner, engine: "WorkflowEngine") -> StepResult:
        """One turn of the coder, then commit what changed."""
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

    role = "This is where the tests run. You check that changes work and say what broke."

    def instructions(self, task: Task) -> str:
        return f"Run the project's tests for: {task.title}"

    async def run(self, job: AgentJob, runner: AgentRunner, engine: "WorkflowEngine") -> StepResult:
        if not (engine.test_command and job.workdir):
            return await runner.run(job)
        await job.report(f"Running: {engine.test_command}")
        started = time.monotonic()
        code, output = await run_shell(engine.test_command, job.workdir, timeout=engine.step_timeout)
        tail = _tail(output)
        if code == 0:
            return StepResult("pass", f"Tests passed.\n{tail}".strip())
        # A failure that looks flaky (timeouts, ports, ordering...) gets one more run before it
        # bounces the package back to the Workshop. The judge decides; the rules never re-run.
        # The re-run fits in what is left of the step budget (_run_step's wait_for is the hard cap).
        left = engine.step_timeout - (time.monotonic() - started)
        if left > RERUN_MIN_LEFT:
            stat = await asyncio.to_thread(job.sandbox.diff_stat, job.task.id) if job.sandbox else ""
            if await engine.judge.flaky(output, stat) >= FLAKY_RERUN_AT:
                await job.report("That failure looks flaky; running the tests once more")
                code, output = await run_shell(engine.test_command, job.workdir, timeout=left - 1.0)
                tail = _tail(output)
                if code == 0:
                    return StepResult(
                        "pass", f"Tests passed on the second run (the first looked flaky).\n{tail}".strip(), flaky=True
                    )
        return StepResult("fail", f"Tests failed (exit {code}).\n{tail}".strip(), test_output=tail or f"exit {code}")


class Courthouse(BuildingHandler):
    """The reviewer judges the diff. Pass goes to the post office; fail goes back.

    Real mode, on an island with a GitHub repo: once the reviewer passes it, you
    get a ✅ Ready bubble (unless the island's `merge_pr` house rule is off, which
    approves it for you). "Approve & merge" has the Post Office merge the PR it
    opens; "Send back: <note>" fails the step, so the package goes back to the
    Workshop with your note. No answer: the PR is opened but left for you.
    """

    read_only = True
    role = "This is where changes are reviewed. You judge diffs honestly and say exactly what to fix."
    APPROVE = "Approve & merge"

    def instructions(self, task: Task) -> str:
        return f"Review the changes on this branch for: {task.title}. If you fail it, say exactly what to fix."

    async def run(self, job: AgentJob, runner: AgentRunner, engine: "WorkflowEngine") -> StepResult:
        if not (job.sandbox and job.workdir):
            return await runner.run(job)
        stat = await asyncio.to_thread(job.sandbox.diff_stat, job.task.id)
        job.instructions = f"{job.instructions}\n\nWhat changed on this branch (git log + diff --stat):\n{stat}"
        result = await runner.run(job)
        if result.outcome != "pass" or not job.island.repo:
            return result  # nothing to merge without a GitHub repo
        if not job.rules.get("merge_pr"):
            engine.approve(job.task.id)  # house rule: merging needs no OK
            return result

        verdict = _first_words(result.summary, 220) if result.summary.strip() else "All clear! It looks good to me."
        answer = await job.ask("ready", verdict, [self.APPROVE, SEND_BACK], "\n".join(stat.splitlines()[-12:]))
        if answer and answer.startswith(SEND_BACK):
            note = answer[len(SEND_BACK) :].lstrip(" :").strip()
            return StepResult("fail", f"The island owner sent it back{': ' + note if note else '.'}")
        if answer:
            engine.approve(job.task.id)
            result.summary += " Approved: the Post Office will merge it."
        else:
            result.summary += " Nobody approved it in time, so the PR will stay open."
        return result


class PostOffice(BuildingHandler):
    """Finished work leaves the island as a pull request."""

    read_only = True
    role = "This is where finished work leaves the island as a pull request. You write PR titles and descriptions."

    def instructions(self, task: Task) -> str:
        return (
            f"The change for '{task.title}' passed review. Write a pull request title and description. "
            "Put the title on the first line, then a blank line, then the description."
        )

    async def run(self, job: AgentJob, runner: AgentRunner, engine: "WorkflowEngine") -> StepResult:
        result = await runner.run(job)
        if result.outcome != "pass" or not (job.sandbox and job.workdir and job.task.branch):
            return result
        island = job.island
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
        if not engine.approved(job.task.id):
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


@dataclass
class Note:
    """One step's note in the package history."""

    line: str  # "Carlos at the Workshop: FAIL", for the recap
    text: str  # the line plus the step's report
    outcome: str
    building_type: str


def notes_for_prompt(history: list[Note]) -> list[str]:
    """What the next resident reads about earlier steps: a one-line recap of every step, then
    in full only the plan, the latest failure report and the step just before this one. The
    Step rows and events keep everything; this only keeps prompts short on long trips."""
    if not history:
        return []
    plan = next((n for n in history if n.building_type == "mailbox" and n.outcome == "pass"), None)
    failure = next((n for n in reversed(history) if n.outcome == "fail"), None)
    full = [n for n in history if n is plan or n is failure or n is history[-1]]
    recap = [f"So far: {' -> '.join(n.line for n in history)}."] if len(full) < len(history) else []
    return recap + [n.text for n in full]


def master_key_answer(options: list[str] | None) -> str:
    """The master key's answer to a bubble: the first button that isn't a refusal (so "Approve"
    at the Courthouse, "Just this once" for a permission), or "carry on" to a question."""
    for option in options or []:
        if option.strip().lower() not in REFUSALS:
            return option
    return (options or [MASTER_KEY_ANSWER])[0]


def _tail(output: str) -> str:
    """The last TEST_TAIL_LINES lines of a command's output."""
    return "\n".join(output.splitlines()[-TEST_TAIL_LINES:])


def _normal(report: str) -> str:
    """A failure report for comparing: lower case, numbers (timings, line numbers, ids) and
    extra whitespace dropped, so "failed in 0.31s" and "failed in 0.29s" match."""
    return " ".join(re.sub(r"\d+(\.\d+)?", "#", report.lower()).split())


def _first_words(text: str, chars: int) -> str:
    """The start of `text` on one line, cut at a word boundary."""
    text = " ".join(text.split())
    if len(text) <= chars:
        return text or "Done."
    return text[:chars].rsplit(" ", 1)[0] + "…"


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


# --- The engine ---------------------------------------------------------------------


class TalkHolds:
    """Residents the island owner is talking to (talk.py). A resident is inside a building
    only while working there, so the engine keeps a held resident at work, and the package
    with them, until the talk ends: they never walk out mid-conversation."""

    def __init__(self):
        self._held: dict[str, asyncio.Event] = {}  # resident id -> set when the talk ends

    def hold(self, resident_id: str) -> None:
        self._held.setdefault(resident_id, asyncio.Event())

    def release(self, resident_id: str) -> None:
        event = self._held.pop(resident_id, None)
        if event is not None:
            event.set()

    def held(self, resident_id: str) -> bool:
        return resident_id in self._held

    async def wait(self, resident_id: str) -> None:
        event = self._held.get(resident_id)
        if event is not None:
            await event.wait()


@dataclass
class Lane:
    """One island's line of packages. Islands work in parallel; within an island, up to
    `WorkflowEngine.parallel` packages at a time (residents are booked one step at a time)."""

    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    worker: asyncio.Task | None = None
    running: dict[str, asyncio.Task] = field(default_factory=dict)  # task id -> its run in progress
    wake: asyncio.Event = field(default_factory=asyncio.Event)  # set when a package arrives or finishes


class WorkflowEngine:
    """Runs task packages through the islands: up to `parallel` at a time per island, islands in parallel.

    submit() puts a task id in `queue` (the inbox); run_forever() hands each one to
    its island's Lane, whose worker starts that island's packages in order. Each step
    books its resident (`_claim`): when the building's owner is busy with another
    package, a free neighbour stands in, so a big island keeps everyone busy.
    """

    def __init__(
        self,
        bus: events.EventBus,
        bubbles: events.Bubbles,
        runner: AgentRunner,
        *,
        sandboxes: Sandboxes | None = None,  # None in mock mode: no git at all
        walk_seconds: float = 2.0,
        bubble_timeout: float = 30.0,
        permission_timeout: float | None = None,  # None = bubble_timeout
        sleep_after: float = 45.0,
        step_timeout: float = 600.0,
        test_command: str | None = None,
        keep_worktrees: bool = False,
        git_author: str = DEFAULT_GIT_AUTHOR,
        judge: RuleJudge | None = None,  # RuleJudge (default) or JevJudge: moods, verdicts, flaky runs, help
        parallel: int = 1,  # packages one island works on at once (the `parallel_packages` setting)
        master_key: bool = False,  # residents never ask: every bubble answers itself (master_key_answer)
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
        self.holds = TalkHolds()  # residents you're talking to stay inside until you're done
        self.test_command = test_command
        self.keep_worktrees = keep_worktrees
        self.git_author = git_author
        self.judge = judge or RuleJudge()
        self.parallel = parallel
        self.master_key = master_key
        self.queue: asyncio.Queue[str] = asyncio.Queue()  # the inbox: every island's new task ids
        self.lanes: dict[str, Lane] = {}  # island id -> its line of packages (run_forever)
        self._cancelling: set[str] = set()  # task ids that cancel() is stopping
        self._approved: set[str] = set()  # task ids you approved at the Courthouse (merge at the Post Office)
        self._weather: dict[str, str] = {}  # island id -> the last weather we announced
        self._activity: dict[str, str] = {}  # resident id -> the last activity line we saved
        self._claimed: dict[str, str] = {}  # resident id -> the task they are doing a step for right now
        self._freed = asyncio.Event()  # set (and replaced) whenever a resident is released
        self._git = asyncio.Lock()  # one `git worktree add/remove` at a time (they share the repo's files)

    # --- Public API -----------------------------------------------------------

    async def submit(
        self,
        island_id: str,
        title: str,
        description: str = "",
        issue_number: int | None = None,
        attachments: list[tuple[str, bytes]] = (),
        task_id: str | None = None,
    ) -> Task:
        """Add a task to the queue. It starts at the island's mailbox. `attachments` are
        (safe file name, bytes), already checked by tasks.parse_attachments. `task_id` is
        for a task whose branch was made beforehand (merge_locally)."""
        task = await Task.objects.acreate(
            id=task_id or "t_" + uuid.uuid4().hex[:6],
            island_id=island_id,
            title=title,
            description=description,
            issue_number=issue_number,
        )
        for name, data in attachments:
            await Attachment.objects.acreate(task=task, name=name, data=data)
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
        run = lane.running.get(task_id) if lane else None
        if task.status == "in_progress" and run and not run.done():
            self._cancelling.add(task_id)
            run.cancel()
            await asyncio.wait([run])
            return await self._get(Task, task_id)
        raise ValueError(f"task {task_id} is already {task.status}")

    async def merge_locally(self, task_id: str) -> tuple[str, list[str], Task | None]:
        """Merge a finished task's branch into the island folder's checked-out branch.

        Returns (that branch, conflicting files, the new task). On a conflict nothing changes
        in the folder and a new package goes to the mailbox: its branch already has the merge
        with conflict markers, for the residents to fix. Raises KeyError for an unknown task,
        ValueError when there's nothing to merge and SandboxError when git refuses.
        """
        task = await self._get(Task, task_id)
        if task.status != "done" or not task.branch:
            raise ValueError("only a delivered package with a branch can be merged")
        island = await Island.objects.aget(pk=task.island_id)
        if self.sandboxes is None:
            raise ValueError("mock mode doesn't use git, so there is nothing to merge")
        sandbox = self.sandboxes.for_island(island)
        async with self._git:
            branch, conflicts = await asyncio.to_thread(sandbox.merge_into_checkout, task.id, self.git_author)
            if not conflicts:
                return branch, [], None
            fix_id = "t_" + uuid.uuid4().hex[:6]
            await asyncio.to_thread(sandbox.prepare_conflict_branch, fix_id, task.id, branch, self.git_author)
        files = "\n".join(f"- {name}" for name in conflicts)
        description = (
            f"Merging the package '{task.title}' ({task.branch}) into {branch} ran into conflicts. "
            f"The merge is already in this branch, with git's conflict markers (<<<<<<<, =======, >>>>>>>) in:\n"
            f"{files}\n\nKeep what both sides meant, remove every marker, and make sure the tests still pass."
        )
        fix = await self.submit(island.id, f"Fix merge conflicts: {task.title}"[:500], description, task_id=fix_id)
        return branch, conflicts, fix

    def approve(self, task_id: str) -> None:
        """You (or the `merge_pr` house rule) approved this task's PR: the Post Office merges it."""
        self._approved.add(task_id)

    def approved(self, task_id: str) -> bool:
        return task_id in self._approved

    def busy_islands(self) -> set[str]:
        """Islands with a package being worked on right now."""
        return {island for island, lane in self.lanes.items() if lane.running}

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
                lane.wake.set()
                if lane.worker is None or lane.worker.done():
                    lane.worker = asyncio.create_task(self._work_lane(lane))
        finally:  # the server is shutting down: stop every island's worker and package too
            runs = [run for lane in self.lanes.values() for run in [lane.worker, *lane.running.values()] if run]
            for run in runs:
                run.cancel()
            await asyncio.gather(*runs, return_exceptions=True)

    async def _work_lane(self, lane: Lane) -> None:
        """One island's worker: start its packages in order, up to `parallel` at once, until none are left."""
        while lane.running or not lane.queue.empty():
            while len(lane.running) < max(1, self.parallel) and not lane.queue.empty():
                task_id = lane.queue.get_nowait()
                if await Task.objects.filter(pk=task_id, status="queued").aexists():  # else cancelled in line
                    lane.running[task_id] = asyncio.create_task(self._run_in_lane(lane, task_id))
            # Wait until a package finishes or a new one arrives (cleared first: only news from now on counts).
            lane.wake.clear()
            woken = asyncio.create_task(lane.wake.wait())
            try:
                await asyncio.wait([woken, *lane.running.values()], return_when=asyncio.FIRST_COMPLETED)
            finally:
                woken.cancel()

    async def _run_in_lane(self, lane: Lane, task_id: str) -> None:
        """Run one package of the lane; a crash fails the task instead of the worker."""
        try:
            await self.run_task(task_id)
        except asyncio.CancelledError:
            if task_id not in self._cancelling:
                raise  # the server is shutting down
        except Exception:
            log.exception("task %s crashed", task_id)
            task = await self._update(Task, task_id, status="failed", finished_at=utcnow())
            await self._emit(task, events.task(task))
        finally:
            lane.running.pop(task_id, None)
            lane.wake.set()
            self._cancelling.discard(task_id)
            self._approved.discard(task_id)

    async def recover(self) -> None:
        """After a restart: re-queue every unfinished task, oldest first, with the half-done ones
        at the front of their island's line (run_task resumes them where they stopped). Wake everyone."""
        half_done = [t async for t in Task.objects.filter(status="in_progress").order_by("created_at")]
        waiting = [t async for t in Task.objects.filter(status="queued").order_by("created_at")]
        for t in half_done:
            t.status = "queued"
            await t.asave(update_fields=["status"])
        for t in half_done + waiting:
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
        runner = self.runner  # one runner for the whole task, even if settings swap self.runner meanwhile
        shutting_down = False
        try:
            return await self._run_task(task, sandbox, runner)
        except asyncio.CancelledError:
            if task_id not in self._cancelling:
                shutting_down = True
                raise  # the server is shutting down, not a cancel request: recover() resumes the task
            return await self._drop_package(task_id)
        finally:
            # Agents kept alive between visits go now. Shielded: a second cancel can't leave one running.
            with suppress(Exception):
                await asyncio.shield(end_task(runner, task.id))
            # On shutdown the worktree stays, so the resumed task finds its uncommitted work.
            if sandbox and not self.keep_worktrees and not shutting_down:
                try:
                    async with self._git:
                        await asyncio.to_thread(sandbox.remove_worktree, task.id)
                except SandboxError as e:
                    log.warning("could not remove worktree for %s: %s", task.id, e)

    async def _drop_package(self, task_id: str) -> Task:
        """cancel() stopped this task mid-step: the resident puts the package down."""
        task = await self._update(Task, task_id, status="cancelled", finished_at=utcnow())
        if task.agent_id:
            resident = await self._get(Resident, task.agent_id)
            await self._set_mood(resident, "confused")
            await self._rest(resident)
        await self._emit(task, events.task(task))
        return task

    async def _sandbox_for(self, task: Task) -> Sandbox | None:
        """The island's repository sandbox (real mode only). A missing repo fails the task loudly."""
        if self.sandboxes is None:
            return None
        island = await Island.objects.aget(pk=task.island_id)
        return self.sandboxes.for_island(island)  # raises SandboxError -> run_forever marks the task failed

    async def _run_task(self, task: Task, sandbox: Sandbox | None, runner: AgentRunner) -> Task:
        island = await Island.objects.aget(pk=task.island_id)
        graph = await IslandGraph.load(task.island_id)
        # The task branch starts from the island's branch (`main` unless the creator chose another).
        workdir = None
        if sandbox:
            async with self._git:
                workdir = await asyncio.to_thread(sandbox.create_worktree, task.id, island.branch)
        if workdir:
            task = await self._update(Task, task.id, branch=f"mii/{task.id}")
            files = [(a.name, bytes(a.data)) async for a in Attachment.objects.filter(task_id=task.id).order_by("id")]
            if files:
                await asyncio.to_thread(sandbox.add_attachments, task.id, files)

        history: list[Note] = []  # one note per step, passed along with the package (trimmed for prompts)
        visits: dict[str, int] = {}
        last_failure = ""  # the latest failed step's report (its test output, else its summary)
        seen_failure: dict[str, str] = {}  # building id -> the last failure it had to fix (_normal), for "stuck"
        gave_up: str | None = None  # why the task stopped early (MAX_STEPS / MAX_VISITS)
        coder: str | None = None  # the Workshop the package last came through (the Lab's fast path back)
        feedback: str | None = None  # the Lab's test output, on top of the coder's next instructions
        helps = 0  # help visits so far (MAX_HELPS)
        takeover: tuple[Resident, str] | None = None  # (helper, request): a neighbour does the next visit
        previous: Resident | None = None
        outcome = "pass"
        building_id: str | None = graph.start()

        # Resumed after a restart: rebuild the trip so far from its Step rows, and go back to
        # the building it was at (the visit cut short is redone; its unfinished row goes).
        steps = [s async for s in Step.objects.filter(task_id=task.id).order_by("id")]
        if steps:
            names = {r.id: r async for r in Resident.objects.filter(island_id=task.island_id)}
            cut_short = steps[-1] if steps[-1].finished_at is None else None
            for s in steps:
                if s is cut_short:
                    continue
                building = graph.buildings.get(s.building_id)
                where = building.name if building else s.building_type
                who = names[s.resident_id].name if s.resident_id in names else s.resident_id
                did = f"helped {names[s.helped].name if s.helped in names else s.helped} at" if s.helped else "at"
                line = f"{who} {did} the {where}: {s.outcome.upper()}"
                history.append(Note(line, f"{line}. {s.summary}", s.outcome, s.building_type))
                visits[s.building_id] = visits.get(s.building_id, 0) + 1
                helps += bool(s.helped)
                outcome, previous = s.outcome, names.get(s.resident_id)
                if s.outcome == "fail":
                    last_failure = s.summary
                if s.building_type == "workshop":
                    coder = s.building_id
            if cut_short:
                building_id = cut_short.building_id
                await cut_short.adelete()
            else:
                building_id = graph.next_building(steps[-1].building_id, outcome)
            if building_id is not None and building_id not in graph.buildings:
                building_id = graph.start()  # the island was rebuilt meanwhile: start over at the mailbox
            log.info("task %s resumes after %d steps, at %s", task.id, len(history), building_id)

        while building_id is not None:
            building = graph.buildings[building_id]
            if len(history) >= MAX_STEPS:
                gave_up = f"Gave up after {MAX_STEPS} steps"
            elif visits.get(building_id, 0) >= MAX_VISITS:
                gave_up = f"Gave up at the {building.name}"
            if gave_up:
                gave_up += f": {_first_words(last_failure, CHAT_CHARS)}" if last_failure else "."
                break
            owner = await self._get(Resident, building.resident_id)
            visits[building_id] = visits.get(building_id, 0) + 1
            attempt = visits[building_id]

            # "Stuck": sent back with the same failure this building already had to fix last time.
            incoming = _normal(last_failure) if outcome == "fail" else ""
            stuck = bool(incoming) and attempt >= 2 and seen_failure.get(building_id) == incoming
            if incoming:
                seen_failure[building_id] = incoming

            # 0. Back here again after a failed check? A stuck package gets a neighbour right away;
            #    otherwise the owner gets one retry of their own and then the judge may send a free
            #    neighbour to take this turn (never at a check building: the Lab's tests and the
            #    Courthouse's review are the owner's call).
            if (
                takeover is None
                and attempt >= 2
                and building.type not in CHECK_STEPS
                and helps < MAX_HELPS
                and (stuck or await self.judge.needs_help(building, attempt, history[-1].text if history else ""))
            ):
                helper = await self._find_helper(task, building, owner, None)
                if helper is not None:
                    again = "again with the same problem" if stuck else "again"
                    takeover = (
                        helper,
                        f"The package came back to the {building.name} {again}. "
                        f"{_first_words(history[-1].text, CHAT_CHARS)} Can you take this one?",
                    )
            wanted, request, helped = owner, None, None
            if takeover is not None:
                wanted, request = takeover
                takeover, helps, helped = None, helps + 1, owner.id

            # Book whoever does this step. Busy on another package? A free neighbour stands in
            # (or the package waits for the first one to come free).
            resident = await self._claim(task, building, wanted)
            if resident.id != wanted.id:
                request = f"I'm busy with another package. Can you take the {building.name} for '{task.title}'?"
                helped = owner.id
            if helped and resident.id != owner.id:
                await self._emit(task, events.chat(owner.id, resident.id, task.id, request))
            elif resident.id == owner.id:
                helped = request = None  # nobody stood in after all

            try:
                # 1. Hand the package over, if someone else takes it.
                if previous and previous.id != resident.id:
                    await self._rest(previous)
                    result_word = "success" if outcome == "pass" else "fail"
                    await self._emit(task, events.handoff(previous.id, resident.id, task.id, result_word))
                    await self._befriend(task.island_id, previous.id, resident.id)

                # 2. Walk over, then 3. work.
                resident = await self._walk(resident, building_id)
                resident = await self._set_state(resident, "working")
                resident = await self._set_mood(resident, "focused")
                task = await self._update(Task, task.id, building_id=building_id, agent_id=resident.id)
                await self._emit(task, events.task(task))

                result, step = await self._run_step(
                    runner,
                    task,
                    island,
                    building,
                    resident,
                    attempt,
                    history,
                    workdir,
                    sandbox,
                    request=request,
                    helped=helped,
                    feedback=feedback,
                )
            finally:
                self._release(resident)
            feedback = None
            if self.holds.held(resident.id):  # you're talking to them: they finish the chat before moving on
                await self._report(task, resident, "Chatting with you; the package waits until you're done")
                await self.holds.wait(resident.id)
            if building.type == "workshop":
                coder = building_id
            did = f"helped {owner.name} at" if helped else "at"
            line = f"{resident.name} {did} the {building.name}: {result.outcome.upper()}"
            history.append(Note(line, f"{line}. {result.summary}", result.outcome, building.type))
            task = await self._get(Task, task.id)  # a handler may have set pr_url
            await self._update_weather(task.island_id)
            if helped:  # the helper reports back (the handoff to them already grew the friendship)
                await self._emit(
                    task, events.chat(resident.id, owner.id, task.id, _first_words(result.summary, CHAT_CHARS))
                )

            # 4. React.
            resident = await self._set_mood(
                resident,
                await self.judge.mood(resident, building, result, attempt, step.timed_out, step.bubbles_raised),
            )
            previous, outcome = resident, result.outcome
            if outcome == "fail":
                last_failure = result.test_output or result.summary
            if result.test_output and outcome == "pass":  # the Workshop's own pre-check failed on the way
                seen_failure[building_id] = _normal(result.test_output)

            # 5. "HELP: ..." in a failed reply: a free neighbour redoes this building (MAX_HELPS per task).
            if result.outcome == "fail" and result.help_request and not helped and helps < MAX_HELPS:
                helper = await self._find_helper(task, building, resident, result.help_request)
                if helper is not None:
                    takeover = (helper, result.help_request)
                    continue  # same building, next visit

            # 6. Follow the road that matches the outcome. Fast path: red tests at the Lab go straight
            #    back to the Workshop that made the change (skipping anything in between), with the
            #    test output on top of the coder's instructions.
            if building.type == "lab" and outcome == "fail" and result.test_output and coder:
                building_id = coder
                feedback = f"The Lab's tests fail. Fix this first:\n{result.test_output}"
                continue
            building_id = graph.next_building(building_id, outcome)

        if gave_up:
            log.info("task %s: %s", task.id, gave_up)
            if previous:  # said by whoever carried the package last, so the game shows why it stopped
                await self._report(task, previous, gave_up)
        task = await self._update(
            Task, task.id, status="failed" if gave_up or outcome == "fail" else "done", finished_at=utcnow()
        )
        await metrics.record_task(task)
        if previous:
            await self._set_mood(previous, "proud" if task.status == "done" else "frustrated")
            await self._rest(previous)
        await self._emit(task, events.task(task))  # last event of the task (tasks.task_window relies on this)
        return task

    # --- One step ------------------------------------------------------------------

    async def _run_step(
        self,
        runner: AgentRunner,
        task: Task,
        island: Island,
        building: Building,
        resident: Resident,
        attempt: int,
        history: list[Note],
        workdir: Path | None,
        sandbox: Sandbox | None,
        *,
        request: str | None = None,
        helped: str | None = None,
        feedback: str | None = None,
    ) -> tuple[StepResult, Step]:
        """Run one building visit with a timeout, and record it as a Step row.

        `request` + `helped`: this is a help visit; `resident` is the helper, `helped` the
        building's owner, and `request` what was asked (it goes on top of the instructions).
        `feedback`: a failed check's report (the Lab's test output), also put on top.
        """
        step = await Step.objects.acreate(
            island_id=task.island_id,
            task_id=task.id,
            building_id=building.id,
            building_type=building.type,
            resident_id=resident.id,
            brain=resident.brain,
            helped=helped,
        )
        bubbles_raised = 0

        async def ask(kind: str, text: str, options: list[str] | None = None, detail: str | None = None) -> str | None:
            nonlocal bubbles_raised
            # The master key (the setting, or this resident's own): the bubble is shown and
            # closed at once with its answer, so News still tells the story.
            if self.master_key or (resident.keys or {}).get("master") == "yes":
                auto = master_key_answer(options)
                return await self.bubbles.ask(
                    task.island_id, resident.id, kind, text, 0, options, task.id, detail, auto
                )
            bubbles_raised += 1
            # Permission and review bubbles are you approving something: they can wait longer.
            timeout = self.permission_timeout if kind in ("permission", "ready") else self.bubble_timeout
            await self._set_mood(resident, BUBBLE_MOODS.get(kind, "confused"))
            answer = await self.bubbles.ask(task.island_id, resident.id, kind, text, timeout, options, task.id, detail)
            await self._set_mood(resident, "focused")
            return answer

        # Who else lives here (for SAY TO), and what they left for this resident on this task.
        everyone = [r async for r in Resident.objects.filter(island_id=task.island_id).order_by("id")]
        names = {r.id: r.name for r in everyone}
        neighbours = [f"{r.name} ({r.job})" for r in everyone if r.id != resident.id]
        messages = await self._messages_for(task, resident, names, skip=request)

        handler = HANDLERS[building.type]
        instructions = handler.instructions(task)
        if feedback:
            instructions = f"{feedback}\n\n{instructions}"
        if direction := await self._owner_direction(task, names):
            instructions = f"{direction}\n\n{instructions}"
        if request:
            instructions = f"{names.get(helped, 'A neighbour')} asked for your help: {request}\n\n{instructions}"
        job = AgentJob(
            resident=resident,
            task=task,
            island=island,
            building=building,
            instructions=instructions,
            ask=ask,
            report=partial(self._report, task, resident),
            attempt=attempt,
            history=notes_for_prompt(history),
            workdir=workdir,
            sandbox=sandbox,
            read_only=handler.read_only,
            rules=island.rules or {},
            master_key=self.master_key,
            notebook=partial(self._set_notebook, resident),
            messages=messages,
            notes=resident.notes,
            neighbours=neighbours,
            think=partial(self._think, task, resident),
            attachments=[a async for a in Attachment.objects.filter(task_id=task.id).values_list("name", flat=True)],
        )
        timed_out = False
        try:
            result = await asyncio.wait_for(handler.run(job, runner, self), timeout=self.step_timeout)
        except TimeoutError:
            timed_out = True
            result = StepResult("fail", f"{resident.name} ran out of time.")
            await self._set_mood(resident, "tired")
        if not result.explicit:  # the agent forgot its VERDICT line: the judge reads the reply
            result = await self.judge.verdict(job, result)
        await self._after_step(task, resident, result, everyone)

        step = await self._update(
            Step,
            step.id,
            outcome=result.outcome,
            summary=result.summary,
            timed_out=timed_out,
            bubbles_raised=bubbles_raised,
            flaky=result.flaky,
            finished_at=utcnow(),
        )
        await metrics.record_step(step)
        return result, step

    async def _messages_for(
        self, task: Task, resident: Resident, names: dict[str, str], skip: str | None = None
    ) -> list[str]:
        """What neighbours said to this resident on this task since their last step here (`chat`
        events), oldest first. `skip`: a line already in the instructions (a help request)."""
        chats = Event.objects.filter(
            island_id=task.island_id, type="chat", payload__task=task.id, payload__to=resident.id
        )
        last = (
            await Step.objects.filter(task_id=task.id, resident_id=resident.id, finished_at__isnull=False)
            .order_by("-id")
            .afirst()
        )
        if last is not None:
            chats = chats.filter(created_at__gt=last.finished_at)
        return [
            f"{names.get(e.payload.get('from'), e.payload.get('from'))}: {e.payload.get('text', '')}"
            async for e in chats.order_by("id")
            if e.payload.get("text") != skip
        ]

    async def _owner_direction(self, task: Task, names: dict[str, str]) -> str | None:
        """What the island owner said in talks about this package (talk.py stores each line with
        the package), newest DIRECTION_LINES, for the top of every later step's instructions."""
        lines = [
            e.payload
            async for e in Event.objects.filter(
                island_id=task.island_id, type=TALK_TURN, payload__task=task.id, payload__from="you"
            ).order_by("-id")[:DIRECTION_LINES]
        ]
        if not lines:
            return None
        said = [f"- To {names.get(p.get('agent'), p.get('agent'))}: {p.get('text', '')}" for p in reversed(lines)]
        return "\n".join(
            ["The island owner talked with the team about this package. Follow any direction they gave:", *said]
        )

    async def _after_step(self, task: Task, resident: Resident, result: StepResult, everyone: list[Resident]) -> None:
        """Deliver the reply's `SAY TO` notes as chat and write its `REMEMBER` lines in the diary."""
        by_name = {r.name.lower(): r for r in everyone if r.id != resident.id}
        for name, text in result.says:
            to = by_name.get(name.strip().lower())
            if to is None:
                log.info("%s said something to %r, who doesn't live here", resident.name, name)
                continue
            await self._emit(task, events.chat(resident.id, to.id, task.id, text))
        if result.remember:
            lines = [line for line in resident.notes.splitlines() if line.strip()] + result.remember
            notes = "\n".join(lines)
            while len(notes) > NOTES_CHARS and len(lines) > 1:
                lines.pop(0)  # the oldest memory goes first
                notes = "\n".join(lines)
            resident.notes = notes[-NOTES_CHARS:]
            await Resident.objects.filter(pk=resident.id).aupdate(notes=resident.notes)

    # --- Help visits ---------------------------------------------------------------

    async def _find_helper(self, task: Task, building: Building, asker: Resident, text: str | None) -> Resident | None:
        """A free neighbour (idle or asleep) the judge thinks should come over, or None.

        At a check building (Lab, Courthouse) only someone with the same job may stand in,
        so a friendly coder never waves their own work through review.
        """
        others = [
            r
            async for r in Resident.objects.filter(island_id=task.island_id, state__in=["idle", "sleeping"])
            .exclude(pk=asker.id)
            .exclude(pk__in=list(self._claimed))
            .order_by("id")
        ]
        if building.type in CHECK_STEPS:
            others = [r for r in others if r.job == asker.job]
        if not others:
            return None
        scores: dict[str, float] = {}
        async for f in Friendship.objects.filter(island_id=task.island_id).filter(Q(a=asker.id) | Q(b=asker.id)):
            scores[f.b if f.a == asker.id else f.a] = f.score
        request = HelpRequest(
            asker, building, task, text or f"Can someone take the {building.name} for '{task.title}'?", scores
        )
        return await self.judge.pick_helper(request, others)

    # --- Booking residents (several packages on one island) -------------------------

    async def _claim(self, task: Task, building: Building, wanted: Resident) -> Resident:
        """Book a resident for this task's next step: `wanted` when free, else a free neighbour
        who may stand in, else wait until someone comes free."""
        while True:
            freed = self._freed  # taken before looking, so a release while we look still wakes us
            if self._claimed.get(wanted.id, task.id) == task.id:
                pick = wanted
            else:
                pick = await self._stand_in(task, building, wanted)
            if pick is not None and self._claimed.get(pick.id, task.id) == task.id:
                self._claimed[pick.id] = task.id
                return pick
            await freed.wait()

    async def _stand_in(self, task: Task, building: Building, busy: Resident) -> Resident | None:
        """A free neighbour for a building whose resident is busy: the same job first, and
        among them (then among anyone) those without a building of their own, so owners stay
        free for their own buildings. At the Lab and the Courthouse only the same job checks the work."""
        owners = {b.resident_id async for b in Building.objects.filter(island_id=task.island_id)}
        free = [
            r
            async for r in Resident.objects.filter(island_id=task.island_id)
            .exclude(pk__in=list(self._claimed))
            .order_by("id")
        ]
        free.sort(key=lambda r: (r.job != busy.job, r.id in owners))  # stable: keeps the id order within a group
        if building.type in CHECK_STEPS:
            free = [r for r in free if r.job == busy.job]
        return free[0] if free else None

    def _release(self, resident: Resident) -> None:
        """The step is over: the resident may work on another package."""
        self._claimed.pop(resident.id, None)
        self._freed.set()
        self._freed = asyncio.Event()

    async def _rest(self, resident: Resident) -> None:
        """Go idle, unless another package has booked them in the meantime."""
        if resident.id not in self._claimed:
            await self._set_state(resident, "idle")

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

    async def _think(self, task: Task, resident: Resident, kind: str, text: str, **tool_info: str) -> None:
        await self._emit(task, events.thinking(resident.id, task.id, kind, text, **tool_info))

    async def _report(self, task: Task, resident: Resident, text: str) -> None:
        if self._activity.get(resident.id) != text:  # kept on the row so late joiners see it; one write per change
            self._activity[resident.id] = text
            await Resident.objects.filter(pk=resident.id).aupdate(activity=text[:500])
        await self._emit(task, events.activity(resident.id, text))

    async def _update_weather(self, island_id: str) -> None:
        """Announce the island's weather when it changes (after a step, or when everyone sleeps / wakes)."""
        kind = await weather.current(island_id)
        if self._weather.get(island_id) != kind:
            self._weather[island_id] = kind
            await self.bus.emit(island_id, events.weather(kind))

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
        if state in ("sleeping", "working"):  # night falls when everyone sleeps; day breaks when someone works
            await self._update_weather(resident.island_id)
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

    async def _get(self, model: type, row_id: str):
        try:
            return await model.objects.aget(pk=row_id)
        except model.DoesNotExist:
            raise KeyError(f"{model.__name__} {row_id} not found")

    async def _update(self, model: type, row_id: str, **changes):
        """Change some fields of one row and return the updated row."""
        row = await self._get(model, row_id)
        for name, value in changes.items():
            setattr(row, name, value)
        await row.asave(update_fields=list(changes))
        return row
