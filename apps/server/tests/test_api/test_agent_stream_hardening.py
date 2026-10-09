"""/agent/stream 路由层：SSE 心跳、请求级时限、退款判定、断线计费。"""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

import api.agent as agent_api
from models import Project, User
from services.core.auth_service import hash_password

_CHARGED_PERIOD = datetime(2026, 10, 5, 16, tzinfo=UTC)
# 真正的回复正文（超过一句过渡旁白的长度），停止 / 断线时算产出。
_PROSE = "第三章的节奏偏慢：前两节都在交代旧书店的来历，主角直到第三节才第一次碰到那本没有书名的旧账本，读者等得太久，建议把账本提前到开头。"


async def _login_with_project(client: AsyncClient, db_session) -> tuple[str, Project, User]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"stream_hardening_{suffix}",
        email=f"stream_hardening_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Stream Hardening", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    login = await client.post(
        "/api/auth/login", data={"username": user.username, "password": "password123"}
    )
    assert login.status_code == 200
    return login.json()["access_token"], project, user


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _frame_data(text: str, event: str) -> dict | None:
    for frame in text.split("\n\n"):
        lines = frame.strip().splitlines()
        if lines and lines[0] == f"event: {event}":
            return json.loads(lines[1].split(":", 1)[1])
    return None


_FAKE_ASSISTANT_MESSAGE_ID = "assistant-message-of-this-round"


class _FakeService:
    """Yields the given frames; like the real service, hands back the assistant message id
    once the (cancelled) round's history is saved."""

    def __init__(self, frames_factory, message_id: str | None = _FAKE_ASSISTANT_MESSAGE_ID):
        self._frames_factory = frames_factory
        self._message_id = message_id

    async def process_stream(self, *, run_outcome=None, **_kwargs):
        try:
            async for frame in self._frames_factory():
                yield frame
        finally:
            if run_outcome is not None:
                run_outcome.resolve_message_id(self._message_id)


@pytest.fixture(autouse=True)
def _round_outcome_on_test_db(monkeypatch):
    """Placeholder cleanup and outcome records open their own sessions: keep them on the test DB."""
    import database
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(database, "create_session", TestSessionLocal)


