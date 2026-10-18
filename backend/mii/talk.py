"""Talk to a resident inside a building (`talk_open`, `talk`, `talk_close`; docs/protocol.md).

The island owner chats with a resident's brain: the resident answers in character, in
the role of the building they're in. Only a resident working inside that building can
be talked to (idle ones are out on the island), so they're carrying a package and the
talk is about it. Every line the owner says is stored with the package, so the engine puts
those lines on top of its later steps (engine.py `_owner_direction`).

Who answers, like Claude Code's /btw: the agent doing the work itself, through a fork of
its live session (agents.side_chat; Claude Code and Codex can fork). The fork knows
everything done so far, the work carries on untouched and never sees the chat. Otherwise
(mock, OpenRouter, Gemini, nothing running) a fresh agent answers, told the package
(package_context) and reading its working copy.
Always read-only, and it never raises bubbles.

A conversation (`talk_open` ... `talk_close`):
- holds the resident in the building (engine.TalkHolds): they finish their step but
  don't walk out, and the package waits, until the talk ends;
- in real mode, works in the package's worktree (read-only), or a throwaway worktree of the
  island's repo when there is none (agents only run inside worktrees, sandbox.py), and
  starts the resident's agent right away, so the first reply
  doesn't wait for the start-up. Every message reuses that agent session, and its reply
  streams back as it's written (`talk_partial`);
- ends when the game closes it, the game disconnects, or nothing was said for IDLE_LIMIT.

Every turn is stored per building (Event rows of type `talk_turn`, never broadcast, kept
out of News) for the talk scene's history: GET /islands/{id}/talks?building=<id>.
A `talk` without a conversation is answered one-off (its own worktree and agent).
Mock mode answers with a canned line (agents/mock.py).
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from mii.agents import AgentJob, can_side_chat, end_task, prepare, side_chat
from mii.engine import HANDLERS, TALK_TURN, Note, notes_for_prompt
from mii.models import Building, Event, Island, Resident, Step, Task
from mii.sandbox import Sandbox, SandboxError

log = logging.getLogger(__name__)

HISTORY_TURNS = 10  # how much of the conversation the resident sees
MAX_MESSAGE = 2000  # characters per message
IDLE_LIMIT = 300.0  # seconds without a message before an open conversation ends by itself
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
TURN_TYPE = TALK_TURN  # the stored lines (Event.type); the engine reads the owner's as direction
DESCRIPTION_CHARS = 1500  # how much of the package's request the talk prompt quotes
THINKING_TAIL = 1200  # characters of the resident's latest thinking on the package the talk prompt quotes

SendFn = Callable[[dict], Awaitable[None]]

# Residents mid-reply (resident id -> talk id): one conversation turn at a time each.
_talking: dict[str, str] = {}


# --- Events (to the client that talks only) ---------------------------------------


def talk_ready(conversation: str, agent: str) -> dict:
    return {"type": "talk_ready", "id": conversation, "agent": agent}


def talk_thinking(talk_id: str, agent: str, text: str) -> dict:
    return {"type": "talk_thinking", "id": talk_id, "agent": agent, "text": text}


def talk_partial(talk_id: str, agent: str, text: str) -> dict:
    return {"type": "talk_partial", "id": talk_id, "agent": agent, "text": text}


def talk_reply(talk_id: str, agent: str, text: str) -> dict:
    return {"type": "talk_reply", "id": talk_id, "agent": agent, "text": text}


def talk_error(talk_id: str, agent: str, message: str) -> dict:
    return {"type": "talk_error", "id": talk_id, "agent": agent, "message": message}


def talk_closed(conversation: str, agent: str, message: str) -> dict:
    return {"type": "talk_closed", "id": conversation, "agent": agent, "message": message}


class TalkError(Exception):
    """A talk message that can't be answered; the text goes back as `talk_error`."""


# --- Conversations ---------------------------------------------------------------


