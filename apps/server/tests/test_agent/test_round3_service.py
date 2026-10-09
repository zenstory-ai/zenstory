"""Round 3 深度 review 回归测试：服务编排（agent/service.py）。

覆盖的缺陷：
- rank 7(b)：普通异常路径必须与取消路径对等地补偿保存整轮历史。旧守卫
  `if user_id and not history_saved and consumed_steering:` 额外要求本轮消费过
  steering，而绝大多数请求根本没有 steering，于是一次瞬时 DB 冲突就让
  「用户消息 + 已经流式吐给用户看的正文 + 工具调用记录」整轮消失。
- rank 7(a)：历史落库之后、队列被 cleanup 删除之前入队的 steering，被 finally
  的兜底 drain 取出后既没喂给 agent 也没落库，而 /agent/steer 已经回过
  queued=True。并发变体：队列仍被其它 run 持有时，这些消息必须交还回队列。
- rank 35：生成尚未产出任何文本时取消，不得写入 content 为空的 assistant 消息，
  message_count 也不能凭空 +2。
"""

import asyncio
import json
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from models import ChatMessage, ChatSession, File, Project, User
from services.core.auth_service import hash_password
from tests.test_agent.test_service import wait_for_stream_start

pytestmark = pytest.mark.usefixtures("writing_prompt_configs")


@pytest.fixture
def round3_user_with_project(db_session: Session):
    """本文件专用的用户 + 项目 + 草稿（邮箱与其它测试文件隔离）。"""
    user = User(
        email="round3_service@example.com",
        username="round3service",
        hashed_password=hash_password("password123"),
        name="Round3 Service User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name="Round3 Service Project",
        description="round3 回归测试项目",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    draft = File(
        title="第一章",
        content="旧内容",
        file_type="draft",
        project_id=project.id,
        user_id=user.id,
    )
    db_session.add(draft)
    db_session.commit()

    return {"user": user, "project": project}


@pytest.fixture
def round3_service():
    """带 mock context assembler 的 AgentService。"""
    from unittest.mock import MagicMock

    from agent.schemas.context import ContextData
    from agent.service import AgentService

    assembler = MagicMock()
    assembler.assemble.return_value = ContextData(items=[], context="", token_estimate=0)
    return AgentService(context_assembler=assembler)


def _session_id_from_events(events: list[str]) -> str:
    started = [e for e in events if "event: session_started" in e]
    assert started, "缺少 session_started 事件"
    return json.loads(started[0].split("data:", 1)[1].strip())["session_id"]


def _rows(db_session: Session, session_id: str) -> list[ChatMessage]:
    db_session.rollback()
    # 被测代码的补偿保存/追加走的是独立 session（asyncio.to_thread + create_session），
    # 提交对本 session 的身份映射不可见。rollback() 只在当前确实有活动事务时才会
    # 让对象过期，是否有活动事务又取决于此前的读写时机——所以必须显式 expire_all，
    # 否则 db_session.get(ChatSession, ...) 会返回落库前的旧副本（表现为
    # message_count 随执行顺序随机对不上）。
    db_session.expire_all()
    return db_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == session_id)
    ).all()


async def _poll_rows(
    db_session: Session, session_id: str, expected: int, attempts: int = 100
) -> list[ChatMessage]:
    """后台补偿保存是独立任务，轮询等待它落库。"""
    rows: list[ChatMessage] = []
    for _ in range(attempts):
        await asyncio.sleep(0.05)
        rows = _rows(db_session, session_id)
        if len(rows) >= expected:
            break
    return rows


CHAPTER = "第三章 雪原的狐狸\n" + "她在雪地里走了很久。" * 20


@pytest.mark.unit
class TestRound3ExceptionPathCompensation:
    """rank 7(b)：异常路径的补偿保存。"""

    async def test_exception_path_persists_whole_turn_without_steering(
        self, round3_service, round3_user_with_project, db_session: Session
    ):
        """save_messages 抛异常 + 本轮零 steering，整轮历史仍必须补偿落库。"""
        from agent.core.message_manager import MessageManager
        from agent.core.workflow_events import StreamEvent, StreamEventType

        project = round3_user_with_project["project"]
        user = round3_user_with_project["user"]

        async def mock_stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": CHAPTER})
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"}
            )

        async def boom(self, *args, **kwargs):
            raise RuntimeError("PendingRollbackError: transient DB conflict")

        bind = db_session.get_bind()
        events: list[str] = []

        with (
            patch("agent.service.run_writing_workflow_streaming", return_value=mock_stream()),
            patch("agent.service.create_session", side_effect=lambda: Session(bind)),
            patch.object(MessageManager, "save_messages", boom),
        ):
            async for event in round3_service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="写第三章",
                session=db_session,
            ):
                events.append(event)

        assert any("event: error" in e for e in events), "落库失败必须向前端发 error 事件"

        session_id = _session_id_from_events(events)
        rows = _rows(db_session, session_id)
        assert any(
            r.role == "user" and r.content == "写第三章" for r in rows
        ), "异常路径必须补偿保存用户消息"
        assert any(
            r.role == "assistant" and r.content == CHAPTER for r in rows
        ), "异常路径必须补偿保存已流式吐给用户的正文"

    async def test_exception_before_stream_persists_user_message_only(
        self, round3_service, round3_user_with_project, db_session: Session
    ):
        """工作流还没产出任何内容就失败：只留用户消息，不写空 assistant。"""
        project = round3_user_with_project["project"]
        user = round3_user_with_project["user"]

        bind = db_session.get_bind()
        events: list[str] = []

        with (
            patch(
                "agent.service.run_writing_workflow_streaming",
                side_effect=RuntimeError("workflow boot failure"),
            ),
            patch("agent.service.create_session", side_effect=lambda: Session(bind)),
        ):
            async for event in round3_service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="开局就炸",
                session=db_session,
            ):
                events.append(event)

        session_id = _session_id_from_events(events)
        rows = _rows(db_session, session_id)
        assert [(r.role, r.content) for r in rows] == [("user", "开局就炸")]

        chat_session = db_session.get(ChatSession, session_id)
        assert chat_session is not None
        assert chat_session.message_count == 1, "message_count 必须按实际写入行数递增"


