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

    def __init__(self, frames_factory, message_id: str | None = _FAKE_ASSISTANT_MESSAGE_ID, saved_file_word_counts=None):
        self._frames_factory = frames_factory
        self._message_id = message_id
        self._saved_file_word_counts = saved_file_word_counts

    async def process_stream(self, *, run_outcome=None, **_kwargs):
        try:
            async for frame in self._frames_factory():
                yield frame
        finally:
            if run_outcome is not None:
                if self._saved_file_word_counts is not None:
                    run_outcome.saved_file_word_counts = self._saved_file_word_counts
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


@pytest.mark.integration
@pytest.mark.parametrize("invalid_field", [
    {"turn_disposition": "continue-ish"}, {"clarify_first": "probably"},
])
async def test_invalid_semantic_route_preserves_work_and_usage_and_refunds_once(
    client, db_session, invalid_field
):
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.stream_adapter import StreamAdapter
    from models import File

    token, project, user = await _login_with_project(client, db_session)
    original = "她把钥匙留在信封里。\n\n船没有靠岸。"
    file = File(project_id=project.id, user_id=user.id, title="作者原稿", content=original, file_type="draft")
    db_session.add(file)
    db_session.commit()
    file_id = file.id
    usage = {"input_tokens": 17, "output_tokens": 9, "total_tokens": 26}
    payload = {"agent_type": "writer", "workflow_type": "quick", **invalid_field}
    route = AsyncMock(return_value={
        "content": [{"type": "text", "text": json.dumps(payload)}], "usage": usage,
    })
    agent = AsyncMock(side_effect=AssertionError("No agent may execute after a failed route"))
    adapter = StreamAdapter()
    state = {"user_message": "写第五章", "messages": [], "system_prompt": "", "generation_mode": "quality"}

    async def frames():
        yield _sse("session_started", {"session_id": "synthetic-semantic-route"})
        workflow = run_writing_workflow_streaming(state=state, thread_id="synthetic-semantic-route")
        async for frame in adapter.process_workflow_events(workflow):
            yield _sse(getattr(frame.type, "value", frame.type), frame.data)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
        patch("agent.graph.router._route_with_chat_model", route),
        patch("agent.graph.writing_graph.run_streaming_agent", agent),
    ):
        response = await _post_stream(client, token, project)

    error = _frame_data(response.text, "error")
    assert error["code"] == "ERR_AGENT_ROUTING_FAILED"
    assert error["retryable"] is True
    assert response.text.count("event: error\n") == 1
    assert "event: done\n" not in response.text
    assert _frame_data(response.text, "quota_refunded") == {"refunded": True, "kind": "error"}
    refund.assert_called_once()
    assert refund.call_args.kwargs["period_start"] == _CHARGED_PERIOD
    agent.assert_not_called()
    route.assert_awaited_once()
    assert adapter.get_last_message_metadata()["usage"] == usage
    assert state["user_message"] == "写第五章"
    db_session.expire_all()
    assert db_session.get(File, file_id).content == original


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
                    "first_tool_started_ms": 100.0,
                    "first_body_persisted_ms": None,
                    "stage_timings": {"model": {"count": 2, "total_ms": 1000.0, "max_ms": 600.0,
                                                  "error_count": 0, "interrupted_count": 0}},
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
    assert summary["run_first_tool_started_ms"] == 100.0
    assert summary["run_first_body_persisted_ms"] is None
    assert summary["run_stage_timings"]["model"]["max_ms"] == 600.0



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
    # 与真实 create_file 结果一样带建好时的版本（serialize_file 的 updated_at）。
    return _sse(
        "tool_result",
        {
            "tool_name": "create_file",
            "status": "success",
            "data": {
                "id": file.id, "title": file.title, "content": "", "file_type": "draft",
                "updated_at": file.updated_at.isoformat(),
            },
        },
    )


