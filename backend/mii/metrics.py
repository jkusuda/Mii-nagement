"""Tiger Data metrics: one row per finished step and task in a TimescaleDB hypertable.

Only when MII_DATABASE_URL points at Postgres with the timescaledb extension (migration
0015_metrics made `metrics` and the daily continuous aggregate `metrics_daily`).
Everywhere else every function here is a no-op and insights.py counts from the Task table.
Raw SQL through connection.cursor(): the hypertable has no Django model on purpose.
"""

import logging
from datetime import datetime, timedelta

from asgiref.sync import sync_to_async
from django.db import connection

from mii.models import Step, Task

log = logging.getLogger(__name__)

_database: str | None = None  # "sqlite" | "postgres" | "timescale", set by detect()

INSERT = (
    "INSERT INTO metrics (time, island_id, kind, task_id, building_type, resident_id, brain, outcome, "
    "seconds, flaky, timed_out) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
PER_DAY = (
    "SELECT day, sum(n) FROM metrics_daily WHERE island_id = %s AND kind = 'task' AND outcome = 'done' "
    "AND day >= %s GROUP BY day"
)


def detect() -> str:
    """Which database this is; runtime.start() calls it once, after migrate. Sync."""
    global _database
    if connection.vendor != "postgresql":
        _database = "sqlite"
    else:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('metrics') IS NOT NULL")
            _database = "timescale" if cursor.fetchone()[0] else "postgres"
    return _database


def database() -> str:
    """For /health. Before detect() ran, only the vendor is known."""
    return _database or ("postgres" if connection.vendor == "postgresql" else "sqlite")


def enabled() -> bool:
    return _database == "timescale"


def _insert(row: tuple) -> None:
    with connection.cursor() as cursor:
        cursor.execute(INSERT, row)


async def _record(row: tuple) -> None:
    try:
        await sync_to_async(_insert)(row)
    except Exception as e:  # metrics never stop the engine
        log.warning("could not record a metric: %s", e)


async def record_step(step: Step) -> None:
    """A building visit finished."""
    if enabled():
        await _record(
            (step.finished_at, step.island_id, "step", step.task_id, step.building_type, step.resident_id,
             step.brain, step.outcome, step.seconds, step.flaky, step.timed_out)
        )  # fmt: skip


async def record_task(task: Task) -> None:
    """A task reached its final status (done / failed)."""
    if enabled() and task.finished_at:
        seconds = (task.finished_at - task.created_at).total_seconds()
        await _record(
            (task.finished_at, task.island_id, "task", task.id, None, task.agent_id, None, task.status,
             seconds, False, False)
        )  # fmt: skip


def _per_day(island_id: str, start: datetime, days: int) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(PER_DAY, [island_id, start])
        counts = {day.strftime("%Y-%m-%d"): int(n) for day, n in cursor.fetchall()}
    labels = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    return [{"day": label, "done": counts.get(label, 0)} for label in labels]


async def per_day(island_id: str, start: datetime, days: int) -> list[dict] | None:
    """Tasks done per day from the continuous aggregate (UTC days), or None when disabled
    or the query fails: same shape as insights.per_day."""
    if not enabled():
        return None
    try:
        return await sync_to_async(_per_day)(island_id, start, days)
    except Exception as e:
        log.warning("could not read metrics_daily: %s", e)
        return None
