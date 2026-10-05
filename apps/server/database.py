import logging
import os
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import Session, SQLModel

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# Database URL configuration
# Supports both SQLite (development) and PostgreSQL (production)
DATABASE_URL: str | None = os.getenv("DATABASE_URL")

# Determine if using PostgreSQL or SQLite
is_postgres = bool(DATABASE_URL and DATABASE_URL.startswith("postgresql"))

# Log database configuration
log_with_context(
    logger,
    logging.INFO,
    "Database configuration loaded",
    database_type="PostgreSQL" if is_postgres else "SQLite",
    database_url="***" if DATABASE_URL else "Using default SQLite",
)

# 同步与异步 PostgreSQL engine 共用的连接池参数。pool_pre_ping：PG 重启、
# 故障切换或网络设备清掉闲置 TCP 后，池里的死连接会在借出前被探测并替换，
# 而不是让拿到它的请求直接 OperationalError。SQLite 分支早已开启。
POSTGRES_POOL_OPTIONS: dict[str, int | bool] = {
    "pool_size": 10,
    "max_overflow": 20,
    "pool_timeout": 30,
    "pool_recycle": 1800,
    "pool_pre_ping": True,
}

if is_postgres:
    # PostgreSQL connection (Railway production)
    # Convert postgresql:// to postgresql+asyncpg:// for async support
    assert DATABASE_URL is not None  # For type checker
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    async_engine: AsyncEngine | None = create_async_engine(
        DATABASE_URL,
        echo=False,
        **POSTGRES_POOL_OPTIONS,
    )
    AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    # For sync mode in PostgreSQL, use psycopg3 (psycopg) driver
    sync_url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    sync_engine = create_engine(
        sync_url,
        **POSTGRES_POOL_OPTIONS,
    )
else:
    # SQLite connection (local development)
    if not DATABASE_URL:
        DATABASE_URL = "sqlite:///./zenstory.db"
    # Add timeout and enable WAL mode for better concurrency
    sync_engine = create_engine(
        DATABASE_URL,
        connect_args={
            "check_same_thread": False,
            "timeout": 30,  # Wait up to 30 seconds for lock
        },
        pool_pre_ping=True,
    )
    # Enable WAL mode for better concurrent access
    from sqlalchemy import event
    @event.listens_for(sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")  # 30 seconds
        cursor.close()

    async_engine = None
    AsyncSessionLocal = None


COMMON_PERFORMANCE_INDEX_SQL: tuple[str, ...] = (
    # Hot path for full project tree/list refreshes without parent/type filters
    (
        "CREATE INDEX IF NOT EXISTS ix_file_project_active "
        "ON file (project_id) WHERE is_deleted = false"
    ),
)


POSTGRES_PERFORMANCE_INDEX_SQL: tuple[str, ...] = (
    # Common project ownership checks (hot path across APIs)
    "CREATE INDEX IF NOT EXISTS ix_project_owner_active ON project (owner_id) WHERE is_deleted = false",
    # File list / tree endpoints with parent filter + ordering
    (
        'CREATE INDEX IF NOT EXISTS ix_file_project_parent_order_created_active '
        'ON file (project_id, parent_id, "order", created_at DESC) '
        "WHERE is_deleted = false"
    ),
    # File list endpoint with file_type filter + ordering
    (
        'CREATE INDEX IF NOT EXISTS ix_file_project_type_parent_order_created_active '
        'ON file (project_id, file_type, parent_id, "order", created_at DESC) '
        "WHERE is_deleted = false"
    ),
    # Enforce single active chat session per user+project (when data is clean)
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_session_user_project_active "
        "ON chat_session (user_id, project_id) WHERE is_active = true"
    ),
    # Active session lookup (user + project)
    (
        "CREATE INDEX IF NOT EXISTS ix_chat_session_user_project_active "
        "ON chat_session (user_id, project_id) WHERE is_active = true"
    ),
    # Session message pagination by time
    (
        "CREATE INDEX IF NOT EXISTS ix_chat_message_session_created_at_desc "
        "ON chat_message (session_id, created_at DESC)"
    ),
    # Latest job lookup by novel
    (
        "CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_novel_created_at_desc "
        "ON ingestion_jobs (novel_id, created_at DESC)"
    ),
    # Snapshot list pagination by project (and optional file)
    (
        "CREATE INDEX IF NOT EXISTS ix_snapshot_project_created_at_desc "
        "ON snapshot (project_id, created_at DESC)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_snapshot_project_file_created_at_desc "
        "ON snapshot (project_id, file_id, created_at DESC)"
    ),
)


async def init_db():
    """Initialize database tables."""
    log_with_context(
        logger,
        logging.INFO,
        "Initializing database",
        database_type="PostgreSQL" if is_postgres else "SQLite",
    )

    try:
        if is_postgres and async_engine:
            async with async_engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
                for sql in (*COMMON_PERFORMANCE_INDEX_SQL, *POSTGRES_PERFORMANCE_INDEX_SQL):
                    try:
                        await conn.execute(text(sql))
                    except Exception as index_error:
                        log_with_context(
                            logger,
                            logging.WARNING,
                            "Skipped creating performance index",
                            sql=sql,
                            error=str(index_error),
                            error_type=type(index_error).__name__,
                        )
            log_with_context(
                logger,
                logging.INFO,
                "PostgreSQL database initialized successfully",
            )
        else:
            SQLModel.metadata.create_all(sync_engine)
            with sync_engine.begin() as conn:
                for sql in COMMON_PERFORMANCE_INDEX_SQL:
                    try:
                        conn.execute(text(sql))
                    except Exception as index_error:
                        log_with_context(
                            logger,
                            logging.WARNING,
                            "Skipped creating performance index",
                            sql=sql,
                            error=str(index_error),
                            error_type=type(index_error).__name__,
                        )
            log_with_context(
                logger,
                logging.INFO,
                "SQLite database initialized successfully",
                db_file=DATABASE_URL or "./zenstory.db",
            )
    except Exception as e:
        log_with_context(
            logger,
            logging.ERROR,
            "Failed to initialize database",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise


def get_session() -> Generator[Session, None, None]:
    """Get database session (sync for both SQLite and PostgreSQL)."""
    with Session(sync_engine) as session:
        yield session


def create_session() -> Session:
    """
    创建独立的数据库 session。

    与 get_session() 不同，此函数返回的 session 不会自动关闭，
    调用者负责在使用完毕后调用 session.close()。

    适用场景：
    - 需要跨越多个函数调用的 session
    - 无法使用 context manager 的场景（如 ToolContext）
    """
    return Session(sync_engine)
