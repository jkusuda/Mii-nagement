from datetime import timedelta

from conftest import db_test

from mii.insights import island_insights
from mii.models import Step, Task, utcnow

pytestmark = db_test


async def test_insights_has_every_section_and_counts_tasks(island):
    t0 = utcnow()
    t1 = await Task.objects.acreate(id="t1", island_id="sunny", title="A", status="done", pr_url="https://example.com/pr/1", created_at=t0)
    await Task.objects.acreate(id="t2", island_id="sunny", title="B", status="failed")
    for i, (building, outcome, who, brain, secs) in enumerate([
        ("workshop", "pass", "carlos", "claude-code", 10), ("lab", "fail", "carlos", "claude-code", 4),
        ("workshop", "pass", "carlos", "claude-code", 6), ("lab", "pass", "carlos", "claude-code", 4),
        ("courthouse", "pass", "sydney", "codex", 20),
    ]):
        await Step.objects.acreate(island_id="sunny", task_id=t1.id, building_id=building, building_type=building,
                                   resident_id=who, brain=brain, outcome=outcome, bubbles_raised=i % 2,
                                   started_at=t0, finished_at=t0 + timedelta(seconds=secs))

    insights = await island_insights("sunny")

    assert set(insights) == {"island", "residents", "brains", "buildings"}
    assert insights["island"] == {"tasks_done": 1, "tasks_failed": 1, "prs_opened": 1, "avg_issue_to_pr_seconds": 20.0}
    carlos = next(r for r in insights["residents"] if r["resident"] == "carlos")
    assert carlos == {"resident": "carlos", "brain": "claude-code", "steps": 4, "pass_rate": 0.75, "avg_seconds": 6.0, "bubbles": 2}
    lab = next(b for b in insights["buildings"] if b["building"] == "lab")
    assert lab == {"building": "lab", "type": "lab", "visits": 2, "fail_rate": 0.5}
    codex_review = next(b for b in insights["brains"] if b["brain"] == "codex")
    assert codex_review["building_type"] == "courthouse" and codex_review["pass_rate"] == 1.0


async def test_insights_with_no_data(island):
    insights = await island_insights("sunny")
    assert insights["island"]["avg_issue_to_pr_seconds"] is None
    assert insights["residents"] == [] and insights["brains"] == [] and insights["buildings"] == []