@pytest.mark.unit
class TestRound3LateSteering:
    """rank 7(a)：历史落库之后才到达的 steering。"""

    async def test_steering_queued_after_history_save_is_persisted(
        self, round3_service, round3_user_with_project, db_session: Session
    ):
        """save_messages 之后、cleanup 之前入队的 steering 必须单独追加落库。"""
        from agent.core.message_manager import MessageManager
        from agent.core.steering import get_steering_queue_for_user_async
        from agent.core.workflow_events import StreamEvent, StreamEventType

        project = round3_user_with_project["project"]
        user = round3_user_with_project["user"]
        late_text = "结尾再收一下，别烂尾"

        async def mock_stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": CHAPTER})
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"}
            )

        real_save = MessageManager.save_messages

        async def save_then_steer(self, session, session_id, *args, **kwargs):
            result = await real_save(self, session, session_id, *args, **kwargs)
            # 等价于用户在 done 事件到达前那一瞬 POST /agent/steer
            queue = await get_steering_queue_for_user_async(session_id, str(user.id))
            await queue.add(late_text)
            return result

        bind = db_session.get_bind()
        events: list[str] = []

        with (
            patch("agent.service.run_writing_workflow_streaming", return_value=mock_stream()),
            patch("agent.service.create_session", side_effect=lambda: Session(bind)),
            patch.object(MessageManager, "save_messages", save_then_steer),
        ):
            async for event in round3_service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="写第三章",
                session=db_session,
            ):
                events.append(event)

        session_id = _session_id_from_events(events)
        rows = _rows(db_session, session_id)

        assert any(
            r.role == "user" and r.content == late_text for r in rows
        ), "/agent/steer 已确认 queued 的消息必须落库"
        # 只能追加 user 行，不能整轮重写
        assistant_rows = [r for r in rows if r.role == "assistant"]
        assert len(assistant_rows) == 1, "补写 steering 不得把 assistant 正文写第二遍"

        chat_session = db_session.get(ChatSession, session_id)
        assert chat_session is not None
        assert chat_session.message_count == len(rows)

    async def test_late_steering_is_handed_back_to_concurrent_run(
        self, round3_service, round3_user_with_project, db_session: Session
    ):
        """同一 chat session 还有别的 run 在生成时，兜底 drain 出来的 steering
        必须回填给它，而不是被先结束的 run 吞掉或提前写进历史。"""
        from agent.core.message_manager import MessageManager
        from agent.core.steering import (
            cleanup_steering_queue_async,
            create_steering_queue_async,
            get_steering_queue_for_user_async,
        )
        from agent.core.workflow_events import StreamEvent, StreamEventType

        project = round3_user_with_project["project"]
        user = round3_user_with_project["user"]
        other_run_id = "round3-concurrent-run-b"
        late_text = "给另一个 run 的引导"
        captured: dict[str, str] = {}

        def fake_workflow(writing_state, **_kwargs):
            async def _stream():
                # 另一个并发 run 也持有同一个 chat session 的队列
                captured["session_id"] = writing_state["session_id"]
                await create_steering_queue_async(
                    writing_state["session_id"], str(user.id), run_id=other_run_id
                )
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": CHAPTER})
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"}
                )

            return _stream()

        real_save = MessageManager.save_messages

        async def save_then_steer(self, session, session_id, *args, **kwargs):
            result = await real_save(self, session, session_id, *args, **kwargs)
            queue = await get_steering_queue_for_user_async(session_id, str(user.id))
            await queue.add(late_text)
            return result

        bind = db_session.get_bind()
        events: list[str] = []

        try:
            with (
                patch("agent.service.run_writing_workflow_streaming", side_effect=fake_workflow),
                patch("agent.service.create_session", side_effect=lambda: Session(bind)),
                patch.object(MessageManager, "save_messages", save_then_steer),
            ):
                async for event in round3_service.process_stream(
                    project_id=str(project.id),
                    user_id=str(user.id),
                    message="写第三章",
                    session=db_session,
                ):
                    events.append(event)

            session_id = _session_id_from_events(events)
            queue = await get_steering_queue_for_user_async(session_id, str(user.id))
            still_pending = [m.content for m in await queue.peek()]
            assert late_text in still_pending, "队列仍被其它 run 持有时必须把消息交还回去"

            rows = _rows(db_session, session_id)
            assert not any(
                r.content == late_text for r in rows
            ), "已交还给并发 run 的消息不应同时写进本轮历史"
        finally:
            if captured.get("session_id"):
                await cleanup_steering_queue_async(
                    captured["session_id"], run_id=other_run_id
                )


