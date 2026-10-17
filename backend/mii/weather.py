"""Island weather: a mood for the whole island, read off its recent work.

    sunny    the last few steps mostly passed (or nothing has happened yet)
    cloudy   about half of them failed
    rainy    most of them failed
    night    everyone on the island is asleep

It goes in the snapshot (`weather`) and out as a `weather` event when it
changes (the engine checks after every step and whenever a resident falls
asleep or wakes). The game may still roll its own; this is the backend's say.
"""

from mii.models import Resident, Step

RECENT_STEPS = 10
SUNNY_AT = 0.8  # pass rate at or above this: sunny
CLOUDY_AT = 0.5  # pass rate at or above this: cloudy; below: rainy
KINDS = ("sunny", "cloudy", "rainy", "night")


async def current(island_id: str) -> str:
    states = [r.state async for r in Resident.objects.filter(island_id=island_id).only("state")]
    if states and all(state == "sleeping" for state in states):
        return "night"
    outcomes = [
        s.outcome
        async for s in Step.objects.filter(island_id=island_id).exclude(outcome="").order_by("-id")[:RECENT_STEPS]
    ]
    return from_outcomes(outcomes)


def from_outcomes(outcomes: list[str]) -> str:
    """The weather for a list of recent step outcomes ("pass" / "fail"), newest first."""
    if not outcomes:
        return "sunny"
    rate = sum(o == "pass" for o in outcomes) / len(outcomes)
    if rate >= SUNNY_AT:
        return "sunny"
    if rate >= CLOUDY_AT:
        return "cloudy"
    return "rainy"
