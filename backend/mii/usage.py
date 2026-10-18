"""Usage per brain: how much of each agent service the residents used today.

The agents report it themselves (agents/acp.py, agents/openrouter.py): tokens per turn
when they send them, and a running session cost. That is this computer's residents'
use, not the whole account's (which the services don't share with us). GET /usage
serves today's totals to the game's sidebar.
"""

import logging
from datetime import date

from django.db.models import F

from mii.models import BrainUsage, Resident

log = logging.getLogger(__name__)


async def record(brain: str, *, turns: int = 0, tokens: int = 0, cost: float = 0.0) -> None:
    """Add to today's totals for `brain`. Never fails the agent's work: a count that
    can't be saved is only logged."""
    if brain == "mock" or not (turns or tokens or cost):
        return
    try:
        row, _ = await BrainUsage.objects.aget_or_create(brain=brain, day=date.today())  # this computer's day
        await BrainUsage.objects.filter(pk=row.pk).aupdate(
            turns=F("turns") + turns, tokens=F("tokens") + tokens, cost=F("cost") + cost
        )
    except Exception:
        log.warning("couldn't count %s usage", brain, exc_info=True)


async def today() -> dict:
    """Today's usage for every real brain a resident uses (zeros included) or that was used
    today: {"day", "brains": [{"brain", "turns", "tokens", "cost"}]}, brains in a fixed order."""
    day = date.today()
    rows = {u.brain: u async for u in BrainUsage.objects.filter(day=day)}
    brains = set(rows)
    async for brain in Resident.objects.values_list("brain", flat=True).distinct():
        brains.add(brain)
    brains.discard("mock")
    out = []
    for brain in sorted(brains):
        row = rows.get(brain)
        out.append(
            {
                "brain": brain,
                "turns": row.turns if row else 0,
                "tokens": row.tokens if row else 0,
                "cost": round(row.cost, 4) if row else 0.0,
            }
        )
    return {"day": day.isoformat(), "brains": out}
