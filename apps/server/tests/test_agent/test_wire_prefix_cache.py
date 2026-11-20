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
    """Minimal in-memory Redis stand-in implementing get/setex/delete."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int]] = {}
        self.deleted_keys: list[str] = []

    def get(self, key: str) -> str | None:
        entry = self.store.get(key)
        return entry[0] if entry is not None else None

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
    """When MiniMax dual-key routing switches key_slot (e.g. key cooldown or routing window boundary),
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
