"""Insights: plain stats about how the island is working, for the Insights screen.

Everything comes from the Step table (one row per building visit) plus Task.
Served at GET /islands/{id}/insights.
"""

from collections import defaultdict

from mii.models import Step, Task


async def island_insights(island_id: str) -> dict:
    """The full insights payload. Each section is filled in by one helper below."""
    steps = [s async for s in Step.objects.filter(island_id=island_id).order_by("id")]
    tasks = [t async for t in Task.objects.filter(island_id=island_id)]
    return {
        "island": island_summary(tasks, steps),
        "residents": by_resident(steps),
        "brains": by_brain(steps),
        "buildings": by_building(steps),
    }


def island_summary(tasks: list[Task], steps: list[Step] | None = None) -> dict:
    last_finish = {}
    for s in steps or []:
        if s.finished_at and (s.task_id not in last_finish or s.finished_at > last_finish[s.task_id]):
            last_finish[s.task_id] = s.finished_at
    durations = [
        (last_finish[t.id] - t.created_at).total_seconds() for t in tasks if t.pr_url and t.id in last_finish
    ]
    return {
        "tasks_done": sum(t.status == "done" for t in tasks),
        "tasks_failed": sum(t.status == "failed" for t in tasks),
        "prs_opened": sum(t.pr_url is not None for t in tasks),
        "avg_issue_to_pr_seconds": _avg(durations),
    }


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
