"""Insights: plain stats about how the island is working, for the Insights screen.

Everything comes from the Step table (one row per building visit), Task, and
the stored events (task finishes, bubbles). Served at
GET /islands/{id}/insights?range=today|week|month; the shape is in
docs/architecture.md ("Insights payload").

A range ends now and starts at local midnight: today, the last 7 days, or the
last 30 days. `*_before` numbers are the same length of time just before it.
"""

from collections import defaultdict
from datetime import datetime, timedelta

from mii.models import Building, Event, Step, Task

RANGES = {"today": 1, "week": 7, "month": 30}  # range -> days, today included
RANGE_WORDS = {"today": "yesterday", "week": "last week", "month": "last month"}

# Permission answers that let the resident in (the agents' own option names vary).
ALLOW_WORDS = ("allow", "approve", "yes", "accept")


class InsightsError(ValueError):
    pass


async def island_insights(island_id: str, range_: str = "week", now: datetime | None = None) -> dict:
    """The full insights payload. Each section is filled in by one helper below."""
    if range_ not in RANGES:
        raise InsightsError(f"range must be one of {', '.join(RANGES)}")
    start, end, before = range_bounds(range_, now)
    steps = [s async for s in Step.objects.filter(island_id=island_id, started_at__gte=before).order_by("id")]
    tasks = [t async for t in Task.objects.filter(island_id=island_id)]
    events = [
        e async for e in Event.objects.filter(
            island_id=island_id, type__in=["task", "bubble", "bubble_resolved"], created_at__gte=before - timedelta(days=1),
        ).order_by("id")
    ]
    names = {b.id: b.name async for b in Building.objects.filter(island_id=island_id)}

    finished = finish_times(tasks, steps, events)
    prs = pr_times(tasks, events, finished)
    bubbles = answered_bubbles(events)
    now_steps = [s for s in steps if s.started_at >= start]

    summary = {
        **island_summary(tasks, finished, prs, start, end),
        **_suffix(island_summary(tasks, finished, prs, before, start), "_before", ["tasks_done", "prs_opened"]),
        **bubble_summary(bubbles, start, end),
        **_suffix(bubble_summary(bubbles, before, start), "_before", ["answer_seconds"]),
        **lab_summary(now_steps),
    }
    buildings = by_building(now_steps)
    return {
        "range": range_,
        "island": summary,
        "per_day": per_day(tasks, finished, start, RANGES[range_]),
        "tip": tip(summary, buildings, names, range_),
        "residents": by_resident(now_steps),
        "brains": by_brain(now_steps),
        "buildings": buildings,
    }


