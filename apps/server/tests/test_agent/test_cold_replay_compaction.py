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
