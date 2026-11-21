"""Tests for short-TTL Redis active-session wire-prefix cache and prompt-cache prefix stability."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import openai
import pytest
from sqlmodel import Session

from agent.core import llm_failover, llm_providers
from agent.loop import compaction, wire_prefix_cache
from agent.loop.history import LoopHistory, estimate_provider_items_tokens
from agent.openai_agents import model as sdk_model
from models import ChatMessage, ChatSession, File, Project, User
from services.core.auth_service import hash_password


class _FakeRedis:
    """Minimal in-memory Redis stand-in implementing get/set/setex/expire/delete."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int]] = {}
        self.deleted_keys: list[str] = []

    def get(self, key: str) -> str | None:
        entry = self.store.get(key)
        return entry[0] if entry is not None else None

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = (value, ex if ex is not None else 0)
        return True

    def expire(self, key: str, ttl: int) -> bool:
        entry = self.store.get(key)
        if entry is None:
            return False
        self.store[key] = (entry[0], ttl)
        return True

    def setex(self, key: str, ttl: int, value: str) -> bool:
        self.store[key] = (value, ttl)
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            self.deleted_keys.append(key)
            if self.store.pop(key, None) is not None:
                removed += 1
        return removed


@pytest.fixture
def fake_redis(monkeypatch):
    redis_client = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake-wire-prefix-cache/0")
    monkeypatch.setattr(wire_prefix_cache, "get_redis_client", lambda: redis_client)
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: redis_client)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    yield redis_client
    wire_prefix_cache.reset_wire_prefix_cache_state()


def _sample_wire_items() -> list[dict]:
    return [
        {"role": "user", "content": "<turn_context>焦点文件：第一章</turn_context>\n\n写第一章"},
        {
            "id": "__fake_id__",
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "先建第一章再写正文"}],
            "provider_data": {"model": "MiniMax-M3.1-Flash", "response_id": "c1"},
        },
        {
            "id": "__fake_id__",
            "type": "function_call",
            "call_id": "call_provider_001",
            "name": "write_chapter",
            "arguments": '{"chapter": 1, "title": "第一章"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_provider_001",
            "output": '{"status": "success", "data": {"id": "ch1", "awaiting_body": true}}',
        },
        {
            "id": "__fake_id__",
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "写出完整第一章正文"}],
            "provider_data": {"model": "MiniMax-M3.1-Flash", "response_id": "c2"},
        },
        {
            "id": "__fake_id__",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "<file>第一章正文内容在这里。</file>", "annotations": []}],
        },
    ]


# ---------------------------------------------------------------------------
# Unit tests: envelope validation, TTL/size caps, fail-open, privacy
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_store_and_load_round_trip_and_strict_envelope_validation(fake_redis):
    items = _sample_wire_items()
    stored = wire_prefix_cache.store_wire_prefix(
        session_id="sess-1",
        project_id="proj-1",
        user_id="user-1",
        last_assistant_message_id="msg-10",
        system_prompt="static system prompt",
        provider_key="minimax:MiniMax-M3.1-Flash:primary",
        items=items,
        ttl_seconds=900,
    )
    assert stored is True
    raw, ttl = fake_redis.store["agent:wire_prefix:sess-1"]
    assert ttl == 900
    envelope = json.loads(raw)
    assert envelope["schema_version"] == wire_prefix_cache.WIRE_PREFIX_CACHE_SCHEMA_VERSION
    assert envelope["last_assistant_message_id"] == "msg-10"

    loaded = wire_prefix_cache.load_wire_prefix(
        session_id="sess-1",
        project_id="proj-1",
        user_id="user-1",
        last_message_id="msg-10",
        system_prompt="static system prompt",
        provider_key="minimax:MiniMax-M3.1-Flash:primary",
    )
    assert loaded == items

    # Any validation mismatch falls back cleanly to None.
    common = {
        "session_id": "sess-1",
        "project_id": "proj-1",
        "user_id": "user-1",
        "last_message_id": "msg-10",
        "system_prompt": "static system prompt",
        "provider_key": "minimax:MiniMax-M3.1-Flash:primary",
    }
    assert wire_prefix_cache.load_wire_prefix(**{**common, "last_message_id": "msg-11"}) is None
    assert wire_prefix_cache.load_wire_prefix(**{**common, "system_prompt": "changed system prompt"}) is None
    assert wire_prefix_cache.load_wire_prefix(**{**common, "provider_key": "deepseek:deepseek-flash:primary"}) is None
    assert wire_prefix_cache.load_wire_prefix(**{**common, "provider_key": "minimax:MiniMax-M3.1-Flash:secondary"}) is None
    assert wire_prefix_cache.load_wire_prefix(**{**common, "user_id": "other-user"}) is None
    assert wire_prefix_cache.load_wire_prefix(**{**common, "project_id": "other-proj"}) is None


@pytest.mark.unit
def test_disabled_ttl_and_missing_redis_url_and_schema_mismatch(fake_redis, monkeypatch):
    items = _sample_wire_items()
    assert (
        wire_prefix_cache.store_wire_prefix(
            session_id="sess-disabled",
            project_id="p",
            user_id="u",
            last_assistant_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
            items=items,
            ttl_seconds=0,
        )
        is False
    )
    assert "agent:wire_prefix:sess-disabled" not in fake_redis.store

    # Corrupted / wrong schema_version entries fail open.
    fake_redis.setex(
        "agent:wire_prefix:sess-bad",
        900,
        json.dumps({"schema_version": 999, "project_id": "p", "user_id": "u", "last_assistant_message_id": "m1", "items": items}),
    )
    assert (
        wire_prefix_cache.load_wire_prefix(
            session_id="sess-bad",
            project_id="p",
            user_id="u",
            last_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
        )
        is None
    )

    fake_redis.setex("agent:wire_prefix:sess-corrupt", 900, "{not valid json")
    assert (
        wire_prefix_cache.load_wire_prefix(
            session_id="sess-corrupt",
            project_id="p",
            user_id="u",
            last_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
        )
        is None
    )

    monkeypatch.delenv("REDIS_URL", raising=False)
    assert (
        wire_prefix_cache.store_wire_prefix(
            session_id="sess-no-url",
            project_id="p",
            user_id="u",
            last_assistant_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
            items=items,
        )
        is False
    )


