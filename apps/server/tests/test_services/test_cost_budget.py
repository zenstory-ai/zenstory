"""Durable reservations prove the cap before provider calls, including races."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from core.error_handler import APIException
from models import LLMUsageEvent, SubscriptionPlan, User, UserSubscription
from models.ai_cost_budget import AICostDailyBudget, AICostReservation
from services.usage import cost_budget as budget
from services.usage.llm_usage_service import LLMUsageAttribution

NOW = datetime(2026, 10, 7, 2, tzinfo=UTC)  # Holiday, 10:00 Beijing.


def user(session):
    item = User(username="budget_user", email="budget_user@example.com", hashed_password="x")
    session.add(item)
    session.commit()
    return item


def payload(maximum=1000):
    return {"model": "deepseek-flash", "messages": [{"role": "user", "content": "写作"}], "max_tokens": maximum}


def test_actual_holiday_usage_settles_once_and_seed_is_not_readded(db_session):
    account = user(db_session)
    request = payload()
    request["messages"][0]["content"] = "x" * 1_001_000
    first = budget.reserve_model_call(request, user_id=account.id, now=NOW)
    budget.settle_model_call(
        first,
        {
            "prompt_tokens": 1_000_100,
            "prompt_cache_hit_tokens": 1_000_000,
            "prompt_cache_miss_tokens": 100,
            "completion_tokens": 100,
        },
    )
    budget.settle_model_call(first, {"completion_tokens": 999999})
    db_session.expire_all()
    bucket = db_session.exec(select(AICostDailyBudget)).one()
    assert bucket.charged_units == 2_050_000  # ¥0.0205, holiday off-peak.
    db_session.add(
        LLMUsageEvent(
            user_id=account.id,
            source="agent",
            model="deepseek-flash",
            occurred_at=NOW.replace(tzinfo=None),
            price_band="peak",
            pricing_version="deepseek-flash-2026-10",
            output_tokens=100,
        )
    )
    db_session.commit()
    second = budget.reserve_model_call(payload(), user_id=account.id, now=NOW)
    budget.settle_model_call(second, {"prompt_tokens": 0, "completion_tokens": 100})
    db_session.expire_all()
    assert db_session.exec(select(AICostDailyBudget)).one().charged_units == 2_090_000


def test_historical_ledger_seed_reprices_legacy_holiday_and_blocks(db_session):
    account = user(db_session)
    db_session.add(
        LLMUsageEvent(
            user_id=account.id,
            source="agent",
            model="deepseek-flash",
            occurred_at=NOW.replace(tzinfo=None),
            price_band="peak",
            pricing_version="deepseek-flash-2026-10",
            output_tokens=3_750_000,
        )
    )
    db_session.commit()
    with pytest.raises(APIException) as error:
        budget.reserve_model_call(payload(), user_id=account.id, now=NOW)
    assert error.value.error_code == budget.COST_LIMIT_CODE
    assert error.value.status_code == 402
    assert db_session.exec(select(AICostDailyBudget)).one().charged_units == budget.FREE_DAILY_BUDGET_UNITS


def test_many_independent_sessions_cannot_over_reserve(db_session):
    account = user(db_session)
    uid = account.id

    def reserve(_):
        try:
            return budget.reserve_model_call(payload(375_000), user_id=uid, now=NOW)
        except APIException as error:
            assert error.error_code == budget.COST_LIMIT_CODE
            return None

    with ThreadPoolExecutor(max_workers=12) as executor:
        calls = list(executor.map(reserve, range(20)))
    db_session.expire_all()
    assert sum(call is not None for call in calls) == 4
    assert db_session.exec(select(AICostDailyBudget)).one().charged_units <= budget.FREE_DAILY_BUDGET_UNITS
    assert len(db_session.exec(select(AICostReservation)).all()) == 4


def test_unknown_usage_retains_reservation_and_midnight_uses_start_day(db_session):
    account = user(db_session)
    before_midnight = datetime(2026, 10, 7, 15, 59, 59, tzinfo=UTC)
    call = budget.reserve_model_call(payload(125_000), user_id=account.id, now=before_midnight)
    budget.settle_model_call(call, None)
    pending = db_session.exec(select(AICostDailyBudget)).one().charged_units
    assert pending > 100_000_000
    budget.settle_model_call(call, {})
    budget.settle_model_call(call, {"completion_tokens": 0})
    db_session.expire_all()
    assert db_session.exec(select(AICostDailyBudget)).one().charged_units == pending
    next_call = budget.reserve_model_call(payload(), user_id=account.id, now=before_midnight + timedelta(seconds=2))
    budget.settle_model_call(call, {"prompt_tokens": 0, "completion_tokens": 100})
    budget.settle_model_call(next_call, {"prompt_tokens": 0, "completion_tokens": 100})
    db_session.expire_all()
    rows = db_session.exec(select(AICostDailyBudget).order_by(AICostDailyBudget.day)).all()
    assert [str(row.day) for row in rows] == ["2026-10-07", "2026-10-08"]
    assert rows[0].charged_units == 40_000


def test_valid_paid_bypasses_cap_expired_paid_does_not(db_session):
    account = user(db_session)
    plan = SubscriptionPlan(name="pro", display_name="Pro")
    db_session.add(plan)
    db_session.commit()
    sub = UserSubscription(
        user_id=account.id, plan_id=plan.id, current_period_start=NOW, current_period_end=NOW + timedelta(days=1)
    )
    db_session.add(sub)
    db_session.commit()
    assert budget.reserve_model_call(payload(3_000_000), user_id=account.id, now=NOW) is None
    assert not db_session.exec(select(AICostDailyBudget)).all()
    sub.current_period_end = NOW - timedelta(seconds=1)
    db_session.add(sub)
    db_session.commit()
    with pytest.raises(APIException) as error:
        budget.reserve_model_call(payload(3_000_000), user_id=account.id, now=NOW)
    assert error.value.error_code == budget.COST_LIMIT_CODE


def test_unknown_model_and_database_failure_fail_closed(db_session, monkeypatch):
    account = user(db_session)
    unknown = payload()
    unknown["model"] = "unpriced-model"
    with pytest.raises(APIException) as error:
        budget.reserve_model_call(unknown, user_id=account.id, now=NOW)
    assert error.value.error_code == budget.UNAVAILABLE_CODE
    monkeypatch.setattr(budget, "_session_factory", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(APIException) as error:
        budget.reserve_model_call(payload(), user_id=account.id, now=NOW)
    assert error.value.error_code == budget.UNAVAILABLE_CODE


def test_minimax_usage_is_priced_by_its_own_table_live_and_from_history(db_session):
    """MiniMax M3.1-Flash: ¥0.1 cache hit / ¥1 miss / ¥4 output per 1M, same price at peak."""
    weekday_peak = datetime(2026, 10, 13, 2, tzinfo=UTC)  # Tuesday 10:00 Beijing.
    account = user(db_session)
    db_session.add(
        LLMUsageEvent(
            user_id=account.id,
            source="agent",
            model="MiniMax-M3.1-Flash",
            occurred_at=weekday_peak.replace(tzinfo=None),
            price_band="peak",
            pricing_version="minimax-m3.1-flash-2026-10",
            output_tokens=1_000_000,
        )
    )
    db_session.commit()
    request = payload()
    request["model"] = "MiniMax-M3.1-Flash"

    call = budget.reserve_model_call(request, user_id=account.id, now=weekday_peak)
    budget.settle_model_call(
        call,
        {"prompt_tokens": 1_000_100, "prompt_tokens_details": {"cached_tokens": 1_000_000}, "completion_tokens": 100},
    )

    db_session.expire_all()
    # History ¥4 + this call ¥0.1 + ¥0.0001 + ¥0.0004 (DeepSeek peak would be ¥8.041).
    assert db_session.exec(select(AICostDailyBudget)).one().charged_units == 410_050_000


def test_cache_split_cannot_discount_unaccounted_prompt_tokens(db_session):
    account = user(db_session)
    call = budget.reserve_model_call(payload(), user_id=account.id, now=NOW)
    budget.settle_model_call(
        call,
        {"prompt_tokens": 100, "prompt_cache_hit_tokens": 50, "prompt_cache_miss_tokens": 0, "completion_tokens": 0},
    )
    db_session.expire_all()
    assert db_session.exec(select(AICostDailyBudget)).one().charged_units == 5100


def test_unknown_historical_model_does_not_apply_flash_prices(db_session):
    account = user(db_session)
    db_session.add(
        LLMUsageEvent(
            user_id=account.id,
            source="agent",
            model="unknown-legacy-model",
            occurred_at=NOW.replace(tzinfo=None),
            price_band="peak",
            pricing_version="unknown",
            output_tokens=100,
        )
    )
    db_session.commit()
    with pytest.raises(APIException) as error:
        budget.reserve_model_call(payload(), user_id=account.id, now=NOW)
    assert error.value.error_code == budget.UNAVAILABLE_CODE
    assert not db_session.exec(select(AICostReservation)).all()


def test_sync_stream_usage_settles_before_next_model_call(db_session):
    from unittest.mock import Mock

    from services.usage.model_call_guard import install_sync_cost_guard

    account = user(db_session)
    create = Mock(return_value=iter([SimpleNamespace(usage={"prompt_tokens": 0, "completion_tokens": 100})]))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    install_sync_cost_guard(client)
    with budget.budget_attribution(LLMUsageAttribution(user_id=account.id, source="material")):
        list(client.chat.completions.create(**payload(), stream=True))
    db_session.expire_all()
    assert db_session.exec(select(AICostReservation)).one().settled_units is not None
    assert db_session.exec(select(AICostDailyBudget)).one().charged_units == budget.cost_units(
        budget.price_band(db_session.exec(select(AICostReservation)).one().started_at), 0, 0, 100
    )


@pytest.mark.asyncio
async def test_real_transport_guard_stops_next_call_without_waiting_for_ledger(db_session):
    from services.usage.model_call_guard import install_async_cost_guard

    account = user(db_session)
    create = AsyncMock(return_value=SimpleNamespace(usage={"prompt_tokens": 0, "completion_tokens": 1_500_000}))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    install_async_cost_guard(client)
    with budget.budget_attribution(LLMUsageAttribution(user_id=account.id, source="suggest")):
        await client.chat.completions.create(**payload(1_500_000))
        with pytest.raises(APIException) as error:
            await client.chat.completions.create(**payload(1_200_000))
    assert error.value.error_code == budget.COST_LIMIT_CODE
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_cancellation_keeps_inflight_reservation(db_session):
    import asyncio

    from services.usage.model_call_guard import install_async_cost_guard

    account = user(db_session)
    started = asyncio.Event()

    async def provider(**_kwargs):
        started.set()
        await asyncio.Event().wait()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=provider)))
    install_async_cost_guard(client)
    with budget.budget_attribution(LLMUsageAttribution(user_id=account.id, source="polish")):
        task = asyncio.create_task(client.chat.completions.create(**payload()))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    db_session.expire_all()
    assert db_session.exec(select(AICostDailyBudget)).one().charged_units > 0
    assert db_session.exec(select(AICostReservation)).one().settled_units is None


@pytest.mark.asyncio
async def test_real_async_sdk_stream_settles_terminal_usage_and_preserves_start(db_session, monkeypatch):
    import json

    import httpx
    from agents import ModelSettings, OpenAIChatCompletionsModel
    from agents.models._retry_runtime import provider_managed_retries_disabled
    from agents.models.interface import ModelTracing
    from openai import AsyncOpenAI

    from services.usage.llm_usage_service import record_llm_usage
    from services.usage.model_call_guard import install_async_cost_guard

    account = user(db_session)
    monkeypatch.setattr(budget, "utcnow", lambda: NOW)
    calls = []
    usage = {
        "prompt_tokens": 100,
        "prompt_cache_hit_tokens": 50,
        "prompt_cache_miss_tokens": 50,
        "completion_tokens": 10,
        "total_tokens": 110,
    }
    base = {"id": "chatcmpl-budget-test", "object": "chat.completion.chunk", "created": 1, "model": "deepseek-flash"}
    chunks = [
        dict(base, choices=[{"index": 0, "delta": {"role": "assistant", "content": "正文"}, "finish_reason": None}]),
        dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}]),
        dict(base, choices=[], usage=usage),
        dict(base, choices=[], usage=usage),
    ]

    async def handle(request):
        db_session.expire_all()
        assert db_session.exec(select(AICostReservation)).one().settled_units is None
        calls.append(json.loads(request.content))
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
        client = install_async_cost_guard(
            AsyncOpenAI(api_key="test-key", base_url="https://mock.invalid", http_client=http_client, max_retries=2)
        )
        model = OpenAIChatCompletionsModel(model="deepseek-flash", openai_client=client)
        attribution = LLMUsageAttribution(user_id=account.id, source="agent")

        async def stream():
            return [
                event
                async for event in model.stream_response(
                    system_instructions="写作",
                    input="继续",
                    model_settings=ModelSettings(max_tokens=1000),
                    tools=[],
                    output_schema=None,
                    handoffs=[],
                    tracing=ModelTracing.DISABLED,
                )
            ]

        # Exercise the SDK's with_options client clone as well as its stream parser.
        with provider_managed_retries_disabled(True), budget.budget_attribution(attribution):
            events = await stream()
            assert any(event.type == "response.completed" for event in events)
            assert budget.call_started_at(account.id) == NOW.replace(tzinfo=None)
            monkeypatch.setattr(budget, "utcnow", lambda: NOW + timedelta(hours=2))
            assert record_llm_usage(attribution, model="deepseek-flash", usage=usage)
            db_session.expire_all()
            assert db_session.exec(select(LLMUsageEvent)).one().occurred_at == NOW.replace(tzinfo=None)
            reservation = db_session.exec(select(AICostReservation)).one()
            bucket = db_session.exec(select(AICostDailyBudget)).one()
            assert reservation.reserved_units > reservation.settled_units == bucket.charged_units == 9100
            assert len(calls) == 1
            bucket.charged_units = budget.FREE_DAILY_BUDGET_UNITS
            db_session.add(bucket)
            db_session.commit()
            with pytest.raises(APIException) as error:
                await stream()
            assert error.value.error_code == budget.COST_LIMIT_CODE
            assert len(calls) == 1


@pytest.mark.asyncio
async def test_sdk_model_call_is_guarded_before_transport(db_session):
    from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, RunConfig, Runner

    from agent.tools.tool_context import AgentRequestContext
    from services.usage.model_call_guard import install_async_cost_guard

    account = user(db_session)
    db_session.add(
        AICostDailyBudget(
            user_id=account.id,
            day=budget.beijing_today(budget.utcnow()),
            charged_units=budget.FREE_DAILY_BUDGET_UNITS,
        )
    )
    db_session.commit()
    create = AsyncMock()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), base_url="http://fake")
    install_async_cost_guard(client)
    req_ctx = AgentRequestContext(session=None, user_id=account.id, project_id=None, session_id=None)
    agent = Agent(
        name="writer",
        instructions="写作",
        model=OpenAIChatCompletionsModel(model="deepseek-flash", openai_client=client),
        model_settings=ModelSettings(max_tokens=1000),
    )
    with req_ctx.bind_cost_budget_user():
        result = Runner.run_streamed(
            agent, input="继续写作", context=req_ctx, run_config=RunConfig(tracing_disabled=True)
        )
        with pytest.raises(APIException) as error:
            async for _ in result.stream_events():
                pass
    assert error.value.error_code == budget.COST_LIMIT_CODE
    create.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_responses_stream_settles_conservatively_and_releases_unused_output_headroom(
    db_session, monkeypatch
):
    """Early stream abort (e.g. MiniMax leaked-tool-call early stop) must never leave
    ai_cost_reservation.settled_units IS NULL while keeping conservative input + output bounds."""
    from services.usage.model_call_guard import drain_pending_cost_settlements, install_async_cost_guard

    account = user(db_session)
    monkeypatch.setattr(budget, "utcnow", lambda: NOW)

    reasoning_delta = "先思考一下剧情衔接"
    leaked_delta = "<minimax:tool_call><invoke name=\"write_chapter\">"
    closed_upstream = False

    class _FakeAsyncResponsesStream:
        def __aiter__(self):
            async def _gen():
                # Non-terminal event carrying zero-output usage (88396620 must still ignore it!)
                yield SimpleNamespace(
                    type="response.created",
                    response=SimpleNamespace(usage={"input_tokens": 100, "output_tokens": 0}),
                )
                yield SimpleNamespace(type="response.reasoning_text.delta", delta=reasoning_delta)
                yield SimpleNamespace(type="response.output_text.delta", delta=leaked_delta)
                yield SimpleNamespace(type="response.output_text.delta", delta="never reached")

            return _gen()

        async def close(self):
            nonlocal closed_upstream
            closed_upstream = True

    responses_create = AsyncMock(return_value=_FakeAsyncResponsesStream())
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
        responses=SimpleNamespace(create=responses_create),
    )
    install_async_cost_guard(client)

    req = {
        "model": "MiniMax-M3.1-Flash",
        "instructions": "系统提示",
        "input": [{"role": "user", "content": "写第一章" * 200}],
        "max_output_tokens": 65_536,
        "stream": True,
    }
    expected_input_upper = budget._input_upper_bound(req)
    streamed_bytes = len(reasoning_delta.encode("utf-8")) + len(leaked_delta.encode("utf-8"))

    with budget.budget_attribution(LLMUsageAttribution(user_id=account.id, source="agent")):
        stream = await client.responses.create(**req)
        async with stream:
            async for chunk in stream:
                # Non-terminal response.created must NEVER prematurely settle mid-stream (88396620).
                db_session.expire_all()
                assert db_session.exec(select(AICostReservation)).one().settled_units is None
                if getattr(chunk, "delta", "") == leaked_delta:
                    # Simulate runner leaked-tool-call early stop breaking out before response.completed.
                    break
        await drain_pending_cost_settlements()

    assert closed_upstream is True
    db_session.expire_all()
    reservation = db_session.exec(select(AICostReservation)).one()
    daily_bucket = db_session.exec(select(AICostDailyBudget)).one()

    expected_output = streamed_bytes + budget.CANCELLED_STREAM_OUTPUT_BUFFER_TOKENS
    expected_settled = budget.cost_units(
        budget.price_band(NOW),
        0,
        expected_input_upper,
        expected_output,
        pricing_version="minimax-m3.1-flash-2026-10",
    )
    assert reservation.settled_units is not None
    assert reservation.settled_units == expected_settled
    assert reservation.settled_units < reservation.reserved_units
    assert daily_bucket.charged_units == expected_settled


@pytest.mark.asyncio
async def test_cancelled_chat_stream_uses_actual_price_band_and_completed_stream_keeps_exact_usage(
    db_session, monkeypatch
):
    """Cancelled off-peak DeepSeek stream settles at off-peak band; completed stream ignores close fallback."""
    from services.usage.model_call_guard import drain_pending_cost_settlements, install_async_cost_guard

    account = user(db_session)
    monkeypatch.setattr(budget, "utcnow", lambda: NOW)  # Holiday off-peak

    # 1. Cancelled Chat Completions stream without terminal usage chunk
    async def cancelled_chat_gen():
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content="部分正文",
                        reasoning_content="一点思考",
                        refusal=None,
                        tool_calls=None,
                    )
                )
            ],
            usage=None,
        )
        yield SimpleNamespace(choices=[], usage=None)

    # 2. Completed Chat Completions stream with exact usage chunk
    async def completed_chat_gen():
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="完整正文"))],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[],
            usage={
                "prompt_tokens": 500,
                "prompt_cache_hit_tokens": 400,
                "prompt_cache_miss_tokens": 100,
                "completion_tokens": 20,
            },
        )

    streams = [cancelled_chat_gen(), completed_chat_gen()]
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=lambda **_kw: streams.pop(0)))),
    )
    install_async_cost_guard(client)

    req = {**payload(32_768), "stream": True}
    expected_input_upper = budget._input_upper_bound(req)
    with budget.budget_attribution(LLMUsageAttribution(user_id=account.id, source="agent")):
        # Call 1: break early before any usage chunk arrives
        s1 = await client.chat.completions.create(**req)
        async for _chunk in s1:
            break
        await s1.close()
        await drain_pending_cost_settlements()

        # Call 2: consume to completion -> exact usage wins and close() does not overwrite
        s2 = await client.chat.completions.create(**req)
        async with s2:
            _ = [chunk async for chunk in s2]
        await drain_pending_cost_settlements()

    db_session.expire_all()
    rows = db_session.exec(select(AICostReservation).order_by(AICostReservation.started_at, AICostReservation.id)).all()
    assert len(rows) == 2
    assert all(row.settled_units is not None for row in rows)

    streamed_bytes = len("部分正文".encode()) + len("一点思考".encode())
    expected_cancelled = budget.cost_units(
        "offpeak",
        0,
        expected_input_upper,
        streamed_bytes + budget.CANCELLED_STREAM_OUTPUT_BUFFER_TOKENS,
    )
    expected_completed = budget.cost_units("offpeak", 400, 100, 20)
    settled_values = sorted(row.settled_units for row in rows)
    assert settled_values == sorted([expected_cancelled, expected_completed])
    assert db_session.exec(select(AICostDailyBudget)).one().charged_units == (
        expected_cancelled + expected_completed
    )


def test_settle_cancelled_stream_never_relaxes_material_trial_guard(db_session):
    """Defense in depth: free material trial reservations cannot be reduced by cancelled-stream settlement."""
    from models.subscription import UsageQuota

    account = user(db_session)
    quota = UsageQuota(
        user_id=account.id,
        period_start=NOW.replace(tzinfo=None),
        period_end=(NOW + timedelta(days=30)).replace(tzinfo=None),
        material_trial_used_at=NOW.replace(tzinfo=None),
        material_trial_charged_units=0,
    )
    db_session.add(quota)
    db_session.commit()

    with budget.material_trial_call(output_limit=400):
        res = budget.reserve_model_call(payload(400), user_id=account.id, now=NOW)
    assert res is not None
    db_session.expire_all()
    before_trial_units = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == account.id)).one().material_trial_charged_units
    assert before_trial_units > 0

    budget.settle_cancelled_stream_call(res, streamed_output_bytes=0)
    db_session.expire_all()
    row = db_session.exec(select(AICostReservation).where(AICostReservation.id == res.id)).one()
    after_trial_units = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == account.id)).one().material_trial_charged_units
    assert row.settled_units is None
    assert after_trial_units == before_trial_units

