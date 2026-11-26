"""Regression tests for Round 2 agent error-isolation, savepoint, and cost-backstop fixes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from agent.context.assembler import ContextAssembler
from agent.service import AgentService
from agent.suggest_service import SuggestService
from agent.tools import mcp_tools
from agent.tools.file_ops.project import TITLE_SOURCE_UNAVAILABLE, ProjectOperations
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import Character, Novel, Project, User


def _seed_user_and_project(db_session: Session, *, name: str = "我的小说") -> tuple[User, Project]:
    user = User(
        username="r2-agent-owner",
        email="r2-agent-owner@example.test",
        hashed_password="unused",
        email_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    project = Project(name=name, owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(project)
    return user, project


def test_current_title_source_savepoint_protects_locked_project_update(db_session: Session) -> None:
    """A ledger read error inside update_project_status must rollback only its savepoint."""
    user, project = _seed_user_and_project(db_session, name="我的小说")
    bind = db_session.get_bind()
    executed_sql: list[str] = []

    def trace_and_fail_ledger(_conn, _cursor, statement: str, *_rest) -> None:
        executed_sql.append(statement)
        if "agent_artifact_ledger" in statement.lower():
            raise RuntimeError("ledger read failed")

    with Session(bind) as session:
        event.listen(bind, "before_cursor_execute", trace_and_fail_ledger)
        try:
            result = ProjectOperations(session=session, user_id=user.id).update_project_status(
                project_id=project.id,
                summary="新的项目简介",
                title="雾港来信",
            )
        finally:
            event.remove(bind, "before_cursor_execute", trace_and_fail_ledger)

    assert any("SAVEPOINT" in stmt.upper() for stmt in executed_sql)
    assert any("ROLLBACK TO SAVEPOINT" in stmt.upper() for stmt in executed_sql)
    assert result["project_name_updated"] is False
    assert result["title_skipped"] == TITLE_SOURCE_UNAVAILABLE
    assert result["updated_fields"] == ["summary"]
    db_session.expire_all()
    refreshed = db_session.get(Project, project.id)
    assert refreshed.summary == "新的项目简介"
    assert refreshed.name == "我的小说"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "status_code"),
    [
        (ErrorCode.QUOTA_AI_DAILY_COST_EXCEEDED, 429),
        (ErrorCode.AI_COST_BUDGET_UNAVAILABLE, 503),
    ],
)
async def test_suggest_service_propagates_cost_budget_api_exceptions(
    db_session: Session,
    error_code: ErrorCode,
    status_code: int,
) -> None:
    """SuggestService must never mask daily cost backstop 429/503 APIExceptions with 200 fallbacks."""
    user, project = _seed_user_and_project(db_session)
    service = SuggestService()
    service.llm = MagicMock()
    service.llm.acomplete = AsyncMock(
        side_effect=APIException(error_code=error_code, status_code=status_code)
    )

    with pytest.raises(APIException) as exc_info:
        await service.generate_suggestions(
            session=db_session,
            project_id=project.id,
            user_id=user.id,
        )
    assert exc_info.value.status_code == status_code
    assert exc_info.value.error_code == error_code


@pytest.mark.asyncio
async def test_suggest_service_rolls_back_shared_session_on_non_api_error() -> None:
    """Non-offload shared session errors roll back before returning canned suggestions."""
    service = SuggestService()
    service.llm = MagicMock()
    service.context_assembler = MagicMock()
    service.context_assembler.assemble.side_effect = RuntimeError("db read failed")

    fake_session = MagicMock(spec=Session)
    fake_session.get_bind.return_value = MagicMock(dialect=MagicMock(name="sqlite"))

    suggestions = await service.generate_suggestions(
        session=fake_session,
        project_id="proj-1",
        user_id="user-1",
        count=2,
    )
    assert len(suggestions) == 2
    fake_session.rollback.assert_called_once()


def test_attached_library_materials_savepoint_isolates_per_item_failure(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    """One failing library material query must rollback its savepoint and not poison assemble()."""
    user, _project = _seed_user_and_project(db_session)
    novel = Novel(title="参考书", author="作者", user_id=user.id)
    db_session.add(novel)
    db_session.flush()
    character = Character(novel_id=novel.id, name="主角林深", role_type="主角", description="冷静果断")
    db_session.add(character)
    db_session.commit()

    executed_sql: list[str] = []
    bind = db_session.get_bind()

    def record_sql(_conn, _cursor, statement: str, *_rest) -> None:
        executed_sql.append(statement)

    def fail_relationship(*_args, **_kwargs):
        db_session.connection().exec_driver_sql("SELECT * FROM nonexistent_rel_table")

    monkeypatch.setattr(
        "api.material_utils.format_relationship_to_markdown",
        fail_relationship,
    )

    event.listen(bind, "before_cursor_execute", record_sql)
    try:
        items = ContextAssembler()._get_attached_library_materials(
            session=db_session,
            library_materials=[
                {"novel_id": novel.id, "entity_type": "relationships", "entity_id": 999},
                {"novel_id": novel.id, "entity_type": "characters", "entity_id": character.id},
            ],
            user_id=user.id,
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_sql)

    assert any("SAVEPOINT" in stmt.upper() for stmt in executed_sql)
    assert any("ROLLBACK TO SAVEPOINT" in stmt.upper() for stmt in executed_sql)
    assert len(items) == 1
    assert "林深" in items[0].content
    assert db_session.exec(select(Novel).where(Novel.id == novel.id)).first() is not None


@pytest.mark.asyncio
async def test_agent_service_shared_session_helpers_rollback_on_db_error() -> None:
    """_lookup_project_type and _is_first_ai_conversation must rollback shared session on error."""
    service = AgentService()
    fake_session = MagicMock(spec=Session)
    fake_session.get_bind.return_value = MagicMock(dialect=MagicMock(name="sqlite"))
    fake_session.get.side_effect = RuntimeError("db error")
    fake_session.exec.side_effect = RuntimeError("db error")

    assert await service._lookup_project_type(fake_session, "p1") is None
    assert fake_session.rollback.call_count == 1

    assert await service._is_first_ai_conversation(fake_session, "p1") is False
    assert fake_session.rollback.call_count == 2


def test_short_lived_session_rolls_back_shared_session_on_exception() -> None:
    """AgentRequestContext.session_scope must rollback a shared session when the body raises."""
    fake_session = MagicMock(spec=Session)
    req_ctx = mcp_tools.AgentRequestContext(
        session=fake_session,
        user_id="u1",
        project_id="p1",
        session_id="s1",
    )
    try:
        with pytest.raises(RuntimeError, match="probe boom"), req_ctx.session_scope() as s:
            assert s is fake_session
            raise RuntimeError("probe boom")
    finally:
        req_ctx.clear()

    fake_session.rollback.assert_called_once()
    fake_session.close.assert_not_called()


def test_mcp_tools_probe_queries_clean_session_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed probe queries in mcp_tools must rollback idle sessions so later calls stay usable."""
    fake_session = MagicMock(spec=Session)
    fake_session.in_transaction.return_value = False
    fake_session.exec.side_effect = RuntimeError("probe sql error")

    req_ctx = mcp_tools.AgentRequestContext(
        session=fake_session,
        user_id="u1",
        project_id="p1",
        session_id="s1",
    )
    try:
        req_ctx.set_pending_empty_file("f1", "第1章")
        assert mcp_tools._clear_settled_pending_empty_files(fake_session, req_ctx) is False
        assert fake_session.rollback.call_count == 1

        assert mcp_tools._repeat_of_pending_empty_file(fake_session, {"title": "第1章"}, "p1", req_ctx) is None
        assert fake_session.rollback.call_count == 2
    finally:
        req_ctx.clear()