@pytest.mark.unit
def test_oversized_payload_skips_caching_and_evicts_stale_entry(fake_redis):
    items = _sample_wire_items()
    fake_redis.setex("agent:wire_prefix:sess-big", 900, '{"stale": true}')
    stored = wire_prefix_cache.store_wire_prefix(
        session_id="sess-big",
        project_id="p",
        user_id="u",
        last_assistant_message_id="m1",
        system_prompt="sys",
        provider_key="minimax:MiniMax-M3.1-Flash:primary",
        items=items,
        max_bytes=100,
    )
    assert stored is False
    assert "agent:wire_prefix:sess-big" not in fake_redis.store


@pytest.mark.unit
def test_redis_exception_fails_open_without_raising(monkeypatch):
    class _BrokenRedis:
        def get(self, _key):
            raise ConnectionError("redis down")

        def setex(self, _key, _ttl, _value):
            raise ConnectionError("redis down")

        def delete(self, *_keys):
            raise ConnectionError("redis down")

    monkeypatch.setenv("REDIS_URL", "redis://broken/0")
    monkeypatch.setattr(wire_prefix_cache, "get_redis_client", lambda: _BrokenRedis())
    wire_prefix_cache.reset_wire_prefix_cache_state()

    assert (
        wire_prefix_cache.store_wire_prefix(
            session_id="sess-err",
            project_id="p",
            user_id="u",
            last_assistant_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
            items=_sample_wire_items(),
        )
        is False
    )
    wire_prefix_cache.reset_wire_prefix_cache_state()
    assert (
        wire_prefix_cache.load_wire_prefix(
            session_id="sess-err",
            project_id="p",
            user_id="u",
            last_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
        )
        is None
    )
    wire_prefix_cache.reset_wire_prefix_cache_state()


@pytest.mark.unit
def test_wire_prefix_cache_never_logs_raw_items(fake_redis, caplog):
    secret_prose = "绝密小说正文_SECRET_CONTENT_DO_NOT_LOG_987654"
    items = [{"role": "user", "content": secret_prose}]
    with caplog.at_level(logging.DEBUG):
        wire_prefix_cache.store_wire_prefix(
            session_id="sess-priv",
            project_id="p",
            user_id="u",
            last_assistant_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
            items=items,
        )
        wire_prefix_cache.load_wire_prefix(
            session_id="sess-priv",
            project_id="p",
            user_id="u",
            last_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
        )
    for record in caplog.records:
        assert secret_prose not in record.getMessage()
        assert secret_prose not in str(getattr(record, "__dict__", ""))


