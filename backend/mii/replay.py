"""Replay: play a recorded task back into the game, with its original timing.

The demo safety net. Every event is already saved in the Event table, so if a
live agent fails on stage, POST /replay/{task_id} re-broadcasts a good run.
"""

from mii.events import EventBus


async def replay_task(bus: EventBus, db_engine, task_id: str, speed: float = 1.0) -> int:
    """Re-broadcast every island event from this task's first to last `task` event.

    Returns how many events were sent.
    """
    # TODO(mvp):
    #   1. Find the task's first and last `task` events (payload["id"] == task_id).
    #   2. Load every Event for that island between those two times, in order.
    #   3. For each: sleep (gap to the previous event) / speed, then bus.broadcast(payload).
    #      Use broadcast(), not emit(), so the replay isn't saved again.
    raise NotImplementedError("Replay isn't built yet (mii/replay.py)")