async def _post_stream(client, token, project):
    return await client.post(
        "/api/v1/agent/stream",
        json={"project_id": str(project.id), "message": "写第五章"},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_offloaded_refund_preserves_charged_period(db_session):
    """Postgres-style threadpool refunds must target the reservation's day."""
    with (
        patch("api.agent._should_offload_session_work", return_value=True),
        patch("api.agent.asyncio.to_thread", new=AsyncMock(return_value=True)) as to_thread,
    ):
        refunded = await agent_api._refund_quota(
            db_session,
            "user-1",
            _CHARGED_PERIOD,
            billing_reason="internal_error",
        )

    assert refunded is True
    to_thread.assert_awaited_once_with(
        agent_api._release_ai_conversation_sync,
        "user-1",
        _CHARGED_PERIOD,
    )


@pytest.mark.integration
async def test_stream_sends_heartbeat_comment_frames_while_idle(client: AsyncClient, db_session, monkeypatch):
    token, project, _ = await _login_with_project(client, db_session)
    monkeypatch.setattr(agent_api, "AGENT_SSE_HEARTBEAT_INTERVAL_S", 0.05)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        await asyncio.sleep(0.3)
        yield _sse("done", {})

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _post_stream(client, token, project)

    assert response.status_code == 200
    assert ": ping\n\n" in response.text
    assert response.text.rstrip().endswith("data: {}")
    refund.assert_not_called()


@pytest.mark.integration
async def test_stream_wall_clock_deadline_ends_with_timeout_error(client: AsyncClient, db_session, monkeypatch):
    token, project, _ = await _login_with_project(client, db_session)
    monkeypatch.setattr(agent_api, "AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 0.3)
    monkeypatch.setattr(agent_api, "AGENT_SSE_HEARTBEAT_INTERVAL_S", 0.1)
    cancelled = asyncio.Event()

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        yield _sse("done", {})

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _post_stream(client, token, project)

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "ERR_AGENT_RUN_TIMEOUT" in response.text
    assert cancelled.is_set()
    # 没有任何产出：退还额度，并在 error 帧之后告诉前端这一轮不计入。
    assert refund.call_args.kwargs["period_start"] == _CHARGED_PERIOD
    assert _frame_data(response.text, "quota_refunded") == {"refunded": True, "kind": "error"}
    assert response.text.index("event: error") < response.text.index("event: quota_refunded")


@pytest.mark.integration
async def test_tool_failure_circuit_is_not_refunded(client: AsyncClient, db_session):
    token, project, _ = await _login_with_project(client, db_session)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _sse(
            "error",
            {
                "message": "工具 load_skill 连续失败",
                "code": "ERR_AGENT_TOOL_FAILURE_LIMIT",
                "retryable": False,
                "refundable": False,
            },
        )

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _post_stream(client, token, project)

    assert response.status_code == 200
    refund.assert_not_called()
    assert "quota_refunded" not in response.text


@pytest.mark.integration
async def test_error_after_streamed_body_is_not_refunded(client: AsyncClient, db_session):
    token, project, _ = await _login_with_project(client, db_session)

    async def frames():
        yield _sse("file_content", {"file_id": "f1", "chunk": "第五章的正文已经写了三千字……"})
        yield _sse(
            "error",
            {"message": "x", "code": "ERR_AGENT_UPSTREAM_UNAVAILABLE", "retryable": True, "refundable": True},
        )

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        await _post_stream(client, token, project)

    refund.assert_not_called()


@pytest.mark.integration
async def test_real_internal_error_without_output_is_still_refunded(client: AsyncClient, db_session):
    token, project, _ = await _login_with_project(client, db_session)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _sse(
            "error",
            {"message": "x", "code": "ERR_AGENT_UPSTREAM_UNAVAILABLE", "retryable": True, "refundable": True},
        )

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _post_stream(client, token, project)

    refund.assert_called_once()
    assert _frame_data(response.text, "quota_refunded") == {"refunded": True, "kind": "error"}


@pytest.mark.integration
async def test_runaway_stop_without_writes_tells_client_it_was_not_charged(client: AsyncClient, db_session):
    token, project, _ = await _login_with_project(client, db_session)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _sse("content", {"text": "我再看一下第四章……"})
        yield _sse(
            "error",
            {
                "message": "AI 一直在翻看同样的资料",
                "code": "ERR_AGENT_TOOL_FAILURE_LIMIT",
                "retryable": False,
                "refundable": True,
                "reason": "no_progress",
            },
        )

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _post_stream(client, token, project)

    refund.assert_called_once()
    assert _frame_data(response.text, "quota_refunded") == {"refunded": True, "kind": "no_progress"}
    # 退还在终止帧之后才结算，所以说明帧必须排在 error 帧后面。
    assert response.text.index("event: error") < response.text.index("event: quota_refunded")


@pytest.mark.integration
async def test_refund_that_did_not_apply_is_not_announced(client: AsyncClient, db_session):
    """该退但没退成（例如额度行已跨日）：不能告诉作者「不计入」。"""
    token, project, _ = await _login_with_project(client, db_session)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _sse(
            "error",
            {"message": "x", "code": "ERR_AGENT_UPSTREAM_UNAVAILABLE", "retryable": True, "refundable": True},
        )

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=False) as refund,
    ):
        response = await _post_stream(client, token, project)

    refund.assert_called_once()
    assert "quota_refunded" not in response.text


def _billing_log(log_spy) -> dict:
    billing_logs = [
        call.kwargs for call in log_spy.call_args_list
        if len(call.args) >= 3 and call.args[2] == "Agent stream billing evaluated"
    ]
    assert billing_logs
    return billing_logs[-1]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("output_frames", "billing_reason", "refunded"),
    [
        ([], "client_disconnected_no_output", True),
        ([_sse("content", {"text": _PROSE})], "client_disconnected", False),
    ],
)
async def test_generator_exit_disconnect_refunds_only_rounds_without_output(
    client: AsyncClient, db_session, output_frames, billing_reason, refunded
):
    """以 aclose（GeneratorExit）方式断线：没有任何产出就在后台退还，已有产出照常计费。"""
    _token, project, user = await _login_with_project(client, db_session)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        for frame in output_frames:
            yield frame
        await asyncio.sleep(30)
        yield _sse("done", {})

    body = agent_api.AgentRequest(project_id=str(project.id), message="写第五章")
    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
        patch("api.agent.log_with_context") as log_spy,
    ):
        response = await agent_api.stream_request(
            body=body,
            session=db_session,
            current_user=user,
            accept_language=None,
            _rate_limit=0,
        )
        iterator = response.body_iterator
        received = [await iterator.__anext__() for _ in range(1 + len(output_frames))]
        assert received[0].startswith("event: session_started")
        await iterator.aclose()
        await asyncio.gather(*agent_api._detached_refund_tasks)

    assert refund.called is refunded
    log = _billing_log(log_spy)
    assert log["billing_reason"] == billing_reason
    assert log["client_disconnected"] is True
    assert log["user_stopped"] is False