# ---------------------------------------------------------------------------
# Compaction + token estimation on wire-prefix cached items
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compaction_and_token_estimation_handle_sdk_wire_prefix_items():
    items = _sample_wire_items()
    # Token estimation must count structured SDK message content lists and reasoning summaries.
    plain_tokens = estimate_provider_items_tokens([{"role": "user", "content": "写第一章"}])
    wire_tokens = estimate_provider_items_tokens(items)
    assert wire_tokens > plain_tokens + 20

    history = LoopHistory(
        items=items,
        last_prompt_tokens=320_000,
        last_message_id="msg-10",
        author_messages=["写第一章"],
    )
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="交接摘要正文"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=320_000, completion_tokens=80, total_tokens=320_080, prompt_tokens_details=None),
    )
    create = AsyncMock(return_value=(completion, SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")))
    with patch("agent.core.llm_failover.acreate_chat_completion", create):
        outcome = await compaction.compact_history(history, system_prompt="系统提示", session_id="sess-1")

    assert outcome.record is not None
    assert outcome.record["summary"] == "交接摘要正文"
    kwargs = create.await_args.kwargs
    assert "tool_choice" not in kwargs
    assert kwargs["tools"]
    sent_messages = kwargs["messages"]
    assert sent_messages[0] == {"role": "system", "content": "系统提示"}
    # Exact <turn_context>, provider call_id, raw tool output, <file> body, and reasoning_content preserved.
    assert "<turn_context>焦点文件：第一章</turn_context>" in sent_messages[1]["content"]
    assert sent_messages[2]["tool_calls"][0]["id"] == "call_provider_001"
    assert sent_messages[2]["reasoning_content"] == "先建第一章再写正文"
    assert sent_messages[3]["role"] == "tool" and sent_messages[3]["tool_call_id"] == "call_provider_001"
    assert sent_messages[4]["content"] == "<file>第一章正文内容在这里。</file>"
    assert sent_messages[4]["reasoning_content"] == "写出完整第一章正文"


# ---------------------------------------------------------------------------
# End-to-end two-turn exact wire-prefix round-trip through real AgentService + SDK
# ---------------------------------------------------------------------------


def _stream_chunk(*, role: str | None = None, content: str | None = None, reasoning: str | None = None, tool_calls=None, finish_reason: str | None = None):
    from openai.types.chat import ChatCompletionChunk

    delta: dict = {}
    if role is not None:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return ChatCompletionChunk.model_validate(
        {
            "id": "chunk-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "MiniMax-M3.1-Flash",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
    )


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs")
async def test_turn_n_plus_1_exact_wire_prefix_round_trip_through_agent_service(
    db_session: Session, fake_redis, monkeypatch
):
    """Turn 1's exact completed_items reload identically on Turn 2 and form an exact prefix of
    Turn 2's outgoing provider messages (preserving <turn_context>, call_id, tool outputs,
    reasoning_content, and assistant text)."""
    from agent.service import AgentService

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "MiniMax-M3.1-Flash")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test-minimax")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.delenv("MINIMAX_SECONDARY_API_KEY", raising=False)
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()

    user = User(
        email="wire_prefix_e2e@example.com",
        username="wireprefixe2e",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="前缀测试", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chapter = File(project_id=project.id, title="第一章", file_type="draft", content="雾夜降临。")
    db_session.add(chapter)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    recorded_calls: list[dict] = []
    scripted_streams = [
        # Turn 1, step 1: reasoning + query_files tool call
        [
            _stream_chunk(role="assistant", reasoning="先查一下第一章"),
            _stream_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_live_999",
                        "type": "function",
                        "function": {"name": "query_files", "arguments": json.dumps({"id": chapter.id})},
                    }
                ]
            ),
            _stream_chunk(finish_reason="tool_calls"),
        ],
        # Turn 1, step 2: reasoning + final reply
        [
            _stream_chunk(role="assistant", reasoning="读完第一章，回复作者"),
            _stream_chunk(content="已看过第一章，可以继续。"),
            _stream_chunk(finish_reason="stop"),
        ],
        # Turn 2, step 1: direct reply
        [
            _stream_chunk(role="assistant", reasoning="承接上一轮继续"),
            _stream_chunk(content="第二轮收到。"),
            _stream_chunk(finish_reason="stop"),
        ],
    ]

    def fake_client_for(provider):
        client = openai.AsyncOpenAI(api_key=provider.api_key, base_url="https://llm.test/v1", max_retries=0)

        async def create(**kwargs):
            recorded_calls.append(kwargs)
            chunks = scripted_streams.pop(0)

            async def _gen():
                for chunk in chunks:
                    yield chunk

            return _gen()

        client.chat.completions.create = create
        return client

    monkeypatch.setattr(llm_providers, "get_async_client", fake_client_for)
    monkeypatch.setattr("agent.openai_agents.usage_hooks.schedule_llm_usage_record", lambda *_a, **_kw: None)

    service = AgentService()
    try:
        # Turn 1
        async for _frame in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="看一下第一章",
            session=db_session,
            session_id=chat.id,
            metadata={"current_file_id": chapter.id, "current_file_type": "draft", "current_file_title": "第一章"},
        ):
            pass

        assert f"agent:wire_prefix:{chat.id}" in fake_redis.store

        # Turn 2
        async for _frame in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="接着说说人物",
            session=db_session,
            session_id=chat.id,
            metadata={"current_file_id": chapter.id, "current_file_type": "draft", "current_file_title": "第一章"},
        ):
            pass
    finally:
        sdk_model.reset_sdk_model_cache()
        llm_failover.reset_failover_state()

    assert len(recorded_calls) == 3
    turn1_step2_messages = recorded_calls[1]["messages"]
    turn2_step1_messages = recorded_calls[2]["messages"]

    # Every message from Turn 1 step 2 (system prompt + <turn_context> user msg + reasoning + call_live_999 + tool output)
    # is a BYTE-FOR-BYTE EXACT PREFIX of Turn 2 step 1!
    assert turn2_step1_messages[: len(turn1_step2_messages)] == turn1_step2_messages
    # Plus Turn 1 step 2's assistant reply (with reasoning_content) sits right after it, before Turn 2's user msg!
    assert turn2_step1_messages[len(turn1_step2_messages)] == {
        "role": "assistant",
        "content": "已看过第一章，可以继续。",
        "reasoning_content": "读完第一章，回复作者",
    }
    assert turn2_step1_messages[len(turn1_step2_messages) + 1]["role"] == "user"
    assert "接着说说人物" in turn2_step1_messages[len(turn1_step2_messages) + 1]["content"]
    # Tools list is identical across all calls and tool_choice is never "none".
    assert recorded_calls[0]["tools"] == recorded_calls[1]["tools"] == recorded_calls[2]["tools"]
    assert all(call.get("tool_choice") != "none" for call in recorded_calls)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_incomplete_or_error_turn_does_not_store_wire_prefix(db_session: Session, fake_redis):
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.service import AgentService

    user = User(
        email="wire_err@example.com",
        username="wireerr",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="错误不缓存", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    def fake_error_loop(state, **_kwargs):
        state["native_run_holder"] = {"items": _sample_wire_items(), "agent_type": "loop"}
        state["run_meter"].last_served_provider_key = "minimax:MiniMax-M3.1-Flash:primary"

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "写了一半"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})
            yield StreamEvent(type=StreamEventType.ERROR, data={"error": "boom", "code": "INTERNAL_ERROR"})

        return _stream()

    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_error_loop):
        async for _frame in AgentService().process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="写第一章",
            session=db_session,
            session_id=chat.id,
        ):
            pass

    assert f"agent:wire_prefix:{chat.id}" not in fake_redis.store


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs")
async def test_dual_key_slot_switch_misses_wire_prefix_cache_and_falls_back_to_db(
    db_session: Session, fake_redis, monkeypatch
):
    """When MiniMax dual-key routing switches key_slot (e.g. key cooldown or weight change),
    Turn N+1 must miss the cached wire prefix and fall back cleanly to DB load_loop_history."""
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.service import AgentService

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "MiniMax-M3.1-Flash")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-primary-key")
    monkeypatch.setenv("MINIMAX_SECONDARY_API_KEY", "sk-secondary-key")
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "0")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()
    llm_providers.reset_key_routing_state()

    user = User(
        email="wire_dualkey@example.com",
        username="wiredualkey",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="双Key切换", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    seen_histories: list[list[dict]] = []

    def fake_loop(state, **_kwargs):
        seen_histories.append(list(state.get("history_items") or []))
        state["native_run_holder"] = {
            "agent_type": "loop",
            "items": [
                {"role": "user", "content": "<turn_context>LIVE_WIRE_MARKER</turn_context>\n\n第一轮"},
                {"role": "assistant", "content": "第一轮完成"},
            ],
        }
        state["run_meter"].last_served_provider_key = "minimax:MiniMax-M3.1-Flash:primary"

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第一轮完成"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _stream()

    service = AgentService()
    try:
        with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
            # Turn 1 stores wire prefix tagged with primary key_slot.
            async for _frame in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="第一轮",
                session=db_session,
                session_id=chat.id,
            ):
                pass
            assert f"agent:wire_prefix:{chat.id}" in fake_redis.store

            # Turn 2 on SAME key_slot (primary) -> HIT (sees LIVE_WIRE_MARKER).
            async for _frame in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="第二轮同Key",
                session=db_session,
                session_id=chat.id,
            ):
                pass
            assert any("LIVE_WIRE_MARKER" in str(item.get("content") or "") for item in seen_histories[-1])

            # Now route to secondary key (100% weight -> secondary key_slot) -> MISS, falls back to DB history!
            monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "100")
            llm_providers.reset_key_routing_state()
            async for _frame in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="第三轮切Key",
                session=db_session,
                session_id=chat.id,
            ):
                pass
            assert not any("LIVE_WIRE_MARKER" in str(item.get("content") or "") for item in seen_histories[-1])
            assert any(item == {"role": "user", "content": "第一轮"} for item in seen_histories[-1])
    finally:
        sdk_model.reset_sdk_model_cache()
        llm_failover.reset_failover_state()
        llm_providers.reset_key_routing_state()


