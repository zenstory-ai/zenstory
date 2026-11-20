"""
Tests for SessionLoader legacy row replay, author-edit diffing, and session helpers.
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


def _query_call(file_id: str, title: str) -> dict:
    return {
        "id": f"call_q_{file_id}",
        "name": "query_files",
        "arguments": {"id": file_id},
        "status": "success",
        "result": [{"id": file_id, "title": title, "content": "旧内容"}],
        "error": None,
    }


@pytest.mark.unit
class TestSessionLoaderMessageFormatting:
    """Tests for historical ChatMessage row formatting used by loop replay."""

    def test_format_chat_message_includes_stop_reason_from_metadata(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        """Assistant stop_reason should be hydrated into formatted history messages."""
        chat_session_id = session_loader_test_data["chat_session"].id
        msg = ChatMessage(
            session_id=chat_session_id,
            role="assistant",
            content="world",
            message_metadata=json.dumps({
                "stop_reason": "end_turn",
            }),
        )
        db_session.add(msg)
        db_session.commit()
        db_session.refresh(msg)

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        assistant_msg = loader._format_chat_message_for_history(msg)

        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["stop_reason"] == "end_turn"

    def test_format_chat_message_synthesizes_status_only_assistant_turns_into_history_content(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        """Status-only assistant turns should remain visible in model history after reload."""
        chat_session_id = session_loader_test_data["chat_session"].id
        msg = ChatMessage(
            session_id=chat_session_id,
            role="assistant",
            content="",
            message_metadata=json.dumps({
                "status_cards": [
                    {
                        "type": "workflow_stopped",
                        "reason": "clarification_needed",
                        "question": "请确认主角姓名",
                        "context": "姓名会影响后续称呼",
                        "details": ["主角姓名", "时代背景"],
                    }
                ],
            }),
        )
        db_session.add(msg)
        db_session.commit()
        db_session.refresh(msg)

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        assistant_msg = loader._format_chat_message_for_history(msg)

        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"]
        assert "clarification_needed" in assistant_msg["content"]
        assert "请确认主角姓名" in assistant_msg["content"]
        assert "姓名会影响后续称呼" in assistant_msg["content"]
        assert "主角姓名; 时代背景" in assistant_msg["content"]

    def test_tool_call_turn_replays_as_text_breadcrumb(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        """A prior tool-call turn should replay as a plain-text breadcrumb naming the file id."""
        chat_session_id = session_loader_test_data["chat_session"].id
        msg = ChatMessage(
            session_id=chat_session_id,
            role="assistant",
            content="好的，已经为你建立大纲。",
            tool_calls=json.dumps(
                [
                    {
                        "id": "call_1",
                        "name": "create_file",
                        "arguments": {"title": "第一章大纲", "file_type": "outline"},
                        "status": "success",
                        "result": {"id": "file-abc-123", "title": "第一章大纲"},
                        "error": None,
                    }
                ]
            ),
        )
        db_session.add(msg)
        db_session.commit()
        db_session.refresh(msg)

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        assistant_msg = loader._format_chat_message_for_history(msg)
        assert assistant_msg["role"] == "assistant"

        content = assistant_msg["content"]
        content_text = (
            content
            if isinstance(content, str)
            else "\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        )

        # Breadcrumb is plain TEXT and names the created file by id.
        assert "已创建文件" in content_text
        assert "file-abc-123" in content_text
        assert "第一章大纲" in content_text
        # The original prose reply is preserved alongside the breadcrumb.
        assert "已经为你建立大纲" in content_text

    def test_tool_call_breadcrumb_emits_no_raw_tool_blocks(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        """Replayed history must never contain raw tool_use/tool_result blocks or orphaned tool_call_id."""
        from agent.openai_agents.runner import normalize_messages_for_openai_agents

        chat_session_id = session_loader_test_data["chat_session"].id
        user_msg = ChatMessage(
            session_id=chat_session_id,
            role="user",
            content="把第二章删掉",
        )
        assistant_row = ChatMessage(
            session_id=chat_session_id,
            role="assistant",
            content="",
            tool_calls=json.dumps(
                [
                    {
                        "id": "call_del_1",
                        "name": "delete_file",
                        "arguments": {"id": "file-del-9"},
                        "status": "success",
                        "result": {"id": "file-del-9", "title": "第二章"},
                        "error": None,
                    }
                ]
            ),
        )
        db_session.add(user_msg)
        db_session.add(assistant_row)
        db_session.commit()
        db_session.refresh(user_msg)
        db_session.refresh(assistant_row)

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        history_messages = [
            loader._format_chat_message_for_history(user_msg),
            loader._format_chat_message_for_history(assistant_row),
        ]

        # Even with empty prose, the breadcrumb keeps the turn visible.
        assistant_msg = history_messages[-1]
        breadcrumb_content = assistant_msg["content"]
        breadcrumb_text = (
            breadcrumb_content
            if isinstance(breadcrumb_content, str)
            else json.dumps(breadcrumb_content, ensure_ascii=False)
        )
        assert "已删除文件" in breadcrumb_text
        assert "file-del-9" in breadcrumb_text

        # Normalizing for the SDK must produce ONLY plain user/assistant text —
        # no tool_use / tool_result blocks and therefore no orphaned tool_call_id.
        normalized = normalize_messages_for_openai_agents(history_messages)
        for message in normalized:
            assert set(message.keys()) == {"role", "content"}
            assert message["role"] in {"user", "assistant"}
            assert isinstance(message["content"], str)
            assert "tool_use" not in message["content"]
            assert "tool_result" not in message["content"]
            assert "tool_call_id" not in message["content"]
            assert "call_del_1" not in message["content"]

    def test_failed_tool_call_turn_emits_no_breadcrumb(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        """A failed tool call should not synthesize a misleading 'created file' breadcrumb."""
        chat_session_id = session_loader_test_data["chat_session"].id
        msg = ChatMessage(
            session_id=chat_session_id,
            role="assistant",
            content="抱歉，创建失败了。",
            tool_calls=json.dumps(
                [
                    {
                        "id": "call_err",
                        "name": "create_file",
                        "arguments": {"title": "失败文件"},
                        "status": "error",
                        "result": None,
                        "error": "permission denied",
                    }
                ]
            ),
        )
        db_session.add(msg)
        db_session.commit()
        db_session.refresh(msg)

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        assistant_msg = loader._format_chat_message_for_history(msg)
        assert assistant_msg["content"] == "抱歉，创建失败了。"
        assert "已创建文件" not in str(assistant_msg["content"])


@pytest.mark.unit
class TestAuthorEditsAndWorkingSetHelpers:
    """Tests for live author_edits_for_files and working-set user-content attachment."""

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
        """审计 P1-3：作者把 AI 写的第 3 章「老周」手改成「老秦」，<author_edits> 里列出具体改了什么。"""
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
        assert "「老周在收摊。」→「老秦在收摊。」" in author_edits_context
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
        assert edited.id in author_edits_context
        assert "第3章 三十年" in author_edits_context
        assert FAILED_LINE in author_edits_context


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


class TestClarificationCardReplay:
    """补问卡的问题和选项要进模型回放：作者回复选其中一项时模型得看得到选项。"""

    @staticmethod
    def _row(content: str, card: dict, reasoning: str | None = None):
        from types import SimpleNamespace

        return SimpleNamespace(
            id="m1",
            role="assistant",
            content=content,
            message_metadata=json.dumps({"status_cards": [card]}),
            reasoning_content=reasoning,
            tool_calls=None,
        )

    @staticmethod
    def _text(content) -> str:
        if isinstance(content, list):
            return "\n".join(
                block.get("text", "") for block in content if block.get("type") == "text"
            )
        return str(content)

    def test_card_options_are_replayed_even_when_the_turn_has_prose(self):
        card = {
            "type": "workflow_stopped",
            "reason": "clarification_needed",
            "question": "第5集这两处日期现在改吗？",
            "details": ["闪回标头：春天改成冬天", "台词里的日期跟着改"],
            "lastAgent": "writer",
        }
        message = SessionLoader("p", "u")._format_chat_message_for_history(
            self._row("核对完了，第5集有两处对不上。", card)
        )

        text = self._text(message["content"])
        assert text.startswith("核对完了，第5集有两处对不上。")
        assert "第5集这两处日期现在改吗？" in text
        assert "闪回标头：春天改成冬天" in text
        assert "台词里的日期跟着改" in text

    def test_card_options_survive_reasoning_content_blocks(self):
        card = {
            "type": "workflow_stopped",
            "reason": "clarification_needed",
            "question": "开篇用哪种？",
            "details": ["雨夜追车", "葬礼重逢"],
        }
        message = SessionLoader("p", "u")._format_chat_message_for_history(
            self._row("先确认开篇。", card, reasoning="想一想")
        )

        assert "葬礼重逢" in self._text(message["content"])

    def test_replayed_card_text_is_bounded(self):
        card = {
            "type": "workflow_stopped",
            "reason": "clarification_needed",
            "question": "问" * 5000,
            "context": "背" * 5000,
            "details": [f"选项{i}" + "长" * 2000 for i in range(20)],
        }
        message = SessionLoader("p", "u")._format_chat_message_for_history(
            self._row("请选一个方向。", card)
        )

        text = self._text(message["content"])
        assert "选项0" in text
        assert "选项7" in text
        assert "选项8" not in text
        assert len(text) < 5000

    def test_prose_turn_without_clarification_card_replays_prose_only(self):
        card = {
            "type": "iteration_exhausted",
            "layer": "tool_call",
            "reason": "这一轮的时间快用完了",
            "lastAgent": "writer",
        }
        message = SessionLoader("p", "u")._format_chat_message_for_history(
            self._row("第3章写到一半。", card)
        )

        assert self._text(message["content"]) == "第3章写到一半。"

    @staticmethod
    def _long_card() -> dict:
        return {
            "type": "workflow_stopped",
            "reason": "clarification_needed",
            "question": "问" * 1000,
            "details": [f"选项{i}" + "长" * 500 for i in range(10)],
        }

    def test_prose_turn_replays_bounded_clarification_options(self):
        """有正文时追加的补问卡按固定上限截断问题与选项。"""
        from agent.core.session_loader import (
            _CLARIFICATION_DETAIL_MAX_CHARS,
            _CLARIFICATION_MAX_DETAILS,
            _CLARIFICATION_TEXT_MAX_CHARS,
        )

        card = self._long_card()
        message = SessionLoader("p", "u")._format_chat_message_for_history(
            self._row("请选一个方向。", card)
        )

        text = self._text(message["content"])
        expected_question = "问" * _CLARIFICATION_TEXT_MAX_CHARS
        expected_details = [
            (f"选项{i}" + "长" * 500)[:_CLARIFICATION_DETAIL_MAX_CHARS]
            for i in range(_CLARIFICATION_MAX_DETAILS)
        ]
        assert f"question: {expected_question}\n" in text
        assert "details: " + "; ".join(expected_details) in text
        assert "问" * (_CLARIFICATION_TEXT_MAX_CHARS + 1) not in text

    def test_card_only_turn_keeps_the_full_card_text(self):
        """没有正文的轮次只有卡片可看：回放仍是完整文本，不截断。"""
        card = self._long_card()
        message = SessionLoader("p", "u")._format_chat_message_for_history(self._row("", card))

        text = self._text(message["content"])
        assert "问" * 1000 in text
        assert "选项9" + "长" * 500 in text