async def _stream(db_session, user, project, service, *, client_stop_contract: str | None = "1"):
    # 默认是认识停止约定的新前端（带 X-Client-Stop-Contract: 1）。
    body = agent_api.AgentRequest(project_id=str(project.id), message="写第一章")
    return await agent_api.stream_request(
        body=body,
        session=db_session,
        current_user=user,
        accept_language=None,
        client_stop_contract=client_stop_contract,
        _rate_limit=0,
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
    # 没有写进正文的文件：done 不带 written_files（与旧服务端的帧一样）。
    assert "written_files" not in _frame_data(rest, "done")
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
@pytest.mark.parametrize("then_disconnect", [False, True])
async def test_refunded_stop_keeps_a_blank_chapter_the_author_has_unsaved_text_in(
    client: AsyncClient, db_session, then_disconnect
):
    """停止前编辑器没存下作者在空章节里打的字（保存失败 / 超时）：停止请求带上这个文件，
    这一轮照样退还，但这一章不被当作空白占位文件移除；离开页面（停止后马上断线）也一样。"""
    from models import File

    _token, project, user = await _login_with_project(client, db_session)
    message_id, placeholder, filled, _older = _seed_round(db_session, project, user)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _create_file_result(placeholder)
        yield _create_file_result(filled)
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _stream(db_session, user, project, None)
        iterator = response.body_iterator
        for _ in range(3):
            await iterator.__anext__()
        await agent_api.stop_stream(
            body=agent_api.StopRequest(
                agent_run_id=response.headers["X-Agent-Run-ID"], keep_file_ids=[placeholder.id]
            ),
            current_user=user,
            _rate_limit=0,
        )
        if then_disconnect:
            await iterator.aclose()
            await asyncio.gather(*agent_api._detached_refund_tasks)
        else:
            rest = "".join([frame async for frame in iterator])
            assert _frame_data(rest, "quota_refunded")["removed_files"] == [
                {"id": filled.id, "title": "第2章 入宅"}
            ]
        await asyncio.gather(*agent_api._round_outcome_tasks)

    refund.assert_called_once()
    db_session.expire_all()
    assert db_session.get(File, placeholder.id).is_deleted is False
    # 作者没在里面打字的空章节照常移除。
    assert db_session.get(File, filled.id).is_deleted is True


@pytest.mark.integration
async def test_refunded_stop_keeps_a_new_blank_chapter_the_author_renamed_meanwhile(
    client: AsyncClient, db_session
):
    """这一轮新建的空章节，作者在生成期间给它改了标题（版本前进，正文仍空）：照样退还，
    但它不再是这一轮原样留下的占位文件，不删；终态不说移除了它。"""
    from config.datetime_utils import advance_timestamp, utcnow
    from models import ChatMessage, File

    _token, project, user = await _login_with_project(client, db_session)
    message_id, placeholder, _filled, _older = _seed_round(db_session, project, user)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _create_file_result(placeholder)
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _stream(db_session, user, project, None)
        iterator = response.body_iterator
        for _ in range(2):
            await iterator.__anext__()
        placeholder.title = "作者改的标题"
        placeholder.updated_at = advance_timestamp(placeholder.updated_at, now=utcnow())
        db_session.add(placeholder)
        db_session.commit()
        await agent_api.stop_stream(
            body=agent_api.StopRequest(agent_run_id=response.headers["X-Agent-Run-ID"]),
            current_user=user,
            _rate_limit=0,
        )
        rest = "".join([frame async for frame in iterator])
        await asyncio.gather(*agent_api._round_outcome_tasks)

    refund.assert_called_once()
    assert _frame_data(rest, "quota_refunded") == {"refunded": True, "kind": "stopped"}
    db_session.expire_all()
    kept = db_session.get(File, placeholder.id)
    assert kept.is_deleted is False and kept.title == "作者改的标题"
    metadata = json.loads(db_session.get(ChatMessage, message_id).message_metadata)
    assert metadata["stop_outcome"] == {"reason": "user_stopped", "charged": False, "saved_output": False}


@pytest.mark.integration
async def test_stop_after_chapter_body_was_written_is_charged_and_keeps_files(
    client: AsyncClient, db_session
):
    """正文已流进章节再停：计费，文件一个都不动，终态写「已写入的内容已保存」。"""
    from models import ChatMessage, File

    _token, project, user = await _login_with_project(client, db_session)
    message_id, placeholder, filled, _older = _seed_round(db_session, project, user)
    filled.content = "雨还在下。"
    db_session.add(filled)
    db_session.commit()

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _create_file_result(filled)
        yield _sse("file_content", {"file_id": filled.id, "chunk": "雨还在下。"})
        yield _create_file_result(placeholder)
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id, {filled.id: 4})),
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
    # 终态列出写进了正文的章节（空白的第1章不算），作者刷新后知道写了什么。
    assert outcome == {
        "reason": "user_stopped",
        "charged": True,
        "saved_output": True,
        "written_files": [{"id": filled.id, "title": "第2章 入宅", "words": 4}],
        "unwritten_files": [{"id": placeholder.id, "title": placeholder.title}],
    }


@pytest.mark.integration
async def test_stopped_round_done_frame_names_the_written_files_like_the_stop_outcome(
    client: AsyncClient, db_session
):
    """停止当场的 done 帧就带 written_files，与之后落库的 stop_outcome 同一份：作者不刷新
    也看得到「已保存《第2章 入宅》…」。只建了空文件的第1章不列。"""
    from models import ChatMessage

    _token, project, user = await _login_with_project(client, db_session)
    message_id, placeholder, filled, older = _seed_round(db_session, project, user)
    body = "雨还在下。旧书店的门没有锁。"

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _create_file_result(placeholder)
        yield _sse(
            "tool_result",
            {
                "tool_name": "create_file",
                "status": "success",
                "data": {"id": filled.id, "title": filled.title, "content": body, "file_type": "draft"},
            },
        )
        yield _sse(
            "tool_result",
            {
                "tool_name": "edit_file",
                "status": "success",
                "data": {"id": older.id, "title": older.title, "mutation_applied": True},
            },
        )
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True),
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

    done = _frame_data(rest, "done")
    assert done["stop_reason"] == "user_stopped"
    assert done["written_files"] == [
        {"id": filled.id, "title": "第2章 入宅", "words": 12},
        {"id": older.id, "title": "作者自己建的空白页"},
    ]
    db_session.expire_all()
    outcome = json.loads(db_session.get(ChatMessage, message_id).message_metadata)["stop_outcome"]
    assert outcome["written_files"] == done["written_files"]


