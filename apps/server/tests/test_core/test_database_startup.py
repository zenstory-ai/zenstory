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
        # Bounded lock waits: schema creation first, then a shorter one for every optional index.
        f"SET LOCAL lock_timeout = '{database.STARTUP_SCHEMA_LOCK_TIMEOUT_MS}ms'",
        f"SET LOCAL lock_timeout = '{database.STARTUP_INDEX_LOCK_TIMEOUT_MS}ms'",
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


@pytest.mark.asyncio
async def test_init_db_skips_an_index_whose_table_is_locked_instead_of_waiting(monkeypatch):
    """2026-10-20: a startup CREATE INDEX queued behind a long-held lock never finished, failed the
    healthcheck, and made every later write to that table wait behind it."""
    import asyncio

    postgres_url = os.getenv("ZENSTORY_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("isolated PostgreSQL URL not configured")

    async_url = postgres_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_url, pool_pre_ping=True)
    suffix = uuid4().hex[:12]
    locked_table = f"zs_locked_{suffix}"
    free_table = f"zs_free_{suffix}"
    locked_index = f"ix_zs_locked_{suffix}"
    free_index = f"ix_zs_free_{suffix}"
    metadata = MetaData()
    for name in (locked_table, free_table):
        Table(name, metadata, Column("id", Integer, primary_key=True), Column("value", Integer))
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    monkeypatch.setattr(database, "is_postgres", True)
    monkeypatch.setattr(database, "async_engine", engine)
    monkeypatch.setattr(database.SQLModel, "metadata", metadata)
    monkeypatch.setattr(database, "STARTUP_INDEX_LOCK_TIMEOUT_MS", 300)
    monkeypatch.setattr(
        database,
        "COMMON_PERFORMANCE_INDEX_SQL",
        (f"CREATE INDEX IF NOT EXISTS {locked_index} ON {locked_table} (value)",),
    )
    monkeypatch.setattr(
        database,
        "POSTGRES_PERFORMANCE_INDEX_SQL",
        (f"CREATE INDEX IF NOT EXISTS {free_index} ON {free_table} (value)",),
    )

    holder = await engine.connect()
    holder_tx = await holder.begin()
    try:
        # An open write transaction holds ROW EXCLUSIVE on the table, as a stuck request did.
        await holder.execute(text(f"INSERT INTO {locked_table} (value) VALUES (1)"))

        await asyncio.wait_for(database.init_db(), timeout=15)

        async with engine.connect() as conn:
            objects = {
                name: await conn.scalar(text("SELECT to_regclass(:name)"), {"name": name})
                for name in (locked_index, free_index)
            }
        assert objects == {locked_index: None, free_index: free_index}
    finally:
        await holder_tx.rollback()
        await holder.close()
        async with engine.begin() as conn:
            for name in (locked_table, free_table):
                await conn.execute(text(f"DROP TABLE IF EXISTS {name} CASCADE"))
        await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_fails_fast_when_a_new_tables_reference_is_locked(monkeypatch):
    """A new table's foreign key locks the table it references: startup must fail within the bound
    (the old deployment keeps serving) instead of waiting, and queueing writers, indefinitely."""
    import asyncio

    from sqlalchemy import ForeignKey
    from sqlalchemy.exc import DBAPIError

    postgres_url = os.getenv("ZENSTORY_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("isolated PostgreSQL URL not configured")

    async_url = postgres_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_url, pool_pre_ping=True)
    suffix = uuid4().hex[:12]
    parent_table = f"zs_parent_{suffix}"
    child_table = f"zs_child_{suffix}"
    existing = MetaData()
    Table(parent_table, existing, Column("id", Integer, primary_key=True))
    async with engine.begin() as conn:
        await conn.run_sync(existing.create_all)
    with_new_table = MetaData()
    Table(parent_table, with_new_table, Column("id", Integer, primary_key=True))
    Table(
        child_table,
        with_new_table,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey(f"{parent_table}.id")),
    )

    monkeypatch.setattr(database, "is_postgres", True)
    monkeypatch.setattr(database, "async_engine", engine)
    monkeypatch.setattr(database.SQLModel, "metadata", with_new_table)
    monkeypatch.setattr(database, "STARTUP_SCHEMA_LOCK_TIMEOUT_MS", 300)
    monkeypatch.setattr(database, "COMMON_PERFORMANCE_INDEX_SQL", ())
    monkeypatch.setattr(database, "POSTGRES_PERFORMANCE_INDEX_SQL", ())

    holder = await engine.connect()
    holder_tx = await holder.begin()
    try:
        await holder.execute(text(f"INSERT INTO {parent_table} (id) VALUES (1)"))

        with pytest.raises(DBAPIError):
            await asyncio.wait_for(database.init_db(), timeout=15)
    finally:
        await holder_tx.rollback()
        await holder.close()
        async with engine.begin() as conn:
            for name in (child_table, parent_table):
                await conn.execute(text(f"DROP TABLE IF EXISTS {name} CASCADE"))
        await engine.dispose()
