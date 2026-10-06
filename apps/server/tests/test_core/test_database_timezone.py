"""PostgreSQL connections must preserve the application's naive-UTC contract."""

from __future__ import annotations

import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio as sqlalchemy_asyncio
from sqlalchemy import Column, DateTime, Integer, MetaData, Table, select, text

DATABASE_MODULE_PATH = Path(__file__).resolve().parents[2] / "database.py"


class _FakeAsyncSessionmaker:
    def __init__(self, *args, **kwargs):
        pass

    def __class_getitem__(cls, item):
        return cls


def _load_database_module(monkeypatch, database_url: str, *, fake_engines: bool):
    calls: dict[str, dict] = {}
    monkeypatch.setenv("DATABASE_URL", database_url)

    if fake_engines:
        def fake_create_engine(url, **kwargs):
            calls["sync"] = {"url": str(url), **kwargs}
            return object()

        def fake_create_async_engine(url, **kwargs):
            calls["async"] = {"url": str(url), **kwargs}
            return object()

        monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)
        monkeypatch.setattr(sqlalchemy_asyncio, "create_async_engine", fake_create_async_engine)
        monkeypatch.setattr(sqlalchemy_asyncio, "async_sessionmaker", _FakeAsyncSessionmaker)

    module_name = f"database_timezone_probe_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, DATABASE_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


def test_postgres_sync_constructor_preserves_existing_libpq_options(monkeypatch):
    database_url = (
        "postgresql://user:pass@db.example:5432/zenstory"
        "?options=-c%20statement_timeout%3D5000"
    )

    _, calls = _load_database_module(monkeypatch, database_url, fake_engines=True)

    sync_connect_args = calls["sync"]["connect_args"]
    assert "statement_timeout=5000" in sync_connect_args["options"]
    assert "timezone=UTC" in sync_connect_args["options"]


def test_postgres_engine_constructors_pin_utc_without_discarding_url_settings(monkeypatch):
    database_url = "postgresql://user:pass@db.example:5432/zenstory?application_name=zenstory"

    _, calls = _load_database_module(monkeypatch, database_url, fake_engines=True)

    assert "timezone=UTC" in calls["sync"]["connect_args"]["options"]
    async_connect_args = calls["async"]["connect_args"]
    assert async_connect_args["server_settings"]["timezone"] == "UTC"
    assert "application_name=zenstory" in calls["sync"]["url"]
    assert "application_name=zenstory" in calls["async"]["url"]


@pytest.mark.asyncio
async def test_postgres_engines_use_utc_and_sync_naive_timestamp_round_trips_as_utc(monkeypatch):
    postgres_url = os.getenv("ZENSTORY_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("isolated PostgreSQL URL not configured")

    module, _ = _load_database_module(monkeypatch, postgres_url, fake_engines=False)
    assert module.async_engine is not None

    metadata = MetaData()
    timestamp_table = Table(
        f"zs_timezone_{uuid4().hex[:12]}",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("recorded_at", DateTime(timezone=False), nullable=False),
    )
    utc_value = datetime(2026, 9, 30, 16, tzinfo=UTC)

    try:
        with module.sync_engine.begin() as connection:
            assert connection.scalar(text("SELECT current_setting('TimeZone')")) == "UTC"
            timestamp_table.create(connection)
            connection.execute(timestamp_table.insert().values(id=1, recorded_at=utc_value))

        with module.sync_engine.connect() as connection:
            stored = connection.scalar(select(timestamp_table.c.recorded_at).where(timestamp_table.c.id == 1))
        assert stored == utc_value.replace(tzinfo=None)

        async with module.async_engine.connect() as connection:
            assert await connection.scalar(text("SELECT current_setting('TimeZone')")) == "UTC"
    finally:
        with module.sync_engine.begin() as connection:
            timestamp_table.drop(connection, checkfirst=True)
        module.sync_engine.dispose()
        await module.async_engine.dispose()
