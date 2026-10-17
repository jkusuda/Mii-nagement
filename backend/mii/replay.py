"""Replay: play a recorded task back into the game, with its original timing.

The demo safety net. Every event is already saved in the Event table, so if a
live agent fails on stage, POST /replay/{task_id}?speed=4 re-broadcasts a good run.
"""

import asyncio

from mii.events import EventBus
from mii.models import Event


class ReplayError(Exception):
    pass


async def replay_task(bus: EventBus, task_id: str, speed: float = 1.0) -> int:
    """Re-broadcast every island event from this task's first to last `task` event.

    Uses broadcast(), not emit(), so the replay isn't saved again. Returns how
    many events were sent.
    """
    markers = [e async for e in Event.objects.filter(type="task", payload__id=task_id).order_by("id")]
    if not markers:
        raise ReplayError(f"no recorded events for task {task_id!r}")
    first, last = markers[0], markers[-1]
    events = [
        e async for e in Event.objects.filter(island_id=first.island_id, id__gte=first.id, id__lte=last.id).order_by("id")
    ]
    previous = None
    for event in events:
        if previous is not None:
            gap = (event.created_at - previous.created_at).total_seconds()
            await asyncio.sleep(max(gap, 0) / max(speed, 0.01))
        await bus.broadcast(event.payload, first.island_id)
        previous = event
    return len(events)
