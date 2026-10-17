from datetime import timedelta

from conftest import db_test

from mii import events, insights
from mii.insights import island_insights
from mii.models import Step, Task, utcnow

pytestmark = db_test

SECTIONS = {"range", "island", "per_day", "tip", "residents", "brains", "buildings"}
ISLAND_FIELDS = {
    "tasks_done", "tasks_failed", "prs_opened", "avg_issue_to_pr_seconds", "tasks_done_before", "prs_opened_before",
    "prs_merged", "answer_seconds", "answer_seconds_before", "bubbles_answered", "keys_lent", "tests_pass_rate", "flaky_tests",
}


async def test_insights_has_every_section_and_counts_tasks(island):
    t0 = utcnow() - timedelta(minutes=5)
    t1 = await Task.objects.acreate(id="t1", island_id="sunny", title="A", status="done", pr_url="https://example.com/pr/1",
                                    created_at=t0, finished_at=t0 + timedelta(seconds=20))
    await Task.objects.acreate(id="t2", island_id="sunny", title="B", status="failed", finished_at=utcnow())
    for i, (building, outcome, who, brain, secs) in enumerate([
        ("workshop", "pass", "carlos", "claude-code", 10), ("lab", "fail", "carlos", "claude-code", 4),
        ("workshop", "pass", "carlos", "claude-code", 6), ("lab", "pass", "carlos", "claude-code", 4),
        ("courthouse", "pass", "sydney", "codex", 20),
    ]):
        await Step.objects.acreate(island_id="sunny", task_id=t1.id, building_id=building, building_type=building,
                                   resident_id=who, brain=brain, outcome=outcome, bubbles_raised=i % 2,
                                   started_at=t0, finished_at=t0 + timedelta(seconds=secs))

    data = await island_insights("sunny", "week")

    assert set(data) == SECTIONS and data["range"] == "week"
    assert set(data["island"]) == ISLAND_FIELDS
    assert data["island"] | {"tasks_done": 1, "tasks_failed": 1, "prs_opened": 1, "avg_issue_to_pr_seconds": 20.0} == data["island"]
    assert data["island"]["tasks_done_before"] == 0 and data["island"]["prs_merged"] == 0
    assert data["island"]["tests_pass_rate"] == 0.5 and data["island"]["flaky_tests"] == 1  # lab failed, then passed
    assert len(data["per_day"]) == 7 and data["per_day"][-1]["done"] == 1 and sum(d["done"] for d in data["per_day"]) == 1
    carlos = next(r for r in data["residents"] if r["resident"] == "carlos")
    assert carlos == {"resident": "carlos", "brain": "claude-code", "steps": 4, "pass_rate": 0.75, "avg_seconds": 6.0, "bubbles": 2}
    lab = next(b for b in data["buildings"] if b["building"] == "lab")
    assert lab == {"building": "lab", "type": "lab", "visits": 2, "fail_rate": 0.5}
    codex_review = next(b for b in data["brains"] if b["brain"] == "codex")
    assert codex_review["building_type"] == "courthouse" and codex_review["pass_rate"] == 1.0
    assert "Lab" in data["tip"]  # the lab bounces half its packages


async def test_insights_ranges_and_previous_range(island):
    now = utcnow()
    await Task.objects.acreate(id="old", island_id="sunny", title="Old", status="done", finished_at=now - timedelta(days=10))
    await Task.objects.acreate(id="new", island_id="sunny", title="New", status="done", finished_at=now)
    week = (await island_insights("sunny", "week"))["island"]
    assert (week["tasks_done"], week["tasks_done_before"]) == (1, 1)
    month = await island_insights("sunny", "month")
    assert month["island"]["tasks_done"] == 2 and len(month["per_day"]) == 30
    assert "up from" in month["tip"] or "steady" in month["tip"]
    assert len((await island_insights("sunny", "today"))["per_day"]) == 1


async def test_insights_counts_answered_bubbles_and_keys_lent(island):
    bus = events.EventBus()
    bubbles = events.Bubbles(bus)

    async def ask_and_answer(kind, response):
        import asyncio

        asking = asyncio.create_task(bubbles.ask("sunny", "carlos", kind, "?", 5, ["Allow once", "Reject"]))
        while not bubbles.open:
            await asyncio.sleep(0.01)
        if response is not None:
            bubbles.answer(next(iter(bubbles.open)), response)
        return await asking

    assert await ask_and_answer("permission", "Allow once") == "Allow once"
    assert await ask_and_answer("permission", "Reject") == "Reject"
    assert await ask_and_answer("confused", "the auth tests") == "the auth tests"
    bubbles_timeout = events.Bubbles(bus)
    assert await bubbles_timeout.ask("sunny", "carlos", "tired", "zzz", timeout=0.01) is None

    island_stats = (await island_insights("sunny", "today"))["island"]
    assert island_stats["bubbles_answered"] == 3 and island_stats["keys_lent"] == 1
    assert island_stats["answer_seconds"] is not None and island_stats["answer_seconds"] < 5
    assert island_stats["answer_seconds_before"] is None


async def test_insights_asks_gh_about_open_prs(island, monkeypatch):
    now = utcnow()
    await Task.objects.acreate(id="pr", island_id="sunny", title="PR", status="done", pr_url="https://github.com/o/r/pull/1", finished_at=now)
    monkeypatch.setattr(insights.github, "pr_merged_at", lambda url: "2026-10-17T10:00:00Z" if url.endswith("/1") else None)
    assert (await island_insights("sunny", "month"))["island"]["prs_merged"] == 1
    assert (await Task.objects.aget(pk="pr")).merged_at is not None  # remembered, so gh isn't asked again


async def test_insights_with_no_data(island):
    data = await island_insights("sunny")
    assert data["island"]["avg_issue_to_pr_seconds"] is None and data["island"]["tests_pass_rate"] is None
    assert data["residents"] == [] and data["brains"] == [] and data["buildings"] == []
    assert "No packages" in data["tip"]
