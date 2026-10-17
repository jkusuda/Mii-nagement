import json

import pytest
from sqlmodel import Session

from mii import events, residents, seed, server
from mii.models import DEFAULT_KEYS, Resident, island_snapshot, make_engine


class FakeClient:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, event: dict) -> None:
        self.sent.append(event)


@pytest.fixture
def db():
    engine = make_engine("sqlite://")
    seed.seed(engine)
    return engine


def test_create_fills_defaults_and_home(db):
    with Session(db) as session:
        r = residents.create_resident(session, "sunny", {"name": "  Mo  ", "job": "reviewer"})
        assert r.id.startswith("mo_")
        assert r.name == "Mo"
        assert r.building_id == "courthouse"
        assert r.brain == "mock"
        assert r.keys == DEFAULT_KEYS
        assert r.gifts == []


def test_create_keeps_every_creator_field(db):
    data = {
        "name": "Bea", "job": "planner", "color": "#2fa37a", "catchphrase": "Tidy!",
        "brain": "codex", "model": "  ", "bluntness": 1.7, "caution": 0.25,
        "keys": {"edit": "yes"}, "gifts": ["docs", "browser", "docs"],
    }
    with Session(db) as session:
        r = residents.create_resident(session, "sunny", data)
        assert (r.color, r.brain, r.model, r.bluntness, r.caution) == ("#2FA37A", "codex", None, 1.0, 0.25)
        assert r.keys == {**DEFAULT_KEYS, "edit": "yes"}
        assert r.gifts == ["docs", "browser"]
        # ...and the snapshot sends it all to the game.
        sent = next(x for x in island_snapshot(session, "sunny", [])["residents"] if x["id"] == r.id)
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
def test_create_rejects_bad_fields(db, bad):
    with Session(db) as session, pytest.raises(residents.ResidentError):
        residents.create_resident(session, "sunny", bad)


def test_update_changes_only_sent_fields(db):
    with Session(db) as session:
        r = residents.update_resident(session, "sunny", {"id": "jun", "caution": 0.1, "gifts": ["github"]})
        assert (r.caution, r.gifts, r.name, r.job) == (0.1, ["github"], "Jun", "planner")
        with pytest.raises(residents.ResidentError):
            residents.update_resident(session, "sunny", {"id": "nobody"})


async def test_websocket_create_broadcasts_resident(db, monkeypatch):
    bus = events.EventBus(db)
    watcher, sender = FakeClient(), FakeClient()
    bus.add_client(watcher)
    monkeypatch.setattr(server, "db_engine", db)
    monkeypatch.setattr(server, "bus", bus)

    msg = {"type": "create_resident", "resident": {"name": "Pip", "job": "coder", "color": "#EE7C93"}}
    await server.handle_client_message(sender, "sunny", json.dumps(msg))
    [event] = watcher.sent
    assert event["type"] == "resident"
    assert event["resident"]["name"] == "Pip" and event["resident"]["building"] == "workshop"
    with Session(db) as session:
        assert session.get(Resident, event["resident"]["id"]) is not None

    # A bad message only goes back to the sender, as an error.
    await server.handle_client_message(sender, "sunny", json.dumps({"type": "create_resident", "resident": {}}))
    assert sender.sent[-1]["type"] == "error" and len(watcher.sent) == 1
