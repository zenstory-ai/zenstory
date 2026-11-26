"""Regression tests for admin analytics performance optimizations, caching, and index migration."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import create_engine, event, inspect, text
from sqlmodel import Session, SQLModel

import models  # noqa: F401
from models import ChatMessage, ChatSession, File, FileVersion, Project, User, UserFeedback
from models.points import PointsTransaction
from services.core.auth_service import hash_password
from services.infra import admin_analytics_cache
from services.usage.admin_growth_service import get_growth_dashboard
from services.usage.admin_stage_latency_service import get_stage_latency_metrics
from services.usage.admin_ux_metrics_service import get_ux_health_dashboard

NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
SERVER_DIR = Path(__file__).resolve().parents[2]
REVISION = "20261123_000000"
DOWN_REVISION = "20261122_000000"
UX_SUMMARY_REVISION = "20261124_000000"


def _create_user(session: Session, name: str, *, admin: bool = False) -> User:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        is_superuser=admin,
        created_at=(NOW - timedelta(days=2)).replace(tzinfo=None),
        updated_at=(NOW - timedelta(days=2)).replace(tzinfo=None),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


async def _headers(client: AsyncClient, username: str) -> dict[str, str]:
    resp = await client.post("/api/auth/login", data={"username": username, "password": "password123"})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _capture_sql(session: Session, fn):
    statements: list[str] = []

    def before_exec(_conn, _cursor, statement, _params, _ctx, _many):
        statements.append(statement)

    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", before_exec)
    try:
        result = fn()
    finally:
        event.remove(bind, "before_cursor_execute", before_exec)
    return result, statements


# ---------------------------------------------------------------------------
# 1. Admin analytics Redis cache behavior (hit, refresh bypass, fail-open)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_admin_analytics_cache_hit_refresh_and_fail_open(monkeypatch: pytest.MonkeyPatch):
    store: dict[str, str] = {}

    class _FakeRedis:
        def get(self, key: str):
            return store.get(key)

        def setex(self, key: str, _ttl: int, value: str):
            store[key] = value

    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    monkeypatch.setenv("ADMIN_ANALYTICS_CACHE_ENABLED", "true")
    monkeypatch.setattr(admin_analytics_cache, "get_redis_client", lambda: _FakeRedis())

    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return {"value": calls["count"], "at": NOW}

    first = admin_analytics_cache.cached_admin_analytics("test:metric", compute, days=7)
    second = admin_analytics_cache.cached_admin_analytics("test:metric", compute, days=7)
    refreshed = admin_analytics_cache.cached_admin_analytics(
        "test:metric", compute, days=7, refresh=True
    )
    after_refresh = admin_analytics_cache.cached_admin_analytics("test:metric", compute, days=7)

    assert calls["count"] == 2
    assert first["value"] == 1
    assert second["value"] == 1
    assert refreshed["value"] == 2
    assert after_refresh["value"] == 2

    # Fail-open when Redis raises.
    class _BrokenRedis:
        def get(self, _key: str):
            raise RuntimeError("redis down")

        def setex(self, _key: str, _ttl: int, _value: str):
            raise RuntimeError("redis down")

    monkeypatch.setattr(admin_analytics_cache, "get_redis_client", lambda: _BrokenRedis())
    fallback = admin_analytics_cache.cached_admin_analytics("test:metric", compute, days=7)
    assert fallback["value"] == 3
    assert calls["count"] == 3


@pytest.mark.unit
def test_admin_analytics_cache_disabled_without_redis_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return {"n": calls["count"]}

    assert admin_analytics_cache.cached_admin_analytics("test:no_redis", compute)["n"] == 1
    assert admin_analytics_cache.cached_admin_analytics("test:no_redis", compute)["n"] == 2


# ---------------------------------------------------------------------------
# 2. Query-count & projection regressions
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_ux_health_query_budget_and_tool_calls_decoupling(db_session: Session):
    writer = _create_user(db_session, "perf_ux_writer")
    project = Project(name="Perf Novel", owner_id=writer.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat_session = ChatSession(user_id=writer.id, project_id=project.id, title="S")
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)
    db_session.add(
        ChatMessage(
            session_id=chat_session.id,
            role="assistant",
            content="prose",
            message_metadata='{"stop_reason":"end_turn"}',
            tool_calls=None,
            created_at=(NOW - timedelta(hours=1)).replace(tzinfo=None),
        )
    )
    db_session.commit()

    payload, sqls = _capture_sql(
        db_session,
        lambda: get_ux_health_dashboard(db_session, days=30, now=NOW),
    )

    assert len(payload["daily"]) == 30
    assert payload["current"]["metrics"]["total_rounds"] == 1
    assert len(sqls) <= 10

    # Steady-state UX health query must read from chat_message_ux_summary and never
    # detoast chat_message.message_metadata or chat_message.tool_calls.
    for stmt in sqls:
        lower = stmt.lower()
        assert "chat_message.message_metadata" not in lower
        assert "chat_message.tool_calls" not in lower


@pytest.mark.integration
def test_growth_and_stage_latency_reduced_query_counts(db_session: Session):
    writer = _create_user(db_session, "perf_growth_writer")
    project = Project(name="Draft Proj", owner_id=writer.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    f = File(
        project_id=project.id,
        title="Ch1",
        file_type="draft",
        content="body",
        created_at=(NOW - timedelta(days=3)).replace(tzinfo=None),
    )
    db_session.add(f)
    db_session.flush()
    db_session.add(
        FileVersion(
            file_id=f.id,
            project_id=project.id,
            version_number=1,
            content="body",
            is_base_version=True,
            word_count=500,
            char_count=500,
            change_type="create",
            change_source="ai",
            created_at=(NOW - timedelta(days=3)).replace(tzinfo=None),
        )
    )
    db_session.commit()

    growth, growth_sqls = _capture_sql(
        db_session, lambda: get_growth_dashboard(db_session, days=30, now=NOW)
    )
    assert len(growth["daily"]) == 30
    assert len(growth_sqls) <= 9

    stage, stage_sqls = _capture_sql(
        db_session, lambda: get_stage_latency_metrics(db_session, days=30, now=NOW)
    )
    assert stage["ai_draft_survival"]["eligible_drafts"] == 1
    assert stage["ai_draft_survival"]["survived_drafts"] == 1
    assert len(stage_sqls) <= 3


@pytest.mark.integration
async def test_dashboard_stats_can_skip_commercialization_and_lists_skip_optional_counts(
    client: AsyncClient,
    db_session: Session,
):
    admin = _create_user(db_session, "perf_opt_admin", admin=True)
    writer = _create_user(db_session, "perf_opt_writer")
    db_session.add(
        PointsTransaction(
            user_id=writer.id,
            amount=100,
            balance_after=100,
            transaction_type="check_in",
        )
    )
    db_session.add(
        UserFeedback(
            user_id=writer.id,
            source_page="editor",
            issue_text="no screenshot issue",
            status="open",
        )
    )
    db_session.commit()

    headers = await _headers(client, admin.username)

    statements: list[str] = []

    def capture(_conn, _cursor, statement, _params, _ctx, _many):
        statements.append(statement.lower())

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        stats_resp = await client.get(
            "/api/admin/dashboard/stats",
            headers=headers,
        )
        assert stats_resp.status_code == 200
        assert not any("points_transaction" in s for s in statements)
        assert not any("check_in_record" in s for s in statements)

        statements.clear()
        audit_resp = await client.get(
            "/api/admin/audit-logs?page=1&page_size=10&include_total=false",
            headers=headers,
        )
        assert audit_resp.status_code == 200
        assert not any("count(" in s and "admin_audit_log" in s for s in statements)

        statements.clear()
        orders_resp = await client.get(
            "/api/admin/payment-orders?page=1&page_size=10&include_needs_attention_total=false",
            headers=headers,
        )
        assert orders_resp.status_code == 200
        assert orders_resp.json()["needs_attention_total"] == 0
        assert sum(1 for s in statements if "count(" in s and "payment_order" in s) == 1

        # has_screenshot=false must paginate in SQL without selecting full feedback rows
        fb_resp = await client.get(
            "/api/admin/feedback?has_screenshot=false&skip=0&limit=10",
            headers=headers,
        )
        assert fb_resp.status_code == 200
        assert fb_resp.json()["total"] == 1
    finally:
        event.remove(bind, "before_cursor_execute", capture)


# ---------------------------------------------------------------------------
# 3. Alembic migration 20261123_000000 upgrade & downgrade round trip
# ---------------------------------------------------------------------------


def _alembic(db_url: str, *args: str) -> None:
    candidate = Path(sys.executable).parent / "alembic"
    executable = str(candidate) if candidate.exists() else shutil.which("alembic")
    assert executable, "alembic CLI not found"
    completed = subprocess.run(
        [executable, *args],
        cwd=SERVER_DIR,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.integration
@pytest.mark.slow
def test_admin_analytics_indexes_alembic_round_trip(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'admin-analytics-indexes.db'}"
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    expected_indexes = {
        "chat_message": "ix_chat_message_role_created_at",
        "user": "ix_user_created_at_id",
        "payment_order": "ix_payment_order_status_paid_at",
        "subscription_history": "ix_subscription_history_created_at",
        "file": "ix_file_type_created_at",
        "admin_audit_log": "ix_admin_audit_log_created_at_id",
        "points_transaction": "ix_points_transaction_user_id_created_at",
        "upgrade_funnel_event": "ix_upgrade_funnel_event_user_source_created",
        "redemption_code": "ix_redemption_code_created_at_id",
        "referral": "ix_referral_created_at",
    }

    _alembic(db_url, "stamp", REVISION)
    _alembic(db_url, "downgrade", "-1")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    for table, idx_name in expected_indexes.items():
        names = {idx["name"] for idx in inspector.get_indexes(table)}
        assert idx_name not in names
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == DOWN_REVISION
    engine.dispose()

    _alembic(db_url, "upgrade", REVISION)
    engine = create_engine(db_url)
    inspector = inspect(engine)
    for table, idx_name in expected_indexes.items():
        names = {idx["name"] for idx in inspector.get_indexes(table)}
        assert idx_name in names
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
    engine.dispose()


@pytest.mark.integration
@pytest.mark.slow
def test_chat_message_ux_summary_alembic_round_trip(tmp_path: Path):
    """Migration 20261124_000000 creates chat_message_ux_summary, backfills legacy rows, and downgrades cleanly."""
    db_url = f"sqlite:///{tmp_path / 'chat-message-ux-summary.db'}"
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    _alembic(db_url, "stamp", UX_SUMMARY_REVISION)
    _alembic(db_url, "downgrade", "-1")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert not inspector.has_table("chat_message_ux_summary")
    with engine.begin() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
        # Seed a legacy assistant chat_message created before the summary table existed.
        created = (NOW - timedelta(hours=2)).replace(tzinfo=None)
        conn.execute(
            text(
                """
                INSERT INTO "user" (id, username, email, hashed_password, is_active, is_superuser, email_verified, created_at, updated_at)
                VALUES ('u-leg', 'legacy_u', 'legacy@example.com', 'hash', 1, 0, 1, :ts, :ts)
                """
            ),
            {"ts": created},
        )
        conn.execute(
            text(
                """
                INSERT INTO project (id, name, project_type, owner_id, is_deleted, created_at, updated_at)
                VALUES ('p-leg', 'Legacy', 'novel', 'u-leg', 0, :ts, :ts)
                """
            ),
            {"ts": created},
        )
        conn.execute(
            text(
                """
                INSERT INTO chat_session (id, user_id, project_id, title, is_active, message_count, created_at, updated_at)
                VALUES ('s-leg', 'u-leg', 'p-leg', 'S', 1, 1, :ts, :ts)
                """
            ),
            {"ts": created},
        )
        conn.execute(
            text(
                """
                INSERT INTO chat_message (id, session_id, role, content, message_metadata, tool_calls, created_at)
                VALUES ('m-leg', 's-leg', 'assistant', 'hello', :meta, :tc, :ts)
                """
            ),
            {
                "meta": '{"stop_reason":"end_turn","feedback":{"vote":"down","preset":"too_slow"}}',
                "tc": '[{"id":"t1","name":"edit_file","status":"success","result":{"data":{"undo":{"undone":true}}}}]',
                "ts": created,
            },
        )
    engine.dispose()

    _alembic(db_url, "upgrade", UX_SUMMARY_REVISION)
    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert inspector.has_table("chat_message_ux_summary")
    idx_names = {idx["name"] for idx in inspector.get_indexes("chat_message_ux_summary")}
    assert "ix_chat_message_ux_summary_created_at" in idx_names
    assert "ix_chat_message_ux_summary_session_id" in idx_names
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == UX_SUMMARY_REVISION
        )
        row = conn.execute(
            text(
                """
                SELECT round_category, abnormal_reason, feedback_vote, feedback_preset,
                       ai_edit_cards, ai_edit_undone, ai_delete_cards, ai_delete_restored
                FROM chat_message_ux_summary
                WHERE message_id = 'm-leg'
                """
            )
        ).one()
        assert tuple(row) == ("healthy", None, "down", "too_slow", 1, 1, 0, 0)
    engine.dispose()


@pytest.mark.integration
def test_stage_latency_ai_draft_survival_single_pass_cases(db_session: Session):
    """Single-pass ROW_NUMBER() AI draft survival handles survived, co-edited, deleted, reverted, and user-authored drafts."""
    writer = _create_user(db_session, "perf_survival_writer")
    project = Project(name="Survival Proj", owner_id=writer.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    def _add_draft(title: str, versions: list[tuple[str, str, int]], *, deleted: bool = False):
        f = File(
            project_id=project.id,
            title=title,
            file_type="draft",
            content="body",
            is_deleted=deleted,
            created_at=(NOW - timedelta(days=3)).replace(tzinfo=None),
        )
        db_session.add(f)
        db_session.flush()
        for idx, (source, change_type, words) in enumerate(versions, start=1):
            db_session.add(
                FileVersion(
                    file_id=f.id,
                    project_id=project.id,
                    version_number=idx,
                    content="x" * words,
                    is_base_version=(idx == 1),
                    word_count=words,
                    char_count=words,
                    change_type=change_type,
                    change_source=source,
                    created_at=(NOW - timedelta(days=3) + timedelta(minutes=idx)).replace(tzinfo=None),
                )
            )

    # 1. AI created, untouched -> survived, not co-edited
    _add_draft("AI Survived", [("ai", "create", 500)])
    # 2. AI created, later user edited -> survived + co-edited
    _add_draft("AI Co-Edited", [("ai", "create", 500), ("user", "edit", 800)])
    # 3. AI created, soft deleted -> deleted_or_reverted
    _add_draft("AI Deleted", [("ai", "create", 500)], deleted=True)
    # 4. AI created, latest version restored -> deleted_or_reverted (even if user edited earlier)
    _add_draft("AI Restored", [("ai", "create", 500), ("user", "edit", 700), ("user", "restore", 500)])
    # 5. AI created, emptied to 0 words -> deleted_or_reverted
    _add_draft("AI Emptied", [("ai", "create", 500), ("user", "edit", 0)])
    # 6. User created initially -> NOT eligible
    _add_draft("User Created", [("user", "create", 500), ("ai", "ai_edit", 900)])
    db_session.commit()

    stage = get_stage_latency_metrics(db_session, days=7, now=NOW)
    survival = stage["ai_draft_survival"]
    assert survival["eligible_drafts"] == 5
    assert survival["deleted_or_reverted_drafts"] == 3
    assert survival["survived_drafts"] == 2
    assert survival["co_edited_drafts"] == 1
    assert survival["survival_rate"] == 0.4
    assert survival["co_edit_rate"] == 0.2
