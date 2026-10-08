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


class _FakeService:
    def __init__(self, frames_factory):
        self._frames_factory = frames_factory

    async def process_stream(self, **_kwargs):
        async for frame in self._frames_factory():
            yield frame


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


@pytest.mark.integration
async def test_generator_exit_disconnect_is_billed_as_user_cancel(client: AsyncClient, db_session):
    """以 aclose（GeneratorExit）方式断线时按用户取消计费，而不是内部错误退款。"""
    _token, project, user = await _login_with_project(client, db_session)

    async def frames():
        yield _sse("session_started", {"session_id": "s1"})
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
        first = await iterator.__anext__()
        assert first.startswith("event: session_started")
        await iterator.aclose()

    refund.assert_not_called()
    billing_logs = [
        call.kwargs for call in log_spy.call_args_list
        if len(call.args) >= 3 and call.args[2] == "Agent stream billing evaluated"
    ]
    assert billing_logs and billing_logs[-1]["billing_reason"] == "user_cancelled"
    assert billing_logs[-1]["charged"] is True


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
