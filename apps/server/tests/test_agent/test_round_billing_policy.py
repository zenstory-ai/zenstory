"""回归测试：AI 对话按轮计费新契约（进入真实供应商 transport 即计 1 轮，仅未恢复的 provider transport 错误终止整轮退还）。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import openai
import pytest
from httpx import AsyncClient
from starlette.requests import Request

import api.agent as agent_api
from agent.core.stream_billing import StreamBillingTracker
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import ChatMessage, ChatSession, Project, User
from services.core.auth_service import hash_password

pytestmark = [pytest.mark.usefixtures("writing_prompt_configs")]

_CHARGED_PERIOD = datetime(2026, 11, 20, 0, tzinfo=UTC)
_SYNTHETIC_REQUEST = Request({"type": "http", "headers": [(b"x-client-stop-contract", b"1")]})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _frame_data(text: str, event: str) -> dict | None:
    for frame in text.split("\n\n"):
        lines = frame.strip().splitlines()
        if lines and lines[0] == f"event: {event}":
            return json.loads(lines[1].split(":", 1)[1])
    return None


async def _make_user_and_project(db_session) -> tuple[User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"billing_pol_{suffix}",
        email=f"billing_pol_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Billing Policy Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    return user, project


@pytest.fixture(autouse=True)
def _bind_test_db_sessions(monkeypatch, db_session):
    from sqlmodel import Session

    import database

    engine = db_session.get_bind()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    monkeypatch.setattr("agent.service.create_session", lambda: Session(engine))


@pytest.fixture(autouse=True)
async def _drain_async_usage_records(db_session):
    from services.usage.llm_usage_service import drain_pending_usage_records

    yield
    await drain_pending_usage_records()


# ------------------------------------------------------------------ 1. 纯单元状态表验证


@pytest.mark.unit
@pytest.mark.parametrize(
    ("frames", "kwargs", "expected"),
    [
        # M03: 模型尚未启动（provider_attempted=False）时的停止、断线、到时、预检报错均退还
        (
            [_sse("session_started", {"session_id": "s1"})],
            {"user_stopped": True, "provider_attempted": False},
            ("user_stopped_before_model", True),
        ),
        (
            [_sse("session_started", {"session_id": "s1"})],
            {"client_disconnected": True, "provider_attempted": False},
            ("client_disconnected_before_model", True),
        ),
        (
            [_sse("session_started", {"session_id": "s1"})],
            {"deadline_exceeded": True, "provider_attempted": False},
            ("run_deadline_exceeded", True),
        ),
        (
            [
                _sse("session_started", {"session_id": "s1"}),
                _sse("error", {"message": "x", "code": "ERR_QUOTA_AI_DAILY_COST_EXCEEDED", "refundable": True}),
            ],
            {"provider_attempted": False},
            ("pre_model_error", True),
        ),
        # M05: 模型已启动（即使无任何输出帧 / 仅思考 / 仅只读）时停止或断线一律计费，绝不用 SSE 猜启动
        (
            [_sse("session_started", {"session_id": "s1"})],
            {"user_stopped": True, "provider_attempted": True},
            ("user_stopped", False),
        ),
        (
            [_sse("session_started", {"session_id": "s1"})],
            {"client_disconnected": True, "provider_attempted": True},
            ("client_disconnected", False),
        ),
        (
            [
                _sse("thinking_content", {"content": "核对前十章时间线"}),
                _sse("tool_result", {"tool_name": "query_files", "status": "success", "data": []}),
            ],
            {"user_stopped": True, "provider_attempted": True},
            ("user_stopped", False),
        ),
        # M07: 纯读取 / 只思考 + 780s 墙钟硬超时（或发布交接提前时限）一律计费
        (
            [
                _sse("thinking_content", {"content": "长思考未输出正文"}),
                _sse("tool_result", {"tool_name": "query_files", "status": "success", "data": []}),
                _sse("error", {"message": "timeout", "code": "ERR_AGENT_RUN_TIMEOUT", "refundable": True}),
            ],
            {"deadline_exceeded": True, "provider_attempted": True},
            ("run_deadline_exceeded", False),
        ),
        (
            [_sse("thinking_content", {"content": "长思考"})],
            {"deadline_exceeded": True, "server_restarting": True, "provider_attempted": True},
            ("server_restarting", False),
        ),
        # M08: 模型已启动后的非模型服务报错（no_progress、repetition_loop、model_call_limit、output_truncated、DB 错误）均计费
        (
            [
                _sse("error", {"message": "无进展", "code": "ERR_AGENT_NO_PROGRESS", "reason": "no_progress", "refundable": True}),
            ],
            {"provider_attempted": True, "terminal_model_failure": False},
            ("non_model_stop", False),
        ),
        (
            [
                _sse("error", {"message": "内部错误", "code": "ERR_AGENT_UPSTREAM_UNAVAILABLE", "refundable": True}),
            ],
            {"provider_attempted": True, "terminal_model_failure": False},
            ("non_model_error", False),
        ),
        # M10: 真正未恢复的 provider transport 失败终止整轮才退还（即便前一步已有文字或只读调用）
        (
            [
                _sse("content", {"text": "我先帮你核对前三章的时间线和人物年龄设定，确认前后一致后再统一列出需要微调的段落与具体锚点位置。"}),
                _sse("error", {"message": "上游不可用", "code": "ERR_AGENT_UPSTREAM_UNAVAILABLE", "refundable": True}),
            ],
            {"provider_attempted": True, "terminal_model_failure": True},
            ("upstream_model_error", True),
        ),
    ],
)
def test_billing_tracker_matrix(frames, kwargs, expected):
    tracker = StreamBillingTracker()
    for frame in frames:
        tracker.observe(frame)
    decide_kwargs = {
        "client_disconnected": False,
        "unexpected_exception": False,
        **kwargs,
    }
    assert tracker.decide(**decide_kwargs) == expected


@pytest.mark.unit
def test_billing_tracker_never_infers_provider_attempt_from_sse_frames_alone():
    """生产计费绝不从 SSE 帧猜测模型已启动：未收到 provider_attempted=True 时即使有 content 帧也按未启动处理。"""
    tracker = StreamBillingTracker()
    tracker.observe(_sse("content", {"text": "不应靠 SSE 反推已过 transport 门卫。" * 5}))
    assert tracker.decide(
        client_disconnected=False,
        unexpected_exception=False,
        user_stopped=True,
        provider_attempted=False,
    ) == ("user_stopped_before_model", True)


# ------------------------------------------------------------------ 2. 共同 transport guard 真实接线集成测试


def _make_guarded_fake_client(create_impl, *, with_options_impl=None):
    """构造挂载真实 install_async_cost_guard 的模拟客户端，覆盖 compaction 与 SDK 共同的 transport 入口。"""
    from services.usage.model_call_guard import install_async_cost_guard

    inner = SimpleNamespace(
        base_url="https://fake.provider/v1",
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_impl)),
    )
    if with_options_impl is not None:
        inner.with_options = with_options_impl
    else:
        inner.with_options = lambda **_kw: SimpleNamespace(
            base_url="https://fake.provider/v1",
            chat=SimpleNamespace(completions=SimpleNamespace(create=create_impl)),
        )
    return install_async_cost_guard(inner)


@pytest.mark.integration
@pytest.mark.parametrize("reservation_value", [SimpleNamespace(started_at=datetime.now(UTC)), None])
async def test_stop_after_provider_attempt_before_first_token_is_charged_for_free_and_paid(
    client: AsyncClient, db_session, monkeypatch, reservation_value
):
    """reserve_model_call 通过后（含付费用户 reservation=None）已进入 transport、尚无首 token 时停止：计 1 轮。"""
    from agent.core import llm_providers

    user, project = await _make_user_and_project(db_session)
    transport_entered = asyncio.Event()

    async def slow_stream(*_args, **_kwargs):
        transport_entered.set()
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    fake_client = _make_guarded_fake_client(slow_stream)
    monkeypatch.setattr(llm_providers, "get_async_client", lambda _provider: fake_client)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-primary-key")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)

    body = agent_api.AgentRequest(project_id=str(project.id), message="核对最近十章")
    with (
        patch("services.usage.model_call_guard.reserve_model_call", return_value=reservation_value),
        patch("services.usage.model_call_guard.settle_model_call"),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await agent_api.stream_request(
            _SYNTHETIC_REQUEST,
            body=body,
            session=db_session,
            current_user=user,
            accept_language="zh",
            client_stop_contract="1",
            _rate_limit=0,
        )
        run_id = response.headers["X-Agent-Run-ID"]
        iterator = response.body_iterator
        # 消费前置事件，等待真实进入 transport（但未产生任何模型首 token）
        seen_frames: list[str] = []

        async def _PumpReader():
            async for frame in iterator:
                seen_frames.append(frame)

        reader_task = asyncio.create_task(_PumpReader())
        await asyncio.wait_for(transport_entered.wait(), timeout=5.0)
        stopped = await agent_api.stop_stream(
            body=agent_api.StopRequest(agent_run_id=run_id),
            current_user=user,
            _rate_limit=0,
        )
        assert stopped.stop_requested is True
        await asyncio.wait_for(reader_task, timeout=5.0)

    joined = "".join(seen_frames)
    done = _frame_data(joined, "done")
    assert done is not None
    assert done["stop_reason"] == "user_stopped"
    assert done["charged"] is True
    assert _frame_data(joined, "quota_refunded") is None
    refund.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize("fail_in_compaction", [False, True])
async def test_cost_guard_rejection_before_transport_refunds_message(
    client: AsyncClient, db_session, monkeypatch, fail_in_compaction
):
    """成本守卫在 transport 之前拒绝（主调用或历史压缩调用）：provider_attempted=False，退还预扣额度。"""
    from agent.core import llm_providers

    user, project = await _make_user_and_project(db_session)
    if fail_in_compaction:
        chat_sess = ChatSession(user_id=user.id, project_id=project.id, title="Long", is_active=True, message_count=2)
        db_session.add(chat_sess)
        db_session.commit()
        db_session.add_all(
            [
                ChatMessage(session_id=chat_sess.id, role="user", content="写第一章"),
                ChatMessage(
                    session_id=chat_sess.id,
                    role="assistant",
                    content="第一章完成。",
                    message_metadata=json.dumps({"engine": "loop", "prompt_tokens": 350_000, "prompt_chars": 500_000}),
                ),
            ]
        )
        db_session.commit()

    transport_called = False

    async def should_not_touch_transport(*_args, **_kwargs):
        nonlocal transport_called
        transport_called = True
        raise AssertionError("transport must not run when reserve_model_call rejects")

    fake_client = _make_guarded_fake_client(should_not_touch_transport)
    monkeypatch.setattr(llm_providers, "get_async_client", lambda _provider: fake_client)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-primary-key")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)

    cost_exc = APIException(error_code=ErrorCode.QUOTA_AI_DAILY_COST_EXCEEDED, status_code=402)
    req_session_id: str | None = None
    if fail_in_compaction:
        req_session_id = str(chat_sess.id)
        monkeypatch.setattr("agent.service.should_compact", lambda _history: True)
    reserve_calls = 0

    def _reject_reserve(kwargs):
        nonlocal reserve_calls
        reserve_calls += 1
        if fail_in_compaction:
            assert not kwargs.get("stream"), "Expected compaction (non-stream) call to hit reserve_model_call first"
        raise cost_exc

    body = agent_api.AgentRequest(project_id=str(project.id), session_id=req_session_id, message="继续写")
    with (
        patch("services.usage.model_call_guard.reserve_model_call", side_effect=_reject_reserve),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await agent_api.stream_request(
            _SYNTHETIC_REQUEST,
            body=body,
            session=db_session,
            current_user=user,
            accept_language="zh",
            client_stop_contract="1",
            _rate_limit=0,
        )
        text = "".join([frame async for frame in response.body_iterator])

    assert reserve_calls == 1
    assert transport_called is False
    assert "ERR_QUOTA_AI_DAILY_COST_EXCEEDED" in text
    refund.assert_called_once()
    assert _frame_data(text, "quota_refunded") == {"refunded": True, "kind": "error"}


@pytest.mark.integration
@pytest.mark.parametrize("end_mode", ["stop", "deadline"])
async def test_compaction_attempt_started_before_main_loop_charges_on_stop_or_deadline(
    client: AsyncClient, db_session, monkeypatch, end_mode
):
    """历史压缩请求已进入供应商 transport、主 loop 尚未启动时被停止或墙钟超时：计 1 轮，不退额。"""
    from agent.core import llm_providers

    user, project = await _make_user_and_project(db_session)
    chat_sess = ChatSession(user_id=user.id, project_id=project.id, title="Compact", is_active=True, message_count=2)
    db_session.add(chat_sess)
    db_session.commit()
    db_session.add_all(
        [
            ChatMessage(session_id=chat_sess.id, role="user", content="整理前文"),
            ChatMessage(
                session_id=chat_sess.id,
                role="assistant",
                content="已整理。",
                message_metadata=json.dumps({"engine": "loop", "prompt_tokens": 350_000, "prompt_chars": 500_000}),
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr("agent.service.should_compact", lambda _history: True)

    compaction_started = asyncio.Event()
    compaction_transport_calls = 0

    async def slow_compaction(*_args, **kwargs):
        nonlocal compaction_transport_calls
        assert not kwargs.get("stream"), "Expected compaction transport before main streaming loop"
        compaction_transport_calls += 1
        compaction_started.set()
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    fake_client = _make_guarded_fake_client(slow_compaction)
    monkeypatch.setattr(llm_providers, "get_async_client", lambda _provider: fake_client)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-primary-key")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    if end_mode == "deadline":
        monkeypatch.setattr(agent_api, "AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 0.25)
        monkeypatch.setattr(agent_api, "AGENT_SSE_HEARTBEAT_INTERVAL_S", 0.05)

    body = agent_api.AgentRequest(project_id=str(project.id), session_id=str(chat_sess.id), message="继续核对")
    with (
        patch("services.usage.model_call_guard.reserve_model_call", return_value=None),
        patch("services.usage.model_call_guard.settle_model_call"),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await agent_api.stream_request(
            _SYNTHETIC_REQUEST,
            body=body,
            session=db_session,
            current_user=user,
            accept_language="zh",
            client_stop_contract="1",
            _rate_limit=0,
        )
        run_id = response.headers["X-Agent-Run-ID"]
        iterator = response.body_iterator
        frames: list[str] = []

        async def _consume():
            async for f in iterator:
                frames.append(f)

        task = asyncio.create_task(_consume())
        await asyncio.wait_for(compaction_started.wait(), timeout=5.0)
        if end_mode == "stop":
            await agent_api.stop_stream(
                body=agent_api.StopRequest(agent_run_id=run_id),
                current_user=user,
                _rate_limit=0,
            )
        await asyncio.wait_for(task, timeout=5.0)

    joined = "".join(frames)
    assert compaction_transport_calls == 1
    assert _frame_data(joined, "quota_refunded") is None
    refund.assert_not_called()


def _ok_stream_chunks(model_name: str, text: str = "好的。"):
    from openai.types.chat.chat_completion_chunk import ChatCompletionChunk, Choice, ChoiceDelta
    from openai.types.completion_usage import CompletionUsage

    async def _chunks():
        yield ChatCompletionChunk(
            id="cmpl-ok",
            created=1,
            model=model_name,
            object="chat.completion.chunk",
            choices=[Choice(index=0, delta=ChoiceDelta(role="assistant", content=text), finish_reason=None)],
            usage=None,
        )
        yield ChatCompletionChunk(
            id="cmpl-ok",
            created=1,
            model=model_name,
            object="chat.completion.chunk",
            choices=[Choice(index=0, delta=ChoiceDelta(), finish_reason="stop")],
            usage=CompletionUsage(prompt_tokens=20, completion_tokens=5, total_tokens=25),
        )

    return _chunks()


@pytest.mark.integration
async def test_compaction_provider_failure_swallowed_then_main_writer_succeeds_is_charged(
    client: AsyncClient, db_session, monkeypatch
):
    """压缩调用报供应商 503 被 compaction.py 吞掉后，主循环跑通正常结束：绝不残留 sticky 失败标记，正常计费。"""
    from agent.core import llm_providers
    from agent.openai_agents.model import reset_sdk_model_cache

    user, project = await _make_user_and_project(db_session)
    chat_sess = ChatSession(user_id=user.id, project_id=project.id, title="CompactFail", is_active=True, message_count=2)
    db_session.add(chat_sess)
    db_session.commit()
    db_session.add_all(
        [
            ChatMessage(session_id=chat_sess.id, role="user", content="第一轮"),
            ChatMessage(
                session_id=chat_sess.id,
                role="assistant",
                content="第一轮回复。",
                message_metadata=json.dumps({"engine": "loop", "prompt_tokens": 350_000, "prompt_chars": 500_000}),
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr("agent.service.should_compact", lambda _history: True)

    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    resp_503 = httpx.Response(503, request=req)
    compaction_calls = 0
    writer_calls = 0

    async def fail_compaction_then_succeed_writer(*_args, **kwargs):
        nonlocal compaction_calls, writer_calls
        if not kwargs.get("stream"):
            compaction_calls += 1
            raise openai.InternalServerError("503 overloaded", response=resp_503, body=None)
        writer_calls += 1
        return _ok_stream_chunks(kwargs.get("model") or "deepseek-flash", "好的。")

    fake_client = _make_guarded_fake_client(fail_compaction_then_succeed_writer)
    monkeypatch.setattr(llm_providers, "get_async_client", lambda _provider: fake_client)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-primary-key")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    reset_sdk_model_cache()

    body = agent_api.AgentRequest(project_id=str(project.id), session_id=str(chat_sess.id), message="核对设定")
    with (
        patch("services.usage.model_call_guard.reserve_model_call", return_value=None),
        patch("services.usage.model_call_guard.settle_model_call"),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        response = await agent_api.stream_request(
            _SYNTHETIC_REQUEST,
            body=body,
            session=db_session,
            current_user=user,
            accept_language="zh",
            client_stop_contract="1",
            _rate_limit=0,
        )
        text = "".join([frame async for frame in response.body_iterator])

    reset_sdk_model_cache()
    assert compaction_calls == 1
    assert writer_calls == 1
    assert _frame_data(text, "done") is not None
    assert _frame_data(text, "quota_refunded") is None
    refund.assert_not_called()


@pytest.mark.integration
async def test_upstream_provider_transport_failure_exhausting_failover_refunds_while_failover_success_charges(
    client: AsyncClient, db_session, monkeypatch
):
    """主备均因 provider transport 报错终止时退还额度（成本账本仍记账）；主失败切备成功时正常计费。"""
    from agent.core import llm_providers
    from agent.openai_agents.model import reset_sdk_model_cache

    user, project = await _make_user_and_project(db_session)
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    resp_503 = httpx.Response(503, request=req)

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "primary-key")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "backup-key")
    reset_sdk_model_cache()

    # 场景 A: 主备 transport 均 503 -> 终止整轮并退还对话额度，且 llm_usage 账本记录两次失败尝试
    async def always_503(*_args, **_kwargs):
        raise openai.InternalServerError("503 down", response=resp_503, body=None)

    both_fail_client = _make_guarded_fake_client(always_503)
    monkeypatch.setattr(llm_providers, "get_async_client", lambda _provider: both_fail_client)
    ledger_spy = MagicMock()

    body = agent_api.AgentRequest(project_id=str(project.id), message="帮我检查大纲")
    with (
        patch("services.usage.model_call_guard.reserve_model_call", return_value=None),
        patch("services.usage.model_call_guard.settle_model_call"),
        patch("services.usage.llm_usage_service.schedule_llm_usage_record", ledger_spy),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund_a,
    ):
        resp_a = await agent_api.stream_request(
            _SYNTHETIC_REQUEST,
            body=body,
            session=db_session,
            current_user=user,
            accept_language="zh",
            client_stop_contract="1",
            _rate_limit=0,
        )
        text_a = "".join([frame async for frame in resp_a.body_iterator])

    assert "ERR_AGENT_UPSTREAM_UNAVAILABLE" in text_a
    assert _frame_data(text_a, "quota_refunded") == {"refunded": True, "kind": "error"}
    refund_a.assert_called_once()
    assert ledger_spy.call_count >= 2  # 真实 provider 尝试账本不受消息额度退款影响

    # 场景 B: 主 503，备成功流式返回 -> 不留 sticky 失败标记，正常计费
    reset_sdk_model_cache()

    def _client_per_slot(provider):
        if provider.slot == "primary":
            return _make_guarded_fake_client(always_503)

        async def backup_ok(*_args, **_kwargs):
            return _ok_stream_chunks(provider.model, "核对无误。")

        return _make_guarded_fake_client(backup_ok)

    monkeypatch.setattr(llm_providers, "get_async_client", _client_per_slot)
    with (
        patch("services.usage.model_call_guard.reserve_model_call", return_value=None),
        patch("services.usage.model_call_guard.settle_model_call"),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund_b,
    ):
        resp_b = await agent_api.stream_request(
            _SYNTHETIC_REQUEST,
            body=body,
            session=db_session,
            current_user=user,
            accept_language="zh",
            client_stop_contract="1",
            _rate_limit=0,
        )
        text_b = "".join([frame async for frame in resp_b.body_iterator])

    reset_sdk_model_cache()
    assert _frame_data(text_b, "done") is not None
    assert _frame_data(text_b, "quota_refunded") is None
    refund_b.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize("non_service_exc_kind", ["model_behavior", "db_timeout"])
async def test_sdk_model_behavior_error_and_non_model_timeout_after_attempt_are_charged(
    client: AsyncClient, db_session, monkeypatch, non_service_exc_kind
):
    """SDK ModelBehaviorError（协议异常）及非模型侧 TimeoutError 在已进入模型执行后终止：按非服务错误计费，不退还。"""
    from agents.exceptions import ModelBehaviorError

    from agent.core import llm_providers
    from agent.openai_agents.model import reset_sdk_model_cache

    user, project = await _make_user_and_project(db_session)

    async def ok_stream(*_args, **_kwargs):
        return _ok_stream_chunks("deepseek-chat", "思考完")

    fake_client = _make_guarded_fake_client(ok_stream)
    monkeypatch.setattr(llm_providers, "get_async_client", lambda _provider: fake_client)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "primary-key")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    reset_sdk_model_cache()

    body = agent_api.AgentRequest(project_id=str(project.id), message="检查章节")
    with (
        patch("services.usage.model_call_guard.reserve_model_call", return_value=None),
        patch("services.usage.model_call_guard.settle_model_call"),
        patch(
            "agents.Runner.run_streamed",
            side_effect=ModelBehaviorError("bad protocol") if non_service_exc_kind == "model_behavior" else TimeoutError("db timeout"),
        ),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        # 注入已启动尝试后在 runner 内抛非 provider-transport 错误
        orig_service = agent_api.get_agent_service()
        orig_process_stream = orig_service.process_stream

        async def _wrapped_stream(*args, **kwargs):
            await fake_client.chat.completions.create(model="deepseek-chat", messages=[], stream=True)
            async for frame in orig_process_stream(*args, **kwargs):
                yield frame

        with patch.object(orig_service, "process_stream", side_effect=_wrapped_stream):
            resp = await agent_api.stream_request(
                _SYNTHETIC_REQUEST,
                body=body,
                session=db_session,
                current_user=user,
                accept_language="zh",
                client_stop_contract="1",
                _rate_limit=0,
            )
            text = "".join([frame async for frame in resp.body_iterator])

    reset_sdk_model_cache()
    assert "event: error" in text
    assert _frame_data(text, "quota_refunded") is None
    refund.assert_not_called()


@pytest.mark.integration
async def test_with_options_local_clone_failure_keeps_provider_unattempted_and_refunds(
    client: AsyncClient, db_session, monkeypatch
):
    """with_options 本地克隆失败时尚未进入真实 transport：provider_attempted=False 且 transport 未被调用，退还额度。"""
    from agent.core import llm_providers
    from agent.openai_agents.model import reset_sdk_model_cache

    user, project = await _make_user_and_project(db_session)
    transport_called = False

    async def should_not_run(*_args, **_kwargs):
        nonlocal transport_called
        transport_called = True
        raise AssertionError("transport must not execute when with_options fails")

    def broken_with_options(**_kw):
        raise RuntimeError("local with_options clone failure")

    fake_client = _make_guarded_fake_client(should_not_run, with_options_impl=broken_with_options)
    monkeypatch.setattr(llm_providers, "get_async_client", lambda _provider: fake_client)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "primary-key")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    reset_sdk_model_cache()

    body = agent_api.AgentRequest(project_id=str(project.id), message="检查第一章")
    reservation = SimpleNamespace(started_at=datetime.now(UTC))
    with (
        patch("services.usage.model_call_guard.reserve_model_call", return_value=reservation),
        patch("services.usage.model_call_guard.settle_model_call"),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        resp = await agent_api.stream_request(
            _SYNTHETIC_REQUEST,
            body=body,
            session=db_session,
            current_user=user,
            accept_language="zh",
            client_stop_contract="1",
            _rate_limit=0,
        )
        text = "".join([frame async for frame in resp.body_iterator])

    reset_sdk_model_cache()
    assert transport_called is False
    assert _frame_data(text, "quota_refunded") == {"refunded": True, "kind": "error"}
    refund.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("scenario", "expect_refund"),
    [
        ("local_runtime_error_in_transport", False),
        ("local_api_error_outside_guard_after_recovered_failover", False),
        ("provider_429", True),
        ("provider_connect", True),
        ("provider_read_timeout", True),
        ("provider_in_stream_error", True),
    ],
)
async def test_positive_provider_transport_error_classification_vs_local_errors(
    client: AsyncClient, db_session, monkeypatch, caplog, scenario, expect_refund
):
    """正向确认真实供应商服务/网络/流异常才退款；普通 RuntimeError 或 guard 外本地构造的同类异常一律计费。"""
    from agent.core import llm_providers
    from agent.openai_agents import model as sdk_model_mod
    from agent.openai_agents.model import reset_sdk_model_cache

    user, project = await _make_user_and_project(db_session)
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    resp_429 = httpx.Response(429, request=req)
    resp_503 = httpx.Response(503, request=req)

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "primary-key")
    if scenario == "local_api_error_outside_guard_after_recovered_failover":
        monkeypatch.setenv("LLM_BACKUP_PROVIDER", "minimax")
        monkeypatch.setenv("MINIMAX_API_KEY", "backup-key")
    else:
        monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    reset_sdk_model_cache()

    async def transport_error(*_args, **_kwargs):
        if scenario == "local_runtime_error_in_transport":
            raise RuntimeError("local SDK/config failure")
        if scenario == "provider_429":
            raise openai.RateLimitError("429 rate limited", response=resp_429, body=None)
        if scenario == "provider_connect":
            raise openai.APIConnectionError(request=req)
        if scenario == "provider_read_timeout":
            raise httpx.ReadTimeout("read timed out", request=req)
        if scenario == "provider_in_stream_error":
            async def _broken_stream():
                if False:
                    yield None
                raise openai.APIError("in-stream failure", request=req, body={"error": "1000"})

            return _broken_stream()
        raise AssertionError(f"unexpected scenario {scenario}")

    if scenario == "local_api_error_outside_guard_after_recovered_failover":
        # 主供应商 503 已走备援成功，随后 SDK/本地层在 guard 外构造出一个未带 transport tag 的 APIError（且挂着旧 __context__）
        recovered_503 = openai.InternalServerError("503 primary", response=resp_503, body=None)

        def _client_per_slot(provider):
            if provider.slot == "primary":
                async def _primary_fail(*_a, **_kw):
                    raise recovered_503

                return _make_guarded_fake_client(_primary_fail)

            async def _backup_then_local_parse_fail(*_a, **_kw):
                return _ok_stream_chunks(provider.model, "备援首字")

            return _make_guarded_fake_client(_backup_then_local_parse_fail)

        monkeypatch.setattr(llm_providers, "get_async_client", _client_per_slot)
        orig_sdk_model_for = sdk_model_mod._sdk_model_for

        def _inject_untagged_local_api_error(provider):
            real_model = orig_sdk_model_for(provider)
            if provider.slot != "backup":
                return real_model

            class _LocalParseFailWrapper:
                async def stream_response(self, *a, **kw):
                    async for ev in real_model.stream_response(*a, **kw):
                        yield ev
                    local_exc = openai.APIError("locally synthesized parse error", request=req, body=None)
                    local_exc.__context__ = recovered_503
                    raise local_exc

            return _LocalParseFailWrapper()

        monkeypatch.setattr(sdk_model_mod, "_sdk_model_for", _inject_untagged_local_api_error)
    else:
        fake_client = _make_guarded_fake_client(transport_error)
        monkeypatch.setattr(llm_providers, "get_async_client", lambda _provider: fake_client)

    caplog.set_level("INFO")
    body = agent_api.AgentRequest(project_id=str(project.id), message="核对人物关系")
    with (
        patch("services.usage.model_call_guard.reserve_model_call", return_value=None),
        patch("services.usage.model_call_guard.settle_model_call"),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        resp = await agent_api.stream_request(
            _SYNTHETIC_REQUEST,
            body=body,
            session=db_session,
            current_user=user,
            accept_language="zh",
            client_stop_contract="1",
            _rate_limit=0,
        )
        text = "".join([frame async for frame in resp.body_iterator])

    reset_sdk_model_cache()
    eval_logs = [rec for rec in caplog.records if rec.message == "Agent stream billing evaluated"]
    assert eval_logs, "Expected Agent stream billing evaluated log"
    fields = eval_logs[-1].custom_fields
    if expect_refund:
        assert _frame_data(text, "quota_refunded") == {"refunded": True, "kind": "error"}
        refund.assert_called_once()
        assert fields["charged"] is False
    else:
        assert _frame_data(text, "quota_refunded") is None
        refund.assert_not_called()
        assert fields["charged"] is True


@pytest.mark.integration
@pytest.mark.parametrize("release_ok", [True, False])
async def test_pre_model_stop_aligns_done_charged_refund_frame_stop_outcome_and_log(
    client: AsyncClient, db_session, monkeypatch, caplog, release_ok
):
    """模型启动前停止：先完成 _settle_billing 再发 done，确保 done.charged、quota_refunded、落库 stop_outcome 与日志一致。"""
    from sqlmodel import select

    user, project = await _make_user_and_project(db_session)
    pre_model_paused = asyncio.Event()

    async def pause_before_any_model_call(*_args, **_kwargs):
        pre_model_paused.set()
        await asyncio.sleep(30)
        if False:
            yield None

    caplog.set_level("INFO")
    body = agent_api.AgentRequest(project_id=str(project.id), message="帮我看看大纲")
    with (
        patch("agent.service.run_loop_agent_streaming", pause_before_any_model_call),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=release_ok) as refund,
    ):
        resp = await agent_api.stream_request(
            _SYNTHETIC_REQUEST,
            body=body,
            session=db_session,
            current_user=user,
            accept_language="zh",
            client_stop_contract="1",
            _rate_limit=0,
        )
        run_id = resp.headers["X-Agent-Run-ID"]
        seen_frames: list[str] = []

        async def _consume():
            async for frame in resp.body_iterator:
                seen_frames.append(frame)

        consume_task = asyncio.create_task(_consume())
        await asyncio.wait_for(pre_model_paused.wait(), timeout=5.0)
        stopped = await agent_api.stop_stream(
            body=agent_api.StopRequest(agent_run_id=run_id),
            current_user=user,
            _rate_limit=0,
        )
        assert stopped.stop_requested is True
        await asyncio.wait_for(consume_task, timeout=5.0)
        if agent_api._round_outcome_tasks:
            await asyncio.gather(*list(agent_api._round_outcome_tasks))

    joined = "".join(seen_frames)
    done = _frame_data(joined, "done")
    assert done is not None
    assert done["stop_reason"] == "user_stopped"
    refund.assert_called_once()

    db_session.expire_all()
    assistant_msgs = db_session.exec(
        select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.project_id == project.id, ChatMessage.role == "assistant")
    ).all()
    assert len(assistant_msgs) == 1
    meta = json.loads(assistant_msgs[0].message_metadata or "{}")
    stop_outcome = meta.get("stop_outcome") or {}

    eval_logs = [rec for rec in caplog.records if rec.message == "Agent stream billing evaluated"]
    assert eval_logs
    fields = eval_logs[-1].custom_fields

    if release_ok:
        assert done["charged"] is False
        assert _frame_data(joined, "quota_refunded") == {"refunded": True, "kind": "stopped"}
        assert stop_outcome["charged"] is False
        assert fields["charged"] is False
        assert fields["should_refund"] is True
        assert fields["refunded"] is True
    else:
        assert done["charged"] is True
        assert _frame_data(joined, "quota_refunded") is None
        assert stop_outcome["charged"] is True
        assert fields["charged"] is True
        assert fields["should_refund"] is True
        assert fields["refunded"] is False


@pytest.mark.integration
async def test_mid_stream_provider_error_after_substantial_answer_output_skips_backup_preserves_partial_text_and_refunds(
    client: AsyncClient, db_session, monkeypatch, caplog
):
    """流中已输出超过 PROSE_MIN_CHARS 的实质正文后 __anext__ 抛供应商 APIError：不切备援避免重复回答，保留部分正文并退还额度。"""
    from openai.types.chat.chat_completion_chunk import ChatCompletionChunk, Choice, ChoiceDelta
    from sqlmodel import select

    from agent.core import llm_providers
    from agent.openai_agents.model import reset_sdk_model_cache

    user, project = await _make_user_and_project(db_session)
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    substantial_partial = (
        "第三章开篇先把旧书店的油灯、账本缺页与窗外夜雨交代清楚，"
        "让读者立刻意识到掌柜隐瞒了十年前那桩沉船旧案的真正经手人，"
        "随后再引出林舟推门而入时的第一句追问。"
    )
    assert len(substantial_partial) >= 75

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "primary-key")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "backup-key")
    reset_sdk_model_cache()

    primary_calls = 0
    backup_calls = 0

    def _client_per_slot(provider):
        if provider.slot == "primary":
            async def _primary_mid_stream_error(*_args, **_kwargs):
                nonlocal primary_calls
                primary_calls += 1

                async def _chunks():
                    yield ChatCompletionChunk(
                        id="cmpl-mid-err",
                        created=1,
                        model=provider.model,
                        object="chat.completion.chunk",
                        choices=[
                            Choice(
                                index=0,
                                delta=ChoiceDelta(role="assistant", content=substantial_partial),
                                finish_reason=None,
                            )
                        ],
                        usage=None,
                    )
                    raise openai.APIError("mid-stream upstream disconnect", request=req, body={"error": "1000"})

                return _chunks()

            return _make_guarded_fake_client(_primary_mid_stream_error)

        async def _backup_must_not_run(*_args, **_kwargs):
            nonlocal backup_calls
            backup_calls += 1
            raise AssertionError("backup must not be called once answer output already streamed")

        return _make_guarded_fake_client(_backup_must_not_run)

    monkeypatch.setattr(llm_providers, "get_async_client", _client_per_slot)
    caplog.set_level("INFO")
    body = agent_api.AgentRequest(project_id=str(project.id), message="帮我改写第三章开头导语")
    with (
        patch("services.usage.model_call_guard.reserve_model_call", return_value=None),
        patch("services.usage.model_call_guard.settle_model_call"),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        resp = await agent_api.stream_request(
            _SYNTHETIC_REQUEST,
            body=body,
            session=db_session,
            current_user=user,
            accept_language="zh",
            client_stop_contract="1",
            _rate_limit=0,
        )
        text = "".join([frame async for frame in resp.body_iterator])
        if agent_api._round_outcome_tasks:
            await asyncio.gather(*list(agent_api._round_outcome_tasks))

    reset_sdk_model_cache()
    assert primary_calls == 1
    assert backup_calls == 0  # answer_started=True，禁止切备援重复回答
    assert substantial_partial in text
    assert "event: error" in text
    assert _frame_data(text, "quota_refunded") == {"refunded": True, "kind": "error"}
    refund.assert_called_once()

    eval_logs = [rec for rec in caplog.records if rec.message == "Agent stream billing evaluated"]
    assert eval_logs
    fields = eval_logs[-1].custom_fields
    assert fields["produced_output"] is True
    assert fields["provider_attempted"] is True
    assert fields["terminal_model_failure"] is True
    assert fields["billing_reason"] == "upstream_model_error"
    assert fields["charged"] is False
    assert fields["refunded"] is True

    db_session.expire_all()
    assistant_msgs = db_session.exec(
        select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.project_id == project.id, ChatMessage.role == "assistant")
    ).all()
    assert len(assistant_msgs) == 1
    assert substantial_partial in assistant_msgs[0].content
    meta = json.loads(assistant_msgs[0].message_metadata or "{}")
    assert meta.get("display_events") == [{"type": "content", "content": substantial_partial}]
