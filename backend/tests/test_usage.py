"""Usage per brain (usage.py): what the agents report adds up per day, and GET /usage."""

from acp.schema import Cost, UsageUpdate
from conftest import db_test, make_job
from django.test import AsyncClient

from mii import usage
from mii.agents.acp import MiiClient
from mii.models import BrainUsage, Resident

pytestmark = db_test


async def test_record_adds_up_and_skips_nothing():
    await usage.record("claude-code", turns=1, tokens=1200, cost=0.25)
    await usage.record("claude-code", turns=1, tokens=800)
    await usage.record("codex")
    rows = [u async for u in BrainUsage.objects.all()]
    assert [(u.brain, u.turns, u.tokens, u.cost) for u in rows] == [("claude-code", 2, 2000, 0.25)]


async def test_usage_lists_brains_in_use_with_zeros(island):
    await Resident.objects.filter(island=island).aupdate(brain="gemini")
    await usage.record("claude-code", turns=3, tokens=900, cost=0.1)
    data = (await AsyncClient().get("/usage")).json()
    assert data["brains"] == [
        {"brain": "claude-code", "turns": 3, "tokens": 900, "cost": 0.1},
        {"brain": "gemini", "turns": 0, "tokens": 0, "cost": 0.0},
    ]
    assert data["day"]


async def test_session_cost_counts_only_what_it_went_up_by(tmp_path):
    client = MiiClient(make_job("claude-code", workdir=tmp_path), tmp_path, "claude-code")
    for amount in (0.5, 0.8, 0.8):
        update = UsageUpdate(session_update="usage_update", used=1, size=10, cost=Cost(amount=amount, currency="USD"))
        await client.session_update("s1", update)
    euros = UsageUpdate(session_update="usage_update", used=1, size=10, cost=Cost(amount=5.0, currency="EUR"))
    await client.session_update("s1", euros)
    row = await BrainUsage.objects.aget(brain="claude-code")
    assert round(row.cost, 6) == 0.8
