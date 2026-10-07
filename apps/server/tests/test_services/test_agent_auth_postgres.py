from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session

from models.agent_api_key import AgentApiKey
from models.entities import User
from services import agent_auth_service
from services.agent_auth_service import generate_api_key, hash_api_key

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
    reason="isolated PostgreSQL URL not configured",
)

TABLES = [User.__table__, AgentApiKey.__table__]


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(
        os.environ["ZENSTORY_TEST_POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC"},
    )
    for table in reversed(TABLES):
        table.drop(engine, checkfirst=True)
    for table in TABLES:
        table.create(engine, checkfirst=True)
    try:
        yield engine
    finally:
        for table in reversed(TABLES):
            table.drop(engine, checkfirst=True)
        engine.dispose()


def test_concurrent_authorized_requests_increment_usage_exactly(pg_engine, monkeypatch):
    plain_key = generate_api_key()
    with Session(pg_engine) as setup:
        owner = User(
            email="agent-auth-concurrency@example.com",
            username="agent-auth-concurrency",
            hashed_password="hashed",
            email_verified=True,
            is_active=True,
        )
        setup.add(owner)
        setup.commit()
        setup.refresh(owner)
        api_key = AgentApiKey(
            user_id=owner.id,
            key_prefix=plain_key[:8],
            key_hash=hash_api_key(plain_key),
            name="Concurrent counter",
            scopes=["read"],
            is_active=True,
            request_count=0,
        )
        setup.add(api_key)
        setup.commit()
        setup.refresh(api_key)
        key_id = api_key.id

    both_loaded = Barrier(2)
    fixed_now = datetime(2026, 10, 6, 12, tzinfo=UTC)

    def synchronized_now():
        both_loaded.wait(timeout=5)
        return fixed_now

    monkeypatch.setattr(agent_auth_service, "utcnow", synchronized_now)

    def authenticate_once():
        with Session(pg_engine) as session:
            context = agent_auth_service.get_agent_user(
                x_agent_api_key=plain_key,
                session=session,
            )
            return context[2].id

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: authenticate_once(), range(2)))

    assert outcomes == [key_id, key_id]
    with Session(pg_engine) as verify:
        persisted = verify.get(AgentApiKey, key_id)
        assert persisted is not None
        assert persisted.request_count == 2
        assert persisted.last_used_at is not None
        assert persisted.last_used_at.replace(tzinfo=UTC) == fixed_now