@dataclass
class Conversation:
    id: str
    island_id: str
    resident_id: str
    building_id: str
    send: SendFn  # the client that opened it
    runner: object  # the runner it started with (settings may swap runtime's later)
    sandbox: Sandbox | None = None
    workdir: Path | None = None
    own_worktree: bool = False  # True: a throwaway worktree made for this talk, removed after it
    warming: asyncio.Task | None = None  # the agent starting up (real mode)
    watchdog: asyncio.Task | None = None  # ends it after IDLE_LIMIT of silence
    busy: bool = False  # a reply is being written
    closed: bool = False
    cleaned: bool = False
    last_active: float = field(default_factory=time.monotonic)

    @property
    def task_id(self) -> str:
        return f"talk_{self.id}"  # the runner keeps its agent under this "task"

    @property
    def worktree_id(self) -> str:
        return f"talk-{self.id}"


_conversations: dict[str, Conversation] = {}


async def open_conversation(runtime, island_id: str, message: dict, send: SendFn) -> None:
    """`talk_open`: hold the resident inside, start their agent, then answer `talk_ready` (or `talk_error`)."""
    conv_id = str(message.get("id") or "")
    agent = str(message.get("resident") or "")
    try:
        if not ID_PATTERN.match(conv_id):
            raise TalkError("talk_open needs an `id` (letters, digits, _ or -)")
        if conv_id in _conversations:
            raise TalkError("that conversation is already open")
        resident, island, building = await _inside(island_id, agent, message.get("building"))
        if runtime.workflow.holds.held(resident.id):
            raise TalkError(f"{resident.name} is already talking with someone.")
        conv = Conversation(conv_id, island_id, resident.id, building.id, send, runtime.workflow.runner)
        _conversations[conv_id] = conv
        runtime.workflow.holds.hold(resident.id)
        conv.watchdog = asyncio.create_task(_expire(runtime, conv))
        package = await _package(island_id, resident)
        # The agent at work answers itself (a fork of its session) when it can: nothing to start.
        if not (package and can_side_chat(conv.runner, package.id, resident.id)) and await _open_workdir(
            runtime, conv, island, resident, package
        ):
            job = _job(resident, island, building, conv.task_id, "", "", send, [], conv.workdir, conv.sandbox)
            conv.warming = asyncio.create_task(prepare(conv.runner, job))
        await send(talk_ready(conv_id, resident.id))
    except TalkError as e:
        await send(talk_error(conv_id, agent, str(e)))
    except Exception:
        log.exception("talk_open %s failed", conv_id)
        await close_conversation(runtime, conv_id)
        await send(talk_error(conv_id, agent, "Something went wrong; try again."))


async def _open_workdir(runtime, conv: Conversation, island: Island, resident: Resident, package: Task | None) -> bool:
    """Give the conversation a worktree for a fresh agent of its own (real mode only): the
    package's, else a throwaway one. True when it has one. Raises TalkError (and closes it)."""
    if conv.sandbox is not None:
        return True
    if runtime.sandboxes is None or resident.brain == "mock":
        return False
    try:
        conv.sandbox = runtime.sandboxes.for_island(island)
        conv.workdir, conv.own_worktree = await asyncio.to_thread(
            _working_copy, conv.sandbox, package, conv.worktree_id, island.branch
        )
    except SandboxError as e:
        conv.sandbox = None
        await close_conversation(runtime, conv.id)
        raise TalkError(f"Can't open the island's repository: {e}") from None
    return True


async def close_conversation(runtime, conv_id: str, message: str | None = None) -> None:
    """`talk_close` (or a disconnect, or silence): let the resident go, end the agent, drop the
    worktree. `message`: also tell the client why (`talk_closed`)."""
    conv = _conversations.pop(conv_id, None)
    if conv is None:
        return
    conv.closed = True
    runtime.workflow.holds.release(conv.resident_id)
    if conv.watchdog is not None and conv.watchdog is not asyncio.current_task():
        conv.watchdog.cancel()
    if message:
        with suppress(Exception):
            await conv.send(talk_closed(conv.id, conv.resident_id, message))
    if not conv.busy:  # a reply in progress cleans up when it finishes
        await _cleanup(conv)


