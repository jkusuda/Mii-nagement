from datetime import datetime, timedelta

import pytest
from conftest import db_test
from django.test import AsyncClient

from mii.insights import InsightsError, island_insights, range_bounds
from mii.models import Event, Step, Task

pytestmark = db_test

NOW = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)  # midday, local time


async def _task(task_id: str, status: str, finished: datetime, pr_url: str | None = None, created: datetime | None = None):
    """A task plus the `task` event that finished it (what the engine stores)."""
    created = created or finished - timedelta(seconds=20)
    await Task.objects.acreate(id=task_id, island_id="sunny", title=task_id, status=status, pr_url=pr_url, created_at=created)
    await Event.objects.acreate(island_id="sunny", type="task", created_at=finished,
                                payload={"type": "task", "id": task_id, "status": status, "pr_url": pr_url})


async def _bubble(bubble_id: str, kind: str, shown: datetime, answer: str | None, after_seconds: int):
    await Event.objects.acreate(island_id="sunny", type="bubble", created_at=shown,
                                payload={"type": "bubble", "id": bubble_id, "kind": kind, "agent": "carlos", "text": "?"})
    await Event.objects.acreate(island_id="sunny", type="bubble_resolved", created_at=shown + timedelta(seconds=after_seconds),
                                payload={"type": "bubble_resolved", "id": bubble_id, "answer": answer})


async def test_insights_has_every_section_and_counts_tasks(island):
    t0 = NOW - timedelta(hours=1)
    t1 = await Task.objects.acreate(id="t1", island_id="sunny", title="A", status="done", pr_url="https://example.com/pr/1", created_at=t0)
    await Task.objects.acreate(id="t2", island_id="sunny", title="B", status="failed", created_at=t0)
    await Step.objects.acreate(island_id="sunny", task_id="t2", building_id="lab", building_type="lab", resident_id="carlos",
                               brain="claude-code", outcome="fail", started_at=t0, finished_at=t0 + timedelta(seconds=3))
    for i, (building, outcome, who, brain, secs) in enumerate([
        ("workshop", "pass", "carlos", "claude-code", 10), ("lab", "fail", "carlos", "claude-code", 4),
        ("workshop", "pass", "carlos", "claude-code", 6), ("lab", "pass", "carlos", "claude-code", 4),
        ("courthouse", "pass", "sydney", "codex", 20),
    ]):
        await Step.objects.acreate(island_id="sunny", task_id=t1.id, building_id=building, building_type=building,
                                   resident_id=who, brain=brain, outcome=outcome, bubbles_raised=i % 2,
                                   started_at=t0, finished_at=t0 + timedelta(seconds=secs))

    insights = await island_insights("sunny", "week", now=NOW)

    assert set(insights) == {"range", "island", "per_day", "tip", "residents", "brains", "buildings"}
    island_stats = insights["island"]
    assert (island_stats["tasks_done"], island_stats["tasks_failed"], island_stats["prs_opened"]) == (1, 1, 1)
    assert island_stats["avg_issue_to_pr_seconds"] == 20.0  # no events: the last step's finish counts
    assert island_stats["tests_pass_rate"] == 0.33  # 1 of 3 lab runs passed
    carlos = next(r for r in insights["residents"] if r["resident"] == "carlos")
    assert carlos == {"resident": "carlos", "brain": "claude-code", "steps": 5, "pass_rate": 0.6, "avg_seconds": 5.4, "bubbles": 2}
    lab = next(b for b in insights["buildings"] if b["building"] == "lab")
    assert lab == {"building": "lab", "type": "lab", "visits": 3, "fail_rate": 0.67}
    codex_review = next(b for b in insights["brains"] if b["brain"] == "codex")
    assert codex_review["building_type"] == "courthouse" and codex_review["pass_rate"] == 1.0
    assert insights["tip"].startswith("Packages bounce back at the Lab")


async def test_ranges_and_previous_range(island):
    await _task("today", "done", NOW - timedelta(hours=1), pr_url="https://example.com/pr/1")
    await _task("yesterday", "done", NOW - timedelta(days=1))
    await _task("last_week", "done", NOW - timedelta(days=9))
    await _task("long_ago", "done", NOW - timedelta(days=90))
    await _task("failed_today", "failed", NOW - timedelta(minutes=5))

    today = (await island_insights("sunny", "today", now=NOW))["island"]
    assert (today["tasks_done"], today["tasks_done_before"], today["tasks_failed"]) == (1, 1, 1)
    assert (today["prs_opened"], today["prs_opened_before"]) == (1, 0)

    week = await island_insights("sunny", "week", now=NOW)
    assert (week["island"]["tasks_done"], week["island"]["tasks_done_before"]) == (2, 1)
    assert [d["done"] for d in week["per_day"]] == [0, 0, 0, 0, 0, 1, 1]
    assert week["per_day"][-1]["day"] == NOW.date().isoformat()
    assert week["tip"] == "1 more package delivered than last week. Nice work!"

    month = await island_insights("sunny", "month", now=NOW)
    assert month["island"]["tasks_done"] == 3 and len(month["per_day"]) == 30

    with pytest.raises(InsightsError):
        await island_insights("sunny", "year")


async def test_bubbles_answer_time_and_keys_lent(island):
    await _bubble("b1", "confused", NOW - timedelta(hours=2), "Use tests/test_login.py", 60)
    await _bubble("b2", "permission", NOW - timedelta(hours=1), "Allow once", 120)
    await _bubble("b3", "permission", NOW - timedelta(minutes=30), "Reject", 30)
    await _bubble("b4", "permission", NOW - timedelta(minutes=10), None, 300)  # timed out: not answered
    await _bubble("b5", "confused", NOW - timedelta(days=1, hours=1), "ok", 30)  # yesterday

    today = (await island_insights("sunny", "today", now=NOW))["island"]
    assert today["bubbles_answered"] == 3 and today["keys_lent"] == 1
    assert today["answer_seconds"] == 70.0 and today["answer_seconds_before"] == 30.0


async def test_insights_with_no_data(island):
    insights = await island_insights("sunny", "today", now=NOW)
    assert insights["island"]["avg_issue_to_pr_seconds"] is None
    assert insights["island"]["answer_seconds"] is None and insights["island"]["tests_pass_rate"] is None
    assert insights["per_day"] == [{"day": NOW.date().isoformat(), "done": 0}] and insights["tip"] is None
    assert insights["residents"] == [] and insights["brains"] == [] and insights["buildings"] == []


def test_range_bounds_start_at_local_midnight():
    start, end, before = range_bounds("week", NOW)
    assert (start.hour, start.minute) == (0, 0) and end == NOW
    assert (end - start).days == 6 and (start - before).days == 7


async def test_insights_endpoint_rejects_unknown_range(island):
    client = AsyncClient()
    assert (await client.get("/islands/sunny/insights?range=today")).json()["range"] == "today"
    assert (await client.get("/islands/sunny/insights?range=year")).status_code == 400