@pytest.mark.integration
async def test_disconnect_after_a_chapter_was_written_records_which_chapter(
    client: AsyncClient, db_session
):
    """U088：create_file 已带正文写好第2章，回复只有一句开场白时连接断开。计费，终态写明
    写进了《第2章 入宅》和字数，而不只是「已写入的内容已保存」。"""
    from models import ChatMessage

    _token, project, user = await _login_with_project(client, db_session)
    message_id, _placeholder, filled, _older = _seed_round(db_session, project, user)
    body = "雨还在下。旧书店的门没有锁。"

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _sse("content", {"text": "我先看卷一细纲里第2章的要求。"})
        yield _sse(
            "tool_result",
            {
                "tool_name": "create_file",
                "status": "success",
                "data": {"id": filled.id, "title": filled.title, "content": body, "file_type": "draft"},
            },
        )
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _stream(db_session, user, project, None)
        iterator = response.body_iterator
        for _ in range(3):
            await iterator.__anext__()
        await iterator.aclose()
        await asyncio.gather(*agent_api._detached_refund_tasks)
        await asyncio.gather(*agent_api._round_outcome_tasks)

    refund.assert_not_called()
    db_session.expire_all()
    outcome = json.loads(db_session.get(ChatMessage, message_id).message_metadata)["stop_outcome"]
    assert outcome["reason"] == "client_disconnected"
    assert outcome["charged"] is True
    assert outcome["saved_output"] is True
    assert outcome["written_files"] == [{"id": filled.id, "title": "第2章 入宅", "words": 12}]


@pytest.mark.integration
async def test_complete_delivery_keeps_normal_done_without_round_warning(client, db_session):
    from models import ChatMessage

    _token, project, user = await _login_with_project(client, db_session)
    message_id, empty, filled, _older = _seed_round(db_session, project, user)
    for file in (empty, filled):
        file.content = "雨还在下。"
        db_session.add(file)
    db_session.commit()

    async def frames():
        for file in (empty, filled):
            yield _sse("tool_result", {"tool_name": "create_file", "status": "success",
                                      "data": {"id": file.id, "title": file.title, "content": file.content, "file_type": "draft"}})
        yield _sse("done", {"assistant_message_id": message_id})

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
    ):
        response = await _stream(db_session, user, project, None)
        text = "".join([frame async for frame in response.body_iterator])
        if agent_api._round_outcome_tasks:
            await asyncio.gather(*agent_api._round_outcome_tasks)
    # Fully delivered: a write receipt (#267), but no "save not confirmed" warning.
    done = _frame_data(text, "done")
    assert [item["id"] for item in done["written_files"]] == [empty.id, filled.id]
    assert "unwritten_files" not in done
    db_session.expire_all()
    outcome = json.loads(db_session.get(ChatMessage, message_id).message_metadata or "{}")["stop_outcome"]
    assert outcome["reason"] == "completed"
    assert "unwritten_files" not in outcome


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


@pytest.mark.integration
async def test_disconnect_from_a_tab_without_the_stop_contract_keeps_files_and_writes_no_stop_row(
    client: AsyncClient, db_session
):
    """发布前打开的旧标签页（没有 /stop、不带停止约定头）断线：照旧退还没产出的一轮，
    但不移除这一轮建的空章节、不让 service 写空的「已中断」助手消息、不写终态——
    旧前端收不到 removed_files，文件树里还列着那一章。"""
    from models import ChatMessage, File

    _token, project, user = await _login_with_project(client, db_session)
    message_id, placeholder, _filled, _older = _seed_round(db_session, project, user)
    seen_stop_kind: list[str | None] = []

    class _RecordingService(_FakeService):
        async def process_stream(self, *, run_outcome=None, **kwargs):
            try:
                async for frame in super().process_stream(run_outcome=run_outcome, **kwargs):
                    yield frame
            finally:
                seen_stop_kind.append(run_outcome.stop_kind if run_outcome is not None else None)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _create_file_result(placeholder)
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_RecordingService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await _stream(db_session, user, project, None, client_stop_contract=None)
        iterator = response.body_iterator
        for _ in range(2):
            await iterator.__anext__()
        await iterator.aclose()
        await asyncio.gather(*agent_api._detached_refund_tasks)
        await asyncio.gather(*agent_api._round_outcome_tasks)

    refund.assert_called_once()
    # service 拿不到 stop_kind：按原来的取消原因补存，没有产出就不写助手消息。
    assert seen_stop_kind == [None]
    db_session.expire_all()
    assert db_session.get(File, placeholder.id).is_deleted is False
    metadata = json.loads(db_session.get(ChatMessage, message_id).message_metadata)
    assert "stop_outcome" not in metadata


