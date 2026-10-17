"""Talk to a resident at a building (the `talk` message, docs/protocol.md).

The island owner chats with a resident's brain outside any package: the resident
answers in character, in the role of the building you found them at. Always
read-only, and it never raises bubbles. The game keeps the conversation; each
`talk` carries the history, so nothing is stored here.

Real brains run in a throwaway worktree of the island's repo (agents only ever run
inside worktrees, sandbox.py), removed again with its branch. Mock mode answers
with a canned line (agents/mock.py).
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable

from mii.agents import AgentJob
from mii.engine import HANDLERS
from mii.models import Building, Island, Resident, Task
from mii.sandbox import SandboxError

log = logging.getLogger(__name__)

HISTORY_TURNS = 10  # how much of the conversation the resident sees
MAX_MESSAGE = 2000  # characters per message
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

SendFn = Callable[[dict], Awaitable[None]]

# Residents mid-talk (resident id -> talk id): one conversation turn at a time each.
_talking: dict[str, str] = {}


def talk_thinking(talk_id: str, agent: str, text: str) -> dict:
    return {"type": "talk_thinking", "id": talk_id, "agent": agent, "text": text}


def talk_reply(talk_id: str, agent: str, text: str) -> dict:
    return {"type": "talk_reply", "id": talk_id, "agent": agent, "text": text}


def talk_error(talk_id: str, agent: str, message: str) -> dict:
    return {"type": "talk_error", "id": talk_id, "agent": agent, "message": message}


class TalkError(Exception):
    """A talk message that can't be answered; the text goes back as `talk_error`."""


def chat_instructions(building: Building, role: str, history: list[dict], message: str) -> str:
    """The building's role, the conversation so far and the new message, for the resident."""
    lines = [
        f"You're at the {building.name}. {role}",
        "The island owner (the human you work for) dropped by for a chat. This is not a task: "
        "don't create or change any files. You may read the repository to answer.",
        "Answer in character, in a few friendly sentences (plain text, no headings).",
    ]
    if history:
        lines += ["", "The conversation so far:"]
        for turn in history:
            who = "You" if turn["from"] == "resident" else "Owner"
            lines.append(f"{who}: {turn['text']}")
    lines += ["", f"Owner: {message}"]
    return "\n".join(lines)


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
    """Answer one `talk` message with `talk_thinking`* then `talk_reply` or `talk_error`, via `send`.

    Run it as its own asyncio task: a real brain can take a minute.
    """
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
    try:
        resident = await Resident.objects.aget(pk=agent, island_id=island_id)
        island = await Island.objects.aget(pk=island_id)
    except (Resident.DoesNotExist, Island.DoesNotExist):
        raise TalkError(f"no resident {agent!r} on this island") from None
    building = await _building(island_id, resident, message.get("building"))
    handler = HANDLERS.get(building.type)
    role = handler.role if handler else "You work here."

    async def think(kind: str, chunk: str) -> None:
        if kind == "tool":  # one line per tool: "Reading src/auth.py"
            await send(talk_thinking(talk_id, agent, chunk.strip()[:200]))

    async def ask(*args, **kwargs) -> None:
        return None  # a chat never raises bubbles: "nobody answered", the agent decides

    async def report(_text: str) -> None:
        return None

    job = AgentJob(
        resident=resident,
        task=Task(id=f"talk_{talk_id}", island=island, title="A chat"),  # never saved
        island=island,
        building=building,
        instructions=chat_instructions(building, role, clean_history(message.get("history")), text),
        ask=ask,
        report=report,
        read_only=True,
        rules=island.rules or {},
        notes=resident.notes,
        think=think,
        chat=text,
    )
    runner = runtime.workflow.runner
    sandbox = None
    worktree_id = f"talk-{uuid.uuid4().hex[:8]}"
    if runtime.sandboxes is not None and resident.brain != "mock":
        try:
            sandbox = runtime.sandboxes.for_island(island)
            job.workdir = await asyncio.to_thread(sandbox.create_worktree, worktree_id, island.branch)
            job.sandbox = sandbox
        except SandboxError as e:
            raise TalkError(f"Can't open the island's repository: {e}") from None
    try:
        result = await asyncio.wait_for(runner.run(job), timeout=runtime.workflow.step_timeout)
    except TimeoutError:
        raise TalkError(f"{resident.name} took too long to answer.") from None
    finally:
        if sandbox is not None:
            await asyncio.to_thread(sandbox.remove_worktree, worktree_id, True)
    if result.outcome == "fail":  # runner refusals (no login, no key, stopped early) come back as a fail
        raise TalkError(result.summary or f"{resident.name} couldn't answer.")
    return result.summary or "..."


async def _building(island_id: str, resident: Resident, building_id: object) -> Building:
    """The building named in the message, else the resident's home building, else where they stand."""
    if building_id:
        try:
            return await Building.objects.aget(pk=str(building_id), island_id=island_id)
        except Building.DoesNotExist:
            raise TalkError(f"no building {building_id!r} on this island") from None
    home = await Building.objects.filter(island_id=island_id, resident_id=resident.id).order_by("id").afirst()
    if home is None and resident.building_id:
        home = await Building.objects.filter(pk=resident.building_id).afirst()
    if home is None:
        raise TalkError(f"{resident.name} doesn't work at any building yet")
    return home