async def close_all(runtime, send: SendFn) -> None:
    """The client went away: end every conversation it had open."""
    for conv in [c for c in _conversations.values() if c.send == send]:
        await close_conversation(runtime, conv.id)


async def _cleanup(conv: Conversation) -> None:
    if conv.cleaned:
        return
    conv.cleaned = True
    if conv.warming is not None and not conv.warming.done():
        conv.warming.cancel()
        with suppress(BaseException):
            await conv.warming
    with suppress(Exception):
        await end_task(conv.runner, conv.task_id)
    if conv.sandbox is not None and conv.own_worktree:  # the package's own worktree stays: it's still working
        try:
            await asyncio.to_thread(conv.sandbox.remove_worktree, conv.worktree_id, True)
        except SandboxError as e:
            log.warning("could not remove talk worktree %s: %s", conv.worktree_id, e)


async def _expire(runtime, conv: Conversation) -> None:
    """End the conversation after IDLE_LIMIT seconds without a message (never mid-reply)."""
    while not conv.closed:
        left = conv.last_active + IDLE_LIMIT - time.monotonic()
        if left <= 0 and not conv.busy:
            await close_conversation(runtime, conv.id, "We talked a while ago, so I went back to work.")
            return
        await asyncio.sleep(max(left, 1.0))


# --- One message -----------------------------------------------------------------


def chat_instructions(
    building: Building, role: str, history: list[dict], message: str, package: list[str] | None = None
) -> str:
    """The building's role, the package they carry (package_context), the conversation so far
    and the new message, for the resident."""
    lines = [f"You're at the {building.name}. {role}"]
    if package:
        lines += [
            *package,
            "",
            "The island owner (the human you work for) dropped by to talk about this package. Anything "
            "they want done differently is passed on to whoever works on it next (you included), so say "
            "plainly what you'll do. Don't create or change any files during this chat.",
        ]
    else:
        lines.append(
            "The island owner (the human you work for) dropped by for a chat. This is not a task: "
            "don't create or change any files."
        )
    lines.append(
        "Answer quickly, in character, in a few friendly sentences (plain text, no headings). "
        "Answer from what you already know; only read files in the repository when the question needs it."
    )
    return "\n".join(lines + _conversation(history, message))


def btw_instructions(building: Building, history: list[dict], message: str) -> str:
    """A side question for the agent doing the work, like Claude Code's /btw: a fork of its
    session (agents.side_chat) already knows its role, the package and everything it did."""
    lines = [
        f"btw: the island owner (the human you work for) dropped by the {building.name} with a side "
        "question while you work. Answering it is not part of your task: reply in character, in a few "
        "friendly sentences (plain text, no headings), from what you already know. Don't change any "
        "files, and only use a tool when the question really needs it. Anything they want done "
        "differently is passed on to the package's next step (you included), so say plainly what you'll do."
    ]
    return "\n".join(lines + _conversation(history, message))


def _conversation(history: list[dict], message: str) -> list[str]:
    """The talk so far and the new message, as prompt lines."""
    lines = []
    if history:
        lines += ["", "The conversation so far:"]
        for turn in history:
            who = "You" if turn["from"] == "resident" else "Owner"
            lines.append(f"{who}: {turn['text']}")
    return [*lines, "", f"Owner: {message}"]


async def _package(island_id: str, resident: Resident) -> Task | None:
    """The package the resident is carrying (inside a building means working on one), or None."""
    return (
        await Task.objects.filter(island_id=island_id, agent_id=resident.id, status="in_progress")
        .order_by("-created_at")
        .afirst()
    )


