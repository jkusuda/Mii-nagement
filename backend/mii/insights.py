"""Insights: plain stats about how the island is working, for the Insights screen.

Everything comes from the Step table (one row per building visit) plus Task.
Served at GET /islands/{id}/insights.
"""

from sqlmodel import Session, select

from mii.models import Step, Task


def island_insights(session: Session, island_id: str) -> dict:
    """The full insights payload. Each section is filled in by one helper below."""
    steps = session.exec(select(Step).where(Step.island_id == island_id)).all()
    tasks = session.exec(select(Task).where(Task.island_id == island_id)).all()
    return {
        "island": island_summary(tasks),
        "residents": by_resident(steps),
        "brains": by_brain(steps),
        "buildings": by_building(steps),
    }


def island_summary(tasks: list[Task]) -> dict:
    # TODO(mvp): also the average time from issue to PR.
    return {
        "tasks_done": sum(t.status == "done" for t in tasks),
        "tasks_failed": sum(t.status == "failed" for t in tasks),
        "prs_opened": sum(t.pr_url is not None for t in tasks),
    }


def by_resident(steps: list[Step]) -> list[dict]:
    # TODO(mvp): per resident: steps, pass rate, average step time, bubbles raised.
    return []


def by_brain(steps: list[Step]) -> list[dict]:
    # TODO(mvp): per brain and building type: pass rate and average step time,
    # e.g. "Codex passes review 80% of the time; Claude Code is fastest in the Workshop".
    return []


def by_building(steps: list[Step]) -> list[dict]:
    # TODO(mvp): per building: visits and fail rate (where packages bounce back most).
    return []