@pytest.mark.integration
@pytest.mark.parametrize(
    ("output_frames", "billing_reason", "refund_kind"),
    [
        ([_sse("thinking_content", {"content": "先读大纲"})], "user_stopped_no_output", "stopped"),
        ([_sse("content", {"text": _PROSE})], "user_stopped", None),
    ],
)
async def test_author_stop_ends_run_on_open_stream_and_settles_billing(
    client: AsyncClient, db_session, output_frames, billing_reason, refund_kind
):
    """作者点停止：运行在原连接上收尾（停止卡片 + done），没有产出时退还并告诉前端。"""
    _token, project, user = await _login_with_project(client, db_session)
    source_closed = asyncio.Event()

    async def frames():
        try:
            yield _sse("session_started", {"session_id": "s1"})
            for frame in output_frames:
                yield frame
            await asyncio.sleep(30)
            yield _sse("done", {})
        finally:
            source_closed.set()

    body = agent_api.AgentRequest(project_id=str(project.id), message="写第五章")
    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
        patch("api.agent.log_with_context") as log_spy,
    ):
        response = await agent_api.stream_request(
            body=body,
            session=db_session,
            current_user=user,
            accept_language=None,
            _rate_limit=0,
        )
        run_id = response.headers["X-Agent-Run-ID"]
        iterator = response.body_iterator
        for _ in range(1 + len(output_frames)):
            await iterator.__anext__()

        stopped = await agent_api.stop_stream(
            body=agent_api.StopRequest(agent_run_id=run_id),
            current_user=user,
            _rate_limit=0,
        )
        assert stopped.stop_requested is True
        rest = "".join([frame async for frame in iterator])

    assert source_closed.is_set()
    assert _frame_data(rest, "workflow_stopped") == {
        "reason": "user_stopped",
        "message": agent_api.USER_STOPPED_MESSAGE,
    }
    done = _frame_data(rest, "done")
    # 停止轮的 done 带上这一轮助手消息的 id：反馈、时间戳绑到正确的消息上。
    assert done["assistant_message_id"] == _FAKE_ASSISTANT_MESSAGE_ID
    assert done["session_id"] == "s1"
    assert done["stop_reason"] == "user_stopped"
    assert done["produced_output"] is (refund_kind is None)
    refund_frame = _frame_data(rest, "quota_refunded")
    if refund_kind is None:
        assert refund_frame is None
        refund.assert_not_called()
    else:
        assert refund_frame == {"refunded": True, "kind": refund_kind}
        refund.assert_called_once()
    log = _billing_log(log_spy)
    assert log["billing_reason"] == billing_reason
    assert log["user_stopped"] is True
    assert log["client_disconnected"] is False


@pytest.mark.integration
async def test_stop_request_for_another_users_run_does_not_stop_it(client: AsyncClient, db_session):
    """停止信号按用户命名：别人拿到 run id 也停不了这次运行。"""
    _token, project, owner = await _login_with_project(client, db_session)
    _other_token, _other_project, other = await _login_with_project(client, db_session)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        await asyncio.sleep(0.8)
        yield _sse("content", {"text": "照常写完"})
        yield _sse("done", {})

    body = agent_api.AgentRequest(project_id=str(project.id), message="写第五章")
    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True),
    ):
        response = await agent_api.stream_request(
            body=body,
            session=db_session,
            current_user=owner,
            accept_language=None,
            _rate_limit=0,
        )
        iterator = response.body_iterator
        await iterator.__anext__()
        stopped = await agent_api.stop_stream(
            body=agent_api.StopRequest(agent_run_id=response.headers["X-Agent-Run-ID"]),
            current_user=other,
            _rate_limit=0,
        )
        rest = "".join([frame async for frame in iterator])

    assert stopped.stop_requested is False

    assert _frame_data(rest, "workflow_stopped") is None
    assert _frame_data(rest, "content") == {"text": "照常写完"}


