from sqlmodel import Session

from mii import seed
from mii.insights import island_insights
from mii.models import Task, make_engine


def test_insights_has_every_section_and_counts_tasks():
    db = make_engine("sqlite://")
    seed.seed(db)
    with Session(db) as session:
        session.add(Task(id="t1", island_id="sunny", title="A", status="done", pr_url="https://example.com/pr/1"))
        session.add(Task(id="t2", island_id="sunny", title="B", status="failed"))
        session.commit()
        insights = island_insights(session, "sunny")

    assert set(insights) == {"island", "residents", "brains", "buildings"}
    assert insights["island"] == {"tasks_done": 1, "tasks_failed": 1, "prs_opened": 1}
