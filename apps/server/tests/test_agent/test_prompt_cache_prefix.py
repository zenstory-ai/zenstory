"""模型输入的前缀缓存布局：[系统提示][历史] 跨轮逐字不变，随轮次变化的内容只在本轮用户消息里。

DeepSeek 按逐字前缀命中缓存（miss 的单价是 hit 的 50 倍）。系统提示里只要有一处随轮次变化，
后面整段回放的历史就全部按 miss 计价。这里用真实的 process_stream 组装单循环 agent 的两轮输入，
比较系统提示与回放历史。不调用模型。
"""

from typing import Any
from unittest.mock import patch

import pytest
from sqlmodel import Session

from agent.core.workflow_events import StreamEvent, StreamEventType
from models import ChatMessage, ChatSession, File, Project, User
from services.core.auth_service import hash_password

pytestmark = pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")


@pytest.fixture
def project_with_history(db_session: Session):
    user = User(
        email="cache_prefix@example.com",
        username="cacheprefix",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="雾港来信", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="先写第一章"))
    db_session.add(ChatMessage(session_id=chat.id, role="assistant", content="第一章已经写进正文。"))
    db_session.commit()
    return user, project, chat


async def _run_turn(db_session, user, project, chat, *, message, metadata) -> dict[str, Any]:
    """跑一轮真实的 process_stream，返回单循环 agent 收到的 state（模型不调用）。"""
    from agent.service import AgentService

    captured: dict[str, Any] = {}

    def fake_loop(state, **_kwargs):
        captured.update(state)
        captured["history_items"] = list(state.get("history_items") or [])

        async def stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"已按「{message}」处理。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return stream()

    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
        service = AgentService()
        async for _event in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message=message,
            session=db_session,
            session_id=chat.id,
            metadata=metadata,
        ):
            pass
    return captured


@pytest.mark.asyncio
@pytest.mark.unit
async def test_consecutive_turns_share_system_prompt_and_history_prefix(db_session, project_with_history):
    user, project, chat = project_with_history
    chapter_two = File(project_id=project.id, title="第二章", file_type="draft", content="雾港的第二封信到了。")
    chapter_three = File(project_id=project.id, title="第三章", file_type="draft", content="雾港的第三封信到了。")
    db_session.add_all([chapter_two, chapter_three])
    db_session.commit()

    first = await _run_turn(
        db_session, user, project, chat, message="续写第二章",
        metadata={"current_file_id": chapter_two.id, "current_file_type": "draft", "current_file_title": "第二章"},
    )
    second = await _run_turn(
        db_session, user, project, chat, message="Rewrite chapter three in English",
        metadata={"current_file_id": chapter_three.id, "current_file_type": "draft", "current_file_title": "第三章"},
    )

    # 系统提示跨轮逐字相同，不含任何一轮的正文、当前文件或作者消息。
    assert first["system_prompt"] == second["system_prompt"]
    for per_turn in ("雾港的第二封信", "雾港的第三封信", chapter_two.id, chapter_three.id, "续写第二章"):
        assert per_turn not in first["system_prompt"]

    # 第二轮回放的历史以第一轮回放的历史开头，逐项相同；第一轮本身接在后面。
    history_1, history_2 = first["history_items"], second["history_items"]
    assert history_1[:1] == [{"role": "user", "content": "先写第一章"}]
    assert history_2[: len(history_1)] == history_1
    assert history_2[len(history_1)] == {"role": "user", "content": "续写第二章"}

    # 每一轮的作者消息与本轮资料只在本轮用户消息里。
    assert "续写第二章" in first["user_message"] and "Rewrite chapter three in English" in second["user_message"]
    assert "续写第二章" not in second["user_message"]
