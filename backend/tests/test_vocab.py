"""The shared vocabulary (mii/vocab.py) matches the tables that implement it."""

from conftest import ScriptedRunner, db_test, make_workflow

from mii import events, vocab
from mii.agents.acp import BRAINS
from mii.engine import BUBBLE_MOODS, HANDLERS
from mii.island_map import SHAPES
from mii.judge import MOODS
from mii.tasks import task_window


def test_every_step_has_a_handler_and_a_lot_in_every_shape():
    assert tuple(HANDLERS) == vocab.STEP_ORDER
    for shape in SHAPES.values():
        assert set(shape["lots"]) == set(vocab.STEP_ORDER)


def test_brains_moods_and_bubble_kinds_agree():
    assert tuple(BRAINS) == vocab.ACP_BRAINS
    assert tuple(MOODS) == vocab.MOODS
    assert tuple(BUBBLE_MOODS) == vocab.BUBBLE_KINDS
    assert set(BUBBLE_MOODS.values()) <= set(vocab.MOODS)


def test_snapshot_event_carries_mock_agents():
    event = events.snapshot({"id": "sunny"}, mock_agents=True)
    assert event == {"type": "snapshot", "island": {"id": "sunny", "mock_agents": True}}


@db_test
async def test_task_window_spans_one_task(island):
    workflow, _ = make_workflow(ScriptedRunner())
    task = await workflow.submit("sunny", "Never mind")
    assert await task_window(task.id) is None  # queued, not started

    await workflow.run_task(task.id)
    window = [e.payload["type"] async for e in (await task_window(task.id)).order_by("id")]
    assert window[0] == "task" and window[-1] == "task"  # in_progress ... done