async def package_context(task: Task, resident: Resident, building: Building) -> list[str]:
    """What the resident knows about their package while working on it: the request, the steps
    so far (trimmed like the engine's prompts: notes_for_prompt), and their latest thinking."""
    names = {r.id: r.name async for r in Resident.objects.filter(island_id=task.island_id)}
    places = {b.id: b.name async for b in Building.objects.filter(island_id=task.island_id)}
    steps = [s async for s in Step.objects.filter(task_id=task.id).order_by("id")]
    notes = []
    for s in steps:
        if s.finished_at is None:
            continue  # the visit in progress: their thinking below says how it's going
        line = f"{names.get(s.resident_id, s.resident_id)} at the {places.get(s.building_id, s.building_type)}"
        line += f": {s.outcome.upper()}"
        notes.append(Note(line, f"{line}. {s.summary}", s.outcome, s.building_type))
    visit = sum(1 for s in steps if s.building_id == building.id) or 1
    lines = [f"You're working on the package '{task.title}' (visit {visit} to the {building.name})."]
    if task.description:
        lines.append(f"The request: {task.description[:DESCRIPTION_CHARS]}")
    if notes:
        lines += ["What happened to it so far:"] + [f"- {note}" for note in notes_for_prompt(notes)]
    if thinking := await _thinking_tail(task, resident):
        lines.append(f"What you were just thinking while working on it: …{thinking}")
    return lines


async def _thinking_tail(task: Task, resident: Resident) -> str:
    """The end of the resident's train of thought on the package (stored `thinking` events)."""
    rows = Event.objects.filter(
        island_id=task.island_id, type="thinking", payload__task=task.id, payload__agent=resident.id
    ).order_by("-id")[:60]
    chunks = [e.payload async for e in rows]
    text = "".join(str(p.get("text", "")) for p in reversed(chunks) if p.get("kind") in ("thought", "reply"))
    return " ".join(text.split())[-THINKING_TAIL:]


def _working_copy(sandbox: Sandbox, package: Task | None, worktree_id: str, branch: str | None) -> tuple[Path, bool]:
    """Where a talk's agent reads: the package's own worktree (its work in progress; the talk is
    read-only), else a new throwaway worktree of the island's branch. Returns (path, made for the
    talk: remove it after). Sync (git)."""
    if package is not None:
        path = sandbox.worktree_path(package.id)
        if path.exists():
            return path, False
    return sandbox.create_worktree(worktree_id, branch), True


def clean_history(raw: object) -> list[dict]:
    """The last HISTORY_TURNS well-formed turns of the client's `history`."""
    turns = []
    for turn in raw if isinstance(raw, list) else []:
        if not isinstance(turn, dict) or turn.get("from") not in ("you", "resident"):
            continue
        text = str(turn.get("text") or "").strip()[:MAX_MESSAGE]
        if text:
            turns.append({"from": turn["from"], "text": text})
    return turns[-HISTORY_TURNS:]


async def talk(runtime, island_id: str, message: dict, send: SendFn) -> None:
    """Answer one `talk` message with `talk_thinking` / `talk_partial`*, then `talk_reply` or
    `talk_error`, via `send`. Run it as its own asyncio task: a real brain can take a while."""
    talk_id = str(message.get("id") or "")
    agent = str(message.get("resident") or "")
    try:
        if not ID_PATTERN.match(talk_id):
            raise TalkError("talk needs an `id` (letters, digits, _ or -)")
        text = str(message.get("message") or "").strip()[:MAX_MESSAGE]
        if not text:
            raise TalkError("talk needs a `message`")
        if agent in _talking:
            raise TalkError("they're still thinking about your last message")
        _talking[agent] = talk_id
        try:
            reply = await _answer(runtime, island_id, message, talk_id, agent, text, send)
        finally:
            _talking.pop(agent, None)
        await send(talk_reply(talk_id, agent, reply))
    except TalkError as e:
        await send(talk_error(talk_id, agent, str(e)))
    except Exception:
        log.exception("talk %s failed", talk_id)
        await send(talk_error(talk_id, agent, "Something went wrong; try again."))


