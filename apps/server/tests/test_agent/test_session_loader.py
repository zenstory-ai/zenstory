"""
Tests for SessionLoader author manual-edit alerts, working-set attachment, and loop history compatibility.
"""

import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.core.session_loader import SessionLoader
from models import ChatMessage, ChatSession, Project, User


@pytest.fixture
def session_loader_test_data(db_session: Session):
    """Create user/project/chat session for session loader tests."""
    suffix = uuid4().hex[:8]
    user = User(
        email=f"session-loader-{suffix}@example.com",
        username=f"session_loader_{suffix}",
        hashed_password="hashed",
        name="Session Loader Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"Session Loader Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    chat_session = ChatSession(
        user_id=user.id,
        project_id=project.id,
        title="Session Loader Test Chat",
        is_active=True,
        message_count=0,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)

    return {
        "user": user,
        "project": project,
        "chat_session": chat_session,
    }


def _add_turn(
    db_session: Session,
    chat_session_id: str,
    *,
    user_text: str,
    tool_calls: list[dict],
    at: datetime,
) -> None:
    """Persist one user + assistant turn with tool_calls in the production shape."""
    db_session.add(
        ChatMessage(session_id=chat_session_id, role="user", content=user_text, created_at=at)
    )
    db_session.add(
        ChatMessage(
            session_id=chat_session_id,
            role="assistant",
            content="好的。",
            tool_calls=json.dumps(tool_calls, ensure_ascii=False),
            created_at=at + timedelta(seconds=1),
        )
    )
    db_session.commit()


@pytest.mark.unit
class TestAuthorEditsAndWorkingSetHelpers:
    """Tests for lightweight author_edits_for_files and working-set user-content attachment."""

    def _make_file(self, db_session: Session, project_id: str, title: str, content: str, **kw):
        from models import File

        file = File(project_id=project_id, title=title, content=content, file_type="outline", **kw)
        db_session.add(file)
        db_session.commit()
        db_session.refresh(file)
        return file

    def test_attach_working_set_to_user_content_places_user_message_last(self):
        text = "<previous_turn_working_set>\n《卷纲》\n</previous_turn_working_set>"
        user_content = SessionLoader.attach_working_set_to_user_content("继续", text)
        assert user_content.startswith("<previous_turn_working_set>")
        assert user_content.endswith("【本轮用户消息】\n继续")

    def test_author_edits_for_files_marks_chapters_the_author_edited_after_the_ai(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        """作者把 AI 写的第 3 章手改后，<author_edits> 给出简洁 1 行提醒，未改动章节不出现。"""
        from services.features.file_version_service import FileVersionService

        project = session_loader_test_data["project"]
        versions = FileVersionService()

        edited = self._make_file(db_session, project.id, "第3章 三十年", "老周在收摊。")
        versions.create_version(db_session, edited.id, "老周在收摊。", change_type="create", change_source="ai")
        edited.content = "老秦在收摊。"
        db_session.add(edited)
        db_session.commit()
        versions.create_version(db_session, edited.id, "老秦在收摊。", change_type="edit", change_source="user")

        untouched = self._make_file(db_session, project.id, "第2章", "老周的摊子。")
        versions.create_version(db_session, untouched.id, "老周的摊子。", change_type="create", change_source="ai")

        loader = SessionLoader(project_id=project.id, user_id=session_loader_test_data["user"].id)
        author_edits_context, author_edited_ids, files_by_id = loader.author_edits_for_files(
            db_session, [edited.id, untouched.id], {}
        )

        assert author_edited_ids == {edited.id}
        assert files_by_id is not None and set(files_by_id) == {edited.id, untouched.id}
        assert edited.id in author_edits_context
        assert "第3章 三十年" in author_edits_context
        assert "自你上次写入后已被作者手动修改" in author_edits_context
        assert untouched.id not in author_edits_context

    def test_author_edits_for_files_query_failure_rolls_back_and_discloses_incomplete(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """File 查询失败时必须回滚事务，且如实标注手改未算出而非伪装成零改动。"""
        from agent.tools.author_changes import FAILED_LINE

        project = session_loader_test_data["project"]
        edited = self._make_file(db_session, project.id, "第3章 三十年", "老秦在收摊。")

        loader = SessionLoader(project_id=project.id, user_id=session_loader_test_data["user"].id)

        real_exec = db_session.exec
        real_rollback = db_session.rollback
        state = {"failed_once": False, "aborted": False, "rollbacks": 0}

        def flaky_exec(statement, *args, **kwargs):
            if not state["failed_once"]:
                state["failed_once"] = True
                state["aborted"] = True
                raise RuntimeError("transient db error")
            if state["aborted"]:
                raise RuntimeError("InFailedSqlTransaction: current transaction is aborted")
            return real_exec(statement, *args, **kwargs)

        def tracking_rollback():
            state["aborted"] = False
            state["rollbacks"] += 1
            return real_rollback()

        monkeypatch.setattr(db_session, "exec", flaky_exec)
        monkeypatch.setattr(db_session, "rollback", tracking_rollback)

        author_edits_context, author_edited_ids, files_by_id = loader.author_edits_for_files(
            db_session, [edited.id], {edited.id: edited.title}
        )
        assert state["rollbacks"] == 1
        assert author_edited_ids == set()
        assert files_by_id is None
        assert "<author_edits>" in author_edits_context
        assert FAILED_LINE in author_edits_context

    def test_author_edits_for_files_cancel_event_stops_cleanly(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        """协同取消信号置位后立刻返回，不再执行 FileVersion 批量查询。"""
        import threading

        from services.features.file_version_service import FileVersionService

        project = session_loader_test_data["project"]
        versions = FileVersionService()
        f1 = self._make_file(db_session, project.id, "第1章", "内容1")
        versions.create_version(db_session, f1.id, "AI原文", change_type="create", change_source="ai")
        versions.create_version(db_session, f1.id, f1.content, change_type="edit", change_source="user")

        loader = SessionLoader(project_id=project.id, user_id=session_loader_test_data["user"].id)
        cancel_event = threading.Event()
        cancel_event.set()
        block, edited_ids, by_id = loader.author_edits_for_files(
            db_session, [f1.id], {}, cancel_event=cancel_event
        )
        assert block == ""
        assert edited_ids == set()
        assert by_id == {}

    @pytest.mark.asyncio
    async def test_process_stream_cancellation_sets_prepare_cancel_event(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """流在 _prepare_loop_turn_sync 执行期间被取消时，必须置位传入的 cancel_event。"""
        import asyncio
        import threading

        from agent.loop.history import LoopHistory
        from agent.loop.turn_context import LoopTurnContext
        from agent.service import AgentService

        project = session_loader_test_data["project"]
        user = session_loader_test_data["user"]
        chat = session_loader_test_data["chat_session"]
        entered = threading.Event()
        observed: dict[str, threading.Event | None] = {"event": None}
        exited_early = threading.Event()

        def blocking_prepare_sync(**kwargs):
            ev = kwargs.get("cancel_event")
            observed["event"] = ev
            entered.set()
            if ev is not None and ev.wait(timeout=2.0):
                exited_early.set()
            return LoopHistory(), LoopTurnContext(), [], "sys"

        service = AgentService()
        monkeypatch.setattr("agent.service.should_offload_session_work", lambda _s: True)
        monkeypatch.setattr(service, "_resolve_or_create_chat_session_id_sync", lambda **_kw: chat.id)
        monkeypatch.setattr(service, "_prepare_loop_turn_sync", blocking_prepare_sync)

        async def _consume():
            async for _frame in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="继续",
                session=db_session,
                session_id=chat.id,
            ):
                pass

        task = asyncio.create_task(_consume())
        # Wait until the worker thread enters _prepare_loop_turn_sync.
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        assert isinstance(observed["event"], threading.Event)
        assert not observed["event"].is_set()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert observed["event"].is_set()
        assert exited_early.wait(timeout=1.0)


@pytest.mark.unit
def test_history_with_removed_proposal_tool_results_still_loads(
    db_session: Session,
    session_loader_test_data,
):
    """Chats from before the AI-proposal removal keep update_project results with proposal fields."""
    from agent.context.assembler import ContextAssembler
    from agent.loop.history import load_loop_history

    project = session_loader_test_data["project"]
    user = session_loader_test_data["user"]
    chat_session = session_loader_test_data["chat_session"]
    project.notes = "【作者确认】\n女主前期隐藏身份\n\n【AI 提议·待作者确认】\n关键配角：沈度"
    db_session.add(project)
    db_session.commit()
    old_result = {
        "status": "success",
        "data": {
            "project_id": project.id,
            "updated_fields": ["notes"],
            "proposals_added": ["关键配角：沈度"],
            "proposals_removed": ["沈度是配角"],
            "notes_recorded_as_proposals": ["关键配角：沈度"],
            "notes_note": "已记在备注的「AI 提议·待作者确认」里",
        },
    }
    _add_turn(
        db_session,
        chat_session.id,
        user_text="记一下配角",
        tool_calls=[{
            "id": "call_up_old",
            "name": "update_project",
            "arguments": {"proposals": "关键配角：沈度"},
            "status": "success",
            "result": old_result,
        }],
        at=datetime.utcnow() - timedelta(minutes=5),
    )

    history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat_session.id,
    )
    context_data = ContextAssembler().assemble(db_session, project.id, user_id=user.id, query="继续")

    assert any(
        item.get("role") == "user" and "记一下配角" in str(item.get("content"))
        for item in history.items
    )
    context = context_data.context
    assert "女主前期隐藏身份" in context
    assert "作者已忽略" not in context and "【备注·AI 提议" not in context