@pytest.mark.integration
async def test_disconnect_after_done_settles_as_completed_without_an_interrupted_stamp(
    client: AsyncClient, db_session
):
    """答完（done 已发出）后还在收尾时面板卸载：短回答照常计费，助手消息不被写成「已中断」。"""
    from models import ChatMessage

    _token, project, user = await _login_with_project(client, db_session)
    message_id, _placeholder, _filled, _older = _seed_round(db_session, project, user)
    message = db_session.get(ChatMessage, message_id)
    message.content = "第三章叫《雪夜》。"
    message.message_metadata = json.dumps({"stop_reason": "end_turn"})
    db_session.add(message)
    db_session.commit()

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _sse("content", {"text": "第三章叫《雪夜》。"})
        yield _sse("done", {"assistant_message_id": message_id})
        # done 之后 process_stream 还在清理 steering 队列等收尾。
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
        patch("api.agent.log_with_context") as log_spy,
    ):
        response = await _stream(db_session, user, project, None)
        iterator = response.body_iterator
        for _ in range(3):
            await iterator.__anext__()
        await iterator.aclose()
        await asyncio.gather(*agent_api._detached_refund_tasks)
        await asyncio.gather(*agent_api._round_outcome_tasks)

    refund.assert_not_called()
    log = _billing_log(log_spy)
    assert log["billing_reason"] == "completed"
    assert log["client_disconnected"] is True
    db_session.expire_all()
    metadata = json.loads(db_session.get(ChatMessage, message_id).message_metadata)
    assert metadata == {"stop_reason": "end_turn"}


@pytest.mark.integration
async def test_stop_refund_survives_the_request_being_cancelled_while_it_is_queued(
    client: AsyncClient, db_session
):
    """作者停止后、退还还在排队时连接被取消：退还照样完成，终态记「未计入」，空章节照样移除。"""
    from models import ChatMessage, File

    _token, project, user = await _login_with_project(client, db_session)
    message_id, placeholder, _filled, _older = _seed_round(db_session, project, user)
    entered = asyncio.Event()
    gate = asyncio.Event()
    finished: list[bool] = []

    async def slow_refund(*_args, **_kwargs) -> bool:
        entered.set()
        await gate.wait()
        finished.append(True)
        return True

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _create_file_result(placeholder)
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent._refund_quota", side_effect=slow_refund),
        patch("api.agent.log_with_context") as log_spy,
    ):
        response = await _stream(db_session, user, project, None)
        iterator = response.body_iterator
        for _ in range(2):
            await iterator.__anext__()
        await agent_api.stop_stream(
            body=agent_api.StopRequest(agent_run_id=response.headers["X-Agent-Run-ID"]),
            current_user=user,
            _rate_limit=0,
        )

        async def consume() -> list[str]:
            return [frame async for frame in iterator]

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), timeout=5)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        gate.set()
        await asyncio.gather(*agent_api._detached_refund_tasks)
        await asyncio.gather(*agent_api._round_outcome_tasks)

    assert finished == [True]
    log = _billing_log(log_spy)
    assert log["billing_reason"] == "user_stopped_no_output"
    assert log["refund_scheduled"] is True
    db_session.expire_all()
    assert db_session.get(File, placeholder.id).is_deleted is True
    outcome = json.loads(db_session.get(ChatMessage, message_id).message_metadata)["stop_outcome"]
    assert outcome == {
        "reason": "user_stopped",
        "charged": False,
        "saved_output": False,
        "removed_files": ["第1章 最后一页"],
    }


