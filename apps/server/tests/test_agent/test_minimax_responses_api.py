"""Regression tests for MiniMax /v1/responses migration + prompt_cache_key + safety guards."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import openai
import pytest
from openai import AsyncOpenAI
from openai.types.responses import (
    Response,
    ResponseIncompleteEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningItem,
)
from openai.types.responses.response import IncompleteDetails
from openai.types.responses.response_reasoning_item import Content as ReasoningContent
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails, ResponseUsage
from sqlmodel import select

from agent.core import llm_failover, llm_providers
from agent.core.workflow_events import StreamEventType
from agent.loop import compaction, wire_prefix_cache
from agent.loop.history import LoopHistory
from agent.openai_agents import model as sdk_model
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import AICostDailyBudget, AICostReservation, User
from models.subscription import UsageQuota
from services.usage import cost_budget as budget
from services.usage.llm_usage_service import LLMUsageAttribution
from services.usage.model_call_guard import install_async_cost_guard, install_sync_cost_guard

NOW = datetime(2026, 10, 13, 2, tzinfo=UTC)  # Tuesday 10:00 Beijing


def _make_user(db_session, suffix: str = "resp") -> User:
    account = User(
        username=f"resp_{suffix}",
        email=f"resp_{suffix}@example.com",
        hashed_password="x",
    )
    db_session.add(account)
    db_session.commit()
    return account


def _make_response(
    *,
    status: str = "completed",
    text: str = "正文回复。",
    thinking: str = "",
    output_tokens: int = 40,
    input_tokens: int = 1000,
    cached_tokens: int = 900,
    incomplete_reason: str | None = None,
) -> Response:
    output: list = []
    if thinking:
        output.append(
            ResponseReasoningItem(
                id="rs_1",
                type="reasoning",
                summary=[],
                content=[ReasoningContent(type="reasoning_text", text=thinking)],
                encrypted_content="enc_blob_123",
                status="completed",
            )
        )
    if text:
        output.append(
            ResponseOutputMessage(
                id="msg_1",
                type="message",
                role="assistant",
                status="completed" if status == "completed" else "incomplete",
                content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
            )
        )
    return Response(
        id="resp_test_1",
        created_at=1.0,
        model="MiniMax-M3.1-Flash",
        object="response",
        output=output,
        parallel_tool_calls=True,
        tool_choice="auto",
        tools=[],
        status=status,  # type: ignore[arg-type]
        incomplete_details=IncompleteDetails(reason=incomplete_reason) if incomplete_reason else None,  # type: ignore[arg-type]
        usage=ResponseUsage(
            input_tokens=input_tokens,
            input_tokens_details=InputTokensDetails(cached_tokens=cached_tokens, cache_write_tokens=0),
            output_tokens=output_tokens,
            output_tokens_details=OutputTokensDetails(reasoning_tokens=10 if thinking else 0),
            total_tokens=input_tokens + output_tokens,
        ),
    )


def _responses_sse_body(events: list[dict]) -> str:
    return "".join(f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n" for ev in events)


# ---------------------------------------------------------------------------
# 1. Runtime surface switch & deterministic prompt_cache_key & wire_prefix_cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_minimax_api_surface_switch_and_provider_cache_key(monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)

    primary, backup = llm_providers.configured_providers()
    assert primary.api_surface == llm_providers.API_SURFACE_RESPONSES
    assert primary.uses_responses_api is True
    assert backup.api_surface == llm_providers.API_SURFACE_CHAT_COMPLETIONS
    assert backup.uses_responses_api is False
    assert wire_prefix_cache.provider_cache_key(primary) == "minimax:MiniMax-M3.1-Flash:primary:responses"
    assert wire_prefix_cache.provider_cache_key(backup) == "deepseek:deepseek-flash:primary"

    # Instant env-only rollback to Chat Completions
    monkeypatch.setenv("MINIMAX_API_SURFACE", "chat_completions")
    rolled_back, _ = llm_providers.configured_providers()
    assert rolled_back.api_surface == llm_providers.API_SURFACE_CHAT_COMPLETIONS
    assert rolled_back.uses_responses_api is False
    assert wire_prefix_cache.provider_cache_key(rolled_back) == "minimax:MiniMax-M3.1-Flash:primary"

    # Invalid value falls back safely to provider default ("responses")
    monkeypatch.setenv("MINIMAX_API_SURFACE", "invalid_surface")
    fallback_default, _ = llm_providers.configured_providers()
    assert fallback_default.uses_responses_api is True


@pytest.mark.unit
def test_prompt_cache_key_for_session_is_deterministic_and_bounded():
    assert llm_providers.prompt_cache_key_for_session(None) is None
    assert llm_providers.prompt_cache_key_for_session("   ") is None
    assert llm_providers.prompt_cache_key_for_session("sess-123") == "zs:sess-123"
    long_id = "session-" + ("a" * 100)
    key1 = llm_providers.prompt_cache_key_for_session(long_id)
    key2 = llm_providers.prompt_cache_key_for_session(long_id)
    assert key1 == key2
    assert key1 is not None and key1.startswith("zs:") and len(key1) <= 64


# ---------------------------------------------------------------------------
# 2. [P0] Cost-budget guard, sanitizer, trial cap & transport hook on Responses API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_responses_streaming_cost_guard_settles_completed_and_blocks_over_cap(db_session, monkeypatch):
    from agents import ModelSettings, OpenAIResponsesModel
    from agents.models.interface import ModelTracing

    account = _make_user(db_session, "resp_stream")
    monkeypatch.setattr(budget, "utcnow", lambda: NOW)
    captured_bodies: list[dict] = []
    transport_notified: list[bool] = []

    completed_resp = _make_response(
        status="completed",
        text="正文内容",
        input_tokens=1_000_100,
        cached_tokens=1_000_000,
        output_tokens=100,
    )
    sse_payload = _responses_sse_body([
        {"type": "response.created", "sequence_number": 0, "response": completed_resp.model_dump(mode="json")},
        {"type": "response.output_text.delta", "sequence_number": 1, "item_id": "msg_1", "output_index": 0, "content_index": 0, "delta": "正文内容", "logprobs": []},
        {"type": "response.completed", "sequence_number": 2, "response": completed_resp.model_dump(mode="json")},
    ])

    async def handle(request: httpx.Request) -> httpx.Response:
        db_session.expire_all()
        assert db_session.exec(select(AICostReservation)).one().settled_units is None
        captured_bodies.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
        client = install_async_cost_guard(
            AsyncOpenAI(api_key="test-key", base_url="https://mock.invalid/v1", http_client=http_client, max_retries=2)
        )
        model = OpenAIResponsesModel(model="MiniMax-M3.1-Flash", openai_client=client)
        attribution = LLMUsageAttribution(user_id=account.id, source="agent")

        async def run_stream():
            return [
                ev
                async for ev in model.stream_response(
                    system_instructions="系统\x00提示\n保留换行",
                    input=[
                        {"role": "user", "content": "写\x07正文\t保留制表"},
                        {
                            "id": "rs_opaque",
                            "type": "reasoning",
                            "summary": [],
                            "content": [{"type": "reasoning_text", "text": "思\x1f考"}],
                            "encrypted_content": "opaque\x00keep",
                        },
                    ],
                    model_settings=ModelSettings(
                        max_tokens=1000,
                        store=False,
                        extra_args={"prompt_cache_key": "zs:sess-guard"},
                    ),
                    tools=[],
                    output_schema=None,
                    handoffs=[],
                    tracing=ModelTracing.DISABLED,
                )
            ]

        with (
            budget.budget_attribution(attribution),
            budget.on_model_transport_start(lambda: transport_notified.append(True)),
        ):
            events = await run_stream()
            assert any(ev.type == "response.completed" for ev in events)
            assert transport_notified == [True]
            assert len(captured_bodies) == 1
            # Sanitizer stripped NUL / BEL / US while keeping newline, tab, and opaque encrypted_content
            assert captured_bodies[0]["instructions"] == "系统提示\n保留换行"
            assert captured_bodies[0]["input"][0]["content"] == "写正文\t保留制表"
            assert captured_bodies[0]["input"][1]["content"][0]["text"] == "思考"
            assert captured_bodies[0]["input"][1]["encrypted_content"] == "opaque\x00keep"
            assert captured_bodies[0]["prompt_cache_key"] == "zs:sess-guard"
            assert captured_bodies[0]["store"] is False

            db_session.expire_all()
            reservation = db_session.exec(select(AICostReservation)).one()
            bucket = db_session.exec(select(AICostDailyBudget)).one()
            # MiniMax M3.1-Flash: 1M hit (10M units) + 100 miss (10k units) + 100 out (40k units) = 10,050,000
            assert reservation.settled_units == bucket.charged_units == 10_050_000

            # Exhaust free daily cap -> next Responses stream call is blocked BEFORE transport
            bucket.charged_units = budget.FREE_DAILY_BUDGET_UNITS
            db_session.add(bucket)
            db_session.commit()
            with pytest.raises(APIException) as err:
                await run_stream()
            assert err.value.error_code == budget.COST_LIMIT_CODE
            assert err.value.status_code == 402
            assert len(captured_bodies) == 1
            assert transport_notified == [True]


@pytest.mark.asyncio
async def test_responses_stream_settles_usage_on_incomplete_event(db_session, monkeypatch):
    account = _make_user(db_session, "resp_incomplete_settle")
    monkeypatch.setattr(budget, "utcnow", lambda: NOW)
    incomplete_resp = _make_response(
        status="incomplete",
        text="半章正文",
        input_tokens=500,
        cached_tokens=400,
        output_tokens=256,
        incomplete_reason="max_output_tokens",
    )

    async def fake_stream():
        yield ResponseIncompleteEvent(type="response.incomplete", response=incomplete_resp, sequence_number=1)

    responses_create = AsyncMock(return_value=fake_stream())
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
        responses=SimpleNamespace(create=responses_create),
    )
    install_async_cost_guard(client)

    with budget.budget_attribution(LLMUsageAttribution(user_id=account.id, source="agent")):
        stream = await client.responses.create(
            model="MiniMax-M3.1-Flash",
            instructions="sys",
            input=[{"role": "user", "content": "继续"}],
            max_output_tokens=256,
            stream=True,
        )
        seen = [chunk async for chunk in stream]

    assert len(seen) == 1
    db_session.expire_all()
    reservation = db_session.exec(select(AICostReservation)).one()
    # 400 hit (4000) + 100 miss (10000) + 256 out (102400) = 116400
    assert reservation.settled_units == 116_400


@pytest.mark.asyncio
async def test_responses_material_trial_cap_clamps_max_output_tokens_and_blocks_over_budget(db_session, monkeypatch):
    account = _make_user(db_session, "resp_trial")
    monkeypatch.setattr(budget, "utcnow", lambda: NOW)
    quota = UsageQuota(
        user_id=account.id,
        period_start=NOW.replace(tzinfo=None),
        period_end=(NOW + timedelta(days=30)).replace(tzinfo=None),
        material_trial_used_at=NOW.replace(tzinfo=None),
        material_trial_charged_units=0,
    )
    db_session.add(quota)
    db_session.commit()

    captured: list[dict] = []

    async def fake_create(**kwargs):
        captured.append(dict(kwargs))
        return _make_response(input_tokens=200, cached_tokens=0, output_tokens=50)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
        responses=SimpleNamespace(create=fake_create),
    )
    install_async_cost_guard(client)

    with (
        budget.budget_attribution(LLMUsageAttribution(user_id=account.id, source="material")),
        budget.material_trial_call(output_limit=400),
    ):
        await client.responses.create(
            model="MiniMax-M3.1-Flash",
            instructions="sys",
            input="hi",
            max_output_tokens=9000,
        )
        assert captured[0]["max_output_tokens"] == 400

        # Exceed lifetime 1 CNY trial cap -> blocked with 402 before transport
        db_session.expire_all()
        q = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == account.id)).one()
        q.material_trial_charged_units = budget.MATERIAL_TRIAL_BUDGET_UNITS
        db_session.add(q)
        db_session.commit()
        with pytest.raises(APIException) as err:
            await client.responses.create(
                model="MiniMax-M3.1-Flash",
                instructions="sys",
                input="hi",
                max_output_tokens=400,
            )
        assert err.value.error_code == ErrorCode.MATERIAL_TRIAL_COST_LIMIT
        assert err.value.status_code == 402
        assert len(captured) == 1


@pytest.mark.unit
def test_responses_multimodal_input_fails_closed_and_sync_guard_settles(db_session, monkeypatch):
    account = _make_user(db_session, "resp_sync_mm")
    monkeypatch.setattr(budget, "utcnow", lambda: NOW)

    with pytest.raises(APIException) as err:
        budget.reserve_model_call(
            {
                "model": "MiniMax-M3.1-Flash",
                "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "https://x"}]}],
                "max_output_tokens": 100,
            },
            user_id=account.id,
            now=NOW,
        )
    assert err.value.error_code == budget.UNAVAILABLE_CODE

    sync_create = Mock(return_value=_make_response(input_tokens=100, cached_tokens=0, output_tokens=50))
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=Mock())),
        responses=SimpleNamespace(create=sync_create),
    )
    install_sync_cost_guard(client)
    with budget.budget_attribution(LLMUsageAttribution(user_id=account.id, source="material")):
        client.responses.create(
            model="MiniMax-M3.1-Flash",
            instructions="s\x00y",
            input="u\x07i",
            max_output_tokens=100,
        )
    assert sync_create.call_args.kwargs["instructions"] == "sy"
    assert sync_create.call_args.kwargs["input"] == "ui"
    db_session.expire_all()
    assert db_session.exec(select(AICostReservation)).one().settled_units == 30_000


# ---------------------------------------------------------------------------
# 3. [P0] Graceful response.incomplete(reason="max_output_tokens") handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_handles_responses_incomplete_max_output_tokens_with_partial_file_body(monkeypatch):
    """Streaming response.incomplete(reason='max_output_tokens') must NEVER raise ModelBehaviorError;
    it preserves streamed text, records usage, and sets output_capped=True on MESSAGE_END."""
    from agent.openai_agents import runner
    from agent.openai_agents.runner import OUTPUT_CAPPED_KEY, run_openai_agents_streaming_agent

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()

    incomplete_resp = _make_response(
        status="incomplete",
        text="<file>\n第一章残稿写到一半",
        thinking="先写开头",
        input_tokens=1200,
        cached_tokens=1024,
        output_tokens=200,  # Intentionally below 0.98 * 64000 to prove incomplete_details drives output_capped!
        incomplete_reason="max_output_tokens",
    )
    sse_payload = _responses_sse_body([
        {"type": "response.created", "sequence_number": 0, "response": incomplete_resp.model_dump(mode="json")},
        {"type": "response.reasoning_text.delta", "sequence_number": 1, "item_id": "rs_1", "output_index": 0, "content_index": 0, "delta": "先写开头"},
        {"type": "response.output_text.delta", "sequence_number": 2, "item_id": "msg_1", "output_index": 1, "content_index": 0, "delta": "<file>\n第一章残稿写到一半", "logprobs": []},
        {"type": "response.incomplete", "sequence_number": 3, "response": incomplete_resp.model_dump(mode="json")},
    ])

    async def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_payload)

    scheduled_usage: list[dict] = []
    monkeypatch.setattr(
        "agent.openai_agents.usage_hooks.schedule_llm_usage_record",
        lambda _attr, *, model, usage=None, **_kw: scheduled_usage.append({"model": model, "usage": usage}),
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
        mock_client = AsyncOpenAI(
            api_key="sk-test", base_url="https://mock.invalid/v1", http_client=http_client, max_retries=0
        )
        monkeypatch.setattr(llm_providers, "get_async_client", lambda _p: mock_client)
        state = {"user_message": "写第一章", "session_id": "sess-cap-body"}
        events = [
            ev
            async for ev in run_openai_agents_streaming_agent(state, "loop", "系统提示")  # type: ignore[arg-type]
        ]

    types = [ev.type for ev in events]
    assert StreamEventType.ERROR not in types
    assert StreamEventType.TEXT in types
    assert types[-1] == StreamEventType.MESSAGE_END
    end = events[-1].data
    assert end["stop_reason"] == "end_turn"
    assert end.get(OUTPUT_CAPPED_KEY) is True
    assert end["usage"]["cache_read_tokens"] == 1024
    assert end["usage"]["output_tokens"] == 200
    assert len(scheduled_usage) == 1
    assert scheduled_usage[0]["usage"].output_tokens == 200
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()
    del runner


@pytest.mark.asyncio
async def test_runner_handles_responses_incomplete_thinking_only_as_output_truncated(monkeypatch):
    from agent.openai_agents.runner import OUTPUT_TRUNCATED_STOP_REASON, run_openai_agents_streaming_agent

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()

    incomplete_resp = _make_response(
        status="incomplete",
        text="",
        thinking="只思考没写正文",
        input_tokens=1000,
        cached_tokens=800,
        output_tokens=256,
        incomplete_reason="max_output_tokens",
    )
    sse_payload = _responses_sse_body([
        {"type": "response.created", "sequence_number": 0, "response": incomplete_resp.model_dump(mode="json")},
        {"type": "response.reasoning_text.delta", "sequence_number": 1, "item_id": "rs_1", "output_index": 0, "content_index": 0, "delta": "只思考没写正文"},
        {"type": "response.incomplete", "sequence_number": 2, "response": incomplete_resp.model_dump(mode="json")},
    ])

    async def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_payload)

    monkeypatch.setattr("agent.openai_agents.usage_hooks.schedule_llm_usage_record", lambda *_a, **_kw: None)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
        mock_client = AsyncOpenAI(
            api_key="sk-test", base_url="https://mock.invalid/v1", http_client=http_client, max_retries=0
        )
        monkeypatch.setattr(llm_providers, "get_async_client", lambda _p: mock_client)
        state = {"user_message": "写第一章", "session_id": "sess-cap-think"}
        events = [
            ev
            async for ev in run_openai_agents_streaming_agent(state, "loop", "系统提示")  # type: ignore[arg-type]
        ]

    types = [ev.type for ev in events]
    assert types[-2:] == [StreamEventType.MESSAGE_END, StreamEventType.ERROR]
    assert events[-2].data["stop_reason"] == OUTPUT_TRUNCATED_STOP_REASON
    assert events[-2].data["usage"]["output_tokens"] == 256
    assert events[-1].data["code"] == ErrorCode.AGENT_OUTPUT_TRUNCATED
    assert events[-1].data["retryable"] is False
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()


@pytest.mark.asyncio
async def test_non_token_response_incomplete_still_raises_model_behavior_error(monkeypatch):
    from agents import ModelSettings
    from agents.exceptions import ModelBehaviorError
    from agents.models.interface import ModelTracing

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()

    cf_resp = _make_response(status="incomplete", text="", incomplete_reason="content_filter")
    sse_payload = _responses_sse_body([
        {"type": "response.created", "sequence_number": 0, "response": cf_resp.model_dump(mode="json")},
        {"type": "response.incomplete", "sequence_number": 1, "response": cf_resp.model_dump(mode="json")},
    ])

    async def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
        mock_client = AsyncOpenAI(
            api_key="sk-test", base_url="https://mock.invalid/v1", http_client=http_client, max_retries=0
        )
        monkeypatch.setattr(llm_providers, "get_async_client", lambda _p: mock_client)
        model = sdk_model.build_failover_chat_model("sess-cf")
        with pytest.raises(ModelBehaviorError):
            async for _ in model.stream_response("sys", "hi", ModelSettings(), [], None, [], ModelTracing.DISABLED):
                pass
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()


# ---------------------------------------------------------------------------
# 4. [P1] Compaction on /v1/responses + retry + failover to DeepSeek Chat Completions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_uses_responses_api_with_session_cache_key_and_retries_invalid_output(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    llm_failover.reset_failover_state()

    calls: list[dict] = []
    # Attempt 1 leaks a function_call item; Attempt 2 succeeds with clean summary text.
    bad_resp = SimpleNamespace(
        status="completed",
        output=[{"type": "function_call", "call_id": "c1", "name": "query_files", "arguments": "{}"}],
        usage={"input_tokens": 15000, "input_tokens_details": {"cached_tokens": 14500}, "output_tokens": 30, "total_tokens": 15030},
    )
    good_resp = _make_response(
        status="completed",
        text="## 交接摘要\n- 已完成第1章",
        input_tokens=15050,
        cached_tokens=14500,
        output_tokens=80,
    )
    script = [bad_resp, good_resp]

    async def fake_responses_create(**kwargs):
        calls.append(kwargs)
        return script.pop(0)

    fake_client = SimpleNamespace(
        responses=SimpleNamespace(create=fake_responses_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
    )
    monkeypatch.setattr(llm_providers, "get_async_client", lambda _p: fake_client)

    history = LoopHistory(
        items=[
            {"role": "user", "content": "写第1章"},
            {
                "id": "__fake_id__",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "构思第1章"}],
                "encrypted_content": "enc1",
            },
            {"role": "assistant", "content": "第1章已写完。"},
        ],
        last_prompt_tokens=320_000,
        last_message_id="msg-20",
        author_messages=["写第1章"],
    )

    outcome = await compaction.compact_history(history, system_prompt="静态系统提示", session_id="sess-compact-1")
    assert outcome.record is not None
    assert outcome.record["summary"] == "## 交接摘要\n- 已完成第1章"
    assert len(calls) == 2
    for req in calls:
        assert req["instructions"] == "静态系统提示"
        assert req["store"] is False
        assert req["stream"] is True
        assert req["prompt_cache_key"] == "zs:sess-compact-1"
        assert req["reasoning"] == {"effort": "high"}
        assert req["tools"] and all(t["type"] == "function" for t in req["tools"])
        assert all(it.get("id") != "__fake_id__" for it in req["input"] if isinstance(it, dict))
    # Retry prompt note appended on attempt 2
    assert "[重试提醒]" in calls[1]["input"][-1]["content"]
    assert outcome.usage is not None
    assert outcome.usage["cache_read_tokens"] == 29_000
    llm_failover.reset_failover_state()


@pytest.mark.asyncio
async def test_compaction_fails_over_from_minimax_responses_to_deepseek_chat_completions(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    llm_failover.reset_failover_state()

    req_obj = httpx.Request("POST", "https://llm.test")
    mm_err = openai.InternalServerError("503", response=httpx.Response(503, request=req_obj), body=None)
    ds_calls: list[dict] = []

    async def mm_responses_create(**_kwargs):
        raise mm_err

    async def ds_chat_create(**kwargs):
        ds_calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="备源交接摘要"), finish_reason="stop")],
            usage={"prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550},
        )

    clients = {
        "minimax": SimpleNamespace(responses=SimpleNamespace(create=mm_responses_create)),
        "deepseek": SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=ds_chat_create))),
    }
    monkeypatch.setattr(llm_providers, "get_async_client", lambda p: clients[p.name])

    history = LoopHistory(
        items=[
            {"role": "user", "content": "查一下文件"},
            {
                "id": "rs_1",
                "type": "reasoning",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "先查大纲"}],
                "encrypted_content": "enc",
            },
            {"type": "function_call", "call_id": "c1", "name": "query_files", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": '{"status":"success"}'},
            {"role": "assistant", "content": "查完了。"},
        ],
        last_prompt_tokens=320_000,
        last_message_id="msg-30",
        author_messages=["查一下文件"],
    )

    outcome = await compaction.compact_history(history, system_prompt="系统提示", session_id="sess-compact-fo")
    assert outcome.record is not None
    assert outcome.record["summary"] == "备源交接摘要"
    assert len(ds_calls) == 1
    assert ds_calls[0]["model"] == "deepseek-flash"
    tool_asst = next(m for m in ds_calls[0]["messages"] if m.get("role") == "assistant" and m.get("tool_calls"))
    assert tool_asst["reasoning_content"] == "先查大纲"
    llm_failover.reset_failover_state()


# ---------------------------------------------------------------------------
# 5. [P1] FailoverChatModel dispatch, settings isolation, DeepSeek reasoning bridge & diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sdk_model_dispatch_and_surface_settings_isolation(monkeypatch):
    from agents import ModelSettings, OpenAIChatCompletionsModel, OpenAIResponsesModel

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    sdk_model.reset_sdk_model_cache()

    mm, ds = llm_providers.configured_providers()
    assert isinstance(sdk_model._sdk_model_for(mm), OpenAIResponsesModel)
    assert isinstance(sdk_model._sdk_model_for(ds), OpenAIChatCompletionsModel)

    base_settings = ModelSettings(temperature=1.0, max_tokens=2048)
    mm_settings = sdk_model._apply_surface_settings(mm, sdk_model._settings_for(mm, base_settings), "sess-abc")
    assert mm_settings.store is False
    assert mm_settings.extra_args == {"prompt_cache_key": "zs:sess-abc"}
    assert mm_settings.reasoning is not None and mm_settings.reasoning.effort == "high"

    # Failing over to DeepSeek strips store and prompt_cache_key even if pre-populated
    ds_settings = sdk_model._apply_surface_settings(ds, mm_settings, "sess-abc")
    assert ds_settings.store is None
    assert ds_settings.extra_args is None
    sdk_model.reset_sdk_model_cache()


@pytest.mark.asyncio
async def test_mid_run_failover_from_minimax_responses_to_deepseek_bridges_reasoning_content(monkeypatch):
    from agents import ModelSettings
    from agents.models.interface import ModelTracing

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()

    # Exact MiniMax /v1/responses history shape: reasoning text under content[], summary=[]
    responses_history = [
        {"role": "user", "content": "帮我看下文件"},
        {
            "id": "0753_rs",
            "type": "reasoning",
            "summary": [],
            "content": [{"type": "reasoning_text", "text": "先调用 query_files 查目录"}],
            "encrypted_content": "enc_xyz",
            "status": "completed",
        },
        {
            "id": "0753_fc",
            "type": "function_call",
            "call_id": "call_mm_1",
            "name": "query_files",
            "arguments": "{}",
            "status": "completed",
        },
        {"type": "function_call_output", "call_id": "call_mm_1", "output": '{"status":"success","files":[]}'},
    ]

    _mm, ds = llm_providers.configured_providers()
    # Bridge prevents false-positive rejection of DeepSeek failover!
    assert sdk_model._rejects_missing_tool_turn_reasoning(ds, responses_history) is False
    # Original history must NOT be mutated
    assert responses_history[1]["summary"] == []

    # Verify live mid-run failover from MiniMax Responses (503) to DeepSeek Chat Completions
    ds_payloads: list[dict] = []

    async def handle_mm(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    async def handle_ds(req: httpx.Request) -> httpx.Response:
        ds_payloads.append(json.loads(req.content))
        chunks = [
            {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "deepseek-flash", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "备源接管成功"}, "finish_reason": None}]},
            {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "deepseek-flash", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handle_mm)) as mm_http,
        httpx.AsyncClient(transport=httpx.MockTransport(handle_ds)) as ds_http,
    ):
        clients = {
            "minimax": AsyncOpenAI(api_key="sk-mm", base_url="https://mm.invalid/v1", http_client=mm_http, max_retries=0),
            "deepseek": AsyncOpenAI(api_key="sk-ds", base_url="https://ds.invalid/v1", http_client=ds_http, max_retries=0),
        }
        monkeypatch.setattr(llm_providers, "get_async_client", lambda p: clients[p.name])
        model = sdk_model.build_failover_chat_model("sess-bridge-fo")
        events = [
            ev
            async for ev in model.stream_response(
                "系统提示", responses_history, ModelSettings(), [], None, [], ModelTracing.DISABLED
            )
        ]

    assert model.served_provider_name == "deepseek"
    assert any(getattr(ev, "delta", None) == "备源接管成功" for ev in events)
    assert len(ds_payloads) == 1
    sent_msgs = ds_payloads[0]["messages"]
    tool_msg = next(m for m in sent_msgs if m.get("role") == "assistant" and m.get("tool_calls"))
    assert tool_msg["reasoning_content"] == "先调用 query_files 查目录"
    assert "prompt_cache_key" not in ds_payloads[0]
    assert "store" not in ds_payloads[0]
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()


@pytest.mark.unit
def test_failover_diagnostics_cover_responses_params_and_httpx2():
    import httpx2

    for field in ("input", "instructions", "max_output_tokens", "prompt_cache_key", "store", "reasoning"):
        assert field in llm_failover._FAILURE_PARAMETER_FIELDS
    assert llm_failover.is_provider_unavailable(httpx2.ReadTimeout("timeout")) is True
    assert llm_failover.is_provider_unavailable(httpx2.ConnectError("connect")) is True


# ---------------------------------------------------------------------------
# 6. [P2] Responses API response.failed -> in-stream failover & upstream refund
# ---------------------------------------------------------------------------


def _make_failed_response(*, code: str = "internal_error", message: str = "upstream failure") -> dict:
    base = _make_response(status="failed", text="", output_tokens=0).model_dump(mode="json")
    base["error"] = {"code": code, "message": message}
    return base


@pytest.mark.asyncio
async def test_responses_failed_pre_answer_fails_over_to_backup_without_tripping_circuit(monkeypatch):
    """MiniMax /v1/responses response.failed(internal_error) before answer output must fail over
    to the backup provider and pin the session without opening the global circuit."""
    from agents import ModelSettings
    from agents.models.interface import ModelTracing

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-mm")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    monkeypatch.setattr(llm_failover, "LLM_CIRCUIT_FAILURE_THRESHOLD", 1)
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()

    failed_resp = _make_failed_response(code="internal_error", message="internal error")
    mm_sse = _responses_sse_body([
        {"type": "response.created", "sequence_number": 0, "response": _make_response(status="in_progress", text="", output_tokens=0).model_dump(mode="json")},
        {"type": "response.reasoning_text.delta", "sequence_number": 1, "item_id": "rs_1", "output_index": 0, "content_index": 0, "delta": "先想一下"},
        {"type": "response.failed", "sequence_number": 2, "response": failed_resp},
    ])

    ds_chunks = [
        {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "deepseek-flash", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "备源已接管"}, "finish_reason": None}]},
        {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "deepseek-flash", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    ds_sse = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in ds_chunks) + "data: [DONE]\n\n"

    calls = {"minimax": 0, "deepseek": 0}

    async def handle_mm(_req: httpx.Request) -> httpx.Response:
        calls["minimax"] += 1
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=mm_sse)

    async def handle_ds(_req: httpx.Request) -> httpx.Response:
        calls["deepseek"] += 1
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=ds_sse)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handle_mm)) as mm_http,
        httpx.AsyncClient(transport=httpx.MockTransport(handle_ds)) as ds_http,
    ):
        clients = {
            "minimax": AsyncOpenAI(api_key="sk-mm", base_url="https://mm.invalid/v1", http_client=mm_http, max_retries=0),
            "deepseek": AsyncOpenAI(api_key="sk-ds", base_url="https://ds.invalid/v1", http_client=ds_http, max_retries=0),
        }
        monkeypatch.setattr(llm_providers, "get_async_client", lambda p: clients[p.name])
        model = sdk_model.build_failover_chat_model("sess-resp-failed-fo")
        events = [
            ev
            async for ev in model.stream_response(
                "系统提示", [{"role": "user", "content": "写一段"}], ModelSettings(), [], None, [], ModelTracing.DISABLED
            )
        ]

    assert calls == {"minimax": 1, "deepseek": 1}
    assert model.served_provider_name == "deepseek"
    assert model.unrecovered_provider_error is None
    assert any(getattr(ev, "delta", None) == "备源已接管" for ev in events)
    # Pinned for this session, while a different session still starts on primary (no global circuit trip)
    assert llm_failover.ordered_providers("sess-resp-failed-fo")[0].name == "deepseek"
    assert llm_failover.ordered_providers("sess-other-author")[0].name == "minimax"
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "expected_stream_code"),
    [
        ("internal_error", ErrorCode.AGENT_UPSTREAM_UNAVAILABLE),
        ("rate_limit_exceeded", ErrorCode.AGENT_UPSTREAM_RATE_LIMITED),
    ],
)
async def test_responses_failed_unrecovered_is_classified_as_upstream_model_error_and_refunded(
    monkeypatch, error_code, expected_stream_code
):
    """Unrecovered response.failed must NOT become ModelBehaviorError / non_model_error;
    it emits retryable+refundable upstream error with error_source='model' for quota refund."""
    from agent.core.stream_billing import StreamBillingTracker
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()

    failed_resp = _make_failed_response(code=error_code, message="upstream boom")
    sse_payload = _responses_sse_body([
        {"type": "response.created", "sequence_number": 0, "response": _make_response(status="in_progress", text="", output_tokens=0).model_dump(mode="json")},
        {"type": "response.failed", "sequence_number": 1, "response": failed_resp},
    ])

    async def handle(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
        mock_client = AsyncOpenAI(
            api_key="sk-test", base_url="https://mock.invalid/v1", http_client=http_client, max_retries=0
        )
        monkeypatch.setattr(llm_providers, "get_async_client", lambda _p: mock_client)
        state = {"user_message": "继续写", "session_id": f"sess-fail-{error_code}"}
        events = [
            ev
            async for ev in run_openai_agents_streaming_agent(state, "loop", "系统提示")  # type: ignore[arg-type]
        ]

    err_events = [ev for ev in events if ev.type == StreamEventType.ERROR]
    assert len(err_events) == 1
    data = err_events[0].data
    assert data["code"] == expected_stream_code
    assert data["retryable"] is True
    assert data["refundable"] is True
    assert data["error_source"] == "model"

    tracker = StreamBillingTracker()
    assert tracker.decide(
        client_disconnected=False,
        unexpected_exception=False,
        provider_attempted=True,
        terminal_model_failure=True,
    ) == ("upstream_model_error", True)
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()


# ---------------------------------------------------------------------------
# 7. [P0] Streaming compaction collection, request_context attribution, budget guard & pin TTL
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_session_pin_ttl_and_empty_attribution_budget_fallback(monkeypatch):
    from services.usage import cost_budget
    from services.usage.llm_usage_service import LLMUsageAttribution

    monkeypatch.delenv("LLM_SESSION_PIN_TTL_S", raising=False)
    assert llm_failover.get_int_env("LLM_SESSION_PIN_TTL_S", 300) == 300
    assert llm_failover.LLM_SESSION_PIN_TTL_S == 300

    # An empty attribution (user_id=None) must NOT shadow the bound ambient user_id.
    cost_budget.set_context_user_id("free-user-ctx")
    try:
        with cost_budget.budget_attribution(LLMUsageAttribution(user_id=None, source="agent")):
            assert cost_budget.current_user_id() == "free-user-ctx"
        with cost_budget.budget_attribution(LLMUsageAttribution(user_id="explicit-user", source="agent")):
            assert cost_budget.current_user_id() == "explicit-user"
    finally:
        cost_budget.set_context_user_id(None)


@pytest.mark.asyncio
async def test_compaction_streaming_collects_terminal_response_and_meters_request_context(monkeypatch):
    from services.usage import llm_usage_service

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    llm_failover.reset_failover_state()

    terminal_response = _make_response(
        text="## 交接摘要\n- 已通过流式 Responses 完成压缩",
        input_tokens=310_000,
        cached_tokens=295_000,
        output_tokens=1_200,
    )

    async def _event_stream():
        yield SimpleNamespace(type="response.created")
        yield SimpleNamespace(type="response.reasoning_text.delta", delta="整理前文脉络")
        yield SimpleNamespace(type="response.output_text.delta", delta="## 交接摘要")
        yield SimpleNamespace(type="response.completed", response=terminal_response)

    calls: list[dict] = []

    async def fake_responses_create(**kwargs):
        calls.append(kwargs)
        return _event_stream()

    recorded: list[dict] = []

    def fake_schedule(attribution, *, model, usage=None, tokens=None, timing=None):
        recorded.append({"attribution": attribution, "model": model, "usage": usage, "timing": timing})

    monkeypatch.setattr(llm_usage_service, "schedule_llm_usage_record", fake_schedule)
    fake_client = SimpleNamespace(
        responses=SimpleNamespace(create=fake_responses_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
    )
    monkeypatch.setattr(llm_providers, "get_async_client", lambda _p: fake_client)

    req_ctx = SimpleNamespace(user_id="user-compact-1", project_id="proj-compact-1")
    history = LoopHistory(
        items=[{"role": "user", "content": "继续写下一章"}, {"role": "assistant", "content": "好的。"}],
        last_prompt_tokens=320_000,
        last_message_id="msg-50",
        author_messages=["继续写下一章"],
    )

    outcome = await compaction.compact_history(
        history,
        system_prompt="静态系统提示",
        session_id="sess-stream-compact",
        request_context=req_ctx,  # type: ignore[arg-type]
    )

    assert outcome.record is not None
    assert outcome.record["summary"] == "## 交接摘要\n- 已通过流式 Responses 完成压缩"
    assert len(calls) == 1
    assert calls[0]["stream"] is True
    assert calls[0]["prompt_cache_key"] == "zs:sess-stream-compact"
    assert len(recorded) == 1
    assert recorded[0]["attribution"].user_id == "user-compact-1"
    assert recorded[0]["attribution"].project_id == "proj-compact-1"
    assert recorded[0]["model"] == "MiniMax-M3.1-Flash"
    assert recorded[0]["usage"] is terminal_response.usage
    assert recorded[0]["timing"].outcome == "success"
    llm_failover.reset_failover_state()


@pytest.mark.asyncio
async def test_compaction_streaming_failover_pre_answer_vs_post_answer_and_truncated_stream(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    llm_failover.reset_failover_state()

    failed_event = SimpleNamespace(
        type="response.failed",
        response=SimpleNamespace(error={"code": "internal_error", "message": "boom"}),
    )

    async def pre_answer_failed_stream():
        yield SimpleNamespace(type="response.created")
        yield SimpleNamespace(type="response.reasoning_text.delta", delta="先梳理")
        yield failed_event

    async def post_answer_failed_stream():
        yield SimpleNamespace(type="response.created")
        yield SimpleNamespace(type="response.output_text.delta", delta="已经开始输出摘要")
        yield failed_event

    async def truncated_stream():
        yield SimpleNamespace(type="response.created")
        yield SimpleNamespace(type="response.reasoning_text.delta", delta="断流前思考")

    ds_calls: list[dict] = []

    async def ds_chat_create(**kwargs):
        ds_calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="备源兜底摘要"), finish_reason="stop")],
            usage={"prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550},
        )

    history = LoopHistory(
        items=[{"role": "user", "content": "写第2章"}, {"role": "assistant", "content": "写完了。"}],
        last_prompt_tokens=320_000,
        last_message_id="msg-60",
        author_messages=["写第2章"],
    )

    # 1) Pre-answer response.failed -> fails over to DeepSeek
    async def mm_pre(**_kwargs):
        return pre_answer_failed_stream()

    clients = {
        "minimax": SimpleNamespace(responses=SimpleNamespace(create=mm_pre)),
        "deepseek": SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=ds_chat_create))),
    }
    monkeypatch.setattr(llm_providers, "get_async_client", lambda p: clients[p.name])
    outcome1 = await compaction.compact_history(history, system_prompt="系统提示", session_id="sess-pre-fail")
    assert outcome1.record is not None
    assert outcome1.record["summary"] == "备源兜底摘要"
    assert len(ds_calls) == 1

    # 2) Post-answer response.failed -> MUST NOT fail over (no duplicate generation), skips compaction
    llm_failover.reset_failover_state()
    ds_calls.clear()

    async def mm_post(**_kwargs):
        return post_answer_failed_stream()

    clients["minimax"] = SimpleNamespace(responses=SimpleNamespace(create=mm_post))
    outcome2 = await compaction.compact_history(history, system_prompt="系统提示", session_id="sess-post-fail")
    assert outcome2.record is None
    assert outcome2.skipped == compaction.SKIPPED_PROVIDER_ERROR
    assert len(ds_calls) == 0

    # 3) Prematurely ended stream without terminal event -> fails over to DeepSeek
    llm_failover.reset_failover_state()
    ds_calls.clear()

    async def mm_trunc(**_kwargs):
        return truncated_stream()

    clients["minimax"] = SimpleNamespace(responses=SimpleNamespace(create=mm_trunc))
    outcome3 = await compaction.compact_history(history, system_prompt="系统提示", session_id="sess-trunc-fail")
    assert outcome3.record is not None
    assert outcome3.record["summary"] == "备源兜底摘要"
    assert len(ds_calls) == 1
    llm_failover.reset_failover_state()
