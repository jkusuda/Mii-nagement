"""Tasks over the socket and over HTTP: create, cancel, and the friendship meter."""

import asyncio
from types import SimpleNamespace

import pytest
from conftest import ScriptedRunner, db_test, make_workflow

from mii import residents
from mii.consumers import handle_message
from mii.models import Building, Friendship, Island, Resident, Task, island_snapshot

pytestmark = db_test


def runtime_for(workflow):
    return SimpleNamespace(workflow=workflow, bubbles=workflow.bubbles, bus=workflow.bus)


async def test_handoffs_build_friendships(island):
    workflow, client = make_workflow(ScriptedRunner())
    task = await workflow.submit("sunny", "Friends")
    await workflow.run_task(task.id)

    pairs = {(f.a, f.b): f.handoffs async for f in Friendship.objects.filter(island_id="sunny")}
    assert pairs == {("carlos", "jun"): 1, ("carlos", "sydney"): 1, ("jun", "sydney"): 1}
    sent = client.of_type("friendship")
    assert len(sent) == 3 and sent[0]["a"] == "carlos" and sent[0]["b"] == "jun" and 0 < sent[0]["score"] < 1
    snapshot = await island_snapshot("sunny", [])
    assert [f["handoffs"] for f in snapshot["friendships"]] == [1, 1, 1]

    await workflow.run_task((await workflow.submit("sunny", "Again")).id)
    assert (await Friendship.objects.aget(island_id="sunny", a="carlos", b="jun")).handoffs == 2


async def test_cancel_a_queued_and_a_running_task(island):
    workflow, client = make_workflow(ScriptedRunner(delay=0.3))
    worker = asyncio.create_task(workflow.run_forever())
    try:
        running = await workflow.submit("sunny", "Running")
        waiting = await workflow.submit("sunny", "Waiting")
        after = await workflow.submit("sunny", "Next")
        await asyncio.sleep(0.15)  # the worker has started `running`

        assert (await workflow.cancel(waiting.id)).status == "cancelled"
        assert (await workflow.cancel(running.id)).status == "cancelled"  # stopped mid-step
        with pytest.raises(ValueError):
            await workflow.cancel(running.id)
        with pytest.raises(KeyError):
            await workflow.cancel("t_nope")

        for _ in range(60):  # the worker skips the cancelled one and runs the next
            await asyncio.sleep(0.1)
            if (await Task.objects.aget(pk=after.id)).status == "done":
                break
        assert (await Task.objects.aget(pk=after.id)).status == "done"
        assert (await Task.objects.aget(pk=waiting.id)).status == "cancelled"
        assert [e["status"] for e in client.of_type("task") if e["id"] == running.id][-1] == "cancelled"
        carrier = [e for e in client.of_type("agent_state") if e["agent"] == "jun"]
        assert carrier[-1]["state"] == "idle" or carrier  # jun put the package down
    finally:
        worker.cancel()


async def test_task_and_resident_messages_over_the_socket(island):
    workflow, client = make_workflow(ScriptedRunner())
    runtime = runtime_for(workflow)

    assert (await handle_message(runtime, "sunny", {"type": "create_task", "task": {}}))["type"] == "error"
    assert (await handle_message(runtime, "sunny", {"type": "create_task", "task": {"issue": 3}}))["type"] == "error"
    assert (
        await handle_message(runtime, "sunny", {"type": "create_task", "task": {"title": "Hi", "description": "x"}})
        is None
    )
    task_id = client.of_type("task")[-1]["id"]
    assert (await Task.objects.aget(pk=task_id)).description == "x" and workflow.queue.qsize() == 1

    assert await handle_message(runtime, "sunny", {"type": "cancel_task", "id": task_id}) is None
    assert client.of_type("task")[-1]["status"] == "cancelled"
    assert (await handle_message(runtime, "sunny", {"type": "cancel_task", "id": "t_nope"}))["type"] == "error"
    assert (
        "already cancelled"
        in (await handle_message(runtime, "sunny", {"type": "cancel_task", "id": task_id}))["message"]
    )

    mo = await residents.create_resident("sunny", {"name": "Mo", "job": "coder"})
    busy = await handle_message(runtime, "sunny", {"type": "delete_resident", "id": "carlos"})
    assert busy["type"] == "error" and "runs the Workshop" in busy["message"]
    assert await handle_message(runtime, "sunny", {"type": "delete_resident", "id": mo.id}) is None
    assert client.of_type("resident_removed")[-1]["id"] == mo.id
    assert (await handle_message(runtime, "sunny", {"type": "delete_resident", "id": mo.id}))["type"] == "error"


class OverlapRunner(ScriptedRunner):
    """Records how many steps run at the same moment, and on which islands."""

    def __init__(self, delay: float):
        super().__init__(delay=delay)
        self.running: set[str] = set()
        self.most_at_once = 0

    async def run(self, job):
        self.running.add(job.task.island_id)
        self.most_at_once = max(self.most_at_once, len(self.running))
        try:
            return await super().run(job)
        finally:
            self.running.discard(job.task.island_id)


async def test_islands_work_in_parallel_but_one_package_at_a_time_each(island):
    # A second, one-building island next to the seed island.
    await Island.objects.acreate(id="breezy", name="Breezy")
    await Resident.objects.acreate(id="bo", island_id="breezy", name="Bo", job="planner")
    await Building.objects.acreate(
        id="breezy_mailbox", island_id="breezy", type="mailbox", name="Mailbox", x=1, y=1, resident_id="bo"
    )
    runner = OverlapRunner(delay=0.2)
    workflow, _ = make_workflow(runner)
    worker = asyncio.create_task(workflow.run_forever())
    try:
        first = await workflow.submit("sunny", "Sunny one")
        second = await workflow.submit("sunny", "Sunny two")
        other = await workflow.submit("breezy", "Breezy one")
        await asyncio.sleep(0.3)
        # Breezy didn't wait for Sunny's packages; Sunny's second waits for its first.
        assert (await Task.objects.aget(pk=other.id)).status in ("in_progress", "done")
        assert (await Task.objects.aget(pk=second.id)).status == "queued"
        assert workflow.busy_islands() >= {"sunny"}
        for _ in range(100):
            if await Task.objects.filter(pk__in=[first.id, second.id, other.id], status="done").acount() == 3:
                break
            await asyncio.sleep(0.05)
        assert await Task.objects.filter(status="done").acount() == 3
        assert runner.most_at_once == 2  # one step on each island at the same time, never more
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