@pytest.mark.integration
@pytest.mark.parametrize("receipt_ready", [True, False])
async def test_stop_done_uses_confirmed_save_count_or_omits_pending_count(client, db_session, receipt_ready):
    from models import ChatMessage, File
    from utils.text_metrics import count_words

    _token, project, user = await _login_with_project(client, db_session)
    message_id, placeholder, filled, _older = _seed_round(db_session, project, user)
    body = "雨还在下。旧书店的门没有锁。铜钥匙还握在她手里。"
    filled.content = body
    db_session.add(filled)
    db_session.commit()

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        yield _create_file_result(placeholder)
        yield _sse("file_created", {"file_id": filled.id, "title": filled.title})
        yield _sse("file_content", {"file_id": filled.id, "chunk": body[:-12]})
        await asyncio.sleep(30)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id if receipt_ready else None, {filled.id: count_words(body)})),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
    ):
        response = await _stream(db_session, user, project, None)
        iterator = response.body_iterator
        for _ in range(4):
            await iterator.__anext__()
        await agent_api.stop_stream(body=agent_api.StopRequest(agent_run_id=response.headers["X-Agent-Run-ID"]), current_user=user, _rate_limit=0)
        rest = "".join([frame async for frame in iterator])
        await asyncio.gather(*agent_api._round_outcome_tasks)
    done = _frame_data(rest, "done")
    expected = [{"id": filled.id, "title": filled.title, "words": count_words(db_session.get(File, filled.id).content)}]
    if not receipt_ready:
        expected[0].pop("words")
    assert done["written_files"] == expected
    if receipt_ready:
        db_session.expire_all()
        outcome = json.loads(db_session.get(ChatMessage, message_id).message_metadata)["stop_outcome"]
        assert outcome["written_files"] == expected


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["refund", "save_later", "save_fails", "disconnect"])
async def test_deadline_terminal_precedes_edge_even_when_cleanup_is_pending(client, db_session, monkeypatch, mode):
    """Controlled cleanup crosses the simulated edge; terminal must already be delivered."""
    from agent.core import steering
    from models import File

    _token, project, user = await _login_with_project(client, db_session)
    monkeypatch.setattr(agent_api, "AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 0.03)
    monkeypatch.setattr(agent_api, "AGENT_SSE_HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(steering, "_redis_available", AsyncMock(return_value=False))
    gate = asyncio.Event()
    cleanup_entered = asyncio.Event()
    terminal = asyncio.Event()
    refund_finished = asyncio.Event()
    chat_id = f"deadline-{uuid4()}"
    lease_id = "run-before-edge"
    placeholder = File(project_id=project.id, user_id=user.id, title="自造第五章", content="", file_type="draft")
    db_session.add(placeholder)
    db_session.commit()
    has_body = mode in {"save_later", "save_fails"}

    async def frames():
        await steering.create_steering_queue_async(chat_id, user.id, run_id=lease_id, exclusive_run=True)
        try:
            yield _sse("session_started", {"session_id": chat_id})
            if has_body:
                yield _sse("file_created", {"file_id": placeholder.id, "title": placeholder.title})
                yield _sse("file_content", {"file_id": placeholder.id, "chunk": _PROSE})
            await asyncio.Event().wait()
        finally:
            cleanup_entered.set()
            await gate.wait()
            if mode == "save_later":
                placeholder.content = _PROSE
                db_session.add(placeholder)
                db_session.commit()
            await steering.cleanup_steering_queue_async(chat_id, run_id=lease_id)

    captured = []
    async def consume(response):
        async for frame in response.body_iterator:
            captured.append(frame)
            if _frame_data(frame, "error"):
                terminal.set()

    async def refund(*_args, **_kwargs):
        refund_finished.set()
        return True

    logs = []
    original_log = agent_api.log_with_context
    def capture_log(logger, level, message, **fields):
        if message == "Agent stream billing evaluated":
            logs.append(fields)
        return original_log(logger, level, message, **fields)

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id=None)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent._refund_quota", new=AsyncMock(side_effect=refund)) as refund_call,
        patch("api.agent.log_with_context", new=capture_log),
    ):
        response = await _stream(db_session, user, project, None)
        consumer = asyncio.create_task(consume(response))
        try:
            # A simulated absolute edge is reached while application cleanup is still held.
            await asyncio.wait_for(terminal.wait(), timeout=0.3)
            await cleanup_entered.wait()
            assert not gate.is_set()
            assert not consumer.done()
            assert await steering.has_active_runs_async(chat_id)
            with pytest.raises(steering.SteeringSessionBusyError):
                await steering.create_steering_queue_async(chat_id, user.id, run_id="next", exclusive_run=True)
            refund_call.assert_not_called()
            assert db_session.get(File, placeholder.id).content == ""
            first = "".join(captured)
            assert _frame_data(first, "error")["code"] == "ERR_AGENT_RUN_TIMEOUT"
            assert _frame_data(first, "done") is None
            assert _frame_data(first, "quota_refunded") is None
            assert "saved_output" not in first
            if mode == "disconnect":
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
                await asyncio.sleep(0)
                refund_call.assert_not_called()
                assert await steering.has_active_runs_async(chat_id)
        finally:
            gate.set()
            if not consumer.done():
                await asyncio.wait_for(consumer, timeout=1)
            if mode == "disconnect":
                await asyncio.wait_for(refund_finished.wait(), timeout=1)
            assert not await steering.has_active_runs_async(chat_id)
            await steering.cleanup_steering_queue_async(chat_id, run_id=lease_id)

        text = "".join(captured)
        assert text.count("event: error") == 1
        assert _frame_data(text, "done") is None
        if has_body:
            refund_call.assert_not_called()
            assert db_session.get(File, placeholder.id).content == (_PROSE if mode == "save_later" else "")
        elif mode != "disconnect":
            refund_call.assert_awaited_once()
            assert text.index("event: error") < text.index("event: quota_refunded")
        assert logs[-1]["deadline_exceeded"] is True
        assert logs[-1]["billing_reason"] == "run_deadline_exceeded"


