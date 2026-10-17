# The Tiger Data metrics (metrics.py): a TimescaleDB hypertable plus a daily continuous
# aggregate. Only on Postgres with the timescaledb extension; SQLite and plain Postgres skip it.
# No Django model on purpose: a hypertable without a primary key does not fit the ORM.
# Not atomic: Timescale refuses to create a continuous aggregate inside a transaction.

import logging

from django.db import migrations

log = logging.getLogger(__name__)

STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS metrics (
        time TIMESTAMPTZ NOT NULL,
        island_id TEXT,
        kind TEXT,
        task_id TEXT,
        building_type TEXT,
        resident_id TEXT,
        brain TEXT,
        outcome TEXT,
        seconds DOUBLE PRECISION,
        flaky BOOL,
        timed_out BOOL
    ) WITH (tsdb.hypertable, tsdb.segmentby = 'island_id', tsdb.orderby = 'time DESC')""",
    """CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_daily
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT time_bucket('1 day', time) AS day, island_id, kind, brain, building_type, outcome,
               count(*) AS n, avg(seconds) AS avg_seconds
        FROM metrics
        GROUP BY 1, 2, 3, 4, 5, 6
        WITH NO DATA""",
    """SELECT add_continuous_aggregate_policy('metrics_daily',
        start_offset => INTERVAL '40 days', end_offset => NULL,
        schedule_interval => INTERVAL '1 minute', if_not_exists => true)""",
]


def create_metrics(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        try:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        except Exception as e:  # plain Postgres: no metrics table, insights read the Task table
            log.warning("no TimescaleDB extension (%s); skipping the metrics hypertable", e)
            return
        for statement in STATEMENTS:
            cursor.execute(statement)


def drop_metrics(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("DROP MATERIALIZED VIEW IF EXISTS metrics_daily")
        cursor.execute("DROP TABLE IF EXISTS metrics")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("mii", "0014_attachment"),
    ]

    operations = [
        migrations.RunPython(create_metrics, drop_metrics),
    ]
