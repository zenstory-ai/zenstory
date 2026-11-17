"""作者看得到的历史压缩：压缩开始的进度提示、成功后落库的「对话已压缩」标记、没压缩成时不留痕。

用真实的 process_stream 跑一轮（压缩与模型都是替身），检查 SSE 事件和落库的 display_events。不调用模型。
"""

import json
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop.compaction import SKIPPED_PROVIDER_ERROR, CompactionOutcome
from agent.loop.prompts import SUMMARY_PREFIX
from models import ChatMessage, ChatSession, Project, User
from services.core.auth_service import hash_password

pytestmark = pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")


@pytest.fixture
def chat_with_history(db_session: Session):
    user = User(
        email="compact_notice@example.com",
        username="compactnotice",
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


def _sse_events(chunks: list[str]) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            lines = [line for line in block.splitlines() if line]
            event = next((line[6:].strip() for line in lines if line.startswith("event:")), None)
            data = next((line[5:].strip() for line in lines if line.startswith("data:")), None)
            if event and data:
                events.append((event, json.loads(data)))
    return events


def _reply_stream(_state=None):
    yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第二章写好了。"})
    yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})


def _error_stream(_state=None):
    yield StreamEvent(type=StreamEventType.ERROR, data={"error": "synthetic provider failure"})


async def _run_turn(
    db_session,
    user,
    project,
    chat,
    outcome: CompactionOutcome | None,
    *,
    events=_reply_stream,
    metadata: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """跑一轮；outcome 为 None 时这一轮不触发压缩（真实的 should_compact 判断）。"""
    from agent.service import AgentService

    def fake_loop(_state, **_kwargs):
        async def stream():
            for event in events(_state):
                yield event

        return stream()

    chunks: list[str] = []
    with ExitStack() as stack:
        if outcome is not None:
            stack.enter_context(patch("agent.service.should_compact", return_value=True))
            stack.enter_context(patch("agent.service.compact_history", AsyncMock(return_value=outcome)))
        stack.enter_context(patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop))
        # 失败 / 重试路径用独立 session 落库：指向同一个测试库。
        stack.enter_context(
            patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind()))
        )
        async for chunk in AgentService().process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="续写第二章",
            session=db_session,
            session_id=chat.id,
            metadata=metadata or {},
        ):
            chunks.append(chunk)
    return _sse_events(chunks)


def _latest_assistant_metadata(db_session, chat) -> dict[str, Any]:
    db_session.expire_all()
    rows = db_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == chat.id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc())
    ).all()
    return json.loads(rows[0].message_metadata or "{}")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_successful_compaction_streams_progress_then_a_persisted_marker(db_session, chat_with_history):
    user, project, chat = chat_with_history
    record = {
        "summary": "合成交接摘要：第一章已写完。",
        "covers_through_message_id": "row-x",
        "kept_author_messages": ["先写第一章"],
        "created_at": "2026-11-17T08:00:00",
    }
    outcome = CompactionOutcome(
        record=record, items=[{"role": "user", "content": "先写第一章"}, {"role": "user", "content": "摘要"}]
    )

    events = await _run_turn(db_session, user, project, chat, outcome)
    types = [event_type for event_type, _ in events]

    started = [data for event_type, data in events if event_type == "thinking" and data.get("step") == "history_compaction"]
    assert started and started[0]["message"] == "正在压缩对话…"
    compacted = [data for event_type, data in events if event_type == "history_compacted"]
    assert compacted == [{"summary": "合成交接摘要：第一章已写完。"}]
    # 顺序：进度提示 → 标记 → 这一轮的回复。
    assert types.index("history_compacted") < types.index("content")
    assert not any(data.get("step") == "history_compaction_skipped" for event_type, data in events if event_type == "thinking")

    metadata = _latest_assistant_metadata(db_session, chat)
    assert metadata["compaction"]["summary"] == "合成交接摘要：第一章已写完。"
    display_events = metadata["display_events"]
    assert display_events[0] == {"type": "history_compacted", "data": {}}
    assert any(event.get("type") == "content" for event in display_events[1:])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_skipped_compaction_only_ends_the_progress_line(db_session, chat_with_history):
    user, project, chat = chat_with_history
    outcome = CompactionOutcome(skipped=SKIPPED_PROVIDER_ERROR)

    events = await _run_turn(db_session, user, project, chat, outcome)

    steps = [data.get("step") for event_type, data in events if event_type == "thinking"]
    assert steps.index("history_compaction") < steps.index("history_compaction_skipped")
    assert "history_compacted" not in [event_type for event_type, _ in events]

    metadata = _latest_assistant_metadata(db_session, chat)
    assert "compaction" not in metadata or metadata["compaction"] is None
    assert metadata["compaction_skipped"]["reason"] == SKIPPED_PROVIDER_ERROR
    assert all(event.get("type") != "history_compacted" for event in metadata.get("display_events") or [])


def _rows(db_session, chat) -> list[ChatMessage]:
    db_session.expire_all()
    return db_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == chat.id).order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_compaction_then_error_keeps_only_the_author_row_and_retry_reuses_it(db_session, chat_with_history):
    """压缩成功、随后模型报错：不落一条只有标记的空回复；重试复用作者那条消息并沿用它上面的压缩点。"""
    user, project, chat = chat_with_history
    first_rows = _rows(db_session, chat)
    record = {
        "summary": "合成交接摘要：第一章已写完。",
        "covers_through_message_id": first_rows[-1].id,
        "kept_author_messages": ["先写第一章"],
        "created_at": "2026-11-17T08:00:00",
    }
    outcome = CompactionOutcome(
        record=record, items=[{"role": "user", "content": "先写第一章"}, {"role": "user", "content": "摘要"}]
    )

    events = await _run_turn(db_session, user, project, chat, outcome, events=_error_stream)
    assert "history_compacted" in [event_type for event_type, _ in events]

    rows = _rows(db_session, chat)
    assert [(row.role, row.content) for row in rows] == [
        ("user", "先写第一章"),
        ("assistant", "第一章已经写进正文。"),
        ("user", "续写第二章"),
    ]
    assert json.loads(rows[-1].message_metadata or "{}")["compaction"]["summary"] == "合成交接摘要：第一章已写完。"

    # 重试（ChatPanel 的「重试」带 retry_unanswered）复用作者那条消息，并接过它上面的压缩点。
    loop_inputs: list[list[dict[str, Any]]] = []

    def capture(state):
        loop_inputs.append(list(state.get("history_items") or []))
        return _reply_stream()

    await _run_turn(db_session, user, project, chat, None, events=capture, metadata={"retry_unanswered": True})
    # 模型收到的历史就是压缩后的：保留的作者原话 + 摘要，不是整段旧对话（压缩点没随删掉的行丢失）。
    assert [item.get("content") for item in loop_inputs[0]] == ["先写第一章", f"{SUMMARY_PREFIX}合成交接摘要：第一章已写完。"]

    rows = _rows(db_session, chat)
    assert [(row.role, row.content) for row in rows] == [
        ("user", "先写第一章"),
        ("assistant", "第一章已经写进正文。"),
        ("user", "续写第二章"),
        ("assistant", "第二章写好了。"),
    ]
    # 压缩点跟着这一轮的回复落库，之后的轮次照样从它重建。
    assert json.loads(rows[-1].message_metadata or "{}")["compaction"]["summary"] == "合成交接摘要：第一章已写完。"
