"""PostgreSQL 连接池参数：同步与异步 engine 都必须开启 pool_pre_ping。

database.py 在 import 时按 DATABASE_URL 选择后端。这里把它当作独立模块重新
执行一次（不影响测试进程里已经导入的 database 模块），把 DATABASE_URL 指向
PostgreSQL，并截获两个 engine 工厂收到的参数。
"""

import importlib.util
from pathlib import Path

import sqlalchemy
import sqlalchemy.ext.asyncio as sqlalchemy_asyncio

DATABASE_MODULE_PATH = Path(__file__).resolve().parents[2] / "database.py"


def _load_database_module_for_postgres(monkeypatch):
    calls: dict[str, dict] = {}

    def fake_create_engine(url, **kwargs):
        calls["sync"] = {"url": url, **kwargs}
        return object()

    def fake_create_async_engine(url, **kwargs):
        calls["async"] = {"url": url, **kwargs}
        return object()

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example:5432/zenstory")
    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)
    monkeypatch.setattr(sqlalchemy_asyncio, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(sqlalchemy_asyncio, "async_sessionmaker", lambda *a, **k: object())

    spec = importlib.util.spec_from_file_location("database_pg_pool_probe", DATABASE_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


def test_postgres_sync_and_async_engines_enable_pool_pre_ping(monkeypatch):
    module, calls = _load_database_module_for_postgres(monkeypatch)

    assert module.is_postgres is True
    assert calls["sync"]["url"].startswith("postgresql+psycopg://")
    assert calls["async"]["url"].startswith("postgresql+asyncpg://")
    assert calls["sync"]["pool_pre_ping"] is True
    assert calls["async"]["pool_pre_ping"] is True
    # 其余池参数保持原值，避免这次改动顺带改变容量。
    for engine_kwargs in (calls["sync"], calls["async"]):
        assert engine_kwargs["pool_size"] == 10
        assert engine_kwargs["max_overflow"] == 20
        assert engine_kwargs["pool_timeout"] == 30
        assert engine_kwargs["pool_recycle"] == 1800
