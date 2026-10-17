import pytest
from conftest import FakeClient, ScriptedRunner, db_test, make_workflow

from mii import events
from mii.models import Event
from mii.replay import ReplayError, replay_task

pytestmark = db_test


async def test_replay_rebroadcasts_a_recorded_task_without_saving_again(island):
    workflow, live = make_workflow(ScriptedRunner(lab=["fail", "pass"]))
    task = await workflow.run_task((await workflow.submit("sunny", "Recorded")).id)
    stored = await Event.objects.acount()

    bus = events.EventBus()
    viewer = FakeClient()
    bus.add_client(viewer)
    sent = await replay_task(bus, task.id, speed=1000)

    assert sent == len(live.sent) == len(viewer.sent)
    assert viewer.sent[0]["type"] == "task" and viewer.sent[-1]["status"] == "done"
    assert await Event.objects.acount() == stored


async def test_replay_unknown_task(island):
    with pytest.raises(ReplayError):
        await replay_task(events.EventBus(), "t_nope")