@pytest.mark.unit
class TestRound3CancellationTermination:
    """rank 35 + 取消路径的终结动作对等性。"""

    async def test_cancel_before_any_content_writes_no_empty_assistant(
        self, round3_service, round3_user_with_project, db_session: Session
    ):
        """模型还没吐出第一个 token 就取消：只留用户消息，不写空 assistant。"""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        project = round3_user_with_project["project"]
        user = round3_user_with_project["user"]
        started = asyncio.Event()

        async def never_yields():
            started.set()
            await asyncio.sleep(3600)
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "到不了"})

        bind = db_session.get_bind()
        events: list[str] = []

        async def consume():
            async for event in round3_service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="帮我写第五章",
                session=db_session,
            ):
                events.append(event)

        with (
            patch("agent.service.run_writing_workflow_streaming", return_value=never_yields()),
            patch("agent.service.create_session", side_effect=lambda: Session(bind)),
        ):
            task = asyncio.create_task(consume())
            await wait_for_stream_start(started, task)
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            session_id = _session_id_from_events(events)
            rows = await _poll_rows(db_session, session_id, expected=1)

        assert [(r.role, r.content) for r in rows] == [("user", "帮我写第五章")]
        assert not any(
            r.role == "assistant" for r in rows
        ), "尚未产出任何内容时不得写入空 assistant 消息"

        chat_session = db_session.get(ChatSession, session_id)
        assert chat_session is not None
        assert chat_session.message_count == 1, "message_count 不能凭空 +2"

    async def test_cancel_after_history_saved_still_persists_queued_steering(
        self, round3_service, round3_user_with_project, db_session: Session
    ):
        """整轮历史已落库、done 事件还没发出去时被取消：队列里已确认 queued 的
        steering 仍必须落库，不能被随后的队列清理删掉。"""
        from agent.core.message_manager import MessageManager
        from agent.core.steering import get_steering_queue_for_user_async
        from agent.core.workflow_events import StreamEvent, StreamEventType

        project = round3_user_with_project["project"]
        user = round3_user_with_project["user"]
        late_text = "取消前最后一句引导"

        async def mock_stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": CHAPTER})
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"}
            )

        real_save = MessageManager.save_messages

        async def save_then_steer(self, session, session_id, *args, **kwargs):
            result = await real_save(self, session, session_id, *args, **kwargs)
            queue = await get_steering_queue_for_user_async(session_id, str(user.id))
            await queue.add(late_text)
            return result

        def _cancel_instead_of_done(*_args, **_kwargs):
            # 落库完成、done 事件即将发出的那一瞬客户端断开
            raise asyncio.CancelledError()

        bind = db_session.get_bind()
        events: list[str] = []
        scheduled: list[asyncio.Task] = []
        real_schedule = round3_service._schedule_background_cleanup

        def _tracking_schedule(coro, **kwargs):
            task = real_schedule(coro, **kwargs)
            scheduled.append(task)
            return task

        async def consume():
            async for event in round3_service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="写第三章",
                session=db_session,
            ):
                events.append(event)

        with (
            patch("agent.service.run_writing_workflow_streaming", return_value=mock_stream()),
            patch("agent.service.create_session", side_effect=lambda: Session(bind)),
            patch.object(MessageManager, "save_messages", save_then_steer),
            patch("agent.service.done_event", side_effect=_cancel_instead_of_done),
            patch.object(
                round3_service, "_schedule_background_cleanup", side_effect=_tracking_schedule
            ),
        ):
            with pytest.raises(asyncio.CancelledError):
                await consume()

            if scheduled:
                await asyncio.gather(*scheduled, return_exceptions=True)

            session_id = _session_id_from_events(events)
            rows = await _poll_rows(db_session, session_id, expected=3)

        assert any(
            r.role == "user" and r.content == late_text for r in rows
        ), "历史已落库后被取消，队列里的 steering 仍必须落库"
        assistant_rows = [r for r in rows if r.role == "assistant"]
        assert len(assistant_rows) == 1, "取消路径不得把整轮历史重写一遍"
