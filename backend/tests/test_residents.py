import pytest
from conftest import FakeClient, ScriptedRunner, db_test, make_workflow

from mii import residents
from mii.consumers import handle_message
from mii.models import DEFAULT_KEYS, Resident, island_snapshot

pytestmark = db_test


async def test_create_fills_defaults_and_home(island):
    r = await residents.create_resident("sunny", {"name": "  Mo  ", "job": "reviewer", "brain": "codex"})
    assert r.id.startswith("mo_")
    assert r.name == "Mo"
    assert r.building_id == "courthouse"
    assert r.brain == "codex"
    assert r.keys == DEFAULT_KEYS
    assert r.gifts == []


async def test_create_keeps_every_creator_field(island):
    data = {
        "name": "Bea",
        "job": "planner",
        "color": "#2fa37a",
        "catchphrase": "Tidy!",
        "brain": "openrouter",
        "model": "  ",
        "bluntness": 1.7,
        "caution": 0.25,
        "keys": {"edit": "yes"},
        "gifts": ["docs", "browser", "docs"],
        "appearance": {"skin": "#F2C6A0", "hair": 12, "eyes": 3},
    }
    r = await residents.create_resident("sunny", data)
    assert (r.color, r.brain, r.model, r.bluntness, r.caution) == ("#2FA37A", "openrouter", None, 1.0, 0.25)
    assert r.keys == {**DEFAULT_KEYS, "edit": "yes"}
    assert r.gifts == ["docs", "browser"]
    # ...and the snapshot sends it all to the game.
    sent = next(x for x in (await island_snapshot("sunny", []))["residents"] if x["id"] == r.id)
    assert sent["keys"]["edit"] == "yes" and sent["gifts"] == ["docs", "browser"] and sent["bluntness"] == 1.0
    assert sent["appearance"] == {"skin": "#F2C6A0", "hair": 12, "eyes": 3}


@pytest.mark.parametrize(
    "bad",
    [
        {"name": ""},
        {"name": "x" * 21},
        {"name": "Mo", "job": "tester"},
        {"name": "Mo", "brain": "gpt-9"},
        {"name": "Mo", "brain": None},
        {"name": "Mo", "color": "purple"},
        {"name": "Mo", "caution": "very"},
        {"name": "Mo", "keys": {"edit": "maybe"}},
        {"name": "Mo", "keys": {"launch": "yes"}},
        {"name": "Mo", "keys": {"master": "ask"}},
        {"name": "Mo", "gifts": ["jetpack"]},
        {"name": "Mo", "appearance": "bob"},
        {"name": "Mo", "appearance": {"hair": {"style": 1}}},
        {"name": "Mo", "appearance": {"hair": "x" * 21}},
        {"name": "Mo", "appearance": {f"k{i}": 1 for i in range(25)}},
    ],
)
async def test_create_rejects_bad_fields(island, bad):
    with pytest.raises(residents.ResidentError):
        await residents.create_resident("sunny", {"brain": "claude-code", **bad})  # every other field is fine


async def test_create_needs_a_brain(island):
    with pytest.raises(residents.ResidentError, match="pick a brain"):
        await residents.create_resident("sunny", {"name": "Mo", "job": "coder"})


async def test_update_changes_only_sent_fields(island):
    r = await residents.update_resident("sunny", {"id": "jun", "caution": 0.1, "gifts": ["github"]})
    assert (r.caution, r.gifts, r.name, r.job) == (0.1, ["github"], "Jun", "planner")
    assert r.appearance == {}  # never set: the game picks a look from the id
    assert (await Resident.objects.aget(pk="jun")).gifts == ["github"]
    with pytest.raises(residents.ResidentError):
        await residents.update_resident("sunny", {"id": "nobody"})


async def test_resident_can_be_given_buildings(island):
    from mii.models import Building

    r = await residents.create_resident(
        "sunny", {"name": "Mo", "job": "coder", "brain": "claude-code", "buildings": ["lab", "courthouse"]}
    )
    assert (await Building.objects.aget(pk="lab")).resident_id == r.id
    assert (await Building.objects.aget(pk="courthouse")).resident_id == r.id
    assert (await Building.objects.aget(pk="workshop")).resident_id == "carlos"  # still taken
    await residents.update_resident("sunny", {"id": "sydney", "buildings": ["courthouse"]})
    assert (await Building.objects.aget(pk="courthouse")).resident_id == "sydney"
    with pytest.raises(residents.ResidentError):
        await residents.update_resident("sunny", {"id": "sydney", "buildings": ["castle"]})


async def test_websocket_create_broadcasts_resident(island):
    workflow, watcher = make_workflow(ScriptedRunner())
    runtime = type("Runtime", (), {"bus": workflow.bus, "bubbles": workflow.bubbles})()

    msg = {"type": "create_resident", "resident": {"name": "Pip", "job": "coder", "brain": "codex", "color": "#EE7C93"}}
    assert await handle_message(runtime, "sunny", msg) is None
    [event] = watcher.sent
    assert event["type"] == "resident"
    assert event["resident"]["name"] == "Pip" and event["resident"]["building"] == "workshop"
    assert await Resident.objects.filter(pk=event["resident"]["id"]).aexists()

    # A bad message only goes back to the sender, as an error.
    reply = await handle_message(runtime, "sunny", {"type": "create_resident", "resident": {}})
    assert reply["type"] == "error" and len(watcher.sent) == 1

    # Giving a resident a building also broadcasts the new layout.
    msg = {"type": "update_resident", "resident": {"id": event["resident"]["id"], "buildings": ["lab"]}}
    assert await handle_message(runtime, "sunny", msg) is None
    assert [e["type"] for e in watcher.sent[1:]] == ["resident", "layout"]
    lab = next(b for b in watcher.sent[-1]["buildings"] if b["id"] == "lab")
    assert lab["resident"] == event["resident"]["id"]
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


async def test_appearance_is_stored_and_echoed(island):
    look = {"eyes": 2, "hair": "bob", "hair_color": "#4A3426"}
    r = await residents.create_resident("sunny", {"name": "Mo", "brain": "claude-code", "appearance": look})
    assert (await Resident.objects.aget(pk=r.id)).appearance == look
    sent = next(x for x in (await island_snapshot("sunny", []))["residents"] if x["id"] == r.id)
    assert sent["appearance"] == look and sent["activity"] == ""
    with pytest.raises(residents.ResidentError):
        await residents.create_resident("sunny", {"name": "Bea", "brain": "codex", "appearance": ["not", "a", "dict"]})


async def test_update_island_message_and_request_id_echo(island):
    workflow, watcher = make_workflow(ScriptedRunner())
    runtime = type("Runtime", (), {"bus": workflow.bus, "bubbles": workflow.bubbles})()

    msg = {"type": "update_island", "island": {"name": "Sunnier", "branch": "dev"}, "request_id": "r1"}
    assert await handle_message(runtime, "sunny", msg) is None
    [event] = watcher.sent
    assert event["type"] == "island" and event["island"]["name"] == "Sunnier" and event["island"]["branch"] == "dev"

    reply = await handle_message(
        runtime, "sunny", {"type": "update_island", "island": {"repo": "bad"}, "request_id": "r2"}
    )
    assert reply["type"] == "error" and reply["request_id"] == "r2" and len(watcher.sent) == 1
    reply = await handle_message(runtime, "sunny", {"type": "answer_bubble", "id": "b_x"})
    assert reply["type"] == "error" and "request_id" not in reply
