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
    assert "query_files" in q0_receipt["note"]

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
