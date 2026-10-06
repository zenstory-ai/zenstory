"""
数据库会话管理

提供 Prefect 任务中使用的数据库会话上下文管理器
"""
import os
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlmodel import Session

# 加载 .env 文件（Prefect worker 不会自动加载）
load_dotenv()

# 创建专用于 Prefect flows 的数据库引擎
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./zenstory.db")
is_postgres = DATABASE_URL.startswith("postgresql")

# 拆解流程可运行数十分钟：取连接前探活，并在服务端/代理断开空闲连接之前
# 主动回收（与 API 端 database.py 的 pool_recycle 一致）。
POSTGRES_POOL_RECYCLE_SECONDS = 1800


def _build_engine(database_url: str):
    if database_url.startswith("postgresql"):
        # PostgreSQL - use psycopg3 (psycopg) driver
        pg_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return create_engine(
            pg_url,
            pool_size=5,
            max_overflow=0,
            pool_pre_ping=True,
            pool_recycle=POSTGRES_POOL_RECYCLE_SECONDS,
        )

    # SQLite with WAL mode and timeout for better concurrency
    engine = create_engine(
        database_url,
        connect_args={
            "check_same_thread": False,
            "timeout": 30,
        },
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return engine


_prefect_engine = _build_engine(DATABASE_URL)


@contextmanager
def get_prefect_db_session():
    """为 Prefect flows 提供数据库会话"""
    with Session(_prefect_engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


# 别名，兼容旧代码
get_db_session = get_prefect_db_session
