"""Primary/backup failover: which provider serves a call, and when it switches."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest

from agent.core import llm_failover, llm_providers
from agent.openai_agents import model as sdk_model
from core.error_handler import APIException


def _status_error(cls, status_code: int):
    response = httpx.Response(status_code, request=httpx.Request("POST", "https://llm.test"))
    return cls(f"status {status_code}", response=response, body=None)


def _ok(content: str = "ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=None,
    )


class _Provider:
    """A fake provider endpoint: answers from a script and records requests."""

    def __init__(self) -> None:
        self.script: list[object] = []
        self.calls: list[dict] = []

        async def create(**kwargs):
            self.calls.append(kwargs)
            outcome = self.script.pop(0) if self.script else _ok()
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.fixture
def endpoints(monkeypatch):
    """MiniMax primary, DeepSeek backup, in-memory failover state."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek")
    for name in ("LLM_PRIMARY_MODEL", "LLM_BACKUP_MODEL", "MINIMAX_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    fakes = {"minimax": _Provider(), "deepseek": _Provider()}
    monkeypatch.setattr(llm_providers, "get_async_client", lambda provider: fakes[provider.name].client)
    llm_failover.reset_failover_state()
    yield fakes
    llm_failover.reset_failover_state()


async def _call(session_id: str | None = "s-1", **kwargs):
    return await llm_failover.acreate_chat_completion(
        call_site="test",
        session_id=session_id,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
        **kwargs,
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        _status_error(openai.InternalServerError, 503),
        _status_error(openai.RateLimitError, 429),  # e.g. a subscription window used up
        _status_error(openai.AuthenticationError, 401),
        openai.APIConnectionError(request=httpx.Request("POST", "https://llm.test")),
        openai.APITimeoutError(request=httpx.Request("POST", "https://llm.test")),
    ],
)
async def test_provider_failure_before_output_is_served_by_backup(endpoints, error):
    endpoints["minimax"].script = [error]

    response, provider = await _call()

    assert response.choices[0].message.content == "ok"
    assert provider.name == "deepseek"
    assert endpoints["deepseek"].calls[0]["model"] == "deepseek-flash"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        _status_error(openai.BadRequestError, 400),  # context too long, malformed request
        APIException(error_code="ERR_QUOTA_AI_DAILY_COST_EXCEEDED", status_code=402),
        APIException(error_code="ERR_AI_COST_BUDGET_UNAVAILABLE", status_code=503),
    ],
)
async def test_request_and_budget_errors_do_not_fail_over(endpoints, error):
    endpoints["minimax"].script = [error]

    with pytest.raises(type(error)):
        await _call()

    assert endpoints["deepseek"].calls == []
    # Nothing was held against the primary: the session's next call still goes there.
    _, provider = await _call()
    assert provider.name == "minimax"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failover_pins_the_session_not_other_sessions(endpoints):
    endpoints["minimax"].script = [_status_error(openai.InternalServerError, 502)]
    await _call("pinned")

    _, same_session = await _call("pinned")
    _, other_session = await _call("other")

    assert same_session.name == "deepseek"
    assert other_session.name == "minimax"
    assert len(endpoints["minimax"].calls) == 2  # the failure + the other session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repeated_primary_failures_send_new_sessions_to_backup_first(endpoints, monkeypatch):
    monkeypatch.setattr(llm_failover, "LLM_CIRCUIT_FAILURE_THRESHOLD", 2)
    down = _status_error(openai.InternalServerError, 503)
    endpoints["minimax"].script = [down, down]
    await _call("a")
    await _call("b")

    _, provider = await _call("fresh-session")

    assert provider.name == "deepseek"
    assert len(endpoints["minimax"].calls) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_thinking_off_calls_use_a_provider_that_can_turn_it_off(endpoints):
    _, provider = await _call(thinking_enabled=False)

    assert provider.name == "deepseek"
    assert endpoints["deepseek"].calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert endpoints["minimax"].calls == []

    # When that provider is down, MiniMax answers without the field it rejects (HTTP 400),
    # at its lowest effort and with room for the reasoning it always does.
    endpoints["deepseek"].script = [_status_error(openai.InternalServerError, 503)]
    _, fallback = await _call("s-2", thinking_enabled=False)
    minimax_call = endpoints["minimax"].calls[0]
    assert fallback.name == "minimax"
    assert "extra_body" not in minimax_call
    assert minimax_call["reasoning_effort"] == "low"
    assert minimax_call["max_tokens"] == 100 + llm_failover.THINKING_HEADROOM_TOKENS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_minimax_gets_configured_effort_and_model_override(endpoints, monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "MiniMax-M3.1-Flash-Preview")
    monkeypatch.setenv("MINIMAX_REASONING_EFFORT", "medium")

    _, provider = await _call()

    call = endpoints["minimax"].calls[0]
    assert (provider.model, call["model"], call["reasoning_effort"]) == (
        "MiniMax-M3.1-Flash-Preview",
        "MiniMax-M3.1-Flash-Preview",
        "medium",
    )


