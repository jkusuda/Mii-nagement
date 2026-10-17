"""Replay: play a recorded task back into the game, with its original timing.

The demo safety net. Every event is already saved in the Event table, so if a
live agent fails on stage, POST /replay/{task_id}?speed=4 re-broadcasts a good run.
"""

import asyncio

from django.db.models import QuerySet

from mii.events import EventBus
from mii.models import Event


class ReplayError(Exception):
    """No recorded events for that task."""


async def task_window(task_id: str, running_too: bool = False, from_queued: bool = False) -> QuerySet | None:
    """The island's stored events from the task's `in_progress` event to its last `task` event.

    Events aren't tagged with a task, but an island runs one task at a time, so everything
    in that window belongs to it. `running_too` leaves the end open for a task that hasn't
    finished; `from_queued` starts at its first `task` event instead (replay shows the
    package arriving). None when there is nothing to show.
    """
    marks = Event.objects.filter(type="task", payload__id=task_id).order_by("id")
    first = await (marks if from_queued else marks.filter(payload__status="in_progress")).afirst()
    if first is None:
        return None
    rows = Event.objects.filter(island_id=first.island_id, id__gte=first.id)
    last = await marks.alast()
    if last.payload.get("status") in ("done", "failed", "cancelled") or not running_too:
        rows = rows.filter(id__lte=last.id)
    return rows


async def replay_task(bus: EventBus, task_id: str, speed: float = 1.0) -> int:
    """Re-broadcast every island event from this task's first to last `task` event.

    Uses broadcast(), not emit(), so the replay isn't saved again. Returns how
    many events were sent.
    """
    window = await task_window(task_id, from_queued=True)
    if window is None:
        raise ReplayError(f"no recorded events for task {task_id!r}")
    events = [e async for e in window.order_by("id")]
    island_id = events[0].island_id
    previous = None
    for event in events:
        if previous is not None:
            gap = (event.created_at - previous.created_at).total_seconds()
            await asyncio.sleep(max(gap, 0) / max(speed, 0.01))
        await bus.broadcast(event.payload, island_id)
        previous = event
    return len(events)
