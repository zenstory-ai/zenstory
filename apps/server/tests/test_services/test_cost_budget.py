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
async def test_router_propagates_budget_error_instead_of_falling_back(monkeypatch):
    from agent.graph import router

    monkeypatch.setattr(
        router,
        "_route_with_deepseek_chat",
        AsyncMock(side_effect=APIException(error_code=budget.COST_LIMIT_CODE, status_code=402)),
    )
    with pytest.raises(APIException) as error:
        await router.router_node({"user_message": "继续写作"})
    assert error.value.error_code == budget.COST_LIMIT_CODE


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

    from agent.tools.mcp_tools import ToolContext
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
    ToolContext.set_context(session=None, user_id=account.id, project_id=None, session_id=None)
    agent = Agent(
        name="writer",
        instructions="写作",
        model=OpenAIChatCompletionsModel(model="deepseek-flash", openai_client=client),
        model_settings=ModelSettings(max_tokens=1000),
    )
    result = Runner.run_streamed(agent, input="继续写作", run_config=RunConfig(tracing_disabled=True))
    with pytest.raises(APIException) as error:
        async for _ in result.stream_events():
            pass
    assert error.value.error_code == budget.COST_LIMIT_CODE
    create.assert_not_called()