@pytest.mark.integration
@pytest.mark.parametrize("disconnect", [False, True])
async def test_timeout_terminal_precedes_slow_refund_and_keeps_its_final_result(client, db_session, monkeypatch, disconnect):
    _token, project, user = await _login_with_project(client, db_session)
    monkeypatch.setattr(agent_api, "AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 0.03)
    monkeypatch.setattr(agent_api, "AGENT_SSE_HEARTBEAT_INTERVAL_S", 0.01)
    gate, terminal, started, finished = (asyncio.Event() for _ in range(4))
    captured, logs = [], []

    async def frames():
        yield _sse("session_started", {"session_id": "slow-refund"})
        await asyncio.Event().wait()

    async def refund(*_args, **_kwargs):
        started.set()
        await gate.wait()
        finished.set()
        return True

    original_log = agent_api.log_with_context
    def capture_log(logger, level, message, **fields):
        if message == "Agent stream billing evaluated":
            logs.append(fields)
        return original_log(logger, level, message, **fields)

    async def consume(response):
        async for frame in response.body_iterator:
            captured.append(frame)
            if _frame_data(frame, "error"):
                terminal.set()

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent._refund_quota", new=AsyncMock(side_effect=refund)) as refund_call,
        patch("api.agent.log_with_context", new=capture_log),
    ):
        response = await _stream(db_session, user, project, None)
        consumer = asyncio.create_task(consume(response))
        try:
            await asyncio.wait_for(terminal.wait(), 0.3)
            await started.wait()
            assert _frame_data("".join(captured), "quota_refunded") is None
            assert not finished.is_set()
            if disconnect:
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
        finally:
            gate.set()
            if not consumer.done():
                await asyncio.wait_for(consumer, 1)
            await asyncio.wait_for(finished.wait(), 1)
        refund_call.assert_awaited_once()
        assert "".join(captured).count("event: error") == 1
        if not disconnect:
            assert _frame_data("".join(captured), "quota_refunded")["refunded"] is True
        assert logs[-1]["billing_reason"] == "run_deadline_exceeded"
        assert logs[-1]["client_disconnected"] is disconnect


@pytest.mark.integration
@pytest.mark.parametrize("terminal_kind", ["done", "error", "workflow_complete", "workflow_stopped"])
@pytest.mark.parametrize("backlog", [0, 32])
async def test_queued_terminal_cannot_suppress_delivered_deadline_terminal(client, db_session, monkeypatch, terminal_kind, backlog):
    """Produced/queued is not delivered, including a terminal blocked by a full queue."""
    _token, project, user = await _login_with_project(client, db_session)
    monkeypatch.setattr(agent_api, "AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 0.02)
    gate, terminal_generated, cleanup_entered = (asyncio.Event() for _ in range(3))
    payload = {"code": "ORIGINAL_ERROR", "error": "synthetic error"} if terminal_kind == "error" else {}
    if terminal_kind == "workflow_stopped":
        payload = {"reason": "synthetic_terminal_stop"}

    async def frames():
        try:
            yield _sse("session_started", {"session_id": "queued-terminal"})
            for _ in range(backlog):
                yield _sse("thinking", {"text": "pending"})
            terminal_generated.set()
            yield _sse(terminal_kind, payload)
            await asyncio.Event().wait()
        finally:
            cleanup_entered.set()
            await gate.wait()

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent._refund_quota", new=AsyncMock(return_value=True)) as refund,
    ):
        response = await _stream(db_session, user, project, None)
        iterator = response.body_iterator
        delivered = [await anext(iterator)]
        await asyncio.wait_for(terminal_generated.wait(), 0.2)
        # Simulate consumer backpressure crossing the application deadline.
        await asyncio.sleep(0.03)
        next_frame = asyncio.create_task(anext(iterator))
        try:
            frame = await asyncio.wait_for(asyncio.shield(next_frame), 0.2)
            delivered.append(frame)
            assert _frame_data(frame, "error")["code"] == "ERR_AGENT_RUN_TIMEOUT"
            await cleanup_entered.wait()
            refund.assert_not_called()
            assert not gate.is_set()
        finally:
            gate.set()
            if not next_frame.done():
                await asyncio.gather(next_frame, return_exceptions=True)
            delivered.extend([frame async for frame in iterator])
        text = "".join(delivered)
        assert text.count("event: error") == 1
        assert _frame_data(text, "done") is None
        assert _frame_data(text, "workflow_complete") is None
        assert _frame_data(text, "workflow_stopped") is None
        refund.assert_awaited_once()


@pytest.mark.integration
@pytest.mark.parametrize("terminal_kind", ["done", "error"])
async def test_delivered_terminal_is_not_duplicated_after_consumer_deadline(client, db_session, monkeypatch, terminal_kind):
    _token, project, user = await _login_with_project(client, db_session)
    monkeypatch.setattr(agent_api, "AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 0.02)
    gate = asyncio.Event()
    async def frames():
        try:
            yield _sse("session_started", {"session_id": "delivered-terminal"})
            yield _sse(terminal_kind, {"code": "ORIGINAL_ERROR"} if terminal_kind == "error" else {})
            await asyncio.Event().wait()
        finally:
            await gate.wait()
    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent._refund_quota", new=AsyncMock(return_value=True)),
    ):
        response = await _stream(db_session, user, project, None)
        iterator = response.body_iterator
        delivered = [await anext(iterator), await anext(iterator)]
        await asyncio.sleep(0.03)
        gate.set()
        delivered.extend([frame async for frame in iterator])
        text = "".join(delivered)
        assert text.count(f"event: {terminal_kind}") == 1
        assert "ERR_AGENT_RUN_TIMEOUT" not in text