@pytest.mark.unit
def test_backup_without_key_is_skipped_and_primary_without_key_fails(monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    assert [p.name for p in llm_providers.configured_providers()] == ["minimax"]

    monkeypatch.delenv("MINIMAX_API_KEY")
    with pytest.raises(ValueError, match="MINIMAX_API_KEY"):
        llm_providers.configured_providers()


# --------------------------------------------------------------- agent SDK model


class _StreamingModel:
    def __init__(self, events: list[object], error: BaseException | None = None):
        self._events = events
        self._error = error
        self.calls = 0

    async def stream_response(self, *_args, **_kwargs):
        self.calls += 1
        for event in self._events:
            yield event
        if self._error is not None:
            raise self._error


async def _drain(model) -> list[object]:
    settings = SimpleNamespace(reasoning=None)
    return [event async for event in model.stream_response("sys", [], settings, [], None, [], None)]


@pytest.fixture
def sdk_models(endpoints, monkeypatch):
    models: dict[str, _StreamingModel] = {}
    monkeypatch.setattr(sdk_model, "_sdk_model_for", lambda provider: models[provider.name])
    monkeypatch.setattr(sdk_model, "_settings_for", lambda _provider, settings: settings)
    return models


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_stream_fails_over_only_before_output(sdk_models):
    down = _status_error(openai.InternalServerError, 503)
    sdk_models["minimax"] = _StreamingModel([], error=down)
    sdk_models["deepseek"] = _StreamingModel(["d1", "d2"])
    model = sdk_model.build_failover_chat_model("before-output")

    assert await _drain(model) == ["d1", "d2"]
    assert model.served_model == "deepseek-flash"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_stream_failure_after_output_raises_and_pins_the_retry(sdk_models):
    sdk_models["minimax"] = _StreamingModel(["m1"], error=openai.APIConnectionError(
        request=httpx.Request("POST", "https://llm.test")
    ))
    sdk_models["deepseek"] = _StreamingModel(["d1"])
    model = sdk_model.build_failover_chat_model("mid-stream")

    with pytest.raises(openai.APIConnectionError):
        await _drain(model)
    assert sdk_models["deepseek"].calls == 0  # output had started: nothing generated twice

    # The author's retry in the same chat window goes to the backup.
    assert await _drain(sdk_model.build_failover_chat_model("mid-stream")) == ["d1"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, {"minimax": 120.0, "deepseek": 45.0}),
        ({"MINIMAX_CLIENT_TIMEOUT_S": "90", "DEEPSEEK_CLIENT_TIMEOUT_S": "30"}, {"minimax": 90.0, "deepseek": 30.0}),
    ],
)
def test_each_provider_gets_its_own_read_timeout(monkeypatch, env, expected):
    """Production (2026-10-21/22): 14 runs failed when the primary took over 45s to send its first byte
    on 150k+ token inputs; the backup answers fast and keeps the short timeout."""
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek")
    for name in ("MINIMAX_CLIENT_TIMEOUT_S", "DEEPSEEK_CLIENT_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    llm_providers.reset_provider_client_cache()

    timeouts = {p.name: llm_providers.get_async_client(p).timeout.read for p in llm_providers.configured_providers()}
    sync_timeouts = {p.name: llm_providers.get_sync_client(p).timeout for p in llm_providers.configured_providers()}

    llm_providers.reset_provider_client_cache()
    assert timeouts == expected
    assert sync_timeouts == expected


# --------------------------------------------------------------- per-attempt model metrics


def _event(event_type: str):
    return SimpleNamespace(type=event_type)


class _ClockedStreamingModel:
    """Streams (seconds offset, event type) steps against a fake monotonic clock."""

    def __init__(self, clock: list[float], steps: list[tuple[float, str]], error: BaseException | None = None):
        self._clock = clock
        self._steps = steps
        self._error = error

    async def stream_response(self, *_args, **_kwargs):
        for at, event_type in self._steps:
            self._clock[0] = at
            yield _event(event_type)
        if self._error is not None:
            raise self._error


@pytest.fixture
def attempts(monkeypatch):
    """Ledger rows the model would schedule, as (model, outcome, duration_ms, ttft_ms, tokens)."""
    from services.usage import llm_usage_service

    rows: list[tuple] = []

    def schedule(attribution, *, model, usage=None, tokens=None, timing=None):
        counted = tokens if tokens is not None else llm_usage_service.extract_usage_tokens(usage)
        rows.append((model, timing.outcome, timing.duration_ms, timing.ttft_ms, counted.total))

    monkeypatch.setattr(llm_usage_service, "schedule_llm_usage_record", schedule)
    return rows


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_token_is_the_first_output_delta_not_response_created(sdk_models, monkeypatch, attempts):
    from services.usage.llm_usage_service import LLMUsageAttribution

    clock = [100.0]
    monkeypatch.setattr(sdk_model.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr("services.usage.llm_usage_service.time.monotonic", lambda: clock[0])
    sdk_models["minimax"] = _ClockedStreamingModel(clock, [
        (101.0, "response.created"),  # first chunk of any kind (role only)
        (104.0, "response.output_item.added"),
        (106.5, "response.function_call_arguments.delta"),  # first generated output: tool arguments
        (110.0, "response.output_text.delta"),
        (120.0, "response.completed"),
    ])
    model = sdk_model.build_failover_chat_model(
        "ttft", usage_attribution=LLMUsageAttribution(user_id="u", source="agent")
    )
    await _drain(model)
    clock[0] = 130.0  # the SDK consumes the completed event later; the call already ended

    timing = model.take_call_timing()
    assert (timing.outcome, timing.duration_ms, timing.ttft_ms) == ("success", 20_000, 6_500)
    assert model.take_call_timing() is None  # taken once by the usage hook
    assert attempts == []  # the answered attempt's row is written by the usage hook, with its tokens


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failover_records_the_failed_attempt_under_its_own_model(sdk_models, monkeypatch, attempts):
    from services.usage.llm_usage_service import LLMUsageAttribution

    clock = [0.0]
    monkeypatch.setattr(sdk_model.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr("services.usage.llm_usage_service.time.monotonic", lambda: clock[0])
    down = openai.APITimeoutError(request=httpx.Request("POST", "https://llm.test"))
    sdk_models["minimax"] = _ClockedStreamingModel(clock, [], error=down)
    sdk_models["deepseek"] = _ClockedStreamingModel(clock, [(2.0, "response.reasoning_text.delta"), (5.0, "response.completed")])
    model = sdk_model.build_failover_chat_model(
        "failover", usage_attribution=LLMUsageAttribution(user_id="u", source="agent")
    )
    await _drain(model)

    # One logical call, two attempts: the timed-out primary and the backup that answered.
    assert attempts == [("MiniMax-M3.1-Flash", "timeout", 0, None, 0)]
    timing = model.take_call_timing()
    assert (timing.outcome, timing.ttft_ms) == ("success", 2_000)
    assert model.served_model == "deepseek-flash"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mid_stream_error_and_cancellation_are_recorded_without_tokens(sdk_models, attempts):
    import asyncio

    from services.usage.llm_usage_service import LLMUsageAttribution

    attribution = LLMUsageAttribution(user_id="u", source="agent")
    sdk_models["minimax"] = _StreamingModel(
        [_event("response.created"), _event("response.output_text.delta")],
        error=openai.APIConnectionError(request=httpx.Request("POST", "https://llm.test")),
    )
    with pytest.raises(openai.APIConnectionError):
        await _drain(sdk_model.build_failover_chat_model("mid", usage_attribution=attribution))
    assert [(row[1], row[3] is not None, row[4]) for row in attempts] == [("error", True, 0)]

    attempts.clear()
    sdk_models["deepseek"] = _StreamingModel([_event("response.created")], error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await _drain(sdk_model.build_failover_chat_model("mid", usage_attribution=attribution))
    assert [(row[0], row[1], row[4]) for row in attempts] == [("deepseek-flash", "cancelled", 0)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_single_shot_calls_record_every_attempt(endpoints, attempts):
    from services.usage.llm_usage_service import LLMUsageAttribution

    answered = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    endpoints["minimax"].script = [_status_error(openai.InternalServerError, 503)]
    endpoints["deepseek"].script = [answered]
    await _call(usage_attribution=LLMUsageAttribution(user_id="u", source="suggest"))
    assert [(row[0], row[1], row[3], row[4]) for row in attempts] == [
        ("MiniMax-M3.1-Flash", "error", None, 0),
        ("deepseek-flash", "success", None, 15),  # non-streaming: no first-token time
    ]

    # No attribution (e.g. a call that is not billed to anyone): nothing is written.
    attempts.clear()
    await _call(session_id="s-2")
    assert attempts == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_stream_closed_after_it_completed_is_not_also_a_cancelled_attempt(sdk_models, attempts):
    from services.usage.llm_usage_service import LLMUsageAttribution

    sdk_models["minimax"] = _StreamingModel(
        [_event("response.output_text.delta"), _event("response.completed"), _event("response.output_item.done")]
    )
    model = sdk_model.build_failover_chat_model("closed", usage_attribution=LLMUsageAttribution(user_id="u", source="agent"))
    stream = model.stream_response("sys", [], SimpleNamespace(reasoning=None), [], None, [], None)
    async for event in stream:
        if event.type == "response.completed":
            break
    await stream.aclose()  # GeneratorExit after the provider finished

    assert attempts == []  # only the usage hook writes this call, as a success
    assert model.take_call_timing().outcome == "success"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_our_own_budget_refusal_is_not_a_provider_attempt(sdk_models, endpoints, attempts):
    from services.usage.llm_usage_service import LLMUsageAttribution

    attribution = LLMUsageAttribution(user_id="u", source="agent")
    refusal = APIException(error_code="ERR_QUOTA_AI_DAILY_COST_EXCEEDED", status_code=402)
    sdk_models["minimax"] = _StreamingModel([], error=refusal)
    with pytest.raises(APIException):
        await _drain(sdk_model.build_failover_chat_model("budget", usage_attribution=attribution))

    endpoints["minimax"].script = [refusal]
    with pytest.raises(APIException):
        await _call(session_id="budget-2", usage_attribution=LLMUsageAttribution(user_id="u", source="router"))
    assert attempts == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_read_timeout_in_the_middle_of_a_stream_is_a_timeout(sdk_models, attempts):
    from services.usage.llm_usage_service import LLMUsageAttribution

    # The OpenAI client wraps timeouts only while sending; mid-stream it surfaces httpx's own error.
    sdk_models["minimax"] = _StreamingModel(
        [_event("response.reasoning_text.delta")], error=httpx.ReadTimeout("read timed out")
    )
    with pytest.raises(httpx.ReadTimeout):
        await _drain(sdk_model.build_failover_chat_model("slow", usage_attribution=LLMUsageAttribution(user_id="u", source="agent")))
    assert [row[1] for row in attempts] == ["timeout"]


# --------------------------------------------------------------- redacted failure diagnostics


def _private_status_error(status=422, body=None):
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://llm.test/PRIVATE_URL"),
        headers={"x-request-id": "PRIVATE_PROVIDER_ID"},
    )
    return openai.APIStatusError("PRIVATE_EXCEPTION_TEXT", response=response, body=body)


def _diagnostics(caplog):
    return [r for r in caplog.records if r.getMessage() == "LLM provider attempt failed"]


def _assert_private_failure(record):
    assert not record.exc_info
    assert not record.exc_text
    assert "PRIVATE_" not in repr((record.getMessage(), record.custom_fields))


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["deepseek", "minimax"])
async def test_422_is_observed_without_retry_or_raw_data(endpoints, monkeypatch, caplog, attempts, provider):
    from services.usage.llm_usage_service import LLMUsageAttribution
    from utils.request_context import bind_request_context, reset_request_context

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", provider)
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "minimax" if provider == "deepseek" else "deepseek")
    error = _private_status_error(body={
        "base_resp": {"status_code": 2013, "status_msg": "PRIVATE_BODY"},
        "message": "PRIVATE_MESSAGE",
        "param": "messages",
    })
    endpoints[provider].script = [error]
    tokens = bind_request_context(request_id="synthetic-request", agent_run_id="synthetic-run")
    try:
        with pytest.raises(openai.APIStatusError) as raised:
            await llm_failover.acreate_chat_completion(
                call_site="router", messages=[{"role": "user", "content": "PRIVATE_PROMPT"}],
                usage_attribution=LLMUsageAttribution(user_id="PRIVATE_ACCOUNT", source="router"),
            )
    finally:
        reset_request_context(tokens)

    assert raised.value is error
    assert sum(len(ep.calls) for ep in endpoints.values()) == 1
    assert len(attempts) == 1 and attempts[0][1:] == ("error", attempts[0][2], None, 0)
    records = _diagnostics(caplog)
    assert len(records) == 1
    record = records[0]
    _assert_private_failure(record)
    assert record.custom_fields == {
        "request_id": "synthetic-request", "agent_run_id": "synthetic-run",
        "provider": provider, "model": "deepseek-flash" if provider == "deepseek" else "MiniMax-M3.1-Flash",
        "slot": "primary", "call_site": "router", "phase": "request",
        "status_code": 422, "failure_category": "request_rejected",
        "provider_error_format": "base_resp",
        "provider_reason": "invalid_parameters",
        "provider_error_code": 2013 if provider == "minimax" else None,
        "failed_field": "messages",
    }


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    None, "PRIVATE_RAW_BODY", ["PRIVATE_LIST"],
    {"base_resp": {"status_code": True, "status_msg": "PRIVATE_BODY"}},
    {"base_resp": {"status_code": "2013", "status_msg": "PRIVATE_BODY"}},
    {"base_resp": {"status_code": 987654321, "status_msg": "PRIVATE_BODY"}},
    {"error": {"code": "PRIVATE_CODE", "type": "PRIVATE_TYPE", "param": "PRIVATE_PARAM"}},
    {"base_resp": "PRIVATE_BAD_SHAPE", "param": ["PRIVATE_PARAM"]},
])
async def test_unknown_minimax_errors_remain_unknown_and_private(endpoints, caplog, body):
    error = _private_status_error(body=body)
    endpoints["minimax"].script = [error]
    with pytest.raises(openai.APIStatusError):
        await _call()
    record, = _diagnostics(caplog)
    _assert_private_failure(record)
    assert record.custom_fields["provider_reason"] == "unknown"
    assert record.custom_fields["provider_error_code"] is None
    assert record.custom_fields["failed_field"] is None
    assert record.custom_fields["call_site"] == "other"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "status", "reason"), [
    ("deepseek", 400, "invalid_format"), ("deepseek", 401, "authentication_failed"),
    ("deepseek", 402, "insufficient_balance"), ("deepseek", 429, "rate_limited"),
    ("deepseek", 500, "server_error"), ("deepseek", 503, "server_overloaded"),
    ("deepseek", 418, "unknown"), ("minimax", 422, "unknown"),
])
async def test_provider_http_reasons_are_not_guessed_from_messages(
    endpoints, monkeypatch, caplog, provider, status, reason,
):
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", provider)
    monkeypatch.delenv("LLM_BACKUP_PROVIDER")
    endpoints[provider].script = [_private_status_error(status, {
        "code": "PRIVATE_CODE", "type": "PRIVATE_TYPE", "param": "PRIVATE_PARAM",
        "message": "PRIVATE_context length quota invalid request",
    })]
    with pytest.raises(openai.APIStatusError):
        await _call()
    record, = _diagnostics(caplog)
    _assert_private_failure(record)
    assert record.custom_fields["provider_reason"] == reason
    assert record.custom_fields["failed_field"] is None


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize("provider", ["deepseek", "minimax"])
async def test_sdk_stream_failure_is_observed_before_and_after_output(
    sdk_models, endpoints, monkeypatch, caplog, attempts, started, provider,
):
    from services.usage.llm_usage_service import LLMUsageAttribution

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", provider)
    monkeypatch.delenv("LLM_BACKUP_PROVIDER")
    error = httpx.ReadTimeout("PRIVATE_TIMEOUT")
    sdk_models[provider] = _StreamingModel([_event("response.output_text.delta")] if started else [], error)
    with pytest.raises(httpx.ReadTimeout) as raised:
        await _drain(sdk_model.build_failover_chat_model(
            "PRIVATE_SESSION", usage_attribution=LLMUsageAttribution(user_id="PRIVATE_ACCOUNT", source="agent"),
        ))
    assert raised.value is error
    record, = _diagnostics(caplog)
    _assert_private_failure(record)
    assert record.custom_fields["phase"] == ("stream" if started else "stream_start")
    assert record.custom_fields["failure_category"] == "timeout"
    assert record.custom_fields["provider"] == provider
    assert [r[1] for r in attempts] == ["timeout"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sdk_nonstream_failure_has_diagnostics(sdk_models, caplog):
    error = _private_status_error(body={"base_resp": {"status_code": 1042}})

    async def get_response(*_args, **_kwargs):
        raise error

    sdk_models["minimax"] = SimpleNamespace(get_response=get_response)
    with pytest.raises(openai.APIStatusError):
        await sdk_model.build_failover_chat_model("synthetic").get_response("s", [], SimpleNamespace(reasoning=None))
    record, = _diagnostics(caplog)
    _assert_private_failure(record)
    assert record.custom_fields["phase"] == "request"
    assert record.custom_fields["provider_reason"] == "invalid_characters"


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["deepseek", "minimax"])
def test_sync_material_failure_has_the_same_safe_diagnostics(endpoints, monkeypatch, caplog, provider):
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", provider)
    monkeypatch.delenv("LLM_BACKUP_PROVIDER")
    error = _private_status_error(body={"base_resp": {"status_code": 2013, "status_msg": "PRIVATE_BODY"}})
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        raise error

    monkeypatch.setattr(llm_providers, "get_sync_client", lambda *_a, **_kw: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    ))
    with pytest.raises(openai.APIStatusError) as raised:
        llm_failover.create_chat_completion(call_site="material", messages=[])
    assert raised.value is error and len(calls) == 1
    record, = _diagnostics(caplog)
    _assert_private_failure(record)
    assert record.custom_fields["provider"] == provider
    assert record.custom_fields["call_site"] == "material"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_each_failed_provider_has_one_diagnostic_even_when_backup_succeeds(endpoints, caplog):
    endpoints["minimax"].script = [_private_status_error(503, {"base_resp": {"status_code": 2064}})]
    _, served = await _call()
    assert served.name == "deepseek"
    record, = _diagnostics(caplog)
    assert record.custom_fields["provider_reason"] == "server_overloaded"
    assert record.custom_fields["provider_error_code"] == 2064
    _assert_private_failure(record)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_polish_failure_uses_its_existing_source_label(endpoints, caplog):
    endpoints["minimax"].script = [_private_status_error()]
    with pytest.raises(openai.APIStatusError):
        await llm_failover.acreate_chat_completion(call_site="polish", messages=[])
    record, = _diagnostics(caplog)
    assert record.custom_fields["call_site"] == "polish"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_httpx2_midstream_timeout_is_classified_without_reading_its_text(sdk_models, caplog):
    import httpx2

    sdk_models["minimax"] = _StreamingModel([_event("response.output_text.delta")], httpx2.ReadTimeout("PRIVATE_TIMEOUT"))
    with pytest.raises(httpx2.ReadTimeout):
        await _drain(sdk_model.build_failover_chat_model("synthetic"))
    record, = _diagnostics(caplog)
    _assert_private_failure(record)
    assert record.custom_fields["failure_category"] == "timeout"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_exception_is_never_stringified(endpoints, caplog):
    class PrivateError(Exception):
        def __str__(self):
            raise AssertionError("must not read exception text")

    error = PrivateError()
    endpoints["minimax"].script = [error]
    with pytest.raises(PrivateError) as raised:
        await _call()
    assert raised.value is error
    record, = _diagnostics(caplog)
    _assert_private_failure(record)
    assert record.custom_fields["failure_category"] == "unknown"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_success_cancellation_and_our_budget_refusal_are_not_provider_errors(endpoints, caplog):
    import asyncio

    await _call()
    for error in [asyncio.CancelledError(), APIException(error_code="ERR_AI_COST_BUDGET_UNAVAILABLE", status_code=503)]:
        endpoints["minimax"].script = [error]
        with pytest.raises(type(error)):
            await _call()
    assert _diagnostics(caplog) == []


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [413, 422, 503])
async def test_diagnostics_do_not_execute_exception_properties_or_change_failover(endpoints, caplog, status):
    class OriginalProviderError(Exception):
        @property
        def body(self):
            raise RuntimeError("PRIVATE_ACCESSOR")

        @property
        def param(self):
            raise RuntimeError("PRIVATE_ACCESSOR")

    original = OriginalProviderError()
    original.status_code = status
    endpoints["minimax"].script = [original]
    if status == 503:
        _, provider = await _call()
        assert provider.name == "deepseek"
        assert len(endpoints["deepseek"].calls) == 1
    else:
        with pytest.raises(OriginalProviderError) as raised:
            await _call()
        assert raised.value is original
        assert endpoints["deepseek"].calls == []
    record, = _diagnostics(caplog)
    _assert_private_failure(record)
