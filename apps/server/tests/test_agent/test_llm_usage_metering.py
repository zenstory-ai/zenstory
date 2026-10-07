"""Every DeepSeek call site in the API process writes llm_usage_event rows."""

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from agent.tools.mcp_tools import ToolContext
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
    ToolContext.set_context(session=None, user_id=user.id, project_id="project-9", session_id="s-1")
    tokens = bind_request_context(agent_run_id="run-abc")
    yield user
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

    from agent.openai_agents.usage_hooks import build_usage_metering_hooks

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
        hooks=build_usage_metering_hooks("deepseek-flash"),
        run_config=RunConfig(tracing_disabled=True),
    )
    async for _event in result.stream_events():
        pass
    await drain_pending_usage_records()

    [event] = _events(db_session)
    assert completions.calls == 1
    assert event.user_id == bound_request.id
    assert (event.source, event.project_id, event.correlation_id) == ("agent", "project-9", "run-abc")
    assert (event.cache_hit_tokens, event.cache_miss_tokens, event.output_tokens) == (800, 200, 70)


@pytest.mark.unit
def test_hooks_bind_attribution_from_request_context(bound_request):
    from agent.openai_agents.usage_hooks import build_usage_metering_hooks

    hooks = build_usage_metering_hooks("deepseek-flash")
    assert hooks.attribution == LLMUsageAttribution(
        user_id=bound_request.id, source="agent", project_id="project-9", correlation_id="run-abc"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runner_passes_metering_hooks_to_sdk(bound_request):
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    class FakeResult:
        raw_responses = []

        def cancel(self, mode="immediate"):
            del mode

        async def stream_events(self):
            if False:  # pragma: no cover - empty async generator
                yield None

    state = {"user_message": "写一章", "messages": [], "system_prompt": "base"}
    with (
        patch("agent.openai_agents.runner._build_agent", return_value=object()),
        patch("agents.Runner.run_streamed", return_value=FakeResult()) as mock_run,
    ):
        async for _event in run_openai_agents_streaming_agent(
            state=state, agent_type="writer", system_prompt="system"
        ):
            pass

    hooks = mock_run.call_args.kwargs["hooks"]
    assert hooks.attribution.user_id == bound_request.id
    assert hooks.attribution.correlation_id == "run-abc"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def _chat_response(content: str, usage):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=usage,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_router_call_is_metered(db_session: Session, bound_request):
    from agent.graph import router

    usage = SimpleNamespace(
        prompt_tokens=300,
        completion_tokens=40,
        total_tokens=340,
        prompt_cache_hit_tokens=256,
        prompt_cache_miss_tokens=44,
        prompt_tokens_details=SimpleNamespace(cached_tokens=256),
    )
    create = AsyncMock(return_value=_chat_response('{"agent_type":"writer","workflow_type":"quick"}', usage))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch("agent.graph.router.get_deepseek_client", return_value=client):
        response = await router._route_with_deepseek_chat("写一章")
    await drain_pending_usage_records()

    assert response["usage"]["cache_read_tokens"] == 256
    [event] = _events(db_session)
    assert (event.source, event.user_id, event.correlation_id) == ("router", bound_request.id, "run-abc")
    assert (event.cache_hit_tokens, event.cache_miss_tokens, event.output_tokens) == (256, 44, 40)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_router_fallback_keeps_usage_of_unparseable_answer():
    from agent.graph import router

    routed = {"content": [{"type": "text", "text": "???"}], "usage": {"input_tokens": 5, "output_tokens": 2}}
    with (
        patch("agent.graph.router._route_with_deepseek_chat", AsyncMock(return_value=routed)),
        patch("agent.graph.router._parse_router_response", side_effect=ValueError("bad schema")),
    ):
        result = await router.router_node({"user_message": "写一章"})

    assert result["current_agent"] == "writer"
    assert result["routing_metadata"]["reason"] == "router_fallback"
    assert result["routing_usage"] == {"input_tokens": 5, "output_tokens": 2}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_router_fallback_without_response_has_no_usage():
    from agent.graph import router

    with patch("agent.graph.router._route_with_deepseek_chat", AsyncMock(side_effect=RuntimeError("down"))):
        result = await router.router_node({"user_message": "写一章"})

    assert "routing_usage" not in result


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
    client._async_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

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
    client._async_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=_chat_response("ok", usage))))
    )

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
    client._async_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=_chat_response("ok", usage))))
    )
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_slow_ledger_write_does_not_delay_routing(monkeypatch, bound_request):
    from agent.graph import router

    started, release = _blocking_recorder(monkeypatch)
    usage = SimpleNamespace(prompt_tokens=30, completion_tokens=4, total_tokens=34)
    create = AsyncMock(return_value=_chat_response('{"agent_type":"writer"}', usage))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    try:
        with patch("agent.graph.router.get_deepseek_client", return_value=client):
            response = await asyncio.wait_for(router._route_with_deepseek_chat("写一章"), timeout=1.0)
        assert response["content"][0]["text"] == '{"agent_type":"writer"}'
        assert await asyncio.to_thread(started.wait, 5)
    finally:
        release.set()
        await drain_pending_usage_records()