@pytest.mark.integration
@pytest.mark.parametrize("terminal", ["error", "done"])
async def test_partial_delivery_is_identical_live_and_after_history_reload(client, db_session, terminal):
    from models import ChatMessage

    _token, project, user = await _login_with_project(client, db_session)
    message_id, empty, filled, _older = _seed_round(db_session, project, user)
    filled.content = "雨还在下。"
    db_session.add(filled)
    db_session.commit()

    async def frames():
        yield _sse("tool_result", {"tool_name": "create_file", "status": "success",
                                  "data": {"id": filled.id, "title": filled.title, "content": filled.content, "file_type": "draft"}})
        yield _create_file_result(empty)
        yield _sse(terminal, {"assistant_message_id": message_id} if terminal == "done" else
                   {"message": "Upstream timed out", "code": "ERR_AGENT_UPSTREAM_TIMEOUT", "retryable": True})

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
    ):
        response = await _stream(db_session, user, project, None)
        text = "".join([frame async for frame in response.body_iterator])
        if agent_api._round_outcome_tasks:
            await asyncio.gather(*agent_api._round_outcome_tasks)
    error = _frame_data(text, terminal)
    assert error["written_files"][0]["id"] == filled.id
    assert error["unwritten_files"] == [{"id": empty.id, "title": empty.title}]
    if terminal == "error":
        assert error["code"] == "ERR_AGENT_UPSTREAM_TIMEOUT"
        assert error["retryable"] is True
        assert _frame_data(text, "done") is None
    db_session.expire_all()
    outcome = json.loads(db_session.get(ChatMessage, message_id).message_metadata)["stop_outcome"]
    assert outcome["reason"] == ("error" if terminal == "error" else "incomplete")
    assert outcome["written_files"] == error["written_files"]
    assert outcome["unwritten_files"] == error["unwritten_files"]
    assert outcome["saved_output"] is True
    assert outcome["charged"] is True


async def _run_completed_round(client, db_session, frames_after_route, *, routing):
    """A normally completed round (#267): the done frame and the persisted outcome."""
    from models import ChatMessage

    _token, project, user = await _login_with_project(client, db_session)
    message_id, _empty, filled, _older = _seed_round(db_session, project, user)
    assistant = db_session.get(ChatMessage, message_id)
    assistant.message_metadata = json.dumps({"stop_reason": "end_turn"})
    db_session.add(assistant)
    db_session.commit()

    async def frames():
        yield _sse("router_decided", {"initial_agent": "writer", "routing_metadata": routing})
        for frame in frames_after_route(filled):
            yield frame
        yield _sse("text", {"text": "已更新全部内容。"})
        yield _sse("done", {"assistant_message_id": message_id})

    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
    ):
        response = await _stream(db_session, user, project, None)
        text = "".join([frame async for frame in response.body_iterator])
        if agent_api._round_outcome_tasks:
            await asyncio.gather(*agent_api._round_outcome_tasks)
    db_session.expire_all()
    metadata = json.loads(db_session.get(ChatMessage, message_id).message_metadata)
    return _frame_data(text, "done"), metadata, filled


_WRITE_REQUEST = {"write_content": True, "read_only": False}


def _edit_result(file, **data):
    return _sse("tool_result", {"tool_name": "edit_file", "status": "success",
                                "data": {"id": file.id, "title": file.title, **data}})


@pytest.mark.integration
async def test_completed_round_claiming_edits_without_writes_gets_a_no_change_receipt(client, db_session):
    done, metadata, _file = await _run_completed_round(
        client, db_session, lambda _file: [], routing=_WRITE_REQUEST,
    )

    assert done["no_file_changes"] is True
    assert "written_files" not in done
    assert metadata["stop_reason"] == "end_turn"
    assert metadata["stop_outcome"]["reason"] == "completed"
    assert metadata["stop_outcome"]["no_file_changes"] is True


@pytest.mark.integration
@pytest.mark.parametrize("result", [
    {"mutation_applied": False, "edits_applied": 0},  # no_change
    {"mutation_applied": False, "edits_applied": 0, "all_failed": True},
])
async def test_edit_that_changed_nothing_is_not_a_write_receipt(client, db_session, result):
    done, metadata, _file = await _run_completed_round(
        client, db_session, lambda file: [_edit_result(file, **result)], routing=_WRITE_REQUEST,
    )

    assert done["no_file_changes"] is True
    assert "written_files" not in done
    assert metadata["stop_outcome"]["no_file_changes"] is True


@pytest.mark.integration
async def test_partial_edit_is_listed_as_partial_not_as_a_complete_write(client, db_session):
    done, metadata, file = await _run_completed_round(
        client, db_session,
        lambda file: [_edit_result(file, mutation_applied=True, edits_applied=1, partial_success=True,
                                   failed_edits=[{"index": 1, "error": "anchor not found"}])],
        routing=_WRITE_REQUEST,
    )

    assert done["written_files"] == [{"id": file.id, "title": file.title, "partial": True}]
    assert "no_file_changes" not in done
    assert metadata["stop_outcome"]["reason"] == "completed"
    assert metadata["stop_outcome"]["written_files"] == done["written_files"]


