"""Insights: plain stats about how the island is working, for the Insights screen
and the News tab's "This week" box (payload shape: docs/architecture.md).

Served at GET /islands/{id}/insights?range=today|week|month. Everything comes
from the Step table (one row per building visit), the Task table, and the
stored bubble events. Days are the server's local calendar days.
"""

import asyncio
import logging
import re
import shutil
from collections import defaultdict
from datetime import datetime, timedelta

from mii import github
from mii.judge import RuleJudge
from mii.models import Building, Event, Resident, Step, Task, utcnow

log = logging.getLogger(__name__)

RANGES = {"today": 1, "week": 7, "month": 30}  # range name -> number of days, ending today
MAX_PR_CHECKS = 10  # how many open PRs we ask `gh` about per request
REJECT_PATTERN = re.compile(r"^\s*(reject|deny|no|don't|do not|never)\b", re.IGNORECASE)


async def island_insights(island_id: str, range_name: str = "week", judge=None) -> dict:
    """The full insights payload. Each section is filled in by one helper below."""
    days = RANGES[range_name]
    today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)  # the range: `days` calendar days ending today
    before = start - timedelta(days=days)  # the range before it, for "↑ 4 on last week"

    all_tasks = [t async for t in Task.objects.filter(island_id=island_id)]
    await _refresh_merges(all_tasks)
    tasks = [t for t in all_tasks if finished(t) and finished(t) >= start]
    tasks_before = [t for t in all_tasks if finished(t) and before <= finished(t) < start]
    steps = [s async for s in Step.objects.filter(island_id=island_id, started_at__gte=start).order_by("id")]
    bubbles = await bubble_stats(island_id, start, utcnow() + timedelta(days=1))
    bubbles_before = await bubble_stats(island_id, before, start)
    names = {b.id: b.name async for b in Building.objects.filter(island_id=island_id)}
    names |= {r.id: r.name async for r in Resident.objects.filter(island_id=island_id)}

    island = island_summary(tasks, steps) | {
        "tasks_done_before": sum(t.status == "done" for t in tasks_before),
        "prs_opened_before": sum(t.pr_url is not None for t in tasks_before),
        "prs_merged": sum(t.merged_at is not None and t.merged_at >= start for t in all_tasks),
        "answer_seconds": bubbles["answer_seconds"],
        "answer_seconds_before": bubbles_before["answer_seconds"],
        "bubbles_answered": bubbles["answered"],
        "keys_lent": bubbles["keys_lent"],
        **lab_stats(steps),
    }
    payload = {
        "range": range_name,
        "island": island,
        "per_day": per_day(tasks, start, days),
        "residents": by_resident(steps),
        "brains": by_brain(steps),
        "buildings": by_building(steps),
    }
    payload["tip"] = await (judge or RuleJudge()).tip(
        {**payload["island"], "buildings": payload["buildings"], "residents": payload["residents"]},
        tip_candidates(payload, bubbles, names, range_name),
    )
    return payload


def finished(t: Task) -> datetime | None:
    """When a task ended (older rows have no finished_at: use created_at)."""
    if t.status not in ("done", "failed"):
        return None
    return t.finished_at or t.created_at


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


def per_day(tasks: list[Task], start: datetime, days: int) -> list[dict]:
    """Tasks finished (done) per local calendar day, oldest first, today last."""
    counts: dict[str, int] = defaultdict(int)
    for t in tasks:
        if t.status == "done":
            counts[finished(t).astimezone().strftime("%Y-%m-%d")] += 1
    return [
        {"day": (start + timedelta(days=i)).strftime("%Y-%m-%d"), "done": counts[(start + timedelta(days=i)).strftime("%Y-%m-%d")]}
        for i in range(days)
    ]


def lab_stats(steps: list[Step]) -> dict:
    """Test runs at the Lab: pass rate, and "flaky" = tasks whose tests failed once and then passed
    (including the Lab's own re-run when the judge called a failure flaky: Step.flaky)."""
    runs = [s for s in steps if s.building_type == "lab" and s.outcome]
    by_task: dict[str, list[Step]] = defaultdict(list)
    for s in runs:
        by_task[s.task_id].append(s)
    flaky = sum(
        any(s.flaky for s in visits) or ("fail" in [s.outcome for s in visits] and visits[-1].outcome == "pass")
        for visits in by_task.values()
    )
    return {"tests_pass_rate": _rate(runs, "pass") if runs else None, "flaky_tests": flaky}


