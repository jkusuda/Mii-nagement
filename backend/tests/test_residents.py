import pytest
from conftest import FakeClient, ScriptedRunner, db_test, make_workflow

from mii import residents
from mii.consumers import handle_message
from mii.models import DEFAULT_KEYS, Resident, island_snapshot

pytestmark = db_test


async def test_create_fills_defaults_and_home(island):
    r = await residents.create_resident("sunny", {"name": "  Mo  ", "job": "reviewer"})
    assert r.id.startswith("mo_")
    assert r.name == "Mo"
    assert r.building_id == "courthouse"
    assert r.brain == "mock"
    assert r.keys == DEFAULT_KEYS
    assert r.gifts == []


async def test_create_keeps_every_creator_field(island):
    data = {
        "name": "Bea", "job": "planner", "color": "#2fa37a", "catchphrase": "Tidy!",
        "brain": "openrouter", "model": "  ", "bluntness": 1.7, "caution": 0.25,
        "keys": {"edit": "yes"}, "gifts": ["docs", "browser", "docs"],
    }
    r = await residents.create_resident("sunny", data)
    assert (r.color, r.brain, r.model, r.bluntness, r.caution) == ("#2FA37A", "openrouter", None, 1.0, 0.25)
    assert r.keys == {**DEFAULT_KEYS, "edit": "yes"}
    assert r.gifts == ["docs", "browser"]
    # ...and the snapshot sends it all to the game.
    sent = next(x for x in (await island_snapshot("sunny", []))["residents"] if x["id"] == r.id)
    assert sent["keys"]["edit"] == "yes" and sent["gifts"] == ["docs", "browser"] and sent["bluntness"] == 1.0


@pytest.mark.parametrize("bad", [
    {"name": ""},
    {"name": "x" * 21},
    {"name": "Mo", "job": "tester"},
    {"name": "Mo", "brain": "gpt-9"},
    {"name": "Mo", "color": "purple"},
    {"name": "Mo", "caution": "very"},
    {"name": "Mo", "keys": {"edit": "maybe"}},
    {"name": "Mo", "keys": {"launch": "yes"}},
    {"name": "Mo", "gifts": ["jetpack"]},
])
async def test_create_rejects_bad_fields(island, bad):
    with pytest.raises(residents.ResidentError):
        await residents.create_resident("sunny", bad)


async def test_update_changes_only_sent_fields(island):
    r = await residents.update_resident("sunny", {"id": "jun", "caution": 0.1, "gifts": ["github"]})
    assert (r.caution, r.gifts, r.name, r.job) == (0.1, ["github"], "Jun", "planner")
    assert (await Resident.objects.aget(pk="jun")).gifts == ["github"]
    with pytest.raises(residents.ResidentError):
        await residents.update_resident("sunny", {"id": "nobody"})


async def test_websocket_create_broadcasts_resident(island):
    workflow, watcher = make_workflow(ScriptedRunner())
    runtime = type("Runtime", (), {"bus": workflow.bus, "bubbles": workflow.bubbles})()

    msg = {"type": "create_resident", "resident": {"name": "Pip", "job": "coder", "color": "#EE7C93"}}
    assert await handle_message(runtime, "sunny", msg) is None
    [event] = watcher.sent
    assert event["type"] == "resident"
    assert event["resident"]["name"] == "Pip" and event["resident"]["building"] == "workshop"
    assert await Resident.objects.filter(pk=event["resident"]["id"]).aexists()

    # A bad message only goes back to the sender, as an error.
    reply = await handle_message(runtime, "sunny", {"type": "create_resident", "resident": {}})
    assert reply["type"] == "error" and len(watcher.sent) == 1
    assert (await handle_message(runtime, "sunny", "nope"))["type"] == "error"
    assert (await handle_message(runtime, "sunny", {"type": "answer_bubble", "id": "b_x"}))["type"] == "error"


async def test_bus_sends_events_only_to_that_islands_clients(island):
    from mii import events

    bus = events.EventBus()
    sunny, other, everything = FakeClient(), FakeClient(), FakeClient()
    bus.add_client(sunny, "sunny")
    bus.add_client(other, "maple")
    bus.add_client(everything)
    await bus.emit("sunny", events.mood("jun", "proud"))
    assert len(sunny.sent) == 1 and other.sent == [] and len(everything.sent) == 1
