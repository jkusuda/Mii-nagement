"""Tiger Data metrics (metrics.py): a no-op on SQLite, the continuous aggregate on TimescaleDB.

The TimescaleDB test only runs when the whole suite runs on it:
MII_DATABASE_URL=postgres://... uv run pytest tests/test_metrics.py
"""

from datetime import datetime, timedelta

import pytest
from asgiref.sync import sync_to_async
from conftest import db_test
from django.db import connection

from mii import insights, metrics
from mii.insights import island_insights
from mii.models import Step, Task, utcnow

pytestmark = db_test


async def done_task(task_id: str = "t1") -> Task:
    now = utcnow()
    return await Task.objects.acreate(
        id=task_id, island_id="sunny", title="A", status="done", created_at=now - timedelta(seconds=30), finished_at=now
    )


async def a_step(task_id: str = "t1") -> Step:
    now = utcnow()
    return await Step.objects.acreate(
        island_id="sunny", task_id=task_id, building_id="lab", building_type="lab", resident_id="carlos",
        brain="mock", outcome="pass", started_at=now - timedelta(seconds=5), finished_at=now,
    )  # fmt: skip


@pytest.mark.skipif(connection.vendor == "postgresql", reason="the SQLite path")
async def test_sqlite_is_a_no_op_and_insights_fall_back(island):
    assert await sync_to_async(metrics.detect)() == "sqlite"
    assert metrics.database() == "sqlite" and not metrics.enabled()
    task = await done_task()
    await metrics.record_task(task)  # nothing to write to, nothing raised
    await metrics.record_step(await a_step())
    start = utcnow() - timedelta(days=6)
    assert await metrics.per_day("sunny", start, 7) is None
    payload = await island_insights("sunny", "week")
    today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    assert payload["per_day"] == insights.per_day([task], today - timedelta(days=6), 7)
    assert payload["per_day"][-1]["done"] == 1


@pytest.mark.skipif(connection.vendor != "postgresql", reason="needs MII_DATABASE_URL pointing at TimescaleDB")
async def test_timescale_records_and_counts_per_day(island):
    if await sync_to_async(metrics.detect)() != "timescale":
        pytest.skip("Postgres without the timescaledb extension")
    task = await done_task()
    await metrics.record_step(await a_step())
    await metrics.record_task(task)
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
    days = await metrics.per_day("sunny", start, 7)
    assert days is not None and len(days) == 7 and days[-1]["done"] == 1
