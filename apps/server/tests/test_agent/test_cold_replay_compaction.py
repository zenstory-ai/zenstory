"""Regression tests for Fix 1: cold DB replay must not inherit stale warm prompt_tokens.

Covers:
1. Cold DB replay with persist-slimmed history + high stale warm DB ``metadata["prompt_tokens"]``
   does NOT falsely trigger ``should_compact()`` or Stage 2 LLM compaction when actual cold
   history + static overhead + current turn is well below threshold.
2. Warm ``wire_prefix_cache`` hit with real live ``last_prompt_tokens >= threshold`` STILL
   triggers compaction normally (and does not double-count static system/tools overhead).
3. Stage 1 ``archive_old_tool_results()`` on cold DB replay computes ``projected_after_tokens``
   from actual cold tokens rather than a stale warm ``metadata["prompt_tokens"]`` floor.
4. Truly large cold DB replay whose actual cold items + system prompt + tool schemas + current
   turn exceeds threshold STILL compacts accurately.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session

from agent.core.message_manager import MessageManager
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop import wire_prefix_cache
from agent.loop.compaction import (
    SKIPPED_EMPTY_OR_TRUNCATED,
    CompactionOutcome,
    archive_old_tool_results,
    should_compact,
    skipped_record,
)
from agent.loop.history import (
    LOOP_ENGINE_METADATA_VALUE,
    effective_history_provider_tokens,
    estimate_provider_items_tokens,
    estimate_provider_text_tokens,
    estimate_tools_schema_tokens,
    load_loop_history,
)
from agent.service import AgentService
from config import agent_runtime
from config.datetime_utils import utcnow
from models import ChatMessage, ChatSession, Project, User


class _FakeRedis:
    """Minimal in-memory Redis client for wire_prefix_cache hit/miss tests."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def setex(self, key: str, _ttl: int, value: str) -> bool:
        self.store[key] = value
        return True

    def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