@pytest.mark.integration
async def test_billing_log_line_carries_run_summary(client: AsyncClient, db_session):
    """每次 run 一行结构化摘要：模型、token、调用次数、LLM 耗时、结束原因、是否退款。"""
    _token, project, user = await _login_with_project(client, db_session)

    class ReportingService:
        async def process_stream(self, *, run_report, **_kwargs):
            yield _sse("content", {"text": "好的"})
            run_report.update(
                {
                    "model": "deepseek-test",
                    "input_tokens": 1200,
                    "output_tokens": 300,
                    "model_calls": 3,
                    "llm_duration_ms": 4567.0,
                    "stop_reason": "end_turn",
                }
            )
            yield _sse("done", {})

    body = agent_api.AgentRequest(project_id=str(project.id), message="写第五章")
    with (
        patch("api.agent.get_agent_service", return_value=ReportingService()),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True),
        patch("api.agent.log_with_context") as log_spy,
    ):
        response = await agent_api.stream_request(
            body=body,
            session=db_session,
            current_user=user,
            accept_language=None,
            _rate_limit=0,
        )
        _ = [frame async for frame in response.body_iterator]

    summary = [
        call.kwargs for call in log_spy.call_args_list
        if len(call.args) >= 3 and call.args[2] == "Agent stream billing evaluated"
    ][-1]
    assert summary["run_model"] == "deepseek-test"
    assert summary["run_input_tokens"] == 1200
    assert summary["run_output_tokens"] == 300
    assert summary["run_model_calls"] == 3
    assert summary["run_llm_duration_ms"] == 4567.0
    assert summary["run_stop_reason"] == "end_turn"
    assert summary["billing_reason"] == "completed"
    assert summary["refunded"] is False


# ------------------------------------------------------------------ 停止 / 断线一轮的收尾


def _seed_round(db_session, project, user):
    """A chat session with this round's user + (empty, stopped) assistant message, and files."""
    from models import ChatMessage, ChatSession, File

    chat = ChatSession(user_id=user.id, project_id=project.id)
    db_session.add(chat)
    db_session.commit()
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="写第一章"))
    assistant = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="",
        message_metadata=json.dumps({"stop_reason": "user_stopped"}),
    )
    db_session.add(assistant)
    placeholder = File(project_id=project.id, title="第1章 最后一页", content="", file_type="draft")
    filled = File(project_id=project.id, title="第2章 入宅", content="", file_type="draft")
    older_empty = File(project_id=project.id, title="作者自己建的空白页", content="", file_type="draft")
    db_session.add_all([placeholder, filled, older_empty])
    db_session.commit()
    return assistant.id, placeholder, filled, older_empty


def _create_file_result(file) -> str:
    return _sse(
        "tool_result",
        {
            "tool_name": "create_file",
            "status": "success",
            "data": {"id": file.id, "title": file.title, "content": "", "file_type": "draft"},
        },
    )


async def _stream(db_session, user, project, service):
    body = agent_api.AgentRequest(project_id=str(project.id), message="写第一章")
    return await agent_api.stream_request(
        body=body, session=db_session, current_user=user, accept_language=None, _rate_limit=0
    )