# ---------------------------------------------------------------------------
# Invalidation hooks: clear_session, create_new_session, chat_tool_cards
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_chat_clear_and_new_session_invalidate_wire_prefix_cache(db_session: Session, fake_redis):
    from api.chat import clear_session, create_new_session

    user = User(
        email="wire_inv@example.com",
        username="wireinv",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="清空失效", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话", is_active=True)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="hi"))
    db_session.commit()

    key = wire_prefix_cache.wire_prefix_cache_key(chat.id)
    fake_redis.setex(key, 900, '{"cached": 1}')
    clear_session(project_id=project.id, session=db_session, current_user=user)
    assert key not in fake_redis.store

    fake_redis.setex(key, 900, '{"cached": 2}')
    new_sess = create_new_session(project_id=project.id, session=db_session, current_user=user)
    assert new_sess.id != chat.id
    assert key not in fake_redis.store


@pytest.mark.unit
def test_chat_tool_card_mutation_invalidates_wire_prefix_cache(db_session: Session, fake_redis):
    from datetime import UTC, datetime

    from services.chat_tool_cards import record_delete_card_restored, record_edit_card_undone

    user = User(
        email="wire_card@example.com",
        username="wirecard",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="卡片失效", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话", is_active=True)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    after_ts = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
    msg = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已修改并删除",
        tool_calls=json.dumps(
            [
                {
                    "id": "call_edit",
                    "name": "edit_file",
                    "status": "success",
                    "result": {
                        "id": "f1",
                        "undo": {
                            "before_version_number": 1,
                            "expected_after_updated_at": after_ts.isoformat(),
                        },
                    },
                },
                {
                    "id": "call_del",
                    "name": "delete_file",
                    "status": "success",
                    "result": {
                        "id": "f2",
                        "mutation_applied": True,
                        "restored": False,
                    },
                },
            ]
        ),
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    key = wire_prefix_cache.wire_prefix_cache_key(chat.id)
    fake_redis.setex(key, 900, '{"cached": 1}')
    assert (
        record_edit_card_undone(
            db_session,
            user_id=user.id,
            project_id=project.id,
            file_id="f1",
            message_id=msg.id,
            tool_use_id="call_edit",
            before_version_number=1,
            expected_after_updated_at=after_ts,
        )
        is True
    )
    assert key not in fake_redis.store

    fake_redis.setex(key, 900, '{"cached": 2}')
    assert (
        record_delete_card_restored(
            db_session,
            user_id=user.id,
            project_id=project.id,
            file_id="f2",
            message_id=msg.id,
            tool_use_id="call_del",
        )
        is True
    )
    assert key not in fake_redis.store


# ---------------------------------------------------------------------------
# Well-formedness validation, clean soft-land caching, and stale invalidation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_well_formed_wire_prefix_rejects_dangling_calls_and_malformed_tails(fake_redis):
    assert wire_prefix_cache.is_well_formed_wire_prefix(_sample_wire_items()) is True
    # Parallel tool calls answered in any order before next message are valid.
    parallel_valid = [
        {"role": "user", "content": "查两章"},
        {"type": "function_call", "call_id": "c1", "name": "query_files", "arguments": "{}"},
        {"type": "function_call", "call_id": "c2", "name": "query_files", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c2", "output": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "{}"},
        {"role": "assistant", "content": "查完了"},
    ]
    assert wire_prefix_cache.is_well_formed_wire_prefix(parallel_valid) is True

    malformed_cases = [
        [],
        None,
        ["not-a-dict"],
        # Dangling unanswered function_call at tail
        [
            {"role": "user", "content": "写"},
            {"type": "function_call", "call_id": "c1", "name": "query_files", "arguments": "{}"},
        ],
        # Unanswered function_call followed by another message
        [
            {"role": "user", "content": "写"},
            {"type": "function_call", "call_id": "c1", "name": "query_files", "arguments": "{}"},
            {"role": "assistant", "content": "断了"},
        ],
        # Orphan function_call_output
        [
            {"role": "user", "content": "写"},
            {"type": "function_call_output", "call_id": "c_missing", "output": "{}"},
        ],
        # Empty call_id
        [
            {"role": "user", "content": "写"},
            {"type": "function_call", "call_id": "", "name": "query_files", "arguments": "{}"},
        ],
        # Duplicate pending call_id
        [
            {"role": "user", "content": "写"},
            {"type": "function_call", "call_id": "dup_1", "name": "query_files", "arguments": "{}"},
            {"type": "function_call", "call_id": "dup_1", "name": "query_files", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "dup_1", "output": "{}"},
            {"role": "assistant", "content": "ok"},
        ],
        # Trailing function_call_output without concluding assistant message
        [
            {"role": "user", "content": "写"},
            {"type": "function_call", "call_id": "c_tail", "name": "query_files", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c_tail", "output": "{}"},
        ],
        # Dangling reasoning item at tail
        [
            {"role": "user", "content": "写"},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "只想到一半"}]},
        ],
    ]
    for bad_items in malformed_cases:
        assert wire_prefix_cache.is_well_formed_wire_prefix(bad_items) is False

    # store_wire_prefix rejects dangling function_call and evicts any stale entry.
    key = wire_prefix_cache.wire_prefix_cache_key("sess-bad-tail")
    fake_redis.setex(key, 900, '{"stale": true}')
    stored = wire_prefix_cache.store_wire_prefix(
        session_id="sess-bad-tail",
        project_id="p",
        user_id="u",
        last_assistant_message_id="m1",
        system_prompt="sys",
        provider_key="minimax:MiniMax-M3.1-Flash:primary",
        items=malformed_cases[3],
    )
    assert stored is False
    assert key not in fake_redis.store

    # load_wire_prefix also rejects malformed items if present in Redis.
    fake_redis.setex(
        key,
        900,
        json.dumps(
            {
                "schema_version": wire_prefix_cache.WIRE_PREFIX_CACHE_SCHEMA_VERSION,
                "project_id": "p",
                "user_id": "u",
                "last_assistant_message_id": "m1",
                "system_prompt_sha256": wire_prefix_cache.system_prompt_sha256("sys"),
                "provider_key": "minimax:MiniMax-M3.1-Flash:primary",
                "items": malformed_cases[3],
            }
        ),
    )
    assert (
        wire_prefix_cache.load_wire_prefix(
            session_id="sess-bad-tail",
            project_id="p",
            user_id="u",
            last_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs")
async def test_soft_landed_wall_clock_run_stores_wire_prefix_and_turn_n_plus_1_hits(
    db_session: Session, fake_redis, monkeypatch
):
    """A clean wall-clock soft-landed run (past_wrap_up + run_deadline_exceeded + wall_clock pause
    card) populates completed_items, stores wire_prefix_cache, and Turn N+1 hits it byte-for-byte."""
    from sqlmodel import select

    from agent.core.run_meter import AgentRunMeter
    from agent.service import AgentService

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "MiniMax-M3.1-Flash")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test-minimax")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.delenv("MINIMAX_SECONDARY_API_KEY", raising=False)
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()
    llm_providers.reset_key_routing_state()

    user = User(
        email="wire_soft_land@example.com",
        username="wiresoftland",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="软着陆缓存", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chapter = File(project_id=project.id, title="第一章", file_type="draft", content="长夜未央。")
    db_session.add(chapter)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    recorded_calls: list[dict] = []
    scripted_streams = [
        # Turn 1, step 1: tool call (before wrap-up)
        [
            _stream_chunk(role="assistant", reasoning="先查第一章"),
            _stream_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_soft_001",
                        "type": "function",
                        "function": {"name": "query_files", "arguments": json.dumps({"id": chapter.id})},
                    }
                ]
            ),
            _stream_chunk(finish_reason="tool_calls"),
        ],
        # Turn 1, step 2: wall-clock soft-landing summary
        [
            _stream_chunk(role="assistant", reasoning="时间快到了，先做阶段总结"),
            _stream_chunk(content="已核对第一章，回复「继续」后继续。"),
            _stream_chunk(finish_reason="stop"),
        ],
        # Turn 2, step 1: follow-up "继续"
        [
            _stream_chunk(role="assistant", reasoning="承接软着陆继续"),
            _stream_chunk(content="继续完成剩余工作。"),
            _stream_chunk(finish_reason="stop"),
        ],
    ]

    wrap_up_armed = {"active": False}
    monkeypatch.setattr(AgentRunMeter, "past_wrap_up", property(lambda _self: wrap_up_armed["active"]))

    def fake_client_for(provider):
        client = openai.AsyncOpenAI(api_key=provider.api_key, base_url="https://llm.test/v1", max_retries=0)

        async def create(**kwargs):
            recorded_calls.append(kwargs)
            if len(recorded_calls) == 1:
                # Trigger wall-clock wrap-up right after step 1 so step 2 soft-lands cleanly.
                wrap_up_armed["active"] = True
            chunks = scripted_streams.pop(0)

            async def _gen():
                for chunk in chunks:
                    yield chunk

            return _gen()

        client.chat.completions.create = create
        return client

    monkeypatch.setattr(llm_providers, "get_async_client", fake_client_for)
    monkeypatch.setattr("agent.openai_agents.usage_hooks.schedule_llm_usage_record", lambda *_a, **_kw: None)

    service = AgentService()
    try:
        # Turn 1: soft-lands near wall-clock limit.
        async for _frame in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="核对并扩写第一章",
            session=db_session,
            session_id=chat.id,
            metadata={"current_file_id": chapter.id, "current_file_type": "draft", "current_file_title": "第一章"},
        ):
            pass

        assert wire_prefix_cache.wire_prefix_cache_key(chat.id) in fake_redis.store
        assistant_rows = db_session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == chat.id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at)
        ).all()
        assert len(assistant_rows) == 1
        meta = json.loads(assistant_rows[0].message_metadata or "{}")
        assert meta.get("stop_reason") == "run_deadline_exceeded"
        assert meta.get("status_cards") and meta["status_cards"][0]["limit"] == "wall_clock"

        # Turn 2: follow-up hits wire_prefix_cache cleanly.
        wrap_up_armed["active"] = False
        async for _frame in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="继续",
            session=db_session,
            session_id=chat.id,
            metadata={"current_file_id": chapter.id, "current_file_type": "draft", "current_file_title": "第一章"},
        ):
            pass
    finally:
        sdk_model.reset_sdk_model_cache()
        llm_failover.reset_failover_state()
        llm_providers.reset_key_routing_state()

    assert len(recorded_calls) == 3
    # Turn 1 step 2 had the transient LOOP_TIME_LANDING_PROMPT appended at the end;
    # everything before it is an exact prefix of Turn 2 step 1!
    turn1_step2_prefix = recorded_calls[1]["messages"][:-1]
    turn2_step1_messages = recorded_calls[2]["messages"]
    assert turn2_step1_messages[: len(turn1_step2_prefix)] == turn1_step2_prefix
    assert turn2_step1_messages[len(turn1_step2_prefix)] == {
        "role": "assistant",
        "content": "已核对第一章，回复「继续」后继续。",
        "reasoning_content": "时间快到了，先做阶段总结",
    }
    assert turn2_step1_messages[len(turn1_step2_prefix) + 1]["role"] == "user"
    assert "继续" in turn2_step1_messages[len(turn1_step2_prefix) + 1]["content"]


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
@pytest.mark.parametrize(
    ("stop_reason", "card_layer", "card_limit"),
    [
        ("max_turns_exceeded", "tool_call", "max_turns"),
        ("run_deadline_exceeded", "collaboration", "wall_clock"),
    ],
)
async def test_clean_max_turns_and_between_runs_wrap_up_store_wire_prefix(
    db_session: Session, fake_redis, monkeypatch, stop_reason: str, card_layer: str, card_limit: str
):
    """Clean max_turns_exceeded soft-land and between-runs wall_clock wrap-up both store
    wire_prefix_cache and hit on Turn N+1."""
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.service import AgentService

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "MiniMax-M3.1-Flash")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test-minimax")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.delenv("MINIMAX_SECONDARY_API_KEY", raising=False)
    llm_failover.reset_failover_state()
    llm_providers.reset_key_routing_state()

    user = User(
        email=f"wire_{stop_reason}_{card_layer}@example.com",
        username=f"wire_{card_layer}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="软着陆测试", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    seen_histories: list[list[dict]] = []

    def fake_loop(state, **_kwargs):
        seen_histories.append(list(state.get("history_items") or []))
        state["native_run_holder"] = {
            "agent_type": "loop",
            "items": [
                {"role": "user", "content": "<turn_context>SOFT_LAND_WIRE_MARKER</turn_context>\n\n第一轮"},
                {"role": "assistant", "content": "阶段总结"},
            ],
        }
        state["run_meter"].last_served_provider_key = "minimax:MiniMax-M3.1-Flash:primary"

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "阶段总结"})
            yield StreamEvent(
                type=StreamEventType.ITERATION_EXHAUSTED,
                data={
                    "layer": card_layer,
                    "iterations_used": 10,
                    "max_iterations": 60,
                    "reason": "先停在这里",
                    "limit": card_limit,
                },
            )
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": stop_reason})

        return _stream()

    service = AgentService()
    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
        async for _frame in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="第一轮",
            session=db_session,
            session_id=chat.id,
        ):
            pass
        assert wire_prefix_cache.wire_prefix_cache_key(chat.id) in fake_redis.store

        async for _frame in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="继续",
            session=db_session,
            session_id=chat.id,
        ):
            pass

    assert any("SOFT_LAND_WIRE_MARKER" in str(item.get("content") or "") for item in seen_histories[-1])


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
@pytest.mark.parametrize(
    "case",
    [
        "dangling_function_call",
        "stream_error",
        "pending_empty_file",
        "empty_file_correction_card",
        "wall_clock_with_rolled_back_files",
        "unexecuted_tool_text_card",
        "late_steering_before_save",
    ],
)
async def test_unclean_or_skipped_turns_skip_store_and_invalidate_stale_cache(
    db_session: Session, fake_redis, monkeypatch, case: str
):
    """When a turn commits a new assistant message and skips wire_prefix_cache for any safety
    reason, it must NOT store a new entry and MUST invalidate any pre-existing stale key."""
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.service import AgentService
    from agent.tools.mcp_tools import ToolContext

    user = User(
        email=f"wire_skip_{case}@example.com",
        username=f"wireskip_{case[:12]}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="跳过并失效", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    key = wire_prefix_cache.wire_prefix_cache_key(chat.id)
    fake_redis.setex(key, 900, '{"stale": true}')

    if case == "late_steering_before_save":
        calls = {"n": 0}

        async def fake_drain(_session_id, _run_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return [SimpleNamespace(id="steer-1", content="顺便改一下结尾")]
            return []

        monkeypatch.setattr("agent.service.drain_and_seal_steering_async", fake_drain)

    def fake_loop(state, **_kwargs):
        items = _sample_wire_items()
        if case == "dangling_function_call":
            items = [
                {"role": "user", "content": "写"},
                {"type": "function_call", "call_id": "call_dangling", "name": "query_files", "arguments": "{}"},
            ]
        state["native_run_holder"] = {"agent_type": "loop", "items": items}
        state["run_meter"].last_served_provider_key = "minimax:MiniMax-M3.1-Flash:primary"

        async def _stream():
            if case == "pending_empty_file":
                ToolContext.set_pending_empty_file("f-unwritten", "空章节", "v1")
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "部分回复"})
            if case == "empty_file_correction_card":
                yield StreamEvent(
                    type=StreamEventType.ITERATION_EXHAUSTED,
                    data={
                        "layer": "empty_file_correction",
                        "iterations_used": 2,
                        "max_iterations": 2,
                        "reason": "空文件已回滚",
                        "rolled_back_files": [{"id": "f1", "title": "第一章"}],
                    },
                )
            elif case == "wall_clock_with_rolled_back_files":
                yield StreamEvent(
                    type=StreamEventType.ITERATION_EXHAUSTED,
                    data={
                        "layer": "tool_call",
                        "iterations_used": 5,
                        "max_iterations": 60,
                        "reason": "时限收尾但回滚了空文件",
                        "limit": "wall_clock",
                        "rolled_back_files": [{"id": "f1", "title": "第一章"}],
                    },
                )
            elif case == "unexecuted_tool_text_card":
                yield StreamEvent(
                    type=StreamEventType.ITERATION_EXHAUSTED,
                    data={
                        "layer": "unexecuted_tool_text",
                        "iterations_used": 1,
                        "max_iterations": 12,
                        "reason": "协议写成文字未执行",
                    },
                )
            stop = "run_deadline_exceeded" if case == "wall_clock_with_rolled_back_files" else "end_turn"
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": stop})
            if case == "stream_error":
                yield StreamEvent(type=StreamEventType.ERROR, data={"error": "boom", "code": "INTERNAL_ERROR"})

        return _stream()

    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
        async for _frame in AgentService().process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="测试跳过与失效",
            session=db_session,
            session_id=chat.id,
        ):
            pass

    assert key not in fake_redis.store
    assert key in fake_redis.deleted_keys


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
@pytest.mark.parametrize("stop_kind", ["user_stopped", "client_disconnected"])
async def test_cancelled_user_stopped_and_client_disconnected_invalidate_stale_wire_prefix(
    db_session: Session, fake_redis, stop_kind: str
):
    """Cancellation paths (user_stopped / client_disconnected) persist partial history and must
    invalidate any pre-existing stale wire_prefix_cache entry in Redis."""
    import asyncio

    from agent.core.round_outcome import RunOutcome
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.service import AgentService

    user = User(
        email=f"wire_cancel_{stop_kind}@example.com",
        username=f"wirecancel_{stop_kind[:10]}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="取消失效", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    key = wire_prefix_cache.wire_prefix_cache_key(chat.id)
    fake_redis.setex(key, 900, '{"stale": true}')
    run_outcome = RunOutcome()
    scheduled_tasks: list[asyncio.Task] = []

    def fake_loop(state, **_kwargs):
        state["native_run_holder"] = {"agent_type": "loop", "items": _sample_wire_items()}
        state["run_meter"].last_served_provider_key = "minimax:MiniMax-M3.1-Flash:primary"

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "写了一半被停"})
            await asyncio.sleep(60)

        return _stream()

    service = AgentService()
    orig_schedule = service._schedule_background_cleanup

    def _capture_cleanup(coro, **kwargs):
        task = orig_schedule(coro, **kwargs)
        scheduled_tasks.append(task)
        return task

    service._schedule_background_cleanup = _capture_cleanup

    with (
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        gen = service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="写第一章",
            session=db_session,
            session_id=chat.id,
            run_outcome=run_outcome,
        )
        async for frame in gen:
            if "写了一半被停" in frame:
                run_outcome.stop_kind = stop_kind
                await gen.aclose()
                break

        if scheduled_tasks:
            await asyncio.gather(*scheduled_tasks, return_exceptions=True)

    assert key not in fake_redis.store
    assert key in fake_redis.deleted_keys


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_soft_landed_turn_with_unconsumed_steering_skips_cache_and_falls_back_to_db(
    db_session: Session, fake_redis, monkeypatch
):
    """If a soft-landed turn acknowledged mid-run/boundary steering that was appended to
    writing_state['messages'] AFTER native_run_holder captured items (messages_len < len(messages)),
    Turn N must skip wire_prefix_cache and invalidate stale Redis entries so Turn N+1 falls back
    to DB history containing the acknowledged steering message."""
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.service import AgentService

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "MiniMax-M3.1-Flash")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test-minimax")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.delenv("MINIMAX_SECONDARY_API_KEY", raising=False)
    llm_failover.reset_failover_state()
    llm_providers.reset_key_routing_state()

    user = User(
        email="wire_unconsumed_steer@example.com",
        username="wireunconsumedsteer",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="软着陆插话不丢失", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    key = wire_prefix_cache.wire_prefix_cache_key(chat.id)
    fake_redis.setex(key, 900, '{"stale": true}')
    steering_text = "记得把反派的名字改成陆沉"
    seen_histories: list[list[dict]] = []

    def fake_loop_turn1(state, *, get_steering_messages=None, **_kwargs):
        seen_histories.append(list(state.get("history_items") or []))

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "时间快到了，先总结。"})
            # Simulate runner.py capturing native_run_holder BEFORE appending mid_run_steering to state["messages"].
            state["messages"] = [{"role": "user", "content": "写第一章"}, {"role": "assistant", "content": "时间快到了，先总结。"}]
            state["native_run_holder"] = {
                "agent_type": "loop",
                "items": [
                    {"role": "user", "content": "<turn_context>STALE_WIRE_NO_STEER</turn_context>\n\n写第一章"},
                    {"role": "assistant", "content": "时间快到了，先总结。"},
                ],
                "messages_len": len(state["messages"]),
            }
            state["run_meter"].last_served_provider_key = "minimax:MiniMax-M3.1-Flash:primary"
            # Mid-run steering was drained via get_steering_messages() and appended to state["messages"]
            # after native_run_holder was recorded, with no follow-up SDK run because of soft-land.
            if get_steering_messages is not None:
                await get_steering_messages()
            state["messages"].append({"role": "user", "content": steering_text})
            yield StreamEvent(
                type=StreamEventType.ITERATION_EXHAUSTED,
                data={
                    "layer": "tool_call",
                    "iterations_used": 8,
                    "max_iterations": 60,
                    "reason": "时间快到了",
                    "limit": "wall_clock",
                },
            )
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "run_deadline_exceeded"})

        return _stream()

    def fake_loop_turn2(state, **_kwargs):
        seen_histories.append(list(state.get("history_items") or []))

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "收到，按陆沉继续。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _stream()

    # Queue steering message inside fake steering queue so get_steering_messages() records it in consumed_steering.
    class _OneShotQueue:
        def __init__(self) -> None:
            self._used = False

        async def get_pending(self):
            if self._used:
                return []
            self._used = True
            return [SimpleNamespace(id="steer-mid-1", content=steering_text)]

    service = AgentService()
    with (
        patch("agent.service.create_steering_queue_async", AsyncMock(return_value=_OneShotQueue())),
        patch("agent.service.drain_and_seal_steering_async", AsyncMock(return_value=[])),
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop_turn1),
    ):
        async for _frame in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="写第一章",
            session=db_session,
            session_id=chat.id,
        ):
            pass

    # Turn 1 must NOT cache the wire prefix lacking steering_text, and must evict the stale key.
    assert key not in fake_redis.store
    assert key in fake_redis.deleted_keys

    # Turn 2 must fall back to DB history and include the acknowledged steering message.
    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop_turn2):
        async for _frame in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="继续",
            session=db_session,
            session_id=chat.id,
        ):
            pass

    turn2_history = seen_histories[-1]
    assert not any("STALE_WIRE_NO_STEER" in str(item.get("content") or "") for item in turn2_history)
    assert any(steering_text in str(item.get("content") or "") for item in turn2_history)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_cancellation_during_history_save_task_invalidates_stale_wire_prefix(
    db_session: Session, fake_redis
):
    """If cancellation arrives while awaiting asyncio.shield(history_save_task) (with run_outcome=None
    and no late steering), the shielded save commits DB rows while astore_wire_prefix never runs;
    the background cancellation handler must still invalidate any stale Redis key."""
    import asyncio

    from agent.core.message_manager import MessageManager
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.service import AgentService

    user = User(
        email="wire_cancel_save@example.com",
        username="wirecancelsave",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="保存期间取消失效", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    key = wire_prefix_cache.wire_prefix_cache_key(chat.id)
    fake_redis.setex(key, 900, '{"stale": true}')

    save_started = asyncio.Event()
    allow_save_finish = asyncio.Event()
    orig_save_messages = MessageManager.save_messages

    async def slow_save_messages(self, *args, **kwargs):
        save_started.set()
        await allow_save_finish.wait()
        return await orig_save_messages(self, *args, **kwargs)

    def fake_loop(state, **_kwargs):
        state["native_run_holder"] = {"agent_type": "loop", "items": _sample_wire_items()}
        state["run_meter"].last_served_provider_key = "minimax:MiniMax-M3.1-Flash:primary"

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "正常完成"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _stream()

    scheduled_tasks: list[asyncio.Task] = []
    service = AgentService()
    orig_schedule = service._schedule_background_cleanup

    def _capture_cleanup(coro, **kwargs):
        task = orig_schedule(coro, **kwargs)
        scheduled_tasks.append(task)
        return task

    service._schedule_background_cleanup = _capture_cleanup

    async def _consume():
        async for _frame in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="写第一章",
            session=db_session,
            session_id=chat.id,
            run_outcome=None,
        ):
            pass

    with (
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop),
        patch.object(MessageManager, "save_messages", new=slow_save_messages),
    ):
        task = asyncio.create_task(_consume())
        await save_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        allow_save_finish.set()
        if scheduled_tasks:
            await asyncio.gather(*scheduled_tasks, return_exceptions=True)

    assert key not in fake_redis.store
    assert key in fake_redis.deleted_keys
