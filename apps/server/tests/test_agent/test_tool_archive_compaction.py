"""Two-stage smart context compaction (Stage 1 zero-LLM ``tool_archive`` before Stage 2 LLM summary).

Covers:
1. Zero-run below threshold (normal turns keep byte-identical history prefixes).
2. Stage 1 admission skips LLM call & preserves last 3 user turns + write tools + error results + small outputs.
3. Deterministic DB replay via ``load_loop_history`` produces byte-identical archived outputs (including
   provider ``call_id`` mapping and cumulative multi-round archives).
4. Stage 2 fallback receives archived history when savings or post-archive headroom is insufficient.
5. Backward compatibility with existing LLM ``summary`` compaction records & ``client_message_metadata``.
6. ``AGENT_LOOP_TOOL_ARCHIVE_ENABLED=false`` disables Stage 1 cleanly.
7. End-to-end ``AgentService.process_stream`` integration: transient thinking notice (no broken empty
   ``history_compacted`` card), snapshot dropping for archived files, ``wire_prefix_cache`` round-trip,
   and ``retry_unanswered`` carry-over.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop import wire_prefix_cache
from agent.loop.compaction import (
    archive_old_tool_results,
    compact_history,
    should_compact,
)
from agent.loop.history import (
    COMPACTION_MODE_TOOL_ARCHIVE,
    LOOP_ENGINE_METADATA_VALUE,
    LoopHistory,
    client_message_metadata,
    load_loop_history,
    valid_compaction,
    valid_tool_archive_compaction,
)
from agent.loop.prompts import SUMMARY_PREFIX
from config import agent_runtime
from config.datetime_utils import utcnow
from models import ChatMessage, ChatSession, File, Project, User
from services.core.auth_service import hash_password


def _large_query_output(file_id: str, title: str, *, repeat: int = 2500) -> str:
    return json.dumps(
        {
            "status": "success",
            "data": [
                {
                    "id": file_id,
                    "title": title,
                    "file_type": "draft",
                    "content": f"{title}正文段落雾港钟声响起潮水拍打石阶。" * repeat,
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _large_skill_output(skill_id: str, skill_name: str, *, repeat: int = 800) -> str:
    return json.dumps(
        {
            "status": "success",
            "data": {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "instructions": "写作技法与节奏控制规则示例。" * repeat,
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _large_skill_resource_output(skill_id: str, skill_name: str, path: str, *, repeat: int = 800) -> str:
    return json.dumps(
        {
            "status": "success",
            "data": {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "path": path,
                "content": "去AI味词汇对照与句式拆解参考条目。" * repeat,
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _sse_events(chunks: list[str]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            lines = [line for line in block.splitlines() if line]
            event = next((line[6:].strip() for line in lines if line.startswith("event:")), None)
            data = next((line[5:].strip() for line in lines if line.startswith("data:")), None)
            if event and data:
                events.append((event, json.loads(data)))
    return events


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int]] = {}

    def get(self, key: str) -> str | None:
        entry = self.store.get(key)
        return entry[0] if entry is not None else None

    def setex(self, key: str, ttl: int, value: str) -> bool:
        self.store[key] = (value, ttl)
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                removed += 1
        return removed


@pytest.fixture
def fake_redis(monkeypatch):
    redis_client = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake-tool-archive/0")
    monkeypatch.setattr(wire_prefix_cache, "get_redis_client", lambda: redis_client)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    yield redis_client
    wire_prefix_cache.reset_wire_prefix_cache_state()


def _seed_user_project_chat(db_session: Session, suffix: str) -> tuple[User, Project, ChatSession]:
    user = User(
        email=f"tool_archive_{suffix}@example.com",
        username=f"toolarchive_{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name=f"归档测试_{suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    return user, project, chat


# ---------------------------------------------------------------------------
# 1. Zero-run below threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_normal_turn_below_threshold_never_runs_tool_archive(db_session: Session):
    """Below AGENT_LOOP_COMPACT_AT_TOKENS, Stage 1 NEVER runs and history prefix is byte-identical."""
    from agent.service import AgentService

    user, project, chat = _seed_user_project_chat(db_session, "below_thresh")
    base_time = utcnow().replace(tzinfo=None) - timedelta(minutes=20)
    big_content = "正常轮次无需压缩的章节内容。" * 250  # ~3500 chars (>2000), well below 300k tokens
    for turn in range(6):
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="user",
                content=f"第{turn + 1}轮指令",
                created_at=base_time + timedelta(seconds=turn * 10),
            )
        )
        tool_calls = [
            {
                "id": f"call_below_{turn}",
                "name": "query_files",
                "arguments": {"id": f"f-{turn}"},
                "status": "success",
                "result": [{"id": f"f-{turn}", "title": f"第{turn + 1}章", "content": big_content}],
            }
        ]
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="assistant",
                content=f"第{turn + 1}轮完成。",
                tool_calls=json.dumps(tool_calls, ensure_ascii=False),
                message_metadata=json.dumps(
                    {
                        "engine": LOOP_ENGINE_METADATA_VALUE,
                        "prompt_tokens": 50_000,
                        "display_events": [
                            {"type": "tool_call", "tool_call_index": 0},
                            {"type": "content", "content": f"第{turn + 1}轮完成。"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                created_at=base_time + timedelta(seconds=turn * 10 + 5),
            )
        )
    db_session.commit()

    baseline = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert should_compact(baseline) is False

    seen_histories: list[list[dict[str, Any]]] = []

    def fake_loop(state, **_kwargs):
        seen_histories.append(list(state.get("history_items") or []))

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "本轮回复。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _stream()

    archive_spy = patch("agent.service.archive_old_tool_results", wraps=archive_old_tool_results)
    with archive_spy as spy, patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
        async for _chunk in AgentService().process_stream(
            project_id=project.id,
            user_id=user.id,
            message="继续写下一段",
            session=db_session,
            session_id=chat.id,
        ):
            pass

    assert spy.call_count == 0
    assert seen_histories[0] == baseline.items


# ---------------------------------------------------------------------------
# 2. Stage 1 admission skips LLM & preserves recent turns, write tools, errors, small outputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stage1_tool_archive_admits_skips_llm_and_preserves_protected_items():
    """Stage 1 archives only older large successful read-only outputs and skips Stage 2 LLM call."""
    old_query_0 = _large_query_output("ch-1", "第一章 雾港", repeat=3000)
    old_query_1 = _large_query_output("ch-2", "第二章 来信", repeat=3000)
    old_skill = _large_skill_output("sk-1", "网文节奏控制", repeat=1200)
    old_resource = _large_skill_resource_output("sk-1", "网文节奏控制", "refs/pacing.md", repeat=1200)
    small_query = json.dumps(
        {"status": "success", "data": [{"id": "ch-small", "title": "短备注", "content": "只有几十字。"}]},
        ensure_ascii=False,
    )
    error_query = json.dumps(
        {"status": "error", "error": "文件不存在或读取失败：" + "原因详情" * 600, "error_type": "files_not_found"},
        ensure_ascii=False,
    )
    write_outputs = {
        name: json.dumps(
            {"status": "success", "data": {"id": f"w-{name}", "title": name, "detail": "写入结果" * 600}},
            ensure_ascii=False,
        )
        for name in ("write_chapter", "create_file", "edit_file", "delete_file", "rename_file", "update_project")
    }
    recent_query = _large_query_output("ch-recent", "第六章 近期", repeat=500)

    items: list[dict[str, Any]] = [
        # Turn 0 (older)
        {"role": "user", "content": "第1轮：读第一章并加载技能"},
        {"type": "function_call", "call_id": "c_q0", "name": "query_files", "arguments": '{"id":"ch-1"}'},
        {"type": "function_call_output", "call_id": "c_q0", "output": old_query_0},
        {"type": "function_call", "call_id": "c_sk", "name": "load_skill", "arguments": '{"name":"网文节奏控制"}'},
        {"type": "function_call_output", "call_id": "c_sk", "output": old_skill},
        {"type": "function_call", "call_id": "c_res", "name": "read_skill_resource", "arguments": '{"name":"网文节奏控制","path":"refs/pacing.md"}'},
        {"type": "function_call_output", "call_id": "c_res", "output": old_resource},
        {"role": "assistant", "content": "已读取第一章与技能。"},
        # Turn 1 (older: includes small read, error read, and ALL write tools)
        {"role": "user", "content": "第2轮：读第二章并做修改"},
        {"type": "function_call", "call_id": "c_q1", "name": "query_files", "arguments": '{"id":"ch-2"}'},
        {"type": "function_call_output", "call_id": "c_q1", "output": old_query_1},
        {"type": "function_call", "call_id": "c_small", "name": "query_files", "arguments": '{"id":"ch-small"}'},
        {"type": "function_call_output", "call_id": "c_small", "output": small_query},
        {"type": "function_call", "call_id": "c_err", "name": "query_files", "arguments": '{"id":"missing"}'},
        {"type": "function_call_output", "call_id": "c_err", "output": error_query},
    ]
    for w_name, w_out in write_outputs.items():
        items.append({"type": "function_call", "call_id": f"c_{w_name}", "name": w_name, "arguments": "{}"})
        items.append({"type": "function_call_output", "call_id": f"c_{w_name}", "output": w_out})
    items.append({"role": "assistant", "content": "第二轮全部写完。"})

    # Turns 2, 3, 4 (last 3 user turns — MUST stay completely untouched!)
    for recent_idx in range(3):
        items.extend([
            {"role": "user", "content": f"近期第{recent_idx + 1}轮"},
            {"type": "function_call", "call_id": f"c_recent_{recent_idx}", "name": "query_files", "arguments": '{"id":"ch-recent"}'},
            {"type": "function_call_output", "call_id": f"c_recent_{recent_idx}", "output": recent_query},
            {"role": "assistant", "content": f"近期第{recent_idx + 1}轮回复。"},
        ])

    history = LoopHistory(
        items=items,
        last_prompt_tokens=340_000,
        last_message_id="msg-last",
        author_messages=["第1轮：读第一章并加载技能", "近期第3轮"],
    )

    chat_mock = AsyncMock(side_effect=AssertionError("Stage 2 chat_completions must not be called"))
    resp_mock = AsyncMock(side_effect=AssertionError("Stage 2 responses must not be called"))
    with (
        patch("agent.core.llm_failover.acreate_chat_completion", chat_mock),
        patch("agent.core.llm_failover.acreate_response", resp_mock),
    ):
        outcome = await compact_history(
            history,
            system_prompt="静态系统提示",
            session_id="sess-stage1",
            turn_input_tokens=5_000,
        )

    assert chat_mock.await_count == 0
    assert resp_mock.await_count == 0
    assert outcome.usage is None
    assert outcome.skipped is None
    assert outcome.record is not None
    assert outcome.items is not None
    assert outcome.record["mode"] == COMPACTION_MODE_TOOL_ARCHIVE
    assert outcome.record["archived_through_message_id"] == "msg-last"
    assert outcome.record["archived_count"] == 4
    assert outcome.record["archived_call_ids"] == ["c_q0", "c_sk", "c_res", "c_q1"]
    assert outcome.record["saved_tokens"] >= agent_runtime.AGENT_LOOP_TOOL_ARCHIVE_MIN_SAVINGS_TOKENS
    assert outcome.record["after_tokens"] + 5_000 <= agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS
    assert outcome.archived_file_ids == {"ch-1", "ch-2"}

    outputs_by_call = {
        item["call_id"]: item["output"]
        for item in outcome.items
        if item.get("type") == "function_call_output"
    }
    # Archived receipts preserve structured metadata & re-read guidance.
    q0_receipt = json.loads(outputs_by_call["c_q0"])
    assert q0_receipt["status"] == "success"
    assert q0_receipt["archived"] is True
    assert q0_receipt["tool_name"] == "query_files"
    assert q0_receipt["files"] == [{"id": "ch-1", "title": "第一章 雾港"}]
    assert q0_receipt["original_chars"] == len(old_query_0)
    assert "read_files" in q0_receipt["note"]

    sk_receipt = json.loads(outputs_by_call["c_sk"])
    assert sk_receipt["archived"] is True
    assert sk_receipt["tool_name"] == "load_skill"
    assert sk_receipt["skill_id"] == "sk-1"
    assert sk_receipt["skill_name"] == "网文节奏控制"
    assert "load_skill" in sk_receipt["note"]

    res_receipt = json.loads(outputs_by_call["c_res"])
    assert res_receipt["archived"] is True
    assert res_receipt["tool_name"] == "read_skill_resource"
    assert res_receipt["path"] == "refs/pacing.md"
    assert "read_skill_resource" in res_receipt["note"]

    # Protected items are byte-for-byte untouched!
    assert outputs_by_call["c_small"] == small_query
    assert outputs_by_call["c_err"] == error_query
    for w_name, w_out in write_outputs.items():
        assert outputs_by_call[f"c_{w_name}"] == w_out
    for recent_idx in range(3):
        assert outputs_by_call[f"c_recent_{recent_idx}"] == recent_query

    # Idempotency: re-running Stage 1 on already-archived items archives nothing.
    second_pass = archive_old_tool_results(
        LoopHistory(items=outcome.items, last_message_id="msg-last"),
        turn_input_tokens=5_000,
    )
    assert second_pass.archived_count == 0
    assert second_pass.admitted is False


# ---------------------------------------------------------------------------
# 3. Deterministic DB replay via load_loop_history
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_db_replay_produces_byte_identical_archived_outputs_and_handles_cumulative_archives(
    db_session: Session,
):
    """Cold DB replay matches Stage 1 receipts byte-for-byte, supports provider call_ids & cumulative archives."""
    user, project, chat = _seed_user_project_chat(db_session, "db_replay")
    base_time = utcnow().replace(tzinfo=None) - timedelta(minutes=30)
    big_prose_1 = "第一章雾港旧稿正文。" * 2500
    big_prose_2 = "第二章来信旧稿正文。" * 2500
    big_prose_recent = "近期章节正文保留不动。" * 600

    rows_meta: list[ChatMessage] = []
    # 5 turns: turns 0 & 1 are older, turns 2..4 are recent (last 3 turns).
    for idx in range(5):
        u = ChatMessage(
            session_id=chat.id,
            role="user",
            content=f"作者第{idx + 1}轮",
            created_at=base_time + timedelta(seconds=idx * 20),
        )
        db_session.add(u)
        db_session.flush()
        if idx == 0:
            calls = [
                {
                    "id": "prov_call_0",
                    "name": "query_files",
                    "arguments": {"id": "file-1"},
                    "status": "success",
                    "result": [{"id": "file-1", "title": "第一章", "content": big_prose_1}],
                },
                {
                    "id": "prov_call_write_0",
                    "name": "edit_file",
                    "arguments": {"id": "file-1"},
                    "status": "success",
                    "result": {"id": "file-1", "title": "第一章", "edits_applied": 1, "detail": "写回执" * 500},
                },
            ]
            events = [
                {"type": "tool_call", "tool_call_index": 0},
                {"type": "tool_call", "tool_call_index": 1},
                {"type": "content", "content": "第1轮完成。"},
            ]
        elif idx == 1:
            calls = [
                {
                    "id": "prov_call_1",
                    "name": "query_files",
                    "arguments": {"id": "file-2"},
                    "status": "success",
                    "result": [{"id": "file-2", "title": "第二章", "content": big_prose_2}],
                }
            ]
            events = [{"type": "tool_call", "tool_call_index": 0}, {"type": "content", "content": "第2轮完成。"}]
        else:
            calls = [
                {
                    "id": f"prov_call_{idx}",
                    "name": "query_files",
                    "arguments": {"id": f"file-r{idx}"},
                    "status": "success",
                    "result": [{"id": f"file-r{idx}", "title": f"近章{idx}", "content": big_prose_recent}],
                }
            ]
            events = [{"type": "tool_call", "tool_call_index": 0}, {"type": "content", "content": f"第{idx + 1}轮完成。"}]
        a = ChatMessage(
            session_id=chat.id,
            role="assistant",
            content=f"第{idx + 1}轮完成。",
            tool_calls=json.dumps(calls, ensure_ascii=False),
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "prompt_tokens": 330_000 if idx == 4 else 100_000,
                    "display_events": events,
                },
                ensure_ascii=False,
            ),
            created_at=base_time + timedelta(seconds=idx * 20 + 10),
        )
        db_session.add(a)
        db_session.flush()
        rows_meta.append(a)
    db_session.commit()

    # Load pre-archive history and run Stage 1.
    pre_history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    stage1 = archive_old_tool_results(pre_history, turn_input_tokens=1_000, min_savings_tokens=10_000)
    assert stage1.admitted is True
    assert stage1.record is not None
    assert stage1.archived_count == 2
    assert stage1.archived_file_ids == {"file-1", "file-2"}

    # Persist Turn 6 with the Stage 1 tool_archive record.
    turn6_u = ChatMessage(
        session_id=chat.id,
        role="user",
        content="作者第6轮",
        created_at=base_time + timedelta(seconds=120),
    )
    turn6_a = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="第6轮完成。",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "prompt_tokens": 90_000,
                "compaction": stage1.record,
                "display_events": [{"type": "content", "content": "第6轮完成。"}],
            },
            ensure_ascii=False,
        ),
        created_at=base_time + timedelta(seconds=130),
    )
    db_session.add_all([turn6_u, turn6_a])
    db_session.commit()

    # Cold DB replay MUST reproduce the exact same items for Turns 0..4 byte-for-byte!
    replayed = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert replayed.compacted is False  # tool_archive is not a summary cut
    assert replayed.items[: len(stage1.items)] == stage1.items
    assert replayed.last_prompt_tokens == 90_000
    # edit_file in Turn 0 only returns a diff receipt (no full content), so BOTH file-1 and file-2
    # stay in archived_file_ids until a later non-archived full query_files re-reads them!
    assert replayed.archived_file_ids == {"file-1", "file-2"}

    # Also verify provider call_id matching (simulating Stage 1 running from wire_prefix_cache on Turn 7,
    # archiving Turn 2's prov_call_2) + cumulative archiving across two tool_archive records!
    turn7_u = ChatMessage(
        session_id=chat.id,
        role="user",
        content="作者第7轮",
        created_at=base_time + timedelta(seconds=140),
    )
    turn7_a = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="第7轮完成。",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "prompt_tokens": 80_000,
                "compaction": {
                    "mode": COMPACTION_MODE_TOOL_ARCHIVE,
                    "archived_through_message_id": turn6_a.id,
                    "archived_call_ids": ["prov_call_2"],
                    "archived_count": 1,
                    "saved_tokens": 40_000,
                    "created_at": utcnow().isoformat(),
                },
                "display_events": [{"type": "content", "content": "第7轮完成。"}],
            },
            ensure_ascii=False,
        ),
        created_at=base_time + timedelta(seconds=150),
    )
    db_session.add_all([turn7_u, turn7_a])
    db_session.commit()

    replayed2 = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    outputs_by_call = {
        item["call_id"]: json.loads(item["output"])
        for item in replayed2.items
        if item.get("type") == "function_call_output"
    }
    # Both Turn 6's synthetic call_ids AND Turn 7's provider call_id ("prov_call_2") are archived!
    assert outputs_by_call[f"h{rows_meta[0].id}_0"]["archived"] is True
    assert outputs_by_call[f"h{rows_meta[1].id}_0"]["archived"] is True
    assert outputs_by_call[f"h{rows_meta[2].id}_0"]["archived"] is True
    assert outputs_by_call[f"h{rows_meta[2].id}_0"]["files"] == [{"id": "file-r2", "title": "近章2"}]
    # Turn 3 & 4 remain unarchived.
    assert "archived" not in outputs_by_call[f"h{rows_meta[3].id}_0"]
    assert "archived" not in outputs_by_call[f"h{rows_meta[4].id}_0"]


# ---------------------------------------------------------------------------
# 4. Stage 2 fallback receives archived history when savings or headroom is insufficient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stage2_fallback_receives_archived_history_when_savings_insufficient():
    """When Stage 1 archives outputs but saves < MIN_SAVINGS_TOKENS, Stage 2 gets the smaller archived history."""
    medium_read = _large_query_output("ch-old", "旧章", repeat=200)  # ~4.5k chars -> ~3k tokens (< 30k savings)
    heavy_prose = "这是无法归档的纯对话长文本内容，需要靠 Stage 2 交接摘要压缩。" * 3000

    items: list[dict[str, Any]] = [
        {"role": "user", "content": "第1轮"},
        {"type": "function_call", "call_id": "c_old", "name": "query_files", "arguments": '{"id":"ch-old"}'},
        {"type": "function_call_output", "call_id": "c_old", "output": medium_read},
        {"role": "assistant", "content": heavy_prose},
        {"role": "user", "content": "近轮1"},
        {"role": "assistant", "content": "回复1"},
        {"role": "user", "content": "近轮2"},
        {"role": "assistant", "content": "回复2"},
        {"role": "user", "content": "近轮3"},
        {"role": "assistant", "content": "回复3"},
    ]
    history = LoopHistory(
        items=items,
        last_prompt_tokens=320_000,
        last_message_id="msg-10",
        author_messages=["第1轮", "近轮1", "近轮2", "近轮3"],
    )

    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Stage2 交接摘要"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=150_000, completion_tokens=200, total_tokens=150_200, prompt_tokens_details=None),
    )
    mock_call = AsyncMock(return_value=(completion, SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")))
    with patch("agent.core.llm_failover.acreate_chat_completion", mock_call):
        outcome = await compact_history(
            history,
            system_prompt="系统提示",
            session_id="sess-fallback-savings",
            turn_input_tokens=1_000,
        )

    assert mock_call.await_count == 1
    assert outcome.record is not None
    assert outcome.record["summary"] == "Stage2 交接摘要"
    assert outcome.record["covers_through_message_id"] == "msg-10"
    # Stage 2 received the archived receipt for c_old instead of raw prose!
    sent_messages = mock_call.await_args.kwargs["messages"]
    tool_msgs = [m for m in sent_messages if m.get("role") == "tool" and m.get("tool_call_id") == "c_old"]
    assert len(tool_msgs) == 1
    parsed_tool = json.loads(tool_msgs[0]["content"])
    assert parsed_tool["archived"] is True
    assert parsed_tool["files"] == [{"id": "ch-old", "title": "旧章"}]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stage2_fallback_when_after_tokens_plus_turn_input_still_exceeds_threshold():
    """Even with >= 30k savings, if after_tokens + turn_input_tokens > COMPACT_AT_TOKENS, Stage 1 does NOT admit."""
    old_read = _large_query_output("ch-old", "旧章", repeat=4000)  # ~60k tokens saved
    huge_recent_prose = "近期三轮里的大段正文无法由 Stage 1 归档。" * 20_000  # ~260k tokens in recent turns

    items: list[dict[str, Any]] = [
        {"role": "user", "content": "第1轮"},
        {"type": "function_call", "call_id": "c_old", "name": "query_files", "arguments": '{"id":"ch-old"}'},
        {"type": "function_call_output", "call_id": "c_old", "output": old_read},
        {"role": "assistant", "content": "旧回复"},
        {"role": "user", "content": "近轮1"},
        {"role": "assistant", "content": huge_recent_prose},
        {"role": "user", "content": "近轮2"},
        {"role": "assistant", "content": "近回复2"},
        {"role": "user", "content": "近轮3"},
        {"role": "assistant", "content": "近回复3"},
    ]
    history = LoopHistory(
        items=items,
        last_prompt_tokens=360_000,
        last_message_id="msg-20",
        author_messages=["第1轮", "近轮1", "近轮2", "近轮3"],
    )

    stage1 = archive_old_tool_results(history, turn_input_tokens=80_000)
    assert stage1.archived_count == 1
    assert stage1.saved_tokens >= agent_runtime.AGENT_LOOP_TOOL_ARCHIVE_MIN_SAVINGS_TOKENS
    assert stage1.after_tokens + 80_000 > agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS
    assert stage1.admitted is False

    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="超阈值回落摘要"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=240_000, completion_tokens=150, total_tokens=240_150, prompt_tokens_details=None),
    )
    mock_call = AsyncMock(return_value=(completion, SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")))
    with patch("agent.core.llm_failover.acreate_chat_completion", mock_call):
        outcome = await compact_history(
            history,
            system_prompt="系统提示",
            session_id="sess-fallback-headroom",
            turn_input_tokens=80_000,
        )

    assert mock_call.await_count == 1
    assert outcome.record is not None
    assert outcome.record["summary"] == "超阈值回落摘要"


# ---------------------------------------------------------------------------
# 5. Backward compatibility with LLM summary records & client_message_metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_backward_compatibility_with_llm_summary_and_client_metadata(db_session: Session):
    """Existing LLM summary records work identically; tool_archive records are hidden from browser metadata."""
    user, project, chat = _seed_user_project_chat(db_session, "compat")
    base_time = utcnow().replace(tzinfo=None) - timedelta(minutes=15)

    m1 = ChatMessage(session_id=chat.id, role="user", content="最初的需求", created_at=base_time)
    m2 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="最初回复",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "compaction": {
                    "mode": COMPACTION_MODE_TOOL_ARCHIVE,
                    "archived_through_message_id": "older-id",
                    "archived_call_ids": ["old_call_1"],
                    "archived_count": 1,
                    "saved_tokens": 50_000,
                    "created_at": "2026-11-26T00:00:00",
                },
            },
            ensure_ascii=False,
        ),
        created_at=base_time + timedelta(seconds=10),
    )
    m3 = ChatMessage(
        session_id=chat.id,
        role="user",
        content="第二轮需求",
        created_at=base_time + timedelta(seconds=20),
    )
    db_session.add_all([m1, m2, m3])
    db_session.flush()
    m4 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="压缩后的新回复",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "compaction": {
                    "summary": "历史摘要正文",
                    "covers_through_message_id": m2.id,
                    "kept_author_messages": ["最初的需求"],
                    "created_at": "2026-11-26T00:05:00",
                },
                "display_events": [
                    {"type": "history_compacted", "data": {}},
                    {"type": "content", "content": "压缩后的新回复"},
                ],
            },
            ensure_ascii=False,
        ),
        created_at=base_time + timedelta(seconds=30),
    )
    db_session.add(m4)
    db_session.commit()

    # valid_compaction only accepts LLM summary records, never tool_archive.
    tool_archive_meta = json.loads(m2.message_metadata or "{}")
    assert valid_compaction(tool_archive_meta) is None
    assert valid_tool_archive_compaction(tool_archive_meta) is not None

    summary_meta = json.loads(m4.message_metadata or "{}")
    assert valid_compaction(summary_meta) is not None
    assert valid_tool_archive_compaction(summary_meta) is None

    # client_message_metadata strips tool_archive compaction completely, keeps summary without kept_author_messages.
    client_archive = json.loads(client_message_metadata(m2.message_metadata) or "{}")
    assert "compaction" not in client_archive
    assert client_archive["engine"] == LOOP_ENGINE_METADATA_VALUE

    client_summary = json.loads(client_message_metadata(m4.message_metadata) or "{}")
    assert client_summary["compaction"]["summary"] == "历史摘要正文"
    assert "kept_author_messages" not in client_summary["compaction"]

    # load_loop_history cuts at LLM summary boundary m2.id and replays kept + summary + post-cut rows.
    loaded = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert loaded.compacted is True
    assert loaded.items[0] == {"role": "user", "content": "最初的需求"}
    assert loaded.items[1] == {"role": "user", "content": f"{SUMMARY_PREFIX}历史摘要正文"}
    assert loaded.items[2] == {"role": "user", "content": "第二轮需求"}
    assert loaded.items[3] == {"role": "assistant", "content": "压缩后的新回复"}


# ---------------------------------------------------------------------------
# 6. AGENT_LOOP_TOOL_ARCHIVE_ENABLED=false disables Stage 1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_archive_disabled_flag_bypasses_stage1(monkeypatch):
    """When AGENT_LOOP_TOOL_ARCHIVE_ENABLED is False, Stage 1 is skipped and Stage 2 runs directly."""
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_ENABLED", False)
    assert agent_runtime._get_bool_env("NON_EXISTENT_FLAG_XYZ", True) is True
    monkeypatch.setenv("TEST_BOOL_FLAG_XYZ", "false")
    assert agent_runtime._get_bool_env("TEST_BOOL_FLAG_XYZ", True) is False
    monkeypatch.setenv("TEST_BOOL_FLAG_XYZ", "1")
    assert agent_runtime._get_bool_env("TEST_BOOL_FLAG_XYZ", False) is True

    old_read = _large_query_output("ch-1", "第一章", repeat=4000)
    items: list[dict[str, Any]] = [
        {"role": "user", "content": "第1轮"},
        {"type": "function_call", "call_id": "c_q0", "name": "query_files", "arguments": '{"id":"ch-1"}'},
        {"type": "function_call_output", "call_id": "c_q0", "output": old_read},
        {"role": "assistant", "content": "回复1"},
        {"role": "user", "content": "第2轮"},
        {"role": "assistant", "content": "回复2"},
        {"role": "user", "content": "第3轮"},
        {"role": "assistant", "content": "回复3"},
        {"role": "user", "content": "第4轮"},
        {"role": "assistant", "content": "回复4"},
    ]
    history = LoopHistory(
        items=items,
        last_prompt_tokens=340_000,
        last_message_id="msg-dis",
        author_messages=["第1轮", "第4轮"],
    )

    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="禁用Stage1后的摘要"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=120_000, completion_tokens=100, total_tokens=120_100, prompt_tokens_details=None),
    )
    mock_call = AsyncMock(return_value=(completion, SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")))
    with patch("agent.core.llm_failover.acreate_chat_completion", mock_call):
        outcome = await compact_history(
            history,
            system_prompt="系统提示",
            session_id="sess-disabled",
        )

    assert mock_call.await_count == 1
    assert outcome.record is not None
    assert outcome.record["summary"] == "禁用Stage1后的摘要"
    sent_messages = mock_call.await_args.kwargs["messages"]
    tool_msgs = [m for m in sent_messages if m.get("role") == "tool" and m.get("tool_call_id") == "c_q0"]
    assert len(tool_msgs) == 1
    assert '"archived":true' not in tool_msgs[0]["content"]


# ---------------------------------------------------------------------------
# 7. End-to-end AgentService integration: SSE notices, snapshot drop, wire_prefix_cache, retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_service_stage1_tool_archive_sse_snapshots_and_wire_prefix_cache(
    db_session: Session,
    fake_redis,
    monkeypatch,
):
    """End-to-end: Stage 1 emits transient thinking (no empty history_compacted), drops archived file
    snapshots, persists tool_archive metadata, and caches post-archive wire_prefix for Turn N+1."""
    from agent.service import AgentService

    user, project, chat = _seed_user_project_chat(db_session, "svc_e2e")
    old_file = File(
        project_id=project.id,
        title="第一章 雾港",
        file_type="draft",
        content="旧章全文内容雾港钟声响起。" * 4000,
    )
    recent_file = File(
        project_id=project.id,
        title="第五章 近期",
        file_type="draft",
        content="近期章节全文内容。" * 500,
    )
    focus_file = File(
        project_id=project.id,
        title="第六章 焦点",
        file_type="draft",
        content="焦点章节内容。",
    )
    db_session.add_all([old_file, recent_file, focus_file])
    db_session.commit()
    db_session.refresh(old_file)
    db_session.refresh(recent_file)
    db_session.refresh(focus_file)

    base_time = utcnow().replace(tzinfo=None) - timedelta(minutes=25)
    for idx in range(5):
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="user",
                content=f"作者第{idx + 1}轮",
                created_at=base_time + timedelta(seconds=idx * 20),
            )
        )
        target_file = old_file if idx < 2 else recent_file
        tool_calls = [
            {
                "id": f"svc_call_{idx}",
                "name": "query_files",
                "arguments": {"id": target_file.id},
                "status": "success",
                "result": [
                    {
                        "id": target_file.id,
                        "title": target_file.title,
                        "file_type": "draft",
                        "content": target_file.content,
                        "updated_at": target_file.updated_at.isoformat(),
                    }
                ],
            }
        ]
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="assistant",
                content=f"第{idx + 1}轮完成。",
                tool_calls=json.dumps(tool_calls, ensure_ascii=False),
                message_metadata=json.dumps(
                    {
                        "engine": LOOP_ENGINE_METADATA_VALUE,
                        "prompt_tokens": 340_000 if idx == 4 else 120_000,
                        "display_events": [
                            {"type": "tool_call", "tool_call_index": 0},
                            {"type": "content", "content": f"第{idx + 1}轮完成。"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                created_at=base_time + timedelta(seconds=idx * 20 + 10),
            )
        )
    db_session.commit()

    captured_states: list[dict[str, Any]] = []
    snapshot_checks: list[dict[str, bool]] = []
    compact_spy = AsyncMock(side_effect=AssertionError("Stage 2 compact_history must not be called"))

    def fake_loop(state, **_kwargs):
        req_ctx = state["request_context"]
        captured_states.append({"history_items": list(state.get("history_items") or [])})
        snapshot_checks.append(
            {
                "old_file_seen": req_ctx.has_seen_file(old_file.id),
                "recent_file_seen": req_ctx.has_seen_file(recent_file.id),
                "focus_file_seen": req_ctx.has_seen_file(focus_file.id),
            }
        )
        # Simulate SDK completed wire items starting with the post-archive history_items.
        state["run_items"] = [
            *state["history_items"],
            {"role": "user", "content": state["user_message"]},
            {"role": "assistant", "content": "归档后继续完成的回复。"},
        ]
        state["run_meter"].last_prompt_tokens = 110_000
        state["run_meter"].last_served_provider_key = "minimax:MiniMax-M3.1-Flash:primary:responses"

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "归档后继续完成的回复。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _stream()

    fake_provider = SimpleNamespace(
        name="minimax",
        model="MiniMax-M3.1-Flash",
        key_slot="primary",
        uses_responses_api=True,
    )
    monkeypatch.setattr(
        "agent.service.llm_failover.aordered_providers",
        AsyncMock(return_value=[fake_provider]),
    )

    chunks: list[str] = []
    with (
        patch("agent.service.compact_history", compact_spy),
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop),
    ):
        async for chunk in AgentService().process_stream(
            project_id=project.id,
            user_id=user.id,
            message="请继续写第六章",
            session=db_session,
            session_id=chat.id,
            metadata={"current_file_id": focus_file.id, "current_file_type": "draft", "current_file_title": focus_file.title},
        ):
            chunks.append(chunk)

    assert compact_spy.await_count == 0
    events = _sse_events(chunks)
    event_types = [etype for etype, _ in events]
    # No broken empty history_compacted event or running compaction spinner!
    assert "history_compacted" not in event_types
    assert not any(
        etype == "thinking" and data.get("step") == "history_compaction" for etype, data in events
    )
    assert any(
        etype == "thinking" and "归档" in str(data.get("message") or "") for etype, data in events
    )

    # Archived old_file snapshot was dropped, while untouched recent_file & focus_file stay seen!
    assert snapshot_checks[0] == {
        "old_file_seen": False,
        "recent_file_seen": True,
        "focus_file_seen": True,
    }

    # DB metadata persisted cleanly with tool_archive record and NO history_compacted display_event.
    db_session.expire_all()
    rows = db_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == chat.id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
    ).all()
    latest_meta = json.loads(rows[0].message_metadata or "{}")
    assert latest_meta["compaction"]["mode"] == COMPACTION_MODE_TOOL_ARCHIVE
    assert latest_meta["compaction"]["archived_count"] == 2
    assert all(ev.get("type") != "history_compacted" for ev in latest_meta.get("display_events") or [])
    # Browser-facing metadata omits internal tool_archive compaction record.
    assert "compaction" not in json.loads(client_message_metadata(rows[0].message_metadata) or "{}")

    # wire_prefix_cache stored post-archive items cleanly!
    assert f"agent:wire_prefix:{chat.id}" in fake_redis.store

    # Turn N+1 hits wire_prefix_cache with the exact post-archive prefix and does NOT compact again!
    with (
        patch("agent.service.compact_history", compact_spy),
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop),
    ):
        async for _chunk in AgentService().process_stream(
            project_id=project.id,
            user_id=user.id,
            message="再润色一下第六章结尾",
            session=db_session,
            session_id=chat.id,
            metadata={"current_file_id": focus_file.id, "current_file_type": "draft", "current_file_title": focus_file.title},
        ):
            pass

    turn1_post_archive = captured_states[0]["history_items"]
    turn2_history = captured_states[1]["history_items"]
    assert turn2_history[: len(turn1_post_archive)] == turn1_post_archive
    assert compact_spy.await_count == 0
    # On Turn N+1, old_file is STILL not carried over into snapshots!
    assert snapshot_checks[1]["old_file_seen"] is False


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_service_stage1_tool_archive_error_and_retry_carryover(db_session: Session):
    """If the model errors after Stage 1 tool_archive, retry_unanswered carries the archive record cleanly."""
    from agent.service import AgentService

    user, project, chat = _seed_user_project_chat(db_session, "svc_retry")
    base_time = utcnow().replace(tzinfo=None) - timedelta(minutes=25)
    big_prose = "旧章正文内容雾港钟声响起。" * 4000
    for idx in range(5):
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="user",
                content=f"作者第{idx + 1}轮",
                created_at=base_time + timedelta(seconds=idx * 20),
            )
        )
        tool_calls = [
            {
                "id": f"retry_call_{idx}",
                "name": "query_files",
                "arguments": {"id": f"rf-{idx}"},
                "status": "success",
                "result": [{"id": f"rf-{idx}", "title": f"第{idx + 1}章", "content": big_prose if idx < 2 else "短内容"}],
            }
        ]
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="assistant",
                content=f"第{idx + 1}轮完成。",
                tool_calls=json.dumps(tool_calls, ensure_ascii=False),
                message_metadata=json.dumps(
                    {
                        "engine": LOOP_ENGINE_METADATA_VALUE,
                        "prompt_tokens": 340_000 if idx == 4 else 100_000,
                        "display_events": [
                            {"type": "tool_call", "tool_call_index": 0},
                            {"type": "content", "content": f"第{idx + 1}轮完成。"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                created_at=base_time + timedelta(seconds=idx * 20 + 10),
            )
        )
    db_session.commit()

    def error_loop(_state, **_kwargs):
        async def _stream():
            yield StreamEvent(type=StreamEventType.ERROR, data={"error": "synthetic failure"})

        return _stream()

    with (
        patch("agent.service.run_loop_agent_streaming", side_effect=error_loop),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        async for _chunk in AgentService().process_stream(
            project_id=project.id,
            user_id=user.id,
            message="重试这一轮",
            session=db_session,
            session_id=chat.id,
        ):
            pass

    db_session.expire_all()
    rows = db_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == chat.id).order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()
    assert rows[-1].role == "user"
    assert rows[-1].content == "重试这一轮"
    carried = json.loads(rows[-1].message_metadata or "{}")
    assert carried["compaction"]["mode"] == COMPACTION_MODE_TOOL_ARCHIVE

    seen_retry_items: list[list[dict[str, Any]]] = []

    def success_loop(state, **_kwargs):
        seen_retry_items.append(list(state.get("history_items") or []))

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "重试成功回复。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _stream()

    compact_spy = AsyncMock(side_effect=AssertionError("Retry should reuse carried tool_archive without compacting again"))
    with (
        patch("agent.service.compact_history", compact_spy),
        patch("agent.service.run_loop_agent_streaming", side_effect=success_loop),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        async for _chunk in AgentService().process_stream(
            project_id=project.id,
            user_id=user.id,
            message="重试这一轮",
            session=db_session,
            session_id=chat.id,
            metadata={"retry_unanswered": True},
        ):
            pass

    assert compact_spy.await_count == 0
    retry_outputs = [
        json.loads(item["output"])
        for item in seen_retry_items[0]
        if item.get("type") == "function_call_output"
    ]
    assert retry_outputs[0]["archived"] is True
    assert retry_outputs[1]["archived"] is True
    assert "archived" not in retry_outputs[2]

    db_session.expire_all()
    final_rows = db_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == chat.id).order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()
    assert final_rows[-1].role == "assistant"
    final_meta = json.loads(final_rows[-1].message_metadata or "{}")
    assert final_meta["compaction"]["mode"] == COMPACTION_MODE_TOOL_ARCHIVE


# ---------------------------------------------------------------------------
# 8. Regression tests for review findings (P1-1..P1-4, P2-1, P2-2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_p1_1_synthetic_intra_turn_user_messages_do_not_break_keep_recent_turns():
    """Multi-run chapter-write turns in wire_prefix_cache inject synthetic role=user continuations
    ([系统提醒], [System note], AGENT_DIRECTIVE_HEADER, list content blocks). They MUST NOT consume
    the KEEP_RECENT_TURNS=3 real user turn budget."""
    from agent.core.message_manager import AGENT_DIRECTIVE_HEADER

    old_read = _large_query_output("ch-old", "第一章 旧章", repeat=3500)
    recent_read_1 = _large_query_output("ch-r1", "近期一", repeat=1500)
    recent_read_2 = _large_query_output("ch-r2", "近期二", repeat=1500)
    recent_read_3 = _large_query_output("ch-r3", "近期三", repeat=1500)

    items: list[dict[str, Any]] = [
        # Real user turn 0 (older -> eligible)
        {"role": "user", "content": "第1轮：读第一章"},
        {"type": "function_call", "call_id": "c_old", "name": "query_files", "arguments": '{"id":"ch-old"}'},
        {"type": "function_call_output", "call_id": "c_old", "output": old_read},
        {"role": "assistant", "content": "第1轮完成。"},
        # Real user turn 1 (recent #1 of 3 — with multi-run synthetic user continuations!)
        {"role": "user", "content": [{"type": "input_text", "text": "<turn_context>上下文</turn_context>\n\n写近期一"}]},
        {"role": "user", "content": f"{AGENT_DIRECTIVE_HEADER}\n\n## 截断后的本轮交付"},
        {"type": "function_call", "call_id": "c_r1", "name": "query_files", "arguments": '{"id":"ch-r1"}'},
        {"type": "function_call_output", "call_id": "c_r1", "output": recent_read_1},
        {"role": "assistant", "content": "<file>近期一正文</file>"},
        {"role": "user", "content": "[系统提醒] 上一次回复写完的正文已保存：《近期一》。这是系统的续跑说明。"},
        {"role": "assistant", "content": "自查完毕。"},
        # Real user turn 2 (recent #2 of 3 — with English [System note] + list-block synthetic continuation)
        {"role": "user", "content": "写近期二"},
        {"type": "function_call", "call_id": "c_r2", "name": "query_files", "arguments": '{"id":"ch-r2"}'},
        {"type": "function_call_output", "call_id": "c_r2", "output": recent_read_2},
        {"role": "assistant", "content": "<file>近期二正文</file>"},
        {"role": "user", "content": "[System note] The body written in the previous response is saved."},
        {"role": "user", "content": [{"type": "input_text", "text": "[系统提醒] 请立即停止调用工具并收尾。"}]},
        {"role": "assistant", "content": "近期二完毕。"},
        # Real user turn 3 (recent #3 of 3)
        {"role": "user", "content": "写近期三"},
        {"type": "function_call", "call_id": "c_r3", "name": "query_files", "arguments": '{"id":"ch-r3"}'},
        {"type": "function_call_output", "call_id": "c_r3", "output": recent_read_3},
        {"role": "assistant", "content": "近期三完毕。"},
    ]
    history = LoopHistory(items=items, last_message_id="msg-wire", last_prompt_tokens=320_000)

    res = archive_old_tool_results(history, keep_recent_turns=3, min_savings_tokens=10_000)
    assert res.archived_call_ids == ["c_old"]
    outputs_by_call = {
        item["call_id"]: item["output"]
        for item in res.items
        if item.get("type") == "function_call_output"
    }
    assert json.loads(outputs_by_call["c_old"])["archived"] is True
    # All 3 real recent turns stay completely untouched despite 4 synthetic user items!
    assert outputs_by_call["c_r1"] == recent_read_1
    assert outputs_by_call["c_r2"] == recent_read_2
    assert outputs_by_call["c_r3"] == recent_read_3


@pytest.mark.unit
def test_p1_2_partial_or_truncated_query_files_does_not_clear_archived_file_ids(db_session: Session):
    """A partial (content_partial=True) or truncated (content_truncated=True) query_files after
    archiving MUST NOT clear archived_file_ids; only a full non-truncated non-partial read clears it."""
    user, project, chat = _seed_user_project_chat(db_session, "p1_2_partial")
    base_time = utcnow().replace(tzinfo=None) - timedelta(minutes=20)
    full_prose = "雾港第一章完整正文。" * 2500

    # Turn 0: full read of f-partial, f-trunc, and f-full-again
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="第1轮", created_at=base_time))
    a0 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已读三章。",
        tool_calls=json.dumps(
            [
                {
                    "id": "c_p",
                    "name": "query_files",
                    "arguments": {"id": "f-partial"},
                    "status": "success",
                    "result": [{"id": "f-partial", "title": "章P", "file_type": "draft", "content": full_prose}],
                },
                {
                    "id": "c_t",
                    "name": "query_files",
                    "arguments": {"id": "f-trunc"},
                    "status": "success",
                    "result": [{"id": "f-trunc", "title": "章T", "file_type": "draft", "content": full_prose}],
                },
                {
                    "id": "c_f",
                    "name": "query_files",
                    "arguments": {"id": "f-full"},
                    "status": "success",
                    "result": [{"id": "f-full", "title": "章F", "file_type": "draft", "content": full_prose}],
                },
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE}, ensure_ascii=False),
        created_at=base_time + timedelta(seconds=10),
    )
    db_session.add(a0)
    db_session.flush()

    # Turn 1: archives Turn 0's reads, and does partial read of f-partial, truncated read of f-trunc,
    # and full read of f-full.
    db_session.add(
        ChatMessage(session_id=chat.id, role="user", content="第2轮", created_at=base_time + timedelta(seconds=20))
    )
    a1 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="第2轮完成。",
        tool_calls=json.dumps(
            [
                {
                    "id": "c_p_range",
                    "name": "query_files",
                    "arguments": {"id": "f-partial", "tail_chars": 500},
                    "status": "success",
                    "result": [
                        {
                            "id": "f-partial",
                            "title": "章P",
                            "file_type": "draft",
                            "content": "末尾500字片段",
                            "content_partial": True,
                        }
                    ],
                },
                {
                    "id": "c_t_trunc",
                    "name": "query_files",
                    "arguments": {"id": "f-trunc"},
                    "status": "success",
                    "result": [
                        {
                            "id": "f-trunc",
                            "title": "章T",
                            "file_type": "draft",
                            "content": "被截断的片段…",
                            "content_truncated": True,
                        }
                    ],
                },
                {
                    "id": "c_f_full",
                    "name": "query_files",
                    "arguments": {"id": "f-full"},
                    "status": "success",
                    "result": [{"id": "f-full", "title": "章F", "file_type": "draft", "content": full_prose}],
                },
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "compaction": {
                    "mode": COMPACTION_MODE_TOOL_ARCHIVE,
                    "archived_through_message_id": a0.id,
                    "archived_call_ids": ["c_p", "c_t", "c_f"],
                    "archived_count": 3,
                    "saved_tokens": 50_000,
                    "created_at": utcnow().isoformat(),
                },
            },
            ensure_ascii=False,
        ),
        created_at=base_time + timedelta(seconds=30),
    )
    db_session.add(a1)
    db_session.commit()

    loaded = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert loaded.archived_file_ids == {"f-partial", "f-trunc"}


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_p1_3_skill_only_archive_and_recent_reread_preserve_valid_snapshots(db_session: Session):
    """(a) Archiving only load_skill/read_skill_resource MUST NOT drop carried recent file snapshots;
    (b) Archiving file X in an older turn when X was re-read in full in a recent turn keeps X out of
    archived_file_ids; (c) Unidentified query_files archive sets archived_unidentified_query_files=True."""
    from agent.service import AgentService

    # Unit check (b) + (c) on archive_old_tool_results:
    old_x = _large_query_output("ch-x", "章节X", repeat=3000)
    recent_x_full = _large_query_output("ch-x", "章节X", repeat=500)
    old_y = _large_query_output("ch-y", "章节Y", repeat=3000)
    recent_y_partial = json.dumps(
        {
            "status": "success",
            "data": [{"id": "ch-y", "title": "章节Y", "file_type": "draft", "content": "片段", "content_partial": True}],
        },
        ensure_ascii=False,
    )
    unidentified_query = json.dumps(
        {"status": "success", "data": {"content": "无文件ID的查询大段正文。" * 2500}},
        ensure_ascii=False,
    )
    items_bc = [
        {"role": "user", "content": "旧轮"},
        {"type": "function_call", "call_id": "q_x", "name": "query_files", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "q_x", "output": old_x},
        {"type": "function_call", "call_id": "q_y", "name": "query_files", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "q_y", "output": old_y},
        {"role": "assistant", "content": "旧答"},
        {"role": "user", "content": "近1"},
        {"type": "function_call", "call_id": "q_x_recent", "name": "query_files", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "q_x_recent", "output": recent_x_full},
        {"type": "function_call", "call_id": "q_y_partial", "name": "query_files", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "q_y_partial", "output": recent_y_partial},
        {"role": "assistant", "content": "近答1"},
        {"role": "user", "content": "近2"},
        {"role": "assistant", "content": "近答2"},
        {"role": "user", "content": "近3"},
        {"role": "assistant", "content": "近答3"},
    ]
    res_bc = archive_old_tool_results(LoopHistory(items=items_bc, last_message_id="m-bc"), min_savings_tokens=10_000)
    assert res_bc.admitted is True
    assert res_bc.archived_unidentified_query_files is False
    # ch-x was re-read in full in recent turn -> cleared; ch-y was only partial read -> stays archived!
    assert res_bc.archived_file_ids == {"ch-y"}

    items_unid = [
        {"role": "user", "content": "旧轮"},
        {"type": "function_call", "call_id": "q_u", "name": "query_files", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "q_u", "output": unidentified_query},
        {"role": "assistant", "content": "旧答"},
        {"role": "user", "content": "近1"},
        {"role": "assistant", "content": "答1"},
        {"role": "user", "content": "近2"},
        {"role": "assistant", "content": "答2"},
        {"role": "user", "content": "近3"},
        {"role": "assistant", "content": "答3"},
    ]
    res_unid = archive_old_tool_results(LoopHistory(items=items_unid, last_message_id="m-u"), min_savings_tokens=1_000)
    assert res_unid.archived_unidentified_query_files is True

    # Integration check (a): skill-only archive through AgentService keeps recent_file snapshot seen!
    user, project, chat = _seed_user_project_chat(db_session, "p1_3_skill_only")
    recent_file = File(
        project_id=project.id,
        title="近期章",
        file_type="draft",
        content="近期章完整正文。",
    )
    db_session.add(recent_file)
    db_session.commit()
    db_session.refresh(recent_file)

    base_time = utcnow().replace(tzinfo=None) - timedelta(minutes=20)
    # Turn 0: huge load_skill only; Turns 1..3: recent turns reading recent_file.
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="第1轮", created_at=base_time))
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="已读技能。",
            tool_calls=json.dumps(
                [
                    {
                        "id": "sk_call_0",
                        "name": "load_skill",
                        "arguments": {"name": "大技能"},
                        "status": "success",
                        "result": {
                            "skill_id": "sk-big",
                            "skill_name": "大技能",
                            "instructions": "技能规则正文。" * 6000,
                        },
                    }
                ],
                ensure_ascii=False,
            ),
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "display_events": [
                        {"type": "tool_call", "tool_call_index": 0},
                        {"type": "content", "content": "已读技能。"},
                    ],
                },
                ensure_ascii=False,
            ),
            created_at=base_time + timedelta(seconds=5),
        )
    )
    for idx in range(1, 4):
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="user",
                content=f"近轮{idx}",
                created_at=base_time + timedelta(seconds=idx * 20),
            )
        )
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="assistant",
                content=f"近答{idx}",
                tool_calls=json.dumps(
                    [
                        {
                            "id": f"recent_q_{idx}",
                            "name": "query_files",
                            "arguments": {"id": recent_file.id},
                            "status": "success",
                            "result": [
                                {
                                    "id": recent_file.id,
                                    "title": recent_file.title,
                                    "file_type": "draft",
                                    "content": recent_file.content,
                                    "updated_at": recent_file.updated_at.isoformat(),
                                }
                            ],
                        }
                    ],
                    ensure_ascii=False,
                ),
                message_metadata=json.dumps(
                    {
                        "engine": LOOP_ENGINE_METADATA_VALUE,
                        "prompt_tokens": 320_000 if idx == 3 else 100_000,
                        "display_events": [
                            {"type": "tool_call", "tool_call_index": 0},
                            {"type": "content", "content": f"近答{idx}"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                created_at=base_time + timedelta(seconds=idx * 20 + 5),
            )
        )
    db_session.commit()

    seen_flags: list[bool] = []

    def fake_loop(state, **_kwargs):
        seen_flags.append(state["request_context"].has_seen_file(recent_file.id))

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "完成。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _stream()

    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
        async for _chunk in AgentService().process_stream(
            project_id=project.id,
            user_id=user.id,
            message="改一下近期章",
            session=db_session,
            session_id=chat.id,
        ):
            pass

    # Stage 1 archived only load_skill -> recent_file snapshot MUST stay seen!
    assert seen_flags == [True]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_p1_4_stage1_rejects_admission_when_measured_prompt_minus_savings_still_exceeds_limit():
    """If measured last_prompt_tokens (e.g. 380k incl. system prompt/tools) minus saved_tokens (35k)
    still exceeds COMPACT_AT_TOKENS (300k), Stage 1 MUST NOT admit even if estimated after_tokens <= 300k."""
    old_read = _large_query_output("ch-1", "第一章", repeat=3500)  # ~38k saved tokens
    items = [
        {"role": "user", "content": "第1轮"},
        {"type": "function_call", "call_id": "c_q0", "name": "query_files", "arguments": '{"id":"ch-1"}'},
        {"type": "function_call_output", "call_id": "c_q0", "output": old_read},
        {"role": "assistant", "content": "答1"},
        {"role": "user", "content": "近1"},
        {"role": "assistant", "content": "答2"},
        {"role": "user", "content": "近2"},
        {"role": "assistant", "content": "答3"},
        {"role": "user", "content": "近3"},
        {"role": "assistant", "content": "答4"},
    ]
    history = LoopHistory(
        items=items,
        last_prompt_tokens=380_000,
        last_message_id="msg-measured",
        author_messages=["第1轮", "近3"],
    )

    stage1 = archive_old_tool_results(history, turn_input_tokens=5_000)
    assert stage1.archived_count == 1
    assert stage1.saved_tokens >= agent_runtime.AGENT_LOOP_TOOL_ARCHIVE_MIN_SAVINGS_TOKENS
    assert stage1.after_tokens + 5_000 <= agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS
    # Measured projection (380k - ~38k = ~342k + 5k > 300k) blocks premature admission!
    assert stage1.projected_after_tokens + 5_000 > agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS
    assert stage1.admitted is False

    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="实测超限回落Stage2摘要"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=200_000, completion_tokens=100, total_tokens=200_100, prompt_tokens_details=None),
    )
    mock_call = AsyncMock(return_value=(completion, SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")))
    with patch("agent.core.llm_failover.acreate_chat_completion", mock_call):
        outcome = await compact_history(
            history,
            system_prompt="系统提示",
            session_id="sess-p1-4",
            turn_input_tokens=5_000,
        )

    assert mock_call.await_count == 1
    assert outcome.record is not None
    assert outcome.record["summary"] == "实测超限回落Stage2摘要"


@pytest.mark.unit
def test_p2_1_tool_archive_scoped_to_archived_through_message_id(db_session: Session):
    """A tool_archive record MUST ONLY apply to rows <= archived_through_message_id so a colliding
    provider call_id in a newer turn is never mistakenly archived."""
    user, project, chat = _seed_user_project_chat(db_session, "p2_1_scope")
    base_time = utcnow().replace(tzinfo=None) - timedelta(minutes=20)
    big_prose = "雾港正文内容。" * 2500

    db_session.add(ChatMessage(session_id=chat.id, role="user", content="第1轮", created_at=base_time))
    a0 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="第1轮完成。",
        tool_calls=json.dumps(
            [
                {
                    "id": "call_reused_0",
                    "name": "query_files",
                    "arguments": {"id": "f-old"},
                    "status": "success",
                    "result": [{"id": "f-old", "title": "旧章", "content": big_prose}],
                }
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE}, ensure_ascii=False),
        created_at=base_time + timedelta(seconds=10),
    )
    db_session.add(a0)
    db_session.flush()

    # Turn 2 records tool_archive anchored at a0.id for call_reused_0
    db_session.add(
        ChatMessage(session_id=chat.id, role="user", content="第2轮", created_at=base_time + timedelta(seconds=20))
    )
    a1 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="第2轮完成。",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "compaction": {
                    "mode": COMPACTION_MODE_TOOL_ARCHIVE,
                    "archived_through_message_id": a0.id,
                    "archived_call_ids": ["call_reused_0"],
                    "archived_count": 1,
                    "saved_tokens": 40_000,
                    "created_at": utcnow().isoformat(),
                },
            },
            ensure_ascii=False,
        ),
        created_at=base_time + timedelta(seconds=30),
    )
    db_session.add(a1)
    db_session.flush()

    # Turn 3 (AFTER a0.id!) also has a provider tool call with the same ID "call_reused_0"!
    db_session.add(
        ChatMessage(session_id=chat.id, role="user", content="第3轮", created_at=base_time + timedelta(seconds=40))
    )
    a2 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="第3轮完成。",
        tool_calls=json.dumps(
            [
                {
                    "id": "call_reused_0",
                    "name": "query_files",
                    "arguments": {"id": "f-new"},
                    "status": "success",
                    "result": [{"id": "f-new", "title": "新章", "content": big_prose}],
                }
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE}, ensure_ascii=False),
        created_at=base_time + timedelta(seconds=50),
    )
    db_session.add(a2)
    db_session.commit()

    loaded = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    outputs_by_call = {
        item["call_id"]: json.loads(item["output"])
        for item in loaded.items
        if item.get("type") == "function_call_output"
    }
    assert outputs_by_call[f"h{a0.id}_0"]["archived"] is True
    assert "archived" not in outputs_by_call[f"h{a2.id}_0"]
    assert outputs_by_call[f"h{a2.id}_0"]["data"][0]["id"] == "f-new"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_p2_2_retry_unanswered_merges_carried_and_new_stage1_archived_call_ids(
    db_session: Session,
    monkeypatch,
):
    """If an unanswered user row carried a tool_archive record AND the retried turn triggers a second
    Stage-1 tool_archive, both sets of archived_call_ids are merged so cold replay loses neither."""
    from agent.service import AgentService

    user, project, chat = _seed_user_project_chat(db_session, "p2_2_merge")
    base_time = utcnow().replace(tzinfo=None) - timedelta(minutes=25)
    big_prose = "旧章雾港正文内容。" * 3500

    assistant_rows: list[ChatMessage] = []
    for idx in range(5):
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="user",
                content=f"作者第{idx + 1}轮",
                created_at=base_time + timedelta(seconds=idx * 20),
            )
        )
        a = ChatMessage(
            session_id=chat.id,
            role="assistant",
            content=f"第{idx + 1}轮完成。",
            tool_calls=json.dumps(
                [
                    {
                        "id": f"merge_call_{idx}",
                        "name": "query_files",
                        "arguments": {"id": f"mf-{idx}"},
                        "status": "success",
                        "result": [
                            {
                                "id": f"mf-{idx}",
                                "title": f"第{idx + 1}章",
                                "content": big_prose if idx < 2 else "短内容",
                            }
                        ],
                    }
                ],
                ensure_ascii=False,
            ),
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "prompt_tokens": 100_000,
                    "display_events": [
                        {"type": "tool_call", "tool_call_index": 0},
                        {"type": "content", "content": f"第{idx + 1}轮完成。"},
                    ],
                },
                ensure_ascii=False,
            ),
            created_at=base_time + timedelta(seconds=idx * 20 + 10),
        )
        db_session.add(a)
        db_session.flush()
        assistant_rows.append(a)

    # Unanswered user row carrying an earlier Stage 1 archive for Turn 0 only (merge_call_0).
    carried_user = ChatMessage(
        session_id=chat.id,
        role="user",
        content="重跑这一轮",
        message_metadata=json.dumps(
            {
                "compaction": {
                    "mode": COMPACTION_MODE_TOOL_ARCHIVE,
                    "archived_through_message_id": assistant_rows[-1].id,
                    "archived_call_ids": [f"h{assistant_rows[0].id}_0"],
                    "archived_count": 1,
                    "saved_tokens": 35_000,
                    "created_at": utcnow().isoformat(),
                }
            },
            ensure_ascii=False,
        ),
        created_at=base_time + timedelta(seconds=120),
    )
    db_session.add(carried_user)
    db_session.commit()

    # Lower threshold for this test so the retried turn triggers a second Stage-1 archive on Turn 1!
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_COMPACT_AT_TOKENS", 20_000)
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_MIN_SAVINGS_TOKENS", 10_000)

    def ok_loop(_state, **_kwargs):
        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "合并归档后完成。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _stream()

    with (
        patch("agent.service.run_loop_agent_streaming", side_effect=ok_loop),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        async for _chunk in AgentService().process_stream(
            project_id=project.id,
            user_id=user.id,
            message="重跑这一轮",
            session=db_session,
            session_id=chat.id,
            metadata={"retry_unanswered": True},
        ):
            pass

    db_session.expire_all()
    rows = db_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == chat.id).order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()
    assert rows[-1].role == "assistant"
    meta = json.loads(rows[-1].message_metadata or "{}")
    assert meta["compaction"]["mode"] == COMPACTION_MODE_TOOL_ARCHIVE
    assert meta["compaction"]["archived_call_ids"] == [
        f"h{assistant_rows[0].id}_0",
        f"h{assistant_rows[1].id}_0",
    ]
    assert meta["compaction"]["archived_count"] == 2

    # Cold DB replay archives BOTH Turn 0 and Turn 1!
    reloaded = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    outputs_by_call = {
        item["call_id"]: json.loads(item["output"])
        for item in reloaded.items
        if item.get("type") == "function_call_output"
    }
    assert outputs_by_call[f"h{assistant_rows[0].id}_0"]["archived"] is True
    assert outputs_by_call[f"h{assistant_rows[1].id}_0"]["archived"] is True



# ---------------------------------------------------------------------------
# Wave 3 Audit P0-1 & P1-1 regressions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_w3_p0_1_cold_replay_persist_slimmed_archived_call_records_archived_file_ids(
    db_session: Session,
):
    """P0-1: When a query_files row was ALREADY persist-time slimmed (archived_note without content,
    so len(receipt_text) >= len(output_text)), cold replay under Stage 1 tool_archive MUST still
    unconditionally record its file_ids in history.archived_file_ids."""
    from agent.core.chat_storage import slim_query_files_result_for_persistence

    user, project, chat = _seed_user_project_chat(db_session, "p0_1_slimmed_archive")
    large_body = "雾港雨夜的灯塔亮了起来，潮水涌上青石阶。" * 300
    slimmed_result = slim_query_files_result_for_persistence(
        [{"id": "f-slim-1", "title": "第1章", "file_type": "draft", "content": large_body}]
    )
    assert isinstance(slimmed_result, list) and "archived_note" in slimmed_result[0]

    base = utcnow().replace(tzinfo=None) - timedelta(minutes=10)
    u1 = ChatMessage(session_id=chat.id, role="user", content="读第1章", created_at=base)
    a1 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="读完了。",
        tool_calls=json.dumps(
            [
                {
                    "id": "call_slim_0",
                    "name": "query_files",
                    "arguments": {"id": "f-slim-1"},
                    "status": "success",
                    "result": slimmed_result,
                }
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "display_events": [
                    {"type": "tool_call", "tool_call_index": 0},
                    {"type": "content", "content": "读完了。"},
                ],
            },
            ensure_ascii=False,
        ),
        created_at=base + timedelta(seconds=5),
    )
    db_session.add_all([u1, a1])
    db_session.commit()
    db_session.refresh(a1)

    u2 = ChatMessage(session_id=chat.id, role="user", content="继续", created_at=base + timedelta(seconds=10))
    a2 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="继续完成。",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "compaction": {
                    "mode": COMPACTION_MODE_TOOL_ARCHIVE,
                    "archived_through_message_id": a1.id,
                    "archived_call_ids": ["call_slim_0"],
                    "archived_count": 1,
                },
            },
            ensure_ascii=False,
        ),
        created_at=base + timedelta(seconds=15),
    )
    db_session.add_all([u2, a2])
    db_session.commit()

    hist = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert "f-slim-1" in hist.archived_file_ids


@pytest.mark.unit
def test_w3_p1_1_reused_call_ids_never_misarchive_write_tool_or_skip_old_read():
    """P1-1: Non-globally-unique call_ids (e.g. call_0 reused across turns) must match the
    ordered unconsumed function_call, never mis-archiving edit_file or skipping old query_files."""
    large_edit_out = json.dumps(
        {"status": "success", "data": {"id": "f-write", "edits_applied": 1, "note": "写" * 3000}},
        ensure_ascii=False,
    )
    large_read_old = _large_query_output("f-old", "旧章", repeat=200)
    large_read_recent = _large_query_output("f-recent", "近章", repeat=200)

    items = [
        # Turn 1 (old): write tool uses call_0
        {"role": "user", "content": "第1轮改稿"},
        {"type": "function_call", "call_id": "call_0", "name": "edit_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_0", "output": large_edit_out},
        {"role": "assistant", "content": "改好了。"},
        # Turn 2 (old): read tool ALSO uses call_0
        {"role": "user", "content": "第2轮读旧章"},
        {"type": "function_call", "call_id": "call_0", "name": "query_files", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_0", "output": large_read_old},
        {"role": "assistant", "content": "读完了旧章。"},
        # Turns 3-5 (protected recent window): Turn 5 ALSO reuses call_0!
        {"role": "user", "content": "第3轮"},
        {"role": "assistant", "content": "好3。"},
        {"role": "user", "content": "第4轮"},
        {"role": "assistant", "content": "好4。"},
        {"role": "user", "content": "第5轮读近章"},
        {"type": "function_call", "call_id": "call_0", "name": "query_files", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_0", "output": large_read_recent},
        {"role": "assistant", "content": "读完了近章。"},
    ]
    history = LoopHistory(items=items, last_prompt_tokens=320_000, last_message_id="m5")
    res = archive_old_tool_results(
        history,
        keep_recent_turns=3,
        min_output_chars=1000,
        min_savings_tokens=1,
        compact_at_tokens=400_000,
    )
    assert res.admitted is True
    assert res.archived_count == 1
    assert res.archived_call_ids == ["call_0"]
    assert res.archived_file_ids == {"f-old"}
    assert res.record is not None
    assert res.record.get("archived_call_occurrences") == {"call_0": [1]}
    # Turn 1 write tool (index 2) is NEVER archived!
    assert res.items[2]["output"] == large_edit_out
    # Turn 2 old read tool (index 6) IS archived!
    assert json.loads(res.items[6]["output"])["archived"] is True
    # Turn 5 protected recent read tool (index 14) is NEVER archived!
    assert res.items[14]["output"] == large_read_recent


@pytest.mark.unit
def test_w3_p1_1_cold_replay_handles_reused_orig_call_ids_precisely(db_session: Session):
    """P1-1: Cold DB replay with reused orig_call_id ('call_0') archives ONLY the exact
    occurrence recorded in archived_call_occurrences and never touches protected recent turns."""
    user, project, chat = _seed_user_project_chat(db_session, "p1_1_cold_reused")
    base = utcnow().replace(tzinfo=None) - timedelta(minutes=10)
    big_old = "旧章正文段落。" * 400
    big_recent = "近期章节正文段落。" * 400

    rows: list[ChatMessage] = []
    # Turn 1: edit_file with id="call_0"
    u1 = ChatMessage(session_id=chat.id, role="user", content="第1轮", created_at=base)
    a1 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="改完了。",
        tool_calls=json.dumps(
            [{"id": "call_0", "name": "edit_file", "arguments": {"id": "f-w"}, "status": "success", "result": {"id": "f-w", "edits_applied": 1}}],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "display_events": [{"type": "tool_call", "tool_call_index": 0}, {"type": "content", "content": "改完了。"}]}),
        created_at=base + timedelta(seconds=1),
    )
    # Turn 2: query_files with id="call_0" (archived!)
    u2 = ChatMessage(session_id=chat.id, role="user", content="第2轮", created_at=base + timedelta(seconds=2))
    a2 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="读旧章。",
        tool_calls=json.dumps(
            [{"id": "call_0", "name": "query_files", "arguments": {"id": "f-old"}, "status": "success", "result": [{"id": "f-old", "title": "旧章", "content": big_old}]}],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "display_events": [{"type": "tool_call", "tool_call_index": 0}, {"type": "content", "content": "读旧章。"}]}),
        created_at=base + timedelta(seconds=3),
    )
    # Turn 3: query_files with id="call_0" (protected recent turn, NOT archived!)
    u3 = ChatMessage(session_id=chat.id, role="user", content="第3轮", created_at=base + timedelta(seconds=4))
    a3 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="读近章。",
        tool_calls=json.dumps(
            [{"id": "call_0", "name": "query_files", "arguments": {"id": "f-rec"}, "status": "success", "result": [{"id": "f-rec", "title": "近章", "content": big_recent}]}],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "display_events": [{"type": "tool_call", "tool_call_index": 0}, {"type": "content", "content": "读近章。"}]}),
        created_at=base + timedelta(seconds=5),
    )
    rows.extend([u1, a1, u2, a2, u3, a3])
    db_session.add_all(rows)
    db_session.commit()
    for r in rows:
        db_session.refresh(r)

    # Turn 4 assistant row carries Stage 1 record covering through a3 with occurrence [1] for call_0
    u4 = ChatMessage(session_id=chat.id, role="user", content="第4轮", created_at=base + timedelta(seconds=6))
    a4 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="继续。",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "compaction": {
                    "mode": COMPACTION_MODE_TOOL_ARCHIVE,
                    "archived_through_message_id": a3.id,
                    "archived_call_ids": ["call_0"],
                    "archived_call_occurrences": {"call_0": [1]},
                    "archived_count": 1,
                },
            },
            ensure_ascii=False,
        ),
        created_at=base + timedelta(seconds=7),
    )
    db_session.add_all([u4, a4])
    db_session.commit()

    replayed = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    outputs = {
        item["call_id"]: json.loads(item["output"])
        for item in replayed.items
        if item.get("type") == "function_call_output"
    }
    assert outputs[f"h{a1.id}_0"].get("archived") is not True
    assert outputs[f"h{a2.id}_0"].get("archived") is True
    assert outputs[f"h{a3.id}_0"].get("archived") is not True
    assert replayed.archived_file_ids == {"f-old"}


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_w3_stage1_tool_archive_restores_same_turn_and_evicts_next_turn_fingerprints(
    db_session: Session,
    fake_redis,
    monkeypatch: pytest.MonkeyPatch,
):
    """P0-2 Path A × Stage 1 tool_archive:
    1) Same-turn: when Ch1 was deduped via cached_file_hashes (populated from an earlier
       query_files(Ch1)) and Stage 1 tool_archive archives that query_files(Ch1) on the
       current turn, service MUST restore full_text so Ch1's full content is emitted to the model.
    2) Next-turn: when Stage 1 archives Ch1 while focusing Ch2, stored wire_prefix_cache MUST
       evict Ch1 from full_file_hashes (and _prepare_loop_turn filters history.archived_file_ids)
       so Turn N+1 focusing Ch1 emits full text instead of a stale unchanged pointer.
    """
    from agent.core.message_manager import MessageManager
    from agent.service import AgentService
    from services.features.file_version_service import content_sha256

    user, project, chat = _seed_user_project_chat(db_session, "stage1_fp_splitbrain")
    ch1_body = "【第一章完整正文】雾港钟声响起，潮水拍打着青石阶，温梨推开了补纸斋的木门。" * 120
    ch2_body = "【第二章完整正文】长街雨落，老周点亮了柜台上的煤油灯，翻开泛黄的账本。" * 40
    ch1 = File(project_id=project.id, title="第1章 雾港", content=ch1_body, file_type="draft")
    ch2 = File(project_id=project.id, title="第2章 长街", content=ch2_body, file_type="draft")
    db_session.add_all([ch1, ch2])
    db_session.commit()
    db_session.refresh(ch1)
    db_session.refresh(ch2)

    base = utcnow().replace(tzinfo=None) - timedelta(minutes=10)
    u_seed = ChatMessage(session_id=chat.id, role="user", content="先看第1章", created_at=base)
    a_seed = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已看完。",
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 310_000}),
        created_at=base + timedelta(seconds=5),
    )
    db_session.add_all([u_seed, a_seed])
    db_session.commit()
    db_session.refresh(a_seed)

    service = AgentService()
    system_prompt = MessageManager(project_id=project.id, user_id=user.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=""
    )
    provider_key = "minimax:MiniMax-M3.1-Flash:primary"
    tools_sha = wire_prefix_cache.tools_schema_sha256()

    large_ch1_output = json.dumps(
        {"status": "success", "data": [{"id": ch1.id, "title": ch1.title, "file_type": "draft", "content": ch1_body}]},
        ensure_ascii=False,
    )
    large_other_output = json.dumps(
        {
            "status": "success",
            "data": [
                {
                    "id": "ch-old-other",
                    "title": "第0章 序幕",
                    "file_type": "draft",
                    "content": "【序幕长正文】旧轮次读过的参考章节，归档后足以提供净节省量。" * 240,
                }
            ],
        },
        ensure_ascii=False,
    )
    wire_items = [
        # Old Turn 1 (outside keep_recent_turns=3): Ch1 + another old read so net savings keep restored full_text <= 300k!
        {"role": "user", "content": "先看第1章"},
        {"type": "function_call", "call_id": "call_q_ch1", "name": "query_files", "arguments": json.dumps({"id": ch1.id})},
        {"type": "function_call_output", "call_id": "call_q_ch1", "output": large_ch1_output},
        {"type": "function_call", "call_id": "call_q_old", "name": "query_files", "arguments": json.dumps({"id": "ch-old-other"})},
        {"type": "function_call_output", "call_id": "call_q_old", "output": large_other_output},
        {"role": "assistant", "content": "已读完第1章。"},
        # Recent 3 protected turns
        {"role": "user", "content": "第2轮"},
        {"role": "assistant", "content": "好2。"},
        {"role": "user", "content": "第3轮"},
        {"role": "assistant", "content": "好3。"},
        {"role": "user", "content": "第4轮"},
        {"role": "assistant", "content": "好4。"},
    ]

    fake_provider = SimpleNamespace(
        name="minimax",
        model="MiniMax-M3.1-Flash",
        key_slot="primary",
        uses_responses_api=False,
        reasoning_effort_for=lambda _req: "default",
    )
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_COMPACT_AT_TOKENS", 300_000)
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_MIN_SAVINGS_TOKENS", 1)
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_TOOL_ARCHIVE_MIN_OUTPUT_CHARS", 500)

    def _seed_warm_cache(last_msg_id: str, *, archived_fids: list[str] | None = None) -> None:
        assert wire_prefix_cache.store_wire_prefix(
            session_id=chat.id,
            project_id=project.id,
            user_id=user.id,
            last_assistant_message_id=last_msg_id,
            system_prompt=system_prompt,
            provider_key=provider_key,
            tools_sha256=tools_sha,
            reasoning_effort="default",
            items=wire_items,
            history_meta={
                "last_prompt_tokens": 300_500,
                "compacted": False,
                "compaction_skipped_at_tokens": 0,
                "compaction_skipped_reason": None,
                "trailing_output_truncations": 0,
                "archived_call_ids": [],
                "archived_file_ids": list(archived_fids or []),
                "author_messages": ["先看第1章", "第2轮", "第3轮", "第4轮"],
            },
            context_fingerprints={
                "project_files_sha256": "",
                "full_file_hashes": {ch1.id: content_sha256(ch1_body)},
                "author_edits_sha256": "",
            },
        )

    # --- Case 1: Same-turn Stage 1 archive while focusing Ch1 ---
    _seed_warm_cache(a_seed.id)
    captured_same_turn: list[str] = []

    def fake_loop_same_turn(state, **_kwargs):
        captured_same_turn.append(str(state.get("user_message") or ""))
        state["run_items"] = [*state.get("history_items", []), {"role": "user", "content": "改第1章"}, {"role": "assistant", "content": "好的。"}]

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "好的。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _stream()

    with (
        patch("agent.service.llm_failover.aordered_providers", new=AsyncMock(return_value=[fake_provider])),
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop_same_turn),
    ):
        async for _chunk in service.process_stream(
            project_id=project.id,
            user_id=user.id,
            message="改一下第1章",
            session=db_session,
            session_id=chat.id,
            metadata={"current_file_id": ch1.id},
        ):
            pass

    assert len(captured_same_turn) == 1
    assert ch1_body in captured_same_turn[0]
    assert "与本会话近期上下文中已提供的全文一致" not in captured_same_turn[0]

    # --- Case 2: Next-turn Stage 1 archive while focusing Ch2, then Turn N+1 focuses Ch1 ---
    latest_a = db_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == chat.id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
    ).first()
    assert latest_a is not None
    _seed_warm_cache(latest_a.id)

    def fake_loop_focus_ch2(state, **_kwargs):
        state["run_meter"].last_served_provider_key = provider_key
        state["run_meter"].last_served_reasoning_effort = "default"
        state["run_meter"].last_prompt_tokens = 20_000
        state["run_items"] = [
            *state.get("history_items", []),
            {"role": "user", "content": "看第2章"},
            {"role": "assistant", "content": "已看第2章。"},
        ]

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "已看第2章。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _stream()

    with (
        patch("agent.service.llm_failover.aordered_providers", new=AsyncMock(return_value=[fake_provider])),
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop_focus_ch2),
    ):
        async for _chunk in service.process_stream(
            project_id=project.id,
            user_id=user.id,
            message="看第2章",
            session=db_session,
            session_id=chat.id,
            metadata={"current_file_id": ch2.id},
        ):
            pass

    # Stored wire_prefix_cache MUST have evicted ch1.id from full_file_hashes!
    after_ch2_a = db_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == chat.id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
    ).first()
    assert after_ch2_a is not None
    stored_entry = wire_prefix_cache.load_wire_prefix_entry(
        session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        last_message_id=after_ch2_a.id,
        system_prompt=system_prompt,
        provider_key=provider_key,
        tools_sha256=tools_sha,
        reasoning_effort="default",
    )
    assert stored_entry is not None
    assert stored_entry.context_fingerprints is not None
    assert ch1.id not in (stored_entry.context_fingerprints.get("full_file_hashes") or {})
    assert stored_entry.history_meta is not None
    assert ch1.id in (stored_entry.history_meta.get("archived_file_ids") or [])

    # Turn N+1 focusing Ch1 MUST emit full Ch1 text (NOT unchanged pointer)!
    _hist_next, turn_next, _sk, _sp = service._prepare_loop_turn(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
        selected_skill_ids=None,
        message="回到第1章",
        metadata={"current_file_id": ch1.id},
        language="zh",
        expected_provider_key=provider_key,
        expected_tools_sha256=tools_sha,
        expected_reasoning_effort="default",
    )
    assert ch1_body in turn_next.text
    assert "与本会话近期上下文中已提供的全文一致" not in turn_next.text

    # --- Case 3: Defense-in-depth in _prepare_loop_turn when a stale entry still has ch1 in full_file_hashes ---
    _seed_warm_cache(after_ch2_a.id, archived_fids=[ch1.id])
    _hist_def, turn_def, _sk, _sp = service._prepare_loop_turn(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
        selected_skill_ids=None,
        message="再次回到第1章",
        metadata={"current_file_id": ch1.id},
        language="zh",
        expected_provider_key=provider_key,
        expected_tools_sha256=tools_sha,
        expected_reasoning_effort="default",
    )
    assert ch1_body in turn_def.text
    assert "与本会话近期上下文中已提供的全文一致" not in turn_def.text


@pytest.mark.integration
def test_b2_archived_skill_tool_calls_skip_cold_replay_rehydration_and_db_queries(db_session: Session):
    """B2: Archived load_skill / read_skill_resource calls inside SKILL_REPLAY_RECENT_TURNS must
    never invoke skill_rehydrator / DB skill queries during cold replay, while non-archived recent
    skill calls still rehydrate normally."""
    from models import SkillResource, UserSkill

    user, project, chat = _seed_user_project_chat(db_session, "b2_skill_archive")
    archived_skill = UserSkill(
        user_id=user.id,
        name="旧归档技能",
        description="desc",
        instructions="绝不应在已归档调用里被水合的机密正文。" * 200,
    )
    active_skill = UserSkill(
        user_id=user.id,
        name="近期活跃技能",
        description="desc",
        instructions="近期未归档技能应当正常水合的正文。",
    )
    db_session.add_all([archived_skill, active_skill])
    db_session.commit()
    db_session.add(
        SkillResource(
            user_skill_id=archived_skill.id,
            path="references/secret.md",
            content="归档资源的原文绝不应被查询水合。" * 100,
            size=1024,
        )
    )
    db_session.commit()

    base_time = utcnow().replace(tzinfo=None) - timedelta(minutes=10)
    # Turn 1 (still inside SKILL_REPLAY_RECENT_TURNS=3): load_skill + read_skill_resource
    u1 = ChatMessage(session_id=chat.id, role="user", content="第1轮用旧技能", created_at=base_time)
    a1 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="第1轮完成。",
        tool_calls=json.dumps(
            [
                {
                    "id": "call_skill_load_1",
                    "name": "load_skill",
                    "arguments": {"name": "旧归档技能"},
                    "status": "success",
                    "result": {
                        "skill_id": archived_skill.id,
                        "skill_name": "旧归档技能",
                        "source": "user",
                        "instructions_chars": len(archived_skill.instructions),
                        "resources": [{"path": "references/secret.md", "size": 1024}],
                    },
                },
                {
                    "id": "call_skill_res_1",
                    "name": "read_skill_resource",
                    "arguments": {"name": "旧归档技能", "path": "references/secret.md"},
                    "status": "success",
                    "result": {
                        "skill_id": archived_skill.id,
                        "skill_name": "旧归档技能",
                        "path": "references/secret.md",
                        "size": 1024,
                    },
                },
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "display_events": [
                    {"type": "tool_call", "tool_call_index": 0},
                    {"type": "tool_call", "tool_call_index": 1},
                    {"type": "content", "content": "第1轮完成。"},
                ],
            },
            ensure_ascii=False,
        ),
        created_at=base_time + timedelta(seconds=10),
    )
    db_session.add_all([u1, a1])
    db_session.commit()
    db_session.refresh(a1)

    # Turn 2: persists Stage 1 tool_archive covering Turn 1's skill calls (e.g. KEEP_RECENT_TURNS < SKILL_REPLAY_RECENT_TURNS)
    u2 = ChatMessage(session_id=chat.id, role="user", content="第2轮继续", created_at=base_time + timedelta(seconds=20))
    a2 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="第2轮完成。",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "compaction": {
                    "mode": COMPACTION_MODE_TOOL_ARCHIVE,
                    "archived_through_message_id": a1.id,
                    "archived_call_ids": ["call_skill_load_1", "call_skill_res_1"],
                    "archived_count": 2,
                    "saved_tokens": 40_000,
                    "created_at": utcnow().isoformat(),
                },
                "display_events": [{"type": "content", "content": "第2轮完成。"}],
            },
            ensure_ascii=False,
        ),
        created_at=base_time + timedelta(seconds=30),
    )
    db_session.add_all([u2, a2])
    db_session.commit()

    # Case 1: Only archived skill calls exist -> load_active_skills / rehydrate_skill_tool_result MUST NOT be called!
    with (
        patch("agent.skills.active_skills.load_active_skills", side_effect=AssertionError("DB queried for archived skill")) as mock_load,
        patch("agent.skills.replay.rehydrate_skill_tool_result", side_effect=AssertionError("rehydrated archived skill")) as mock_rehydrate,
    ):
        hist_only_archived = load_loop_history(
            db_session,
            project_id=project.id,
            user_id=user.id,
            chat_session_id=chat.id,
        )
    mock_load.assert_not_called()
    mock_rehydrate.assert_not_called()

    outputs = [
        json.loads(item["output"])
        for item in hist_only_archived.items
        if item.get("type") == "function_call_output"
    ]
    assert len(outputs) == 2
    assert all(out.get("archived") is True for out in outputs)
    assert outputs[0]["tool_name"] == "load_skill"
    assert outputs[0]["skill_id"] == archived_skill.id
    assert outputs[1]["tool_name"] == "read_skill_resource"
    assert outputs[1]["path"] == "references/secret.md"
    assert "绝不应在已归档调用里被水合" not in json.dumps(outputs, ensure_ascii=False)
    assert "归档资源的原文绝不应被查询水合" not in json.dumps(outputs, ensure_ascii=False)

    # Case 2: Add Turn 3 with a non-archived load_skill -> Turn 1 stays archived (never rehydrated), Turn 3 rehydrates!
    u3 = ChatMessage(session_id=chat.id, role="user", content="第3轮用新技能", created_at=base_time + timedelta(seconds=40))
    a3 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="第3轮完成。",
        tool_calls=json.dumps(
            [
                {
                    "id": "call_skill_load_3",
                    "name": "load_skill",
                    "arguments": {"name": "近期活跃技能"},
                    "status": "success",
                    "result": {
                        "skill_id": active_skill.id,
                        "skill_name": "近期活跃技能",
                        "source": "user",
                        "instructions_chars": len(active_skill.instructions),
                        "resources": [],
                    },
                }
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "display_events": [
                    {"type": "tool_call", "tool_call_index": 0},
                    {"type": "content", "content": "第3轮完成。"},
                ],
            },
            ensure_ascii=False,
        ),
        created_at=base_time + timedelta(seconds=50),
    )
    db_session.add_all([u3, a3])
    db_session.commit()

    from agent.skills import replay as skill_replay_mod

    rehydrated_ids: list[str] = []
    orig_rehydrate = skill_replay_mod.rehydrate_skill_tool_result

    def _spy_rehydrate(active_by_id, sess, tool_name, result_data, **kwargs):
        if isinstance(result_data, dict) and result_data.get("skill_id"):
            rehydrated_ids.append(str(result_data["skill_id"]))
        return orig_rehydrate(active_by_id, sess, tool_name, result_data, **kwargs)

    with patch("agent.skills.replay.rehydrate_skill_tool_result", side_effect=_spy_rehydrate):
        hist_mixed = load_loop_history(
            db_session,
            project_id=project.id,
            user_id=user.id,
            chat_session_id=chat.id,
        )

    assert rehydrated_ids == [active_skill.id]
    mixed_outputs = [
        json.loads(item["output"])
        for item in hist_mixed.items
        if item.get("type") == "function_call_output"
    ]
    assert len(mixed_outputs) == 3
    assert mixed_outputs[0]["archived"] is True
    assert mixed_outputs[1]["archived"] is True
    assert "archived" not in mixed_outputs[2]
    assert mixed_outputs[2]["data"]["instructions"] == active_skill.instructions


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("writing_prompt_configs")
async def test_m1_stage1_admission_rechecks_restored_full_text_tokens(
    db_session: Session,
    fake_redis,  # noqa: ARG001 - patches wire_prefix_cache.get_redis_client + REDIS_URL
):
    """M1: When warm-cache dedup shrinks <turn_context> (e.g. 21k -> 1k tokens) and Stage 1 would
    restore loop_turn.full_text, Stage 1 MUST re-check admission against restored full_text tokens
    and fall through to Stage 2 if projected_after + restored_turn_tokens > COMPACT_AT_TOKENS."""
    from agent.core.message_manager import MessageManager
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.loop import wire_prefix_cache
    from agent.loop.compaction import CompactionOutcome
    from agent.loop.history import estimate_provider_text_tokens
    from agent.service import AgentService
    from models import File
    from services.features.file_version_service import content_sha256

    user, project, chat = _seed_user_project_chat(db_session, "m1_restored_admission")
    ch1_body = "第一章完整长正文，用于验证恢复全文后的准入复检。" * 1_500  # ~21k+ provider tokens
    ch1 = File(
        project_id=project.id,
        title="第1章 准入复检",
        file_type="draft",
        content=ch1_body,
    )
    db_session.add(ch1)
    db_session.commit()
    db_session.refresh(ch1)

    base_time = utcnow().replace(tzinfo=None) - timedelta(minutes=10)
    u_seed = ChatMessage(session_id=chat.id, role="user", content="先读第1章", created_at=base_time)
    a_seed = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已读第1章。",
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 340_000}),
        created_at=base_time + timedelta(seconds=10),
    )
    db_session.add_all([u_seed, a_seed])
    db_session.commit()
    db_session.refresh(a_seed)

    # Craft warm history where Stage 1 saves >= 30k tokens leaving projected_after = 295k:
    # - With deduped <turn_context> (~1k tokens): 295k + 1k = 296k <= 300k (would falsely admit!)
    # - With restored full_text (~21k tokens):   295k + 21k = 316k > 300k (MUST reject Stage 1 -> Stage 2!)
    old_ch1_output = _large_query_output(ch1.id, ch1.title, repeat=3_500)  # ~50k tokens saved
    warm_items = [
        {"role": "user", "content": "第1轮"},
        {"type": "function_call", "call_id": "call_m1_ch1", "name": "query_files", "arguments": json.dumps({"id": ch1.id})},
        {"type": "function_call_output", "call_id": "call_m1_ch1", "output": old_ch1_output},
        {"role": "assistant", "content": "第1轮回复"},
        {"role": "user", "content": "第2轮"},
        {"role": "assistant", "content": "第2轮回复"},
        {"role": "user", "content": "第3轮"},
        {"role": "assistant", "content": "第3轮回复"},
        {"role": "user", "content": "第4轮"},
        {"role": "assistant", "content": "第4轮回复"},
    ]
    probe_archive = archive_old_tool_results(
        LoopHistory(items=warm_items, last_prompt_tokens=345_000, last_message_id=a_seed.id),
        turn_input_tokens=500,
    )
    assert probe_archive.saved_tokens >= 30_000
    # Pin last_prompt_tokens so projected_after_tokens lands cleanly at 295_000.
    tuned_last_prompt_tokens = 295_000 + probe_archive.saved_tokens
    full_ch1_tokens = estimate_provider_text_tokens(ch1_body)
    assert 295_000 + full_ch1_tokens > agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS

    fake_provider = SimpleNamespace(
        name="minimax",
        model="MiniMax-M3.1-Flash",
        key_slot="primary",
        uses_responses_api=False,
        reasoning_effort_for=lambda _req: "default",
    )
    provider_key = f"{fake_provider.name}:{fake_provider.model}:{fake_provider.key_slot}"
    tools_sha = wire_prefix_cache.tools_schema_sha256()

    service = AgentService()
    system_prompt = MessageManager(project_id=project.id, user_id=user.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=""
    )
    assert wire_prefix_cache.store_wire_prefix(
        session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        last_assistant_message_id=a_seed.id,
        system_prompt=system_prompt,
        provider_key=provider_key,
        tools_sha256=tools_sha,
        reasoning_effort="default",
        items=warm_items,
        history_meta={
            "last_prompt_tokens": tuned_last_prompt_tokens,
            "compacted": False,
            "compaction_skipped_at_tokens": 0,
            "compaction_skipped_reason": None,
            "trailing_output_truncations": 0,
            "archived_call_ids": [],
            "archived_file_ids": [],
            "author_messages": ["第1轮", "第2轮", "第3轮", "第4轮"],
        },
        context_fingerprints={
            "project_files_sha256": "",
            "full_file_hashes": {ch1.id: content_sha256(ch1_body)},
            "author_edits_sha256": "",
        },
    )

    stage2_calls: list[dict[str, Any]] = []

    async def fake_compact_history(_hist, **kwargs):
        stage2_calls.append(kwargs)
        pre = kwargs.get("pre_archive")
        assert pre is not None
        assert pre.admitted is False
        assert pre.record is None
        assert pre.archived_count == 1
        return CompactionOutcome(
            record={
                "summary": "Stage 2 兜底摘要",
                "covers_through_message_id": a_seed.id,
                "kept_author_messages": ["第4轮"],
                "created_at": utcnow().isoformat(),
            },
            items=[{"role": "user", "content": "【上下文已压缩】Stage 2 兜底摘要"}],
            usage={"prompt_tokens": 1000, "completion_tokens": 100},
        )

    captured_user_msg: list[str] = []

    def fake_loop(state, **_kwargs):
        captured_user_msg.append(str(state.get("user_message") or ""))
        state["run_items"] = [*state.get("history_items", []), {"role": "user", "content": "继续"}, {"role": "assistant", "content": "好。"}]

        async def _stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "好。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _stream()

    with (
        patch("agent.service.llm_failover.aordered_providers", new=AsyncMock(return_value=[fake_provider])),
        patch("agent.service.compact_history", side_effect=fake_compact_history),
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop),
    ):
        events = [
            chunk
            async for chunk in service.process_stream(
                project_id=project.id,
                user_id=user.id,
                message="改写一下第1章",
                session=db_session,
                session_id=chat.id,
                metadata={"current_file_id": ch1.id},
            )
        ]

    # Stage 1 MUST have been rejected after re-checking restored full_text, falling through to Stage 2!
    assert len(stage2_calls) == 1
    assert stage2_calls[0]["turn_input_tokens"] >= full_ch1_tokens
    assert any("history_compacted" in ev for ev in events)
    assert len(captured_user_msg) == 1
    assert ch1_body in captured_user_msg[0]