async def bubble_stats(island_id: str, start: datetime, end: datetime) -> dict:
    """Bubbles shown in [start, end): how many were answered, how fast, and how many keys were lent
    (permission bubbles answered with anything but a rejection)."""
    shown: dict[str, Event] = {}
    async for e in Event.objects.filter(island_id=island_id, type="bubble", created_at__gte=start, created_at__lt=end):
        shown[e.payload.get("id")] = e
    answered, waits, keys_lent = 0, [], 0
    async for e in Event.objects.filter(island_id=island_id, type="bubble_resolved", created_at__gte=start, payload__answered=True):
        bubble = shown.get(e.payload.get("id"))
        if bubble is None:
            continue
        answered += 1
        waits.append((e.created_at - bubble.created_at).total_seconds())
        if bubble.payload.get("kind") == "permission" and not REJECT_PATTERN.match(str(e.payload.get("response") or "")):
            keys_lent += 1
    return {"shown": len(shown), "answered": answered, "answer_seconds": _avg(waits), "keys_lent": keys_lent}


async def _refresh_merges(tasks: list[Task]) -> None:
    """Ask `gh` whether open PRs were merged since we last looked (best effort, a few per request)."""
    open_prs = [t for t in tasks if t.pr_url and "github.com/" in t.pr_url and t.merged_at is None][:MAX_PR_CHECKS]
    if not open_prs or shutil.which("gh") is None:
        return

    def check(task: Task) -> str | None:
        try:
            return github.pr_merged_at(task.pr_url)
        except github.GitHubError as e:
            log.info("could not check %s: %s", task.pr_url, e)
            return None

    for task in open_prs:
        merged = await asyncio.to_thread(check, task)
        if merged:
            task.merged_at = datetime.fromisoformat(merged.replace("Z", "+00:00"))
            await Task.objects.filter(pk=task.id).aupdate(merged_at=task.merged_at)


def tip_candidates(payload: dict, bubbles: dict, names: dict[str, str], range_name: str) -> dict[str, str]:
    """Every friendly suggestion that applies, best rule first (the judge picks one; the rules take the first)."""
    island, when = payload["island"], {"today": "today", "week": "this week", "month": "this month"}[range_name]
    total = island["tasks_done"] + island["tasks_failed"]
    if total == 0:
        return {"quiet": f"No packages {when} yet. Drop a task in the Mailbox to get things moving."}
    tips: dict[str, str] = {}
    bouncy = [b for b in payload["buildings"] if b["visits"] >= 2 and b["fail_rate"] >= 0.5]
    if bouncy:
        worst = max(bouncy, key=lambda b: b["fail_rate"])
        tips["bouncy"] = f"The {names.get(worst['building'], worst['building'])} sends most packages back. Worth a look at what it's asking for."
    unanswered = bubbles["shown"] - bubbles["answered"]
    if bubbles["shown"] >= 2 and unanswered / bubbles["shown"] > 0.5:
        tips["unanswered"] = f"{unanswered} bubbles went unanswered {when}. Check the Bubbles sidebar more often, or lend more house keys."
    chatty = max(payload["residents"], key=lambda r: r["bubbles"], default=None)
    if chatty and chatty["bubbles"] >= 3:
        tips["chatty"] = f"{names.get(chatty['resident'], chatty['resident'])} keeps asking ({chatty['bubbles']} bubbles). Clearer task descriptions or more house keys would help."
    rate = island["tests_pass_rate"]
    if rate is not None and rate < 0.7 and sum(b["visits"] for b in payload["buildings"] if b["type"] == "lab") >= 3:
        tips["lab"] = f"The Lab fails {round((1 - rate) * 100)}% of its runs. A more careful coder might help."
    if island["tasks_done"] > island["tasks_done_before"]:
        tips["humming"] = f"{island['tasks_done']} packages delivered {when}, up from {island['tasks_done_before']}. The island is humming."
    tips["steady"] = f"A steady stretch {when}. Nothing needs fixing right now."
    return tips


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
