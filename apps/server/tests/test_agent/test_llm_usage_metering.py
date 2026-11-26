"""Every DeepSeek call site in the API process writes llm_usage_event rows."""

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from agent.tools.tool_context import AgentRequestContext
from models import LLMUsageEvent, User
from services.usage import llm_usage_service
from services.usage.llm_usage_service import LLMUsageAttribution, drain_pending_usage_records
from utils.request_context import bind_request_context, reset_request_context


def _user(db_session: Session, name: str) -> User:
    user = User(username=name, email=f"{name}@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _events(db_session: Session) -> list[LLMUsageEvent]:
    db_session.expire_all()
    return list(db_session.exec(select(LLMUsageEvent).order_by(LLMUsageEvent.occurred_at)).all())


@pytest.fixture
def bound_request(db_session: Session):
    user = _user(db_session, "agent_meter")
    req_ctx = AgentRequestContext(session=None, user_id=user.id, project_id="project-9", session_id="s-1")
    tokens = bind_request_context(agent_run_id="run-abc")
    yield user, req_ctx
    req_ctx.clear()
    reset_request_context(tokens)


# ---------------------------------------------------------------------------
# Agent runs: RunHooks.on_llm_end against the real openai-agents run loop
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for chunk in self._chunks:
                yield chunk

        return gen()

    async def close(self):
        return None


class _FakeStreamingCompletions:
    def __init__(self):
        self.calls = 0

    async def create(self, **_kwargs):
        from openai.types.chat import ChatCompletionChunk
        from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta
        from openai.types.completion_usage import CompletionUsage, PromptTokensDetails

        self.calls += 1
        now = int(time.time())
        common = {"id": "c1", "created": now, "model": "deepseek-flash", "object": "chat.completion.chunk"}
        return _FakeStream(
            [
                ChatCompletionChunk(
                    choices=[Choice(index=0, delta=ChoiceDelta(content="正文"), finish_reason=None)], **common
                ),
                ChatCompletionChunk(
                    choices=[Choice(index=0, delta=ChoiceDelta(), finish_reason="stop")],
                    usage=CompletionUsage(
                        prompt_tokens=1000,
                        completion_tokens=70,
                        total_tokens=1070,
                        prompt_tokens_details=PromptTokensDetails(cached_tokens=800),
                    ),
                    **common,
                ),
            ]
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sdk_run_writes_one_row_per_model_call(db_session: Session, bound_request):
    from agents import Agent, OpenAIChatCompletionsModel, RunConfig, Runner

    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.usage_hooks import build_agent_run_hooks

    user, req_ctx = bound_request
    meter = AgentRunMeter()
    completions = _FakeStreamingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions), base_url="http://fake")
    agent = Agent(
        name="writer",
        instructions="x",
        model=OpenAIChatCompletionsModel(model="deepseek-flash", openai_client=client),
    )
    result = Runner.run_streamed(
        agent,
        input="写一章",
        context=req_ctx,
        hooks=build_agent_run_hooks("deepseek-flash", run_meter=meter, request_context=req_ctx),
        run_config=RunConfig(tracing_disabled=True),
    )
    async for _event in result.stream_events():
        pass
    await drain_pending_usage_records()

    [event] = _events(db_session)
    assert completions.calls == 1
    assert event.user_id == user.id
    assert (event.source, event.project_id, event.correlation_id) == ("agent", "project-9", "run-abc")
    assert (event.cache_hit_tokens, event.cache_miss_tokens, event.output_tokens) == (800, 200, 70)

    timing = meter.timing_report()["stage_timings"]["model"]
    assert timing["count"] == 1
    assert timing["total_ms"] >= 0
    assert timing["interrupted_count"] == 0