@pytest.mark.integration
@pytest.mark.parametrize("routing", [
    {"write_content": False, "read_only": False},
    {"write_content": True, "read_only": True},
    {"write_content": None, "read_only": False},
])
async def test_rounds_that_did_not_ask_for_body_writes_get_no_receipt(client, db_session, routing):
    done, metadata, _file = await _run_completed_round(
        client, db_session, lambda _file: [], routing=routing,
    )

    assert "no_file_changes" not in done
    assert "stop_outcome" not in metadata


@pytest.mark.integration
@pytest.mark.parametrize(
    ("output_frames", "refunded"),
    [
        ([], True),
        ([_sse("content", {"text": _PROSE})], False),
    ],
)
async def test_server_drain_ends_a_running_stream_with_a_restart_terminal(
    client: AsyncClient, db_session, monkeypatch, output_frames, refunded
):
    """发布交接：旧进程收到 SIGTERM 后，仍在运行的一轮在关机宽限内以「服务正在更新」收尾并照超时规则结算。"""
    from agent.core import server_drain

    _token, project, user = await _login_with_project(client, db_session)
    monkeypatch.setattr(server_drain, "_draining_since", None)
    monkeypatch.setattr(agent_api, "AGENT_DRAIN_STOP_AFTER_S", 0.05)
    monkeypatch.setattr(agent_api, "AGENT_SSE_HEARTBEAT_INTERVAL_S", 0.05)
    cancelled = asyncio.Event()

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        for frame in output_frames:
            yield frame
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
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
        server_drain.begin_drain()
        received.extend([frame async for frame in iterator])

    text = "".join(received)
    assert "ERR_AGENT_SERVER_RESTARTING" in text
    assert "ERR_AGENT_RUN_TIMEOUT" not in text
    assert cancelled.is_set()
    assert refund.called is refunded
    log = _billing_log(log_spy)
    assert log["billing_reason"] == "server_restarting"
    assert log["deadline_exceeded"] is True


@pytest.mark.integration
@pytest.mark.parametrize("with_body", [True, False])
async def test_deadline_round_records_an_interrupted_outcome_after_capture_settles(
    client, db_session, monkeypatch, with_body
):
    """时限截停的一轮（含审稿后返修被打断）：残稿补存落定后记 stop_outcome（已中断），刷新后不像正常结束。

    stop_reason 保留 run_deadline_exceeded，「继续」照常可恢复；字数只认保存回执。
    """
    from models import ChatMessage
    from utils.text_metrics import count_words

    _token, project, user = await _login_with_project(client, db_session)
    message_id, _placeholder, filled, _older = _seed_round(db_session, project, user)
    assistant = db_session.get(ChatMessage, message_id)
    assistant.message_metadata = json.dumps({"stop_reason": "run_deadline_exceeded"})
    db_session.add(assistant)
    db_session.commit()
    monkeypatch.setattr(agent_api, "AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 0.2)
    monkeypatch.setattr(agent_api, "AGENT_SSE_HEARTBEAT_INTERVAL_S", 0.05)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
        if with_body:
            yield _sse("file_created", {"file_id": filled.id, "title": filled.title})
            yield _sse("file_content", {"file_id": filled.id, "chunk": _PROSE})
        # 审稿人要求返修，writer 的返修调用还在途时撞上时限
        yield _sse("handoff", {"target_agent": "writer", "reason": "返修"})
        await asyncio.sleep(30)

    receipts = {filled.id: count_words(_PROSE)} if with_body else {}
    with (
        patch("api.agent.get_agent_service", return_value=_FakeService(frames, message_id, receipts)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True),
    ):
        response = await _stream(db_session, user, project, None)
        text = "".join([frame async for frame in response.body_iterator])
        await asyncio.gather(*agent_api._round_outcome_tasks)

    assert "ERR_AGENT_RUN_TIMEOUT" in text
    assert _frame_data(text, "done") is None and _frame_data(text, "workflow_complete") is None
    db_session.expire_all()
    metadata = json.loads(db_session.get(ChatMessage, message_id).message_metadata)
    assert metadata["stop_reason"] == "run_deadline_exceeded"
    outcome = metadata["stop_outcome"]
    assert outcome["reason"] == "error"
    assert outcome["charged"] is with_body
    if with_body:
        assert outcome["written_files"] == [
            {"id": filled.id, "title": filled.title, "words": count_words(_PROSE)}
        ]
    else:
        assert "written_files" not in outcome


@pytest.mark.integration
async def test_author_requested_empty_chapter_completes_without_an_incomplete_stamp(client, db_session):
    """作者只要建「第10章」标题（empty_by_request）：正常 done 不带「未确认保存」，历史也不记 incomplete。"""
    done, metadata, _file = await _run_completed_round(
        client, db_session,
        lambda file: [_sse("tool_result", {"tool_name": "create_file", "status": "success", "data": {
            "id": file.id, "title": file.title, "content": "", "file_type": "draft",
            "empty_by_request": True, "updated_at": file.updated_at.isoformat(),
        }})],
        routing={"write_content": False, "read_only": False},
    )

    assert "unwritten_files" not in done
    assert "written_files" not in done
    assert metadata == {"stop_reason": "end_turn"}