async def _answer(runtime, island_id: str, message: dict, talk_id: str, agent: str, text: str, send: SendFn) -> str:
    resident, island, building = await _inside(island_id, agent, message.get("building"))
    conv = _conversations.get(str(message.get("conversation") or ""))
    if conv is not None and (conv.resident_id != resident.id or conv.island_id != island_id):
        conv = None
    history = clean_history(message.get("history"))
    package = await _package(island_id, resident)
    if package is not None:
        reply = await _btw(runtime, conv, resident, island, building, package, talk_id, history, text, send)
        if reply is not None:
            await _store(island_id, building.id, resident.id, package, text, reply)
            return reply
        if conv is not None and conv.closed:
            raise TalkError("We stopped talking.")
    context = await package_context(package, resident, building) if package else None
    if conv is None:
        return await _one_off(runtime, resident, island, building, package, context, talk_id, history, text, send)

    await _open_workdir(runtime, conv, island, resident, package)  # the side chat couldn't: a fresh agent needs one
    conv.busy = True
    conv.last_active = time.monotonic()
    try:
        if conv.warming is not None:
            with suppress(Exception):
                await conv.warming  # the agent is still starting: the first reply waits for it
        job = _job(
            resident, island, building, conv.task_id, talk_id, text, send, history, conv.workdir, conv.sandbox, context
        )
        reply = await _run(runtime, conv.runner, job, resident)
    finally:
        conv.busy = False
        conv.last_active = time.monotonic()
        if conv.closed:  # closed while the reply was being written
            await _cleanup(conv)
    await _store(island_id, building.id, resident.id, package, text, reply)
    return reply


async def _btw(runtime, conv, resident, island, building, package, talk_id, history, text, send) -> str | None:
    """Ask the agent working on the package itself, like Claude Code's /btw (agents.side_chat:
    a fork of its session, which knows everything done so far). None when it can't (mock,
    OpenRouter or Gemini brains, no agent running, a failed fork): ask a fresh agent instead."""
    job = _job(resident, island, building, f"talk_{talk_id}", talk_id, text, send, history, None, None)
    job.instructions = btw_instructions(building, history, text)
    if conv is not None:
        conv.busy = True
        conv.last_active = time.monotonic()
    try:
        result = await asyncio.wait_for(
            side_chat(runtime.workflow.runner, job, package.id), timeout=runtime.workflow.step_timeout
        )
    except TimeoutError:
        raise TalkError(f"{resident.name} took too long to answer.") from None
    finally:
        if conv is not None:
            conv.busy = False
            conv.last_active = time.monotonic()
            if conv.closed:
                await _cleanup(conv)
    if result is None:
        return None
    if result.outcome == "fail":
        raise TalkError(result.summary or f"{resident.name} couldn't answer.")
    return result.summary or "..."


async def _one_off(runtime, resident, island, building, package, context, talk_id, history, text, send) -> str:
    """A `talk` outside a conversation: its own agent (and throwaway worktree, unless it reads the
    package's), gone after the reply."""
    runner = runtime.workflow.runner
    sandbox, workdir, own = None, None, False
    worktree_id = f"talk-{uuid.uuid4().hex[:8]}"
    if runtime.sandboxes is not None and resident.brain != "mock":
        try:
            sandbox = runtime.sandboxes.for_island(island)
            workdir, own = await asyncio.to_thread(_working_copy, sandbox, package, worktree_id, island.branch)
        except SandboxError as e:
            raise TalkError(f"Can't open the island's repository: {e}") from None
    job = _job(resident, island, building, f"talk_{talk_id}", talk_id, text, send, history, workdir, sandbox, context)
    try:
        reply = await _run(runtime, runner, job, resident)
    finally:
        with suppress(Exception):
            await end_task(runner, job.task.id)
        if sandbox is not None and own:
            await asyncio.to_thread(sandbox.remove_worktree, worktree_id, True)
    await _store(island.id, building.id, resident.id, package, text, reply)
    return reply