@pytest.mark.unit
def test_hooks_bind_attribution_from_request_context(bound_request):
    from agent.openai_agents.usage_hooks import build_agent_run_hooks

    user, req_ctx = bound_request
    hooks = build_agent_run_hooks("deepseek-flash", request_context=req_ctx)
    assert hooks._attribution == LLMUsageAttribution(
        user_id=user.id, source="agent", project_id="project-9", correlation_id="run-abc"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runner_passes_metering_hooks_to_sdk(bound_request):
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    user, req_ctx = bound_request

    class FakeResult:
        raw_responses = []

        def cancel(self, mode="immediate"):
            del mode

        async def stream_events(self):
            if False:  # pragma: no cover - empty async generator
                yield None

    state = {"user_message": "写一章", "messages": [], "system_prompt": "base", "request_context": req_ctx}
    with (
        patch("agent.openai_agents.runner._build_agent", return_value=object()),
        patch("agents.Runner.run_streamed", return_value=FakeResult()) as mock_run,
    ):
        async for _event in run_openai_agents_streaming_agent(
            state=state, agent_type="writer", system_prompt="system", request_context=req_ctx
        ):
            pass

    hooks = mock_run.call_args.kwargs["hooks"]
    assert mock_run.call_args.kwargs["context"] is req_ctx
    assert hooks._attribution.user_id == user.id
    assert hooks._attribution.correlation_id == "run-abc"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def _chat_response(content: str, usage):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=usage,
    )


# ---------------------------------------------------------------------------
# LLMClient.acomplete (suggest + natural polish)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_acomplete_meters_only_with_attribution(db_session: Session, monkeypatch):
    from agent.core.llm_client import LLMClient

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    user = _user(db_session, "polish_meter")
    usage = SimpleNamespace(
        prompt_tokens=120,
        completion_tokens=30,
        total_tokens=150,
        prompt_tokens_details=SimpleNamespace(cached_tokens=100),
    )
    create = AsyncMock(return_value=_chat_response("done", usage))
    client = LLMClient()
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr("agent.core.llm_providers.get_async_client", lambda _provider: fake_client)

    assert await client.acomplete([{"role": "user", "content": "hi"}]) == "done"
    await drain_pending_usage_records()
    assert _events(db_session) == []

    attribution = LLMUsageAttribution(user_id=user.id, source="polish", project_id="p-2")
    assert await client.acomplete([{"role": "user", "content": "hi"}], usage_attribution=attribution) == "done"
    await drain_pending_usage_records()
    [event] = _events(db_session)
    assert (event.source, event.user_id, event.project_id) == ("polish", user.id, "p-2")
    assert (event.cache_hit_tokens, event.cache_miss_tokens, event.output_tokens) == (100, 20, 30)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_acomplete_returns_content_when_metering_fails(db_session: Session, monkeypatch):
    from agent.core.llm_client import LLMClient

    def broken_factory():
        raise RuntimeError("ledger down")

    monkeypatch.setattr(llm_usage_service, "_session_factory", broken_factory)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13)
    client = LLMClient()
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=_chat_response("ok", usage))))
    )
    monkeypatch.setattr("agent.core.llm_providers.get_async_client", lambda _provider: fake_client)

    attribution = LLMUsageAttribution(user_id="u-1", source="suggest")
    assert await client.acomplete([{"role": "user", "content": "hi"}], usage_attribution=attribution) == "ok"
    await drain_pending_usage_records()


def _blocking_recorder(monkeypatch) -> tuple[threading.Event, threading.Event]:
    """Replace the ledger write with one that blocks until released."""
    started = threading.Event()
    release = threading.Event()

    def slow_record(*_args, **_kwargs):
        started.set()
        release.wait(timeout=10)
        return True

    monkeypatch.setattr(llm_usage_service, "record_llm_usage", slow_record)
    return started, release


@pytest.mark.unit
@pytest.mark.asyncio
async def test_slow_ledger_write_does_not_delay_acomplete(monkeypatch):
    """Suggest wraps acomplete in asyncio.wait_for; metering must not spend that deadline."""
    from agent.core.llm_client import LLMClient

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    started, release = _blocking_recorder(monkeypatch)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13)
    client = LLMClient()
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=_chat_response("ok", usage))))
    )
    monkeypatch.setattr("agent.core.llm_providers.get_async_client", lambda _provider: fake_client)
    attribution = LLMUsageAttribution(user_id="u-1", source="suggest")
    try:
        # The recorder is still blocked, yet the call returns well inside a tight deadline.
        result = await asyncio.wait_for(
            client.acomplete([{"role": "user", "content": "hi"}], usage_attribution=attribution),
            timeout=1.0,
        )
        assert result == "ok"
        assert await asyncio.to_thread(started.wait, 5)
        assert any(not task.done() for task in llm_usage_service._pending_records)
    finally:
        release.set()
        await drain_pending_usage_records()
    assert not llm_usage_service._pending_records
