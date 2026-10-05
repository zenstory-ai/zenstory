# Pytest configuration and shared fixtures

# Test database URL (use file-based database for testing)
# Temporary file database to avoid connection isolation issues with in-memory DB
import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy import text as Custom
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel

from database import get_session
from main import app
from middleware.rate_limit import _rate_limit_store

# Tests should not depend on external Redis availability.
os.environ.setdefault("RATE_LIMIT_BACKEND", "memory")

temp_db_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
temp_db_path = temp_db_file.name
temp_db_file.close()
TEST_DATABASE_URL = f"sqlite:///{temp_db_path}"

# Create sync engine for testing
test_engine = create_engine(
    TEST_DATABASE_URL,
    echo=False,  # Set to True for SQL query debugging
    connect_args={"check_same_thread": False}
)

# Create sync session factory
TestSessionLocal = sessionmaker(
    bind=test_engine,
    autocommit=False,
    autoflush=False,
    class_=Session,
    expire_on_commit=False
)


@pytest.fixture(scope="session", autouse=True)
def initialized_test_schema():
    # Prompt-only tests read the same isolated primary database as API fixtures.
    SQLModel.metadata.create_all(test_engine)


@pytest.fixture(autouse=True)
def isolated_runtime_prompt_source(monkeypatch):
    from agent import prompts

    monkeypatch.setattr(prompts, "sync_engine", test_engine)
    monkeypatch.setattr(prompts, "_db_config_cache", None)


@pytest.fixture(scope="function")
def db_session():
    """
    Create a new database session for a test.
    Automatically cleans up all data at the end of the test.
    """
    # Create tables (idempotent, safe to call multiple times)
    SQLModel.metadata.create_all(test_engine)
    _rate_limit_store.clear()

    # Create a new session
    session = TestSessionLocal()

    yield session

    # Clean up: delete all data after the test
    # This is more reliable than nested transactions for SQLite
    session.rollback()
    session.close()

    # Create a new session to clean up data
    cleanup_session = TestSessionLocal()
    try:
        # Delete all data from all tables in reverse dependency order
        # Material library tables (must come first due to foreign key constraints)
        cleanup_session.exec(Custom("DELETE FROM event_timelines"))
        cleanup_session.exec(Custom("DELETE FROM process_checkpoints"))
        cleanup_session.exec(Custom("DELETE FROM chapter_contents"))
        cleanup_session.exec(Custom("DELETE FROM character_mentions"))
        cleanup_session.exec(Custom("DELETE FROM outline_characters"))
        cleanup_session.exec(Custom("DELETE FROM outline_items"))
        cleanup_session.exec(Custom("DELETE FROM outlines"))
        cleanup_session.exec(Custom("DELETE FROM generated_contents"))
        cleanup_session.exec(Custom("DELETE FROM story_plot_links"))
        cleanup_session.exec(Custom("DELETE FROM stories"))
        cleanup_session.exec(Custom("DELETE FROM plots"))
        cleanup_session.exec(Custom("DELETE FROM chapters"))
        cleanup_session.exec(Custom("DELETE FROM character_relationships"))
        cleanup_session.exec(Custom("DELETE FROM characters"))
        cleanup_session.exec(Custom("DELETE FROM golden_fingers"))
        cleanup_session.exec(Custom("DELETE FROM story_lines"))
        cleanup_session.exec(Custom("DELETE FROM world_views"))
        cleanup_session.exec(Custom("DELETE FROM ingestion_jobs"))
        cleanup_session.exec(Custom("DELETE FROM novels"))
        # Referral system tables (must come before user due to foreign key constraints)
        cleanup_session.exec(Custom("DELETE FROM user_reward"))
        cleanup_session.exec(Custom("DELETE FROM referral"))
        cleanup_session.exec(Custom("DELETE FROM user_stats"))
        cleanup_session.exec(Custom("DELETE FROM invite_code"))
        # Points and check-in tables (must come before user due to foreign key constraints)
        cleanup_session.exec(Custom("DELETE FROM points_transaction"))
        cleanup_session.exec(Custom("DELETE FROM check_in_record"))
        # Subscription system tables (must come before user due to foreign key constraints)
        cleanup_session.exec(Custom("DELETE FROM payment_order"))
        cleanup_session.exec(Custom("DELETE FROM admin_audit_log"))
        cleanup_session.exec(Custom("DELETE FROM subscription_history"))
        cleanup_session.exec(Custom("DELETE FROM upgrade_funnel_event"))
        cleanup_session.exec(Custom("DELETE FROM usage_quota"))
        cleanup_session.exec(Custom("DELETE FROM redemption_code"))
        cleanup_session.exec(Custom("DELETE FROM user_subscription"))
        cleanup_session.exec(Custom("DELETE FROM subscription_plan"))
        # Core tables
        cleanup_session.exec(Custom("DELETE FROM skill_resource"))
        cleanup_session.exec(Custom("DELETE FROM skill_usage"))
        cleanup_session.exec(Custom("DELETE FROM user_added_skill"))
        cleanup_session.exec(Custom("DELETE FROM public_skill"))
        cleanup_session.exec(Custom("DELETE FROM user_skill"))
        cleanup_session.exec(Custom("DELETE FROM chat_message"))
        cleanup_session.exec(Custom("DELETE FROM agent_artifact_ledger"))
        cleanup_session.exec(Custom("DELETE FROM chat_session"))
        cleanup_session.exec(Custom("DELETE FROM file_version"))
        cleanup_session.exec(Custom("DELETE FROM file"))
        cleanup_session.exec(Custom("DELETE FROM snapshot"))
        cleanup_session.exec(Custom("DELETE FROM inspiration"))
        cleanup_session.exec(Custom("DELETE FROM user_feedback"))
        cleanup_session.exec(Custom("DELETE FROM refresh_token_record"))
        cleanup_session.exec(Custom("DELETE FROM user_persona_profile"))
        cleanup_session.exec(Custom("DELETE FROM project"))
        cleanup_session.exec(Custom("DELETE FROM system_prompt_config"))
        cleanup_session.exec(Custom("DELETE FROM user"))
        cleanup_session.commit()
        _rate_limit_store.clear()
    finally:
        cleanup_session.close()