def _seed_user_project_chat(db_session: Session, tag: str) -> tuple[User, Project, ChatSession]:
    user = User(
        username=f"u_{tag}",
        email=f"{tag}@example.test",
        hashed_password="x",
        email_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    project = Project(name=f"proj_{tag}", owner_id=user.id)
    db_session.add(project)
    db_session.flush()
    chat = ChatSession(user_id=user.id, project_id=project.id, title=f"chat_{tag}")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    return user, project, chat


def _fake_loop_stream(state: dict[str, Any], **_kwargs: Any):
    state["run_items"] = [
        *state.get("history_items", []),
        {"role": "user", "content": str(state.get("user_message") or "")},
        {"role": "assistant", "content": "已继续处理。"},
    ]

    async def _gen():
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "已继续处理。"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    return _gen()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cold_db_replay_with_stale_warm_prompt_tokens_does_not_falsely_compact(
    db_session: Session,
    writing_prompt_configs,
    shell_engine,
    monkeypatch,
):
    """Persist-slimmed DB rows + stale warm metadata.prompt_tokens=422_446 must NOT trigger compaction."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    wire_prefix_cache.reset_wire_prefix_cache_state()

    user, project, chat = _seed_user_project_chat(db_session, "cold_no_false_compact")
    base = datetime(2026, 11, 27, 22, 0, 0)

    # Simulate a session whose warm run reached 422k tokens with full read_files bodies,
    # then expired from wire_prefix_cache (>20m) and persisted with slimmed archived_note entries.
    slimmed_read_call = [
        {
            "id": "call_read_1",
            "name": "read_files",
            "arguments": {"ids": ["ch-1", "ch-2"]},
            "status": "success",
            "result": [
                {
                    "id": "ch-1",
                    "title": "第1章",
                    "file_type": "draft",
                    "content_length": 180_000,
                    "content_sha256": "abc123",
                    "archived_note": "该文件内容过长，已在历史记录中省略；如需原文请重新调用 read_files。",
                }
            ],
        }
    ]
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="请通读前两章并润色。", created_at=base))
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="已通读前两章并完成润色。",
            tool_calls=json.dumps(slimmed_read_call, ensure_ascii=False),
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "prompt_tokens": 422_446,
                    "display_events": [
                        {"type": "tool_call", "tool_call_index": 0},
                        {"type": "content", "content": "已通读前两章并完成润色。"},
                    ],
                },
                ensure_ascii=False,
            ),
            created_at=base + timedelta(seconds=30),
        )
    )
    db_session.commit()

    # Unit check: load_loop_history does not inherit stale warm 422_446 prompt_tokens.
    cold_history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
    )
    assert cold_history.last_prompt_tokens == 0
    assert effective_history_provider_tokens(cold_history) < 5_000
    assert should_compact(cold_history, extra_tokens=20_000) is False

    # End-to-end through AgentService.process_stream: 0 compaction calls, no history_compacted SSE.
    compact_mock = AsyncMock(side_effect=AssertionError("Cold replay below 300k must not compact"))
    with (
        patch("agent.service.compact_history", compact_mock),
        patch("agent.service.run_loop_agent_streaming", side_effect=_fake_loop_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks = [
            chunk
            async for chunk in AgentService().process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="继续检查第三章节奏。",
                session=db_session,
                session_id=chat.id,
                metadata={},
            )
        ]

    assert compact_mock.await_count == 0
    assert not any("event: history_compacted" in chunk for chunk in chunks)
    assert any("event: done" in chunk for chunk in chunks)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_warm_wire_prefix_cache_hit_with_live_last_prompt_tokens_still_compacts(
    db_session: Session,
    writing_prompt_configs,
    shell_engine,
    monkeypatch,
):
    """Warm wire_prefix_cache hit with live last_prompt_tokens >= threshold MUST still compact."""
    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake-test:6379/0")
    monkeypatch.setattr(wire_prefix_cache, "get_redis_client", lambda: fake_redis)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_ENABLED", False)

    user, project, chat = _seed_user_project_chat(db_session, "warm_hit_compacts")
    base = utcnow().replace(tzinfo=None) - timedelta(minutes=5)
    u1 = ChatMessage(session_id=chat.id, role="user", content="写第1章", created_at=base)
    a1 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="第1章完成。",
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 320_000}),
        created_at=base + timedelta(seconds=10),
    )
    db_session.add_all([u1, a1])
    db_session.commit()
    db_session.refresh(a1)

    service = AgentService()
    system_prompt = MessageManager(project_id=project.id, user_id=user.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=""
    )
    fake_provider = SimpleNamespace(
        name="minimax",
        model="MiniMax-M3.1-Flash",
        key_slot="primary",
        uses_responses_api=False,
        reasoning_effort_for=lambda _req: "default",
    )
    provider_key = wire_prefix_cache.provider_cache_key(fake_provider)
    assert provider_key is not None
    tools_sha = wire_prefix_cache.tools_schema_sha256()

    warm_items = [
        {"role": "user", "content": "写第1章"},
        {"role": "assistant", "content": "第1章完成。"},
    ]
    assert wire_prefix_cache.store_wire_prefix(
        session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        last_assistant_message_id=a1.id,
        system_prompt=system_prompt,
        provider_key=provider_key,
        tools_sha256=tools_sha,
        reasoning_effort="default",
        items=warm_items,
        history_meta={
            "last_prompt_tokens": 320_000,
            "compacted": False,
            "compaction_skipped_at_tokens": 0,
            "compaction_skipped_reason": None,
            "trailing_output_truncations": 0,
            "archived_call_ids": [],
            "archived_file_ids": [],
            "author_messages": ["写第1章"],
        },
    )

    seen_calls: list[dict[str, Any]] = []

    async def fake_compact(hist, **kwargs):
        seen_calls.append({"last_prompt_tokens": hist.last_prompt_tokens, **kwargs})
        return CompactionOutcome(
            record={
                "summary": "热缓存交接摘要",
                "covers_through_message_id": a1.id,
                "kept_author_messages": ["写第1章"],
                "created_at": utcnow().isoformat(),
            },
            items=[{"role": "user", "content": "【上下文已压缩】热缓存交接摘要"}],
            usage={"prompt_tokens": 320_000, "completion_tokens": 120},
        )

    with (
        patch("agent.service.llm_failover.aordered_providers", new=AsyncMock(return_value=[fake_provider])),
        patch("agent.service.compact_history", side_effect=fake_compact),
        patch("agent.service.run_loop_agent_streaming", side_effect=_fake_loop_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks = [
            chunk
            async for chunk in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="写第2章",
                session=db_session,
                session_id=chat.id,
                metadata={},
            )
        ]

    assert len(seen_calls) == 1
    assert seen_calls[0]["last_prompt_tokens"] == 320_000
    # Warm hit already includes system_prompt & tools in last_prompt_tokens, so turn_input_tokens is just current turn.
    assert seen_calls[0]["turn_input_tokens"] < 500
    assert any("event: history_compacted" in chunk for chunk in chunks)


@pytest.mark.unit
def test_stage1_tool_archive_on_cold_replay_uses_actual_cold_tokens_not_stale_warm_floor(
    db_session: Session,
):
    """On cold DB replay, Stage 1 projected_after_tokens must use actual cold after_tokens,
    not stale warm DB prompt_tokens."""
    user, project, chat = _seed_user_project_chat(db_session, "cold_stage1_admission")
    base = datetime(2026, 11, 27, 21, 0, 0)

    # Turn 0: archivable old read_files output (~45k tokens)
    old_read_body = "【第一章旧稿正文】雾港钟声响起，潮水拍打着青石阶，温梨推开木门。" * 2_400
    turn0_calls = [
        {
            "id": "call_old_read",
            "name": "read_files",
            "arguments": {"ids": ["ch-1"]},
            "status": "success",
            "result": [{"id": "ch-1", "title": "第1章", "file_type": "draft", "content": old_read_body}],
        }
    ]
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="第1轮：读第一章", created_at=base))
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="已读完第一章。",
            tool_calls=json.dumps(turn0_calls, ensure_ascii=False),
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "prompt_tokens": 120_000,
                    "display_events": [
                        {"type": "tool_call", "tool_call_index": 0},
                        {"type": "content", "content": "已读完第一章。"},
                    ],
                },
                ensure_ascii=False,
            ),
            created_at=base + timedelta(seconds=10),
        )
    )

    # Turns 1..3: 3 recent protected turns with ~215k tokens of cold text, plus stale warm prompt_tokens=340_000.
    # If stale 340_000 were inherited as floor:
    #   projected_after = max(215k, 340k - 45k) = 295k; 295k + 45k turn_input = 340k > 300k -> FALSE REJECT!
    # With cold replay fix (last_prompt_tokens == 0):
    #   projected_after = after_tokens (~215k); 215k + 45k = 260k <= 300k -> CLEAN STAGE 1 ADMISSION!
    recent_turn_chunk = "【近期创作正文与设定讨论】风起青萍之末，云涌孤城之巅，主角步步为营。" * 3_500
    for idx in range(1, 4):
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="user",
                content=f"近期第{idx}轮：{recent_turn_chunk}",
                created_at=base + timedelta(seconds=idx * 20),
            )
        )
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="assistant",
                content=f"收到近期第{idx}轮。",
                message_metadata=json.dumps(
                    {
                        "engine": LOOP_ENGINE_METADATA_VALUE,
                        "prompt_tokens": 340_000 if idx == 3 else 250_000,
                        "display_events": [{"type": "content", "content": f"收到近期第{idx}轮。"}],
                    },
                    ensure_ascii=False,
                ),
                created_at=base + timedelta(seconds=idx * 20 + 5),
            )
        )
    db_session.commit()

    cold_history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
    )
    assert cold_history.last_prompt_tokens == 0

    turn_input_tokens = 45_000
    cold_before = estimate_provider_items_tokens(cold_history.items)
    assert cold_before + turn_input_tokens > agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS
    assert should_compact(cold_history, extra_tokens=turn_input_tokens) is True

    stage1 = archive_old_tool_results(cold_history, turn_input_tokens=turn_input_tokens)
    assert stage1.archived_count == 1
    assert stage1.saved_tokens >= agent_runtime.AGENT_LOOP_TOOL_ARCHIVE_MIN_SAVINGS_TOKENS
    assert stage1.projected_after_tokens == stage1.after_tokens
    assert stage1.projected_after_tokens + turn_input_tokens <= agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS
    assert stage1.admitted is True
    assert stage1.record is not None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_truly_large_cold_replay_still_compacts_with_system_and_tools_overhead(
    db_session: Session,
    writing_prompt_configs,
    shell_engine,
    monkeypatch,
):
    """A truly huge cold replay MUST still compact, including the boundary where system prompt
    and tool schemas push total input past the threshold."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_ENABLED", False)

    user, project, chat = _seed_user_project_chat(db_session, "cold_truly_large")
    base = datetime(2026, 11, 27, 20, 0, 0)

    # Seed cold history with ~15k real tokens (no stale warm prompt_tokens boost).
    body = "冷回放真实大历史文本，用于验证超过阈值时照常触发压缩。" * 900
    db_session.add(ChatMessage(session_id=chat.id, role="user", content=body, created_at=base))
    a1 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已记录。",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "prompt_tokens": 100,  # deliberately tiny to prove DB prompt_tokens is ignored
                "display_events": [{"type": "content", "content": "已记录。"}],
            },
            ensure_ascii=False,
        ),
        created_at=base + timedelta(seconds=10),
    )
    db_session.add(a1)
    db_session.commit()
    db_session.refresh(a1)

    cold_history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
    )
    assert cold_history.last_prompt_tokens == 0
    items_tokens = estimate_provider_items_tokens(cold_history.items)

    system_prompt = MessageManager(project_id=project.id, user_id=user.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=""
    )
    fixed_overhead = estimate_provider_text_tokens(system_prompt) + estimate_tools_schema_tokens()
    assert fixed_overhead > 2_500

    # Set threshold right above (items_tokens + current_turn_tokens) but BELOW
    # (items_tokens + fixed_overhead + current_turn_tokens) to verify service.py includes
    # system_prompt + tool schemas when last_prompt_tokens == 0!
    turn_msg = "继续。"
    turn_msg_tokens = estimate_provider_text_tokens(turn_msg)
    boundary_threshold = items_tokens + turn_msg_tokens + (fixed_overhead // 2)
    assert items_tokens + turn_msg_tokens <= boundary_threshold
    assert items_tokens + turn_msg_tokens + fixed_overhead > boundary_threshold
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_COMPACT_AT_TOKENS", boundary_threshold)

    seen_calls: list[dict[str, Any]] = []

    async def fake_compact(hist, **kwargs):
        seen_calls.append({"last_prompt_tokens": hist.last_prompt_tokens, **kwargs})
        return CompactionOutcome(
            record={
                "summary": "大体积冷回放交接摘要",
                "covers_through_message_id": a1.id,
                "kept_author_messages": [body],
                "created_at": utcnow().isoformat(),
            },
            items=[{"role": "user", "content": "【上下文已压缩】大体积冷回放交接摘要"}],
            usage={"prompt_tokens": 20_000, "completion_tokens": 80},
        )

    with (
        patch("agent.service.compact_history", side_effect=fake_compact),
        patch("agent.service.run_loop_agent_streaming", side_effect=_fake_loop_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks = [
            chunk
            async for chunk in AgentService().process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message=turn_msg,
                session=db_session,
                session_id=chat.id,
                metadata={},
            )
        ]

    assert len(seen_calls) == 1
    assert seen_calls[0]["last_prompt_tokens"] == 0
    assert seen_calls[0]["turn_input_tokens"] >= turn_msg_tokens + fixed_overhead
    assert any("event: history_compacted" in chunk for chunk in chunks)

    # Also verify skipped_record includes cold_fixed_tokens when provided, matching warm semantics.
    rec = skipped_record(cold_history, SKIPPED_EMPTY_OR_TRUNCATED, extra_tokens=fixed_overhead)
    assert rec["reason"] == SKIPPED_EMPTY_OR_TRUNCATED
    assert rec["at_prompt_tokens"] == items_tokens + fixed_overhead


def _make_provider_and_fake_stream(
    *,
    last_prompt_tokens: int,
    reply_text: str = "已继续处理。",
    captured_states: list[dict[str, Any]] | None = None,
):
    fake_provider = SimpleNamespace(
        name="minimax",
        model="MiniMax-M3.1-Flash",
        key_slot="primary",
        uses_responses_api=False,
        reasoning_effort_for=lambda _req: "default",
    )
    provider_key = wire_prefix_cache.provider_cache_key(fake_provider)
    assert provider_key is not None

    def _stream(state: dict[str, Any], **_kwargs: Any):
        if captured_states is not None:
            captured_states.append({"history_items": list(state.get("history_items") or [])})
        run_meter = state.get("run_meter")
        if run_meter is not None:
            run_meter.last_served_provider_key = provider_key
            run_meter.last_served_reasoning_effort = "default"
            run_meter.last_prompt_tokens = last_prompt_tokens
        state["run_items"] = [
            *state.get("history_items", []),
            {"role": "user", "content": str(state.get("user_message") or "")},
            {"role": "assistant", "content": reply_text},
        ]

        async def _gen():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": reply_text})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _gen()

    return fake_provider, provider_key, _stream


@pytest.mark.asyncio
@pytest.mark.unit
async def test_warm_skip_660k_cold_replay_below_threshold_clears_cache_and_allows_warm_and_cold_compaction(
    db_session: Session,
    writing_prompt_configs,
    shell_engine,
    monkeypatch,
):
    """Finding 1 (660k -> <=300k): cold replay clears stale warm skip in wire_prefix_cache,
    allows subsequent warm turn > 300k to compact, AND allows subsequent cold turn > 300k
    (while DB still holds Turn T's 660k skip) to compact immediately without a moving goalpost."""
    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake-test:6379/0")
    monkeypatch.setattr(wire_prefix_cache, "get_redis_client", lambda: fake_redis)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_ENABLED", False)

    user, project, chat = _seed_user_project_chat(db_session, "warm_skip_660k_clear")
    base = utcnow().replace(tzinfo=None) - timedelta(minutes=30)

    # Turn 0: persist-slimmed read_files (tiny cold size << 300k).
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="请通读前两章。", created_at=base))
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="已通读。",
            message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 500_000}),
            created_at=base + timedelta(seconds=10),
        )
    )
    # Turn 1 (Turn T): warm compaction failed/skipped at 660_000 tokens.
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="user",
            content="继续润色。",
            created_at=base + timedelta(seconds=20),
        )
    )
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="已润色。",
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "prompt_tokens": 660_000,
                    "compaction_skipped": {
                        "reason": SKIPPED_EMPTY_OR_TRUNCATED,
                        "at_prompt_tokens": 660_000,
                        "created_at": base.isoformat(),
                    },
                },
                ensure_ascii=False,
            ),
            created_at=base + timedelta(seconds=30),
        )
    )
    db_session.commit()

    # (a) Cold replay T+1 (empty Redis): cold baseline << 300k -> clears stale 660k skip
    # and writes compaction_skipped_at_tokens == 0 into wire_prefix_cache.
    # Report live last_prompt_tokens=310_000 at end of T+1 so T+2 warm hit immediately exceeds 300k.
    fake_provider, provider_key, fake_stream_t1 = _make_provider_and_fake_stream(last_prompt_tokens=310_000)
    compact_calls: list[dict[str, Any]] = []

    async def fake_compact(hist, **kwargs):
        compact_calls.append(
            {
                "last_prompt_tokens": hist.last_prompt_tokens,
                "skipped_at": hist.compaction_skipped_at_tokens,
                **kwargs,
            }
        )
        return CompactionOutcome(
            record={
                "summary": "清理陈旧退避后的交接摘要",
                "covers_through_message_id": hist.last_message_id,
                "kept_author_messages": list(hist.author_messages[-1:]),
                "created_at": utcnow().isoformat(),
            },
            items=[{"role": "user", "content": "【上下文已压缩】清理陈旧退避后的交接摘要"}],
            usage={"prompt_tokens": 310_000, "completion_tokens": 100},
        )

    service = AgentService()
    with (
        patch("agent.service.llm_failover.aordered_providers", new=AsyncMock(return_value=[fake_provider])),
        patch("agent.service.compact_history", side_effect=fake_compact),
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_stream_t1),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks_t1 = [
            chunk
            async for chunk in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="第T+1轮冷回放。",
                session=db_session,
                session_id=chat.id,
                metadata={},
            )
        ]

    assert any("event: done" in chunk for chunk in chunks_t1)
    assert len(compact_calls) == 0

    latest_rows = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id == chat.id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .all()
    )
    latest_assistant = latest_rows[0]
    system_prompt = MessageManager(project_id=project.id, user_id=user.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=""
    )
    cached_entry = wire_prefix_cache.load_wire_prefix_entry(
        session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        last_message_id=latest_assistant.id,
        system_prompt=system_prompt,
        provider_key=provider_key,
        tools_sha256=wire_prefix_cache.tools_schema_sha256(),
        reasoning_effort="default",
    )
    assert cached_entry is not None
    assert cached_entry.history_meta is not None
    assert cached_entry.history_meta["compaction_skipped_at_tokens"] == 0
    assert cached_entry.history_meta["compaction_skipped_reason"] is None

    # (b) Subsequent warm turn T+2 with live last_prompt_tokens=310_000 > 300k MUST compact.
    _, _, fake_stream_t2 = _make_provider_and_fake_stream(last_prompt_tokens=40_000)
    with (
        patch("agent.service.llm_failover.aordered_providers", new=AsyncMock(return_value=[fake_provider])),
        patch("agent.service.compact_history", side_effect=fake_compact),
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_stream_t2),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks_t2 = [
            chunk
            async for chunk in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="第T+2轮热缓存超阈。",
                session=db_session,
                session_id=chat.id,
                metadata={},
            )
        ]

    assert len(compact_calls) == 1
    assert compact_calls[0]["last_prompt_tokens"] == 310_000
    assert compact_calls[0]["skipped_at"] == 0
    assert any("event: history_compacted" in chunk for chunk in chunks_t2)

    # (c) Subsequent COLD turn > 300k on a fresh session where DB STILL holds Turn T's 660k skip
    # (no wire_prefix_cache, and post-skip turns grew cold history to ~320k > 300k):
    # MUST compact immediately (proves fixed historical skip anchor, zero moving goalpost).
    monkeypatch.delenv("REDIS_URL", raising=False)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    user2, project2, chat2 = _seed_user_project_chat(db_session, "warm_skip_660k_cold_growth")
    db_session.add(ChatMessage(session_id=chat2.id, role="user", content="请通读前两章。", created_at=base))
    db_session.add(
        ChatMessage(
            session_id=chat2.id,
            role="assistant",
            content="已通读。",
            message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 500_000}),
            created_at=base + timedelta(seconds=10),
        )
    )
    db_session.add(
        ChatMessage(
            session_id=chat2.id,
            role="user",
            content="继续润色。",
            created_at=base + timedelta(seconds=20),
        )
    )
    db_session.add(
        ChatMessage(
            session_id=chat2.id,
            role="assistant",
            content="已润色。",
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "prompt_tokens": 660_000,
                    "compaction_skipped": {
                        "reason": SKIPPED_EMPTY_OR_TRUNCATED,
                        "at_prompt_tokens": 660_000,
                        "created_at": base.isoformat(),
                    },
                },
                ensure_ascii=False,
            ),
            created_at=base + timedelta(seconds=30),
        )
    )
    # Post-skip cold turn grew history to ~320k tokens (> 300k), while DB still holds Turn T's 660k skip.
    post_skip_growth = "扩写第三章至第十章详细正文与伏笔回收。" * 27_500
    assert estimate_provider_text_tokens(post_skip_growth) > 310_000
    db_session.add(
        ChatMessage(
            session_id=chat2.id,
            role="user",
            content=post_skip_growth,
            created_at=base + timedelta(seconds=40),
        )
    )
    db_session.add(
        ChatMessage(
            session_id=chat2.id,
            role="assistant",
            content="已收到扩写稿。",
            message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 325_000}),
            created_at=base + timedelta(seconds=50),
        )
    )
    db_session.commit()

    cold_compact_calls: list[dict[str, Any]] = []

    async def fake_cold_compact(hist, **kwargs):
        cold_compact_calls.append(
            {
                "last_prompt_tokens": hist.last_prompt_tokens,
                "skipped_at": hist.compaction_skipped_at_tokens,
                **kwargs,
            }
        )
        return CompactionOutcome(
            record={
                "summary": "冷回放消除移动球门后的交接摘要",
                "covers_through_message_id": hist.last_message_id,
                "kept_author_messages": ["继续检查。"],
                "created_at": utcnow().isoformat(),
            },
            items=[{"role": "user", "content": "【上下文已压缩】冷回放消除移动球门后的交接摘要"}],
            usage={"prompt_tokens": 320_000, "completion_tokens": 100},
        )

    with (
        patch("agent.service.compact_history", side_effect=fake_cold_compact),
        patch("agent.service.run_loop_agent_streaming", side_effect=_fake_loop_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks_cold = [
            chunk
            async for chunk in AgentService().process_stream(
                project_id=str(project2.id),
                user_id=str(user2.id),
                message="继续检查。",
                session=db_session,
                session_id=chat2.id,
                metadata={},
            )
        ]

    assert len(cold_compact_calls) == 1
    assert cold_compact_calls[0]["last_prompt_tokens"] == 0
    assert cold_compact_calls[0]["skipped_at"] == 0
    assert any("event: history_compacted" in chunk for chunk in chunks_cold)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_warm_skip_500k_cold_replay_clamps_to_fixed_350k_anchor_and_retries_at_460k(
    db_session: Session,
    writing_prompt_configs,
    shell_engine,
    monkeypatch,
):
    """Finding 1 (500k -> 350k > 300k): cold T+1 backs off (355k < 450k), AND cold T+2 grown
    +100k to ~460k (no cache, DB still holds 500k) retries compaction using the fixed historical
    skip anchor instead of re-clamping to the growing turn size."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_ENABLED", False)

    user, project, chat = _seed_user_project_chat(db_session, "warm_skip_500k_clamp")
    base = datetime(2026, 11, 27, 18, 0, 0)

    # Pre-skip history ~345k cold tokens (with ~5k fixed overhead -> cold_skip_baseline ~350k > 300k).
    pre_skip_text = "前情大纲与多章完整正文记录，用于验证冷回放固定跳过锚点。" * 18_950
    pre_skip_tokens = estimate_provider_text_tokens(pre_skip_text)
    assert 335_000 <= pre_skip_tokens <= 355_000

    db_session.add(ChatMessage(session_id=chat.id, role="user", content=pre_skip_text, created_at=base))
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="前情已记录。",
            message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 480_000}),
            created_at=base + timedelta(seconds=10),
        )
    )
    # Turn T: warm compaction skipped at 500_000 tokens.
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="user",
            content="请继续。",
            created_at=base + timedelta(seconds=20),
        )
    )
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="已继续。",
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "prompt_tokens": 500_000,
                    "compaction_skipped": {
                        "reason": SKIPPED_EMPTY_OR_TRUNCATED,
                        "at_prompt_tokens": 500_000,
                        "created_at": base.isoformat(),
                    },
                },
                ensure_ascii=False,
            ),
            created_at=base + timedelta(seconds=30),
        )
    )
    db_session.commit()

    compact_calls: list[dict[str, Any]] = []

    async def fake_compact(hist, **kwargs):
        compact_calls.append({"skipped_at": hist.compaction_skipped_at_tokens, **kwargs})
        return CompactionOutcome(
            record={
                "summary": "冷回放固定锚点增长100k后重试压缩成功",
                "covers_through_message_id": hist.last_message_id,
                "kept_author_messages": ["请继续推进。"],
                "created_at": utcnow().isoformat(),
            },
            items=[{"role": "user", "content": "【上下文已压缩】冷回放固定锚点增长100k后重试压缩成功"}],
            usage={"prompt_tokens": 460_000, "completion_tokens": 120},
        )

    # Cold T+1 (~355k < clamped 350k + 100k = 450k): MUST back off (0 compaction calls).
    with (
        patch("agent.service.compact_history", side_effect=fake_compact),
        patch("agent.service.run_loop_agent_streaming", side_effect=_fake_loop_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks_t1 = [
            chunk
            async for chunk in AgentService().process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="冷回放第T+1轮小增量。",
                session=db_session,
                session_id=chat.id,
                metadata={},
            )
        ]

    assert any("event: done" in chunk for chunk in chunks_t1)
    assert len(compact_calls) == 0

    # Grow cold history by +110k tokens (~460k total) while DB STILL holds Turn T's 500k skip.
    growth_text = "新增十一万供应商分词量级的后续章节与设定补充。" * 8_000
    assert estimate_provider_text_tokens(growth_text) >= 105_000
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="user",
            content=growth_text,
            created_at=utcnow().replace(tzinfo=None) + timedelta(seconds=1),
        )
    )
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="已记录后续章节。",
            message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 460_000}),
            created_at=utcnow().replace(tzinfo=None) + timedelta(seconds=2),
        )
    )
    db_session.commit()

    # Cold T+2 (~460k >= fixed 350k + 100k = 450k, while still < stale 500k + 100k = 600k):
    # MUST retry compaction!
    with (
        patch("agent.service.compact_history", side_effect=fake_compact),
        patch("agent.service.run_loop_agent_streaming", side_effect=_fake_loop_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks_t2 = [
            chunk
            async for chunk in AgentService().process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="请继续推进。",
                session=db_session,
                session_id=chat.id,
                metadata={},
            )
        ]

    assert len(compact_calls) == 1
    assert 340_000 <= compact_calls[0]["skipped_at"] <= 365_000
    assert any("event: history_compacted" in chunk for chunk in chunks_t2)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_retry_unanswered_after_warm_skip_cold_replay_clears_clamps_and_preserves_same_size(
    db_session: Session,
    writing_prompt_configs,
    shell_engine,
    monkeypatch,
):
    """Finding 1 (retry_unanswered):
    (a) Warm skip 660k -> cold replay <= 300k clears and does NOT re-stamp compaction_skipped.
    (b) Warm skip 500k -> cold replay ~350k > 300k re-stamps clamped cold_skip_baseline (not 500k).
    (c) Same-size cold skip preserves exact at_prompt_tokens and backoff."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_ENABLED", False)
    base = datetime(2026, 11, 27, 17, 0, 0)

    # --- (a) Warm skip 660k on unanswered user row, cold replay <= 300k ---
    u_a, p_a, c_a = _seed_user_project_chat(db_session, "retry_unanswered_clear")
    db_session.add(ChatMessage(session_id=c_a.id, role="user", content="旧消息", created_at=base))
    db_session.add(
        ChatMessage(
            session_id=c_a.id,
            role="assistant",
            content="旧回复",
            message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 500_000}),
            created_at=base + timedelta(seconds=10),
        )
    )
    db_session.add(
        ChatMessage(
            session_id=c_a.id,
            role="user",
            content="重试这条未回复消息",
            message_metadata=json.dumps(
                {
                    "compaction_skipped": {
                        "reason": SKIPPED_EMPTY_OR_TRUNCATED,
                        "at_prompt_tokens": 660_000,
                        "created_at": base.isoformat(),
                    }
                },
                ensure_ascii=False,
            ),
            created_at=base + timedelta(seconds=20),
        )
    )
    db_session.commit()

    compact_mock_a = AsyncMock(side_effect=AssertionError("Below 300k must not compact"))
    with (
        patch("agent.service.compact_history", compact_mock_a),
        patch("agent.service.run_loop_agent_streaming", side_effect=_fake_loop_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks_a = [
            chunk
            async for chunk in AgentService().process_stream(
                project_id=str(p_a.id),
                user_id=str(u_a.id),
                message="重试这条未回复消息",
                session=db_session,
                session_id=c_a.id,
                metadata={"retry_unanswered": True},
            )
        ]

    assert any("event: done" in chunk for chunk in chunks_a)
    assert compact_mock_a.await_count == 0
    latest_a = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id == c_a.id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .first()
    )
    assert latest_a is not None
    meta_a = json.loads(latest_a.message_metadata or "{}")
    assert "compaction_skipped" not in meta_a

    # --- (b) Warm skip 500k on unanswered user row, cold replay ~350k > 300k ---
    u_b, p_b, c_b = _seed_user_project_chat(db_session, "retry_unanswered_clamp")
    big_text = "用于测试未回复重试场景下冷回放钳制跳过基线的长文本。" * 21_500
    db_session.add(ChatMessage(session_id=c_b.id, role="user", content=big_text, created_at=base))
    db_session.add(
        ChatMessage(
            session_id=c_b.id,
            role="assistant",
            content="已记录长文本。",
            message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 490_000}),
            created_at=base + timedelta(seconds=10),
        )
    )
    db_session.add(
        ChatMessage(
            session_id=c_b.id,
            role="user",
            content="重试这条大历史消息",
            message_metadata=json.dumps(
                {
                    "compaction_skipped": {
                        "reason": SKIPPED_EMPTY_OR_TRUNCATED,
                        "at_prompt_tokens": 500_000,
                        "created_at": base.isoformat(),
                    }
                },
                ensure_ascii=False,
            ),
            created_at=base + timedelta(seconds=20),
        )
    )
    db_session.commit()

    compact_mock_b = AsyncMock(side_effect=AssertionError("Clamped backoff must not compact immediately"))
    with (
        patch("agent.service.compact_history", compact_mock_b),
        patch("agent.service.run_loop_agent_streaming", side_effect=_fake_loop_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks_b = [
            chunk
            async for chunk in AgentService().process_stream(
                project_id=str(p_b.id),
                user_id=str(u_b.id),
                message="重试这条大历史消息",
                session=db_session,
                session_id=c_b.id,
                metadata={"retry_unanswered": True},
            )
        ]

    assert any("event: done" in chunk for chunk in chunks_b)
    assert compact_mock_b.await_count == 0
    latest_b = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id == c_b.id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .first()
    )
    assert latest_b is not None
    meta_b = json.loads(latest_b.message_metadata or "{}")
    assert isinstance(meta_b.get("compaction_skipped"), dict)
    re_stamped_b = meta_b["compaction_skipped"]["at_prompt_tokens"]
    assert agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS < re_stamped_b < 500_000

    # --- (c) Same-size cold skip preserves exact at_prompt_tokens and backoff ---
    u_c, p_c, c_c = _seed_user_project_chat(db_session, "retry_unanswered_same_size")
    db_session.add(ChatMessage(session_id=c_c.id, role="user", content=big_text, created_at=base))
    db_session.add(
        ChatMessage(
            session_id=c_c.id,
            role="assistant",
            content="已记录长文本。",
            message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 0}),
            created_at=base + timedelta(seconds=10),
        )
    )
    db_session.commit()

    cold_hist_c = load_loop_history(db_session, project_id=p_c.id, user_id=u_c.id, chat_session_id=c_c.id)
    sys_prompt_c = MessageManager(project_id=p_c.id, user_id=u_c.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=""
    )
    exact_cold_baseline = (
        estimate_provider_items_tokens(cold_hist_c.items)
        + estimate_provider_text_tokens(sys_prompt_c)
        + estimate_tools_schema_tokens()
    )
    assert exact_cold_baseline > agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS

    db_session.add(
        ChatMessage(
            session_id=c_c.id,
            role="user",
            content="重试同量级冷跳过消息",
            message_metadata=json.dumps(
                {
                    "compaction_skipped": {
                        "reason": SKIPPED_EMPTY_OR_TRUNCATED,
                        "at_prompt_tokens": exact_cold_baseline,
                        "created_at": base.isoformat(),
                    }
                },
                ensure_ascii=False,
            ),
            created_at=base + timedelta(seconds=20),
        )
    )
    db_session.commit()

    compact_mock_c = AsyncMock(side_effect=AssertionError("Same-size cold skip must preserve backoff"))
    with (
        patch("agent.service.compact_history", compact_mock_c),
        patch("agent.service.run_loop_agent_streaming", side_effect=_fake_loop_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks_c = [
            chunk
            async for chunk in AgentService().process_stream(
                project_id=str(p_c.id),
                user_id=str(u_c.id),
                message="重试同量级冷跳过消息",
                session=db_session,
                session_id=c_c.id,
                metadata={"retry_unanswered": True},
            )
        ]

    assert any("event: done" in chunk for chunk in chunks_c)
    assert compact_mock_c.await_count == 0
    latest_c = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id == c_c.id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .first()
    )
    assert latest_c is not None
    meta_c = json.loads(latest_c.message_metadata or "{}")
    assert meta_c["compaction_skipped"]["at_prompt_tokens"] == exact_cold_baseline


@pytest.mark.asyncio
@pytest.mark.unit
async def test_warm_hit_just_below_300k_does_not_double_count_fixed_overhead(
    db_session: Session,
    writing_prompt_configs,
    shell_engine,
    monkeypatch,
):
    """Coverage gap 4(a): warm wire_prefix_cache hit at 297_000 + short turn must NOT compact
    via AgentService.process_stream (proves cold_fixed_tokens is not double-counted)."""
    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake-test:6379/0")
    monkeypatch.setattr(wire_prefix_cache, "get_redis_client", lambda: fake_redis)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_ENABLED", False)

    user, project, chat = _seed_user_project_chat(db_session, "warm_297k_no_compact")
    base = utcnow().replace(tzinfo=None) - timedelta(minutes=2)
    u1 = ChatMessage(session_id=chat.id, role="user", content="写第1章", created_at=base)
    a1 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="第1章完成。",
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 297_000}),
        created_at=base + timedelta(seconds=10),
    )
    db_session.add_all([u1, a1])
    db_session.commit()
    db_session.refresh(a1)

    system_prompt = MessageManager(project_id=project.id, user_id=user.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=""
    )
    fixed_overhead = estimate_provider_text_tokens(system_prompt) + estimate_tools_schema_tokens()
    # Sanity check: double-counting fixed_overhead would push 297k past 300k!
    assert 297_000 + fixed_overhead > agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS

    fake_provider, provider_key, fake_stream = _make_provider_and_fake_stream(last_prompt_tokens=298_000)
    assert wire_prefix_cache.store_wire_prefix(
        session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        last_assistant_message_id=a1.id,
        system_prompt=system_prompt,
        provider_key=provider_key,
        tools_sha256=wire_prefix_cache.tools_schema_sha256(),
        reasoning_effort="default",
        items=[
            {"role": "user", "content": "写第1章"},
            {"role": "assistant", "content": "第1章完成。"},
        ],
        history_meta={
            "last_prompt_tokens": 297_000,
            "compacted": False,
            "compaction_skipped_at_tokens": 0,
            "compaction_skipped_reason": None,
            "trailing_output_truncations": 0,
            "archived_call_ids": [],
            "archived_file_ids": [],
            "author_messages": ["写第1章"],
        },
    )

    compact_mock = AsyncMock(side_effect=AssertionError("Warm 297k + short turn must not compact"))
    with (
        patch("agent.service.llm_failover.aordered_providers", new=AsyncMock(return_value=[fake_provider])),
        patch("agent.service.compact_history", compact_mock),
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks = [
            chunk
            async for chunk in AgentService().process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="好，继续。",
                session=db_session,
                session_id=chat.id,
                metadata={},
            )
        ]

    assert compact_mock.await_count == 0
    assert not any("event: history_compacted" in chunk for chunk in chunks)
    assert any("event: done" in chunk for chunk in chunks)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cold_replay_stage1_enabled_through_process_stream_with_cold_fixed_tokens(
    db_session: Session,
    writing_prompt_configs,
    shell_engine,
    monkeypatch,
):
    """Coverage gap 4(b): cold replay with Stage 1 enabled through AgentService.process_stream
    triggers Stage 1 (zero LLM calls) right at the cold_fixed_tokens boundary and persists tool_archive."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_ENABLED", True)

    user, project, chat = _seed_user_project_chat(db_session, "cold_stage1_service")
    base = datetime(2026, 11, 27, 16, 0, 0)

    old_read_body = "【第一章旧稿正文】雾港钟声响起，潮水拍打着青石阶，温梨推开木门。" * 2_400
    turn0_calls = [
        {
            "id": "call_old_read",
            "name": "read_files",
            "arguments": {"ids": ["ch-1"]},
            "status": "success",
            "result": [{"id": "ch-1", "title": "第1章", "file_type": "draft", "content": old_read_body}],
        }
    ]
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="第1轮：读第一章", created_at=base))
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="已读完第一章。",
            tool_calls=json.dumps(turn0_calls, ensure_ascii=False),
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "prompt_tokens": 120_000,
                    "display_events": [
                        {"type": "tool_call", "tool_call_index": 0},
                        {"type": "content", "content": "已读完第一章。"},
                    ],
                },
                ensure_ascii=False,
            ),
            created_at=base + timedelta(seconds=10),
        )
    )
    for idx in range(1, 4):
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="user",
                content=f"近期第{idx}轮对话内容。",
                created_at=base + timedelta(seconds=idx * 20),
            )
        )
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="assistant",
                content=f"收到近期第{idx}轮。",
                message_metadata=json.dumps(
                    {
                        "engine": LOOP_ENGINE_METADATA_VALUE,
                        "prompt_tokens": 390_000 if idx == 3 else 250_000,
                        "display_events": [{"type": "content", "content": f"收到近期第{idx}轮。"}],
                    },
                    ensure_ascii=False,
                ),
                created_at=base + timedelta(seconds=idx * 20 + 5),
            )
        )
    db_session.commit()

    cold_history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    items_tokens = estimate_provider_items_tokens(cold_history.items)
    system_prompt = MessageManager(project_id=project.id, user_id=user.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=""
    )
    fixed_overhead = estimate_provider_text_tokens(system_prompt) + estimate_tools_schema_tokens()
    turn_msg = "继续。"
    turn_msg_tokens = estimate_provider_text_tokens(turn_msg)
    # Place threshold so cold_fixed_tokens is what pushes the cold replay over the threshold,
    # and Stage 1 archiving old_read_body brings it well back under the threshold.
    boundary_threshold = items_tokens + turn_msg_tokens + (fixed_overhead // 2)
    assert items_tokens + turn_msg_tokens <= boundary_threshold
    assert items_tokens + turn_msg_tokens + fixed_overhead > boundary_threshold
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_COMPACT_AT_TOKENS", boundary_threshold)

    compact_mock = AsyncMock(side_effect=AssertionError("Stage 1 must admit without calling Stage 2 LLM"))
    with (
        patch("agent.service.compact_history", compact_mock),
        patch("agent.service.run_loop_agent_streaming", side_effect=_fake_loop_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks = [
            chunk
            async for chunk in AgentService().process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message=turn_msg,
                session=db_session,
                session_id=chat.id,
                metadata={},
            )
        ]

    assert compact_mock.await_count == 0
    assert any("event: done" in chunk for chunk in chunks)
    assert not any("event: history_compacted" in chunk for chunk in chunks)
    latest = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id == chat.id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .first()
    )
    assert latest is not None
    meta = json.loads(latest.message_metadata or "{}")
    assert meta.get("compaction", {}).get("mode") == "tool_archive"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cold_replay_run_bound_safety_valve_subtracts_cold_fixed_tokens(
    db_session: Session,
    writing_prompt_configs,
    shell_engine,
    monkeypatch,
):
    """Finding 2: cold replay run-bound safety valve must subtract cold_fixed_tokens from
    AGENT_LOOP_RUN_HISTORY_MAX_TOKENS for warm/cold parity."""
    import agent.service as agent_service_mod

    monkeypatch.delenv("REDIS_URL", raising=False)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_ENABLED", False)

    user, project, chat = _seed_user_project_chat(db_session, "cold_run_bound_parity")
    base = datetime(2026, 11, 27, 15, 0, 0)

    # Seed 2 turns totaling ~127k cold tokens so (run_bound - cold_fixed_tokens) stays above the 100k floor.
    chunk_text = "超长历史回放用于验证冷回放运行安全阀扣除静态前缀开销。" * 3_900
    for idx in range(2):
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="user",
                content=f"第{idx + 1}轮：{chunk_text}",
                created_at=base + timedelta(seconds=idx * 20),
            )
        )
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="assistant",
                content=f"第{idx + 1}轮已完成。",
                message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 120_000}),
                created_at=base + timedelta(seconds=idx * 20 + 5),
            )
        )
    db_session.commit()

    cold_history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    items_tokens = estimate_provider_items_tokens(cold_history.items)
    system_prompt = MessageManager(project_id=project.id, user_id=user.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=""
    )
    fixed_overhead = estimate_provider_text_tokens(system_prompt) + estimate_tools_schema_tokens()
    assert items_tokens > 115_000
    assert fixed_overhead > 2_500

    # Place run bound between items_tokens and items_tokens + fixed_overhead (with run_bound - fixed_overhead > 100_000):
    # - Without Finding 2 fix: items_tokens <= run_bound -> no items dropped.
    # - With Finding 2 fix: items_tokens > run_bound - fixed_overhead -> oldest turn dropped!
    run_bound = items_tokens + (fixed_overhead // 2)
    assert run_bound - fixed_overhead > 100_000
    monkeypatch.setattr(agent_service_mod, "AGENT_LOOP_RUN_HISTORY_MAX_TOKENS", run_bound)

    captured_states: list[dict[str, Any]] = []
    _, _, fake_stream = _make_provider_and_fake_stream(last_prompt_tokens=120_000, captured_states=captured_states)

    with (
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_stream),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks = [
            chunk
            async for chunk in AgentService().process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="继续。",
                session=db_session,
                session_id=chat.id,
                metadata={},
            )
        ]

    assert any("event: done" in chunk for chunk in chunks)
    assert len(captured_states) == 1
    assert len(captured_states[0]["history_items"]) < len(cold_history.items)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cold_replay_stage2_compaction_log_includes_effective_and_turn_tokens(
    monkeypatch,
):
    """Finding 3: Stage 2 'Loop history compacted' log includes effective_history_tokens
    and turn_input_tokens alongside measured_prompt_tokens (which is 0 on cold replay)."""
    from agent.loop import compaction as compaction_mod
    from agent.loop.history import LoopHistory

    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_ENABLED", False)

    history = LoopHistory(
        items=[
            {"role": "user", "content": "冷回放历史消息" * 200},
            {"role": "assistant", "content": "冷回放助手回复" * 200},
        ],
        last_prompt_tokens=0,
        last_message_id="msg-cold-1",
        author_messages=["冷回放历史消息" * 200],
    )
    fake_provider = SimpleNamespace(
        name="deepseek",
        model="deepseek-chat",
        key_slot="primary",
        uses_responses_api=False,
        reasoning_effort_for=lambda _req: "default",
    )
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="这是交接摘要正文，覆盖前序所有剧情进展。"),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=310_000, completion_tokens=80),
    )

    logged_events: list[tuple[str, dict[str, Any]]] = []

    def capture_log(_logger, _level, message, **kwargs):
        logged_events.append((message, kwargs))

    with (
        patch("agent.core.llm_failover.aordered_providers", new=AsyncMock(return_value=[fake_provider])),
        patch(
            "agent.core.llm_failover.acreate_chat_completion",
            new=AsyncMock(return_value=(fake_response, fake_provider)),
        ),
        patch.object(compaction_mod, "log_with_context", side_effect=capture_log),
    ):
        outcome = await compaction_mod.compact_history(
            history,
            system_prompt="系统提示",
            session_id="sess-cold-log",
            turn_input_tokens=12_345,
        )

    assert outcome.record is not None
    compacted_logs = [kw for msg, kw in logged_events if msg == "Loop history compacted"]
    assert len(compacted_logs) == 1
    assert compacted_logs[0]["measured_prompt_tokens"] == 0
    assert compacted_logs[0]["effective_history_tokens"] == effective_history_provider_tokens(history)
    assert compacted_logs[0]["effective_history_tokens"] > 0
    assert compacted_logs[0]["turn_input_tokens"] == 12_345
