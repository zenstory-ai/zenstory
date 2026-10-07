"""PostgreSQL startup DDL transaction regressions."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, text
from sqlalchemy.ext.asyncio import create_async_engine

import database


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _RecordingConnection:
    def __init__(self):
        self.executed: list[str] = []
        self.nested_transactions = 0

    async def run_sync(self, _callable):
        return None

    def begin_nested(self):
        self.nested_transactions += 1
        return _AsyncContext(None)

    async def execute(self, statement):
        sql = str(statement)
        self.executed.append(sql)
        if "broken_middle" in sql:
            raise RuntimeError("deliberate index failure")


class _RecordingEngine:
    def __init__(self, connection: _RecordingConnection):
        self.connection = connection

    def begin(self):
        return _AsyncContext(self.connection)


@pytest.mark.asyncio
async def test_init_db_isolates_each_optional_postgres_index(monkeypatch):
    connection = _RecordingConnection()
    monkeypatch.setattr(database, "is_postgres", True)
    monkeypatch.setattr(database, "async_engine", _RecordingEngine(connection))
    monkeypatch.setattr(
        database,
        "COMMON_PERFORMANCE_INDEX_SQL",
        ("CREATE INDEX prior_index", "CREATE INDEX broken_middle"),
    )
    monkeypatch.setattr(database, "POSTGRES_PERFORMANCE_INDEX_SQL", ("CREATE INDEX later_index",))

    await database.init_db()

    assert connection.executed == [
        "CREATE INDEX prior_index",
        "CREATE INDEX broken_middle",
        "CREATE INDEX later_index",
    ]
    assert connection.nested_transactions == 3


@pytest.mark.asyncio
async def test_init_db_commits_valid_ddl_around_a_broken_postgres_index(monkeypatch):
    postgres_url = os.getenv("ZENSTORY_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("isolated PostgreSQL URL not configured")

    async_url = postgres_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_url, pool_pre_ping=True)
    suffix = uuid4().hex[:12]
    table_name = f"zs_init_db_{suffix}"
    prior_index = f"ix_zs_prior_{suffix}"
    broken_index = f"ix_zs_broken_{suffix}"
    later_index = f"ix_zs_later_{suffix}"
    metadata = MetaData()
    Table(
        table_name,
        metadata,
        Column("id", Integer, primary_key=True),
        Column("prior_value", Integer),
        Column("later_value", Integer),
    )

    monkeypatch.setattr(database, "is_postgres", True)
    monkeypatch.setattr(database, "async_engine", engine)
    monkeypatch.setattr(database.SQLModel, "metadata", metadata)
    monkeypatch.setattr(
        database,
        "COMMON_PERFORMANCE_INDEX_SQL",
        (f"CREATE INDEX {prior_index} ON {table_name} (prior_value)",),
    )
    monkeypatch.setattr(
        database,
        "POSTGRES_PERFORMANCE_INDEX_SQL",
        (
            f"CREATE INDEX {broken_index} ON missing_{table_name} (id)",
            f"CREATE INDEX {later_index} ON {table_name} (later_value)",
        ),
    )

    try:
        await database.init_db()
        async with engine.connect() as conn:
            objects = {
                name: await conn.scalar(text("SELECT to_regclass(:name)"), {"name": name})
                for name in (table_name, prior_index, broken_index, later_index)
            }
        assert objects == {
            table_name: table_name,
            prior_index: prior_index,
            broken_index: None,
            later_index: later_index,
        }
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
        await engine.dispose()