@pytest.fixture
async def client(db_session: Session):
    """
    Create an async HTTP client for testing the FastAPI app.
    Uses the test database session.
    """
    def override_get_session():
        yield db_session

    # Override the database dependency
    app.dependency_overrides[get_session] = override_get_session

    # Create async HTTP client
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac

    # Clear overrides after the test
    app.dependency_overrides.clear()


@pytest.fixture
async def sync_client(db_session: Session):
    """
    Create an async HTTP client for testing the FastAPI app.
    Alias for client fixture for clarity in sync tests.
    """
    def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# Pytest configuration
def pytest_configure(config):
    """
    Configure pytest with custom markers.
    """
    config.addinivalue_line(
        "markers", "unit: Unit tests (fast, isolated)"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests (slower, may use external services)"
    )
    config.addinivalue_line(
        "markers", "slow: Slow tests (may take > 1 second)"
    )
    config.addinivalue_line(
        "markers", "async: Async tests that require pytest-asyncio"
    )
    config.addinivalue_line(
        "markers", "asyncio: Async tests managed by pytest-asyncio"
    )
    config.addinivalue_line(
        "markers", "e2e: End-to-end tests (full workflow validation)"
    )
    config.addinivalue_line("markers", "real_llm: Tests that call real LLM providers")


_PRIMARY_TEST_MARKERS = {"unit", "integration", "e2e", "real_llm"}


def _has_primary_marker(item: pytest.Item) -> bool:
    return any(marker.name in _PRIMARY_TEST_MARKERS for marker in item.iter_markers())


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply stable path-based markers so wave lanes can be selected predictably."""
    del config
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if '/tests/e2e/' in path:
            if not any(marker.name == 'e2e' for marker in item.iter_markers()):
                item.add_marker(pytest.mark.e2e)
            if not any(marker.name == 'integration' for marker in item.iter_markers()):
                item.add_marker(pytest.mark.integration)
            continue

        if '/tests/real_llm/' in path:
            if not any(marker.name == 'real_llm' for marker in item.iter_markers()):
                item.add_marker(pytest.mark.real_llm)
            if not any(marker.name == 'integration' for marker in item.iter_markers()):
                item.add_marker(pytest.mark.integration)
            continue

        if '/tests/test_flows/unit/' in path and not _has_primary_marker(item):
            item.add_marker(pytest.mark.unit)
            continue

        if '/tests/test_flows/integration/' in path and not _has_primary_marker(item):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def _reset_tool_context():
    """每个用例前后都把 Agent 的全局 ToolContext 清干净。

    ToolContext 的 project_id / session / pending-empty-file 标记都存在
    ContextVar 里，是跨用例可见的进程级状态。任何一个用例调了
    ToolContext.set_context 而没收尾，后面所有"故意不设上下文"的用例就会
    读到别人残留的 project_id——表现为按执行顺序随机红绿（xdist 每次分组
    不同，更难复现）。这里统一兜底，用例自己就不必再各写一遍 try/finally。
    """
    from agent.tools.mcp_tools import ToolContext

    ToolContext.clear_context()
    try:
        yield
    finally:
        ToolContext.clear_context()


@pytest.fixture
def writing_prompt_configs(db_session):
    """Minimal DB rows for tests exercising writing, not a production seed catalog.

    Authority/missing-configuration tests deliberately do not request this fixture.
    """
    from sqlmodel import select

    from models import SystemPromptConfig

    for project_type in ("novel", "short", "screenplay"):
        if db_session.exec(select(SystemPromptConfig).where(SystemPromptConfig.project_type == project_type)).first() is None:
            db_session.add(SystemPromptConfig(
                project_type=project_type, role_definition=f"Test {project_type} editor",
                capabilities="Test writing capabilities", writing_guidelines="Test scope only",
            ))
    db_session.commit()