async def _run(runtime, runner, job: AgentJob, resident: Resident) -> str:
    try:
        result = await asyncio.wait_for(runner.run(job), timeout=runtime.workflow.step_timeout)
    except TimeoutError:
        raise TalkError(f"{resident.name} took too long to answer.") from None
    if result.outcome == "fail":  # runner refusals (no login, no key, stopped early) come back as a fail
        raise TalkError(result.summary or f"{resident.name} couldn't answer.")
    return result.summary or "..."


def _job(resident, island, building, task_id, talk_id, text, send, history, workdir, sandbox, context=None) -> AgentJob:
    """`context`: package_context() of the package they carry, or None."""
    handler = HANDLERS.get(building.type)
    role = handler.role if handler else "You work here."

    async def think(kind: str, chunk: str, **_details) -> None:  # tool details (sort, call, diff) aren't shown
        if not talk_id:
            return  # warming up: nobody is listening yet
        if kind == "tool":  # one line per tool: "Reading src/auth.py"
            await send(talk_thinking(talk_id, resident.id, chunk.strip()[:200]))
        elif kind == "reply":  # the answer as it's written, for the speech bubble
            await send(talk_partial(talk_id, resident.id, chunk))

    async def ask(*args, **kwargs) -> None:
        return None  # a chat never raises bubbles: "nobody answered", the agent decides

    async def report(_text: str) -> None:
        return None

    return AgentJob(
        resident=resident,
        task=Task(id=task_id, island=island, title="A chat"),  # never saved
        island=island,
        building=building,
        instructions=chat_instructions(building, role, history or [], text, context),
        ask=ask,
        report=report,
        read_only=True,
        rules=island.rules or {},
        notes=resident.notes,
        think=think,
        chat=text,
        workdir=workdir,
        sandbox=sandbox,
    )


async def _inside(island_id: str, agent: str, building_id: object) -> tuple[Resident, Island, Building]:
    """The resident, island and building, if the resident is working inside that building."""
    try:
        resident = await Resident.objects.aget(pk=agent, island_id=island_id)
        island = await Island.objects.aget(pk=island_id)
    except (Resident.DoesNotExist, Island.DoesNotExist):
        raise TalkError(f"no resident {agent!r} on this island") from None
    building = await _building(island_id, resident, building_id)
    if resident.state != "working" or resident.building_id != building.id:
        # Residents are inside a building only while they work there; you talk to them there.
        raise TalkError(f"{resident.name} isn't inside the {building.name} right now.")
    return resident, island, building


async def _building(island_id: str, resident: Resident, building_id: object) -> Building:
    """The building named in the message, else the one the resident is in."""
    building_id = str(building_id or resident.building_id or "")
    if not building_id:
        raise TalkError(f"{resident.name} isn't inside any building right now.")
    try:
        return await Building.objects.aget(pk=building_id, island_id=island_id)
    except Building.DoesNotExist:
        raise TalkError(f"no building {building_id!r} on this island") from None


# --- History -----------------------------------------------------------------------


async def _store(
    island_id: str, building_id: str, resident_id: str, package: Task | None, said: str, reply: str
) -> None:
    """Keep both lines of a turn for the building's talk history (never broadcast), with the
    package it was about: the engine reads the owner's lines back as direction for it."""
    ts = datetime.now(UTC).isoformat()
    for who, text in (("you", said), ("resident", reply)):
        payload = {"type": TURN_TYPE, "building": building_id, "agent": resident_id, "from": who, "text": text}
        payload["task"] = package.id if package else None
        payload["ts"] = ts
        await Event.objects.acreate(island_id=island_id, type=TURN_TYPE, payload=payload)


async def history(island_id: str, building_id: str, limit: int = 200) -> list[dict]:
    """The newest `limit` stored lines said at a building, oldest first."""
    rows = Event.objects.filter(island_id=island_id, type=TURN_TYPE, payload__building=building_id).order_by("-id")
    newest = [e.payload async for e in rows[:limit]]
    return newest[::-1]
