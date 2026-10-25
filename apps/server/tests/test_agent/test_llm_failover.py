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
