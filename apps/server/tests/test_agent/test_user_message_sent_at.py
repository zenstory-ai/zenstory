"""用户消息的时间是作者发出它的时间（2026-10-12 生产审计，serial）。

user 行在回合结束时才落库，created_at 以前取落库时刻：作者 17:13 发的消息，刷新后显示
成 AI 结束的 17:15。整轮保存、停止 / 断线的补偿保存、没有产出只留用户消息三条路径
都要用回合开始的时间；回合结束后才到的 steering 仍按到达时落库，排在助手回复之后。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlmodel import Session

from tests.test_agent.test_round3_service import (  # noqa: F401  (fixtures)
    CHAPTER,
    _poll_rows,
    _rows,
    _session_id_from_events,
    round3_service,
    round3_user_with_project,
)
from tests.test_agent.test_service import wait_for_stream_start

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("writing_prompt_configs")]

ROUND_SECONDS = 0.4


@pytest.fixture
def message_clock(monkeypatch):
    from models import ChatMessage

    clock = {"now": datetime(2026, 10, 12, 9, 13)}
    monkeypatch.setattr("agent.service.utcnow", lambda: clock["now"].replace(tzinfo=UTC))
    monkeypatch.setattr(
        ChatMessage.model_fields["created_at"], "default_factory", lambda: clock["now"]
    )
    return clock


def _ordered(rows):
    return sorted(rows, key=lambda row: (row.created_at, row.id))


async def test_full_round_user_row_keeps_the_send_time(
    round3_service, round3_user_with_project, db_session: Session, message_clock
):
    from agent.core.workflow_events import StreamEvent, StreamEventType

    project = round3_user_with_project["project"]
    user = round3_user_with_project["user"]
    sent_at = message_clock["now"]
    completed_at = sent_at + timedelta(minutes=2)

    async def slow_stream():
        message_clock["now"] = completed_at
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": CHAPTER})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    bind = db_session.get_bind()
    events: list[str] = []
    with (
        patch("agent.service.run_writing_workflow_streaming", return_value=slow_stream()),
        patch("agent.service.create_session", side_effect=lambda: Session(bind)),
    ):
        async for event in round3_service.process_stream(
            project_id=str(project.id), user_id=str(user.id), message="写第三章", session=db_session
        ):
            events.append(event)

    rows = _ordered(_rows(db_session, _session_id_from_events(events)))
    assert [row.role for row in rows] == ["user", "assistant"]
    user_row, assistant_row = rows
    assert user_row.created_at == sent_at
    assert assistant_row.created_at == completed_at


async def test_stopped_round_partial_save_keeps_the_send_time(
    round3_service, round3_user_with_project, db_session: Session, message_clock
):
    """停止 / 断线：补偿保存（_save_partial_history_sync）也用发出时间。"""
    from agent.core.workflow_events import StreamEvent, StreamEventType

    project = round3_user_with_project["project"]
    user = round3_user_with_project["user"]
    sent_at = message_clock["now"]
    stopped_at = sent_at + timedelta(minutes=2)
    started = asyncio.Event()

    async def writes_then_hangs():
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": CHAPTER})
        started.set()
        await asyncio.sleep(3600)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "到不了"})

    bind = db_session.get_bind()
    events: list[str] = []

    async def consume():
        async for event in round3_service.process_stream(
            project_id=str(project.id), user_id=str(user.id), message="写第五章", session=db_session
        ):
            events.append(event)

    with (
        patch("agent.service.run_writing_workflow_streaming", return_value=writes_then_hangs()),
        patch("agent.service.create_session", side_effect=lambda: Session(bind)),
    ):
        task = asyncio.create_task(consume())
        await wait_for_stream_start(started, task)
        message_clock["now"] = stopped_at
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        rows = await _poll_rows(db_session, _session_id_from_events(events), expected=2)

    rows = _ordered(rows)
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[0].created_at == sent_at
    assert rows[1].created_at == stopped_at


async def test_round_without_output_keeps_the_send_time(
    round3_service, round3_user_with_project, db_session: Session, message_clock
):
    """一个字都没产出就取消：只留用户消息（_append_user_messages_sync），时间仍是发出时间。"""
    from agent.core.workflow_events import StreamEvent, StreamEventType

    project = round3_user_with_project["project"]
    user = round3_user_with_project["user"]
    sent_at = message_clock["now"]
    started = asyncio.Event()

    async def never_yields():
        started.set()
        await asyncio.sleep(3600)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "到不了"})

    bind = db_session.get_bind()
    events: list[str] = []

    async def consume():
        async for event in round3_service.process_stream(
            project_id=str(project.id), user_id=str(user.id), message="帮我写第五章", session=db_session
        ):
            events.append(event)

    with (
        patch("agent.service.run_writing_workflow_streaming", return_value=never_yields()),
        patch("agent.service.create_session", side_effect=lambda: Session(bind)),
    ):
        task = asyncio.create_task(consume())
        await wait_for_stream_start(started, task)
        message_clock["now"] = sent_at + timedelta(minutes=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        rows = await _poll_rows(db_session, _session_id_from_events(events), expected=1)

    assert [(row.role, row.content) for row in rows] == [("user", "帮我写第五章")]
    assert rows[0].created_at == sent_at


async def test_steering_after_the_save_stays_after_the_reply(
    round3_service, round3_user_with_project, db_session: Session
):
    """整轮保存之后才到的 steering 单独追加，不拿发出时间：按时间排序仍在助手回复之后。"""
    from agent.core.message_manager import MessageManager
    from agent.core.steering import get_steering_queue_for_user_async
    from agent.core.workflow_events import StreamEvent, StreamEventType

    project = round3_user_with_project["project"]
    user = round3_user_with_project["user"]
    late_text = "结尾再收一下，别烂尾"

    async def slow_stream():
        await asyncio.sleep(ROUND_SECONDS)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": CHAPTER})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    real_save = MessageManager.save_messages

    async def save_then_steer(self, session, session_id, *args, **kwargs):
        result = await real_save(self, session, session_id, *args, **kwargs)
        queue = await get_steering_queue_for_user_async(session_id, str(user.id))
        await queue.add(late_text)
        return result

    bind = db_session.get_bind()
    events: list[str] = []
    with (
        patch("agent.service.run_writing_workflow_streaming", return_value=slow_stream()),
        patch("agent.service.create_session", side_effect=lambda: Session(bind)),
        patch.object(MessageManager, "save_messages", save_then_steer),
    ):
        async for event in round3_service.process_stream(
            project_id=str(project.id), user_id=str(user.id), message="写第三章", session=db_session
        ):
            events.append(event)

    rows = _ordered(_rows(db_session, _session_id_from_events(events)))
    assert [(row.role, row.content if row.role == "user" else None) for row in rows] == [
        ("user", "写第三章"),
        ("assistant", None),
        ("user", late_text),
    ]