@pytest.mark.integration
async def test_stop_after_only_narration_and_an_empty_chapter_refunds_and_removes_the_placeholder(
    client: AsyncClient, db_session
):
    """一句过渡旁白 + create_file 建了空章节就停：不计入，空章节移除并告诉作者，终态写回助手消息。"""
    from models import ChatMessage, File

    _token, project, user = await _login_with_project(client, db_session)
    message_id, placeholder, _filled, older_empty = _seed_round(db_session, project, user)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _sse("content", {"text": "我先看一遍全书大纲。"})
        yield _sse("tool_call", {"tool_name": "query_files", "arguments": {}})
        yield _sse("tool_result", {"tool_name": "query_files", "status": "success"})
        yield _create_file_result(placeholder)
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _stream(db_session, user, project, None)
        iterator = response.body_iterator
        for _ in range(5):
            await iterator.__anext__()
        await agent_api.stop_stream(
            body=agent_api.StopRequest(agent_run_id=response.headers["X-Agent-Run-ID"]),
            current_user=user,
            _rate_limit=0,
        )
        rest = "".join([frame async for frame in iterator])
        await asyncio.gather(*agent_api._round_outcome_tasks)

    refund.assert_called_once()
    assert _frame_data(rest, "done")["assistant_message_id"] == message_id
    assert _frame_data(rest, "done")["produced_output"] is False
    assert _frame_data(rest, "quota_refunded") == {
        "refunded": True,
        "kind": "stopped",
        "removed_files": [{"id": placeholder.id, "title": "第1章 最后一页"}],
    }
    db_session.expire_all()
    assert db_session.get(File, placeholder.id).is_deleted is True
    # 不是这一轮建的空文件不动。
    assert db_session.get(File, older_empty.id).is_deleted is False
    metadata = json.loads(db_session.get(ChatMessage, message_id).message_metadata)
    assert metadata["stop_reason"] == "user_stopped"
    assert metadata["stop_outcome"] == {
        "reason": "user_stopped",
        "charged": False,
        "saved_output": False,
        "removed_files": ["第1章 最后一页"],
    }


@pytest.mark.integration
async def test_stop_after_chapter_body_was_written_is_charged_and_keeps_files(
    client: AsyncClient, db_session
):
    """正文已流进章节再停：计费，文件一个都不动，终态写「已写入的内容已保存」。"""
    from models import ChatMessage, File

    _token, project, user = await _login_with_project(client, db_session)
    message_id, placeholder, filled, _older = _seed_round(db_session, project, user)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _create_file_result(filled)
        yield _sse("file_content", {"file_id": filled.id, "chunk": "雨还在下。"})
        yield _create_file_result(placeholder)
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _stream(db_session, user, project, None)
        iterator = response.body_iterator
        for _ in range(4):
            await iterator.__anext__()
        await agent_api.stop_stream(
            body=agent_api.StopRequest(agent_run_id=response.headers["X-Agent-Run-ID"]),
            current_user=user,
            _rate_limit=0,
        )
        rest = "".join([frame async for frame in iterator])
        await asyncio.gather(*agent_api._round_outcome_tasks)

    refund.assert_not_called()
    assert _frame_data(rest, "quota_refunded") is None
    db_session.expire_all()
    assert db_session.get(File, placeholder.id).is_deleted is False
    assert db_session.get(File, filled.id).is_deleted is False
    outcome = json.loads(db_session.get(ChatMessage, message_id).message_metadata)["stop_outcome"]
    assert outcome == {"reason": "user_stopped", "charged": True, "saved_output": True}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("output_frames", "charged"),
    [
        ([_sse("content", {"text": "正在查看已有的全书大纲，确认现有设定后再梳理主线。"})], False),
        (
            [
                _sse(
                    "tool_result",
                    {
                        "tool_name": "parallel_execute",
                        "status": "success",
                        "data": {"tasks": [{"type": "write_chapter", "status": "completed"}]},
                    },
                )
            ],
            True,
        ),
    ],
)
async def test_disconnect_settles_in_background_and_records_the_outcome(
    client: AsyncClient, db_session, output_frames, charged
):
    """离开 / 后退断线：旁白不算产出（退还并移除空章节）；并行写章已完成算产出（计费）。"""
    from models import ChatMessage, File

    _token, project, user = await _login_with_project(client, db_session)
    message_id, placeholder, _filled, _older = _seed_round(db_session, project, user)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _create_file_result(placeholder)
        for frame in output_frames:
            yield frame
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _stream(db_session, user, project, None)
        iterator = response.body_iterator
        for _ in range(2 + len(output_frames)):
            await iterator.__anext__()
        await iterator.aclose()
        await asyncio.gather(*agent_api._detached_refund_tasks)
        await asyncio.gather(*agent_api._round_outcome_tasks)

    assert refund.called is (not charged)
    db_session.expire_all()
    assert db_session.get(File, placeholder.id).is_deleted is (not charged)
    metadata = json.loads(db_session.get(ChatMessage, message_id).message_metadata)
    assert metadata["stop_reason"] == "client_disconnected"
    assert metadata["stop_outcome"]["reason"] == "client_disconnected"
    assert metadata["stop_outcome"]["charged"] is charged