def range_bounds(range_: str, now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    """(start, end, start of the previous range), in the server's local time zone."""
    end = (now or datetime.now()).astimezone()
    midnight = end.replace(hour=0, minute=0, second=0, microsecond=0)
    days = RANGES[range_]
    start = midnight - timedelta(days=days - 1)
    return start, end, start - timedelta(days=days)


# --- When things happened -----------------------------------------------------


def finish_times(tasks: list[Task], steps: list[Step], events: list[Event]) -> dict[str, datetime]:
    """Task id -> when it finished (done or failed): its last `task` event, else its last step."""
    finished: dict[str, datetime] = {}
    for s in steps:
        if s.finished_at and (s.task_id not in finished or s.finished_at > finished[s.task_id]):
            finished[s.task_id] = s.finished_at
    for e in events:
        if e.type == "task" and e.payload.get("status") in ("done", "failed"):
            finished[e.payload.get("id")] = e.created_at
    return {t.id: finished[t.id] for t in tasks if t.status in ("done", "failed") and t.id in finished}


def pr_times(tasks: list[Task], events: list[Event], finished: dict[str, datetime]) -> dict[str, datetime]:
    """Task id -> when its pull request was opened: the first `task` event with a pr_url, else its finish."""
    opened: dict[str, datetime] = {}
    for e in events:
        if e.type == "task" and e.payload.get("pr_url"):
            opened.setdefault(e.payload.get("id"), e.created_at)
    return {t.id: opened.get(t.id) or finished[t.id] for t in tasks if t.pr_url and (t.id in opened or t.id in finished)}


def answered_bubbles(events: list[Event]) -> list[dict]:
    """Every bubble you answered: {"kind", "answer", "shown", "answered"} (timed-out bubbles are left out)."""
    shown = {e.payload.get("id"): e for e in events if e.type == "bubble"}
    rows = []
    for e in events:
        bubble = shown.get(e.payload.get("id"))
        if e.type == "bubble_resolved" and bubble is not None and e.payload.get("answer") is not None:
            rows.append({"kind": bubble.payload.get("kind"), "answer": str(e.payload["answer"]),
                         "shown": bubble.created_at, "answered": e.created_at})
    return rows


# --- Sections -------------------------------------------------------------------


def island_summary(tasks: list[Task], finished: dict, prs: dict, start: datetime, end: datetime) -> dict:
    """Tasks done / failed, PRs opened, and how long from a package arriving to its PR, between start and end."""
    def inside(when):
        return when is not None and start <= when < end

    by_id = {t.id: t for t in tasks}
    return {
        "tasks_done": sum(by_id[i].status == "done" and inside(w) for i, w in finished.items()),
        "tasks_failed": sum(by_id[i].status == "failed" and inside(w) for i, w in finished.items()),
        "prs_opened": sum(inside(w) for w in prs.values()),
        "avg_issue_to_pr_seconds": _avg([(w - by_id[i].created_at).total_seconds() for i, w in prs.items() if inside(w)]),
    }


def bubble_summary(bubbles: list[dict], start: datetime, end: datetime) -> dict:
    """Bubbles you answered, how fast, and how many house keys you lent (permission bubbles you allowed)."""
    rows = [b for b in bubbles if start <= b["answered"] < end]
    return {
        "bubbles_answered": len(rows),
        "answer_seconds": _avg([(b["answered"] - b["shown"]).total_seconds() for b in rows]),
        "keys_lent": sum(b["kind"] == "permission" and _allows(b["answer"]) for b in rows),
    }


def lab_summary(steps: list[Step]) -> dict:
    """How often the Lab's test runs pass (None until it has run)."""
    runs = [s for s in steps if s.building_type == "lab" and s.outcome]
    return {"tests_pass_rate": _rate(runs, "pass") if runs else None}


def per_day(tasks: list[Task], finished: dict, start: datetime, days: int) -> list[dict]:
    """Tasks done per local day, oldest first, today last."""
    done = defaultdict(int)
    by_id = {t.id: t for t in tasks}
    for task_id, when in finished.items():
        if by_id[task_id].status == "done":
            done[when.astimezone(start.tzinfo).date()] += 1
    first = start.date()
    return [{"day": (first + timedelta(days=i)).isoformat(), "done": done[first + timedelta(days=i)]} for i in range(days)]


def tip(summary: dict, buildings: list[dict], names: dict[str, str], range_: str) -> str | None:
    """One friendly suggestion, or None (then the game words its own)."""
    bouncy = [b for b in buildings if b["visits"] >= 3 and b["fail_rate"] >= 0.5]
    if bouncy:
        worst = max(bouncy, key=lambda b: b["fail_rate"])
        name = names.get(worst["building"], worst["type"].replace("_", " ").title())
        return f"Packages bounce back at the {name} a lot. A more careful resident there could help."
    if summary["answer_seconds"] is not None and summary["answer_seconds"] > 600:
        return (f"Residents wait about {round(summary['answer_seconds'] / 60)} min for your answers. "
                "Lending a few more house keys would save them the wait.")
    more = summary["tasks_done"] - summary["tasks_done_before"]
    if more > 0 and summary["tasks_done_before"] > 0:
        return f"{more} more package{'' if more == 1 else 's'} delivered than {RANGE_WORDS[range_]}. Nice work!"
    if summary["tasks_failed"] > summary["tasks_done"]:
        return "More packages failed than shipped. Smaller tasks are easier to carry."
    return None


def by_resident(steps: list[Step]) -> list[dict]:
    """Per resident: steps, pass rate, average step time, bubbles raised."""
    groups = _group(steps, lambda s: s.resident_id)
    return [
        {
            "resident": resident_id,
            "brain": rows[-1].brain,
            "steps": len(rows),
            "pass_rate": _rate(rows, "pass"),
            "avg_seconds": _avg([s.seconds for s in rows if s.seconds is not None]),
            "bubbles": sum(s.bubbles_raised for s in rows),
        }
        for resident_id, rows in groups.items()
    ]


def by_brain(steps: list[Step]) -> list[dict]:
    """Per brain and building type: pass rate and average step time,
    e.g. "Codex passes review 80% of the time; Claude Code is fastest in the Workshop"."""
    groups = _group(steps, lambda s: (s.brain, s.building_type))
    return [
        {
            "brain": brain,
            "building_type": building_type,
            "steps": len(rows),
            "pass_rate": _rate(rows, "pass"),
            "avg_seconds": _avg([s.seconds for s in rows if s.seconds is not None]),
        }
        for (brain, building_type), rows in groups.items()
    ]


def by_building(steps: list[Step]) -> list[dict]:
    """Per building: visits and fail rate (where packages bounce back most)."""
    groups = _group(steps, lambda s: s.building_id)
    return [
        {"building": building_id, "type": rows[-1].building_type, "visits": len(rows), "fail_rate": _rate(rows, "fail")}
        for building_id, rows in groups.items()
    ]


# --- Helpers ----------------------------------------------------------------------


def _allows(answer: str) -> bool:
    words = answer.lower()
    return any(w in words for w in ALLOW_WORDS) and not any(w in words for w in ("reject", "deny", "don't"))


def _suffix(values: dict, suffix: str, keys: list[str]) -> dict:
    return {key + suffix: values[key] for key in keys}


def _group(steps, key):
    groups: dict = defaultdict(list)
    for s in steps:
        if s.outcome:  # skip steps still running
            groups[key(s)].append(s)
    return groups


def _rate(rows: list[Step], outcome: str) -> float:
    return round(sum(s.outcome == outcome for s in rows) / len(rows), 2) if rows else 0.0


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None
