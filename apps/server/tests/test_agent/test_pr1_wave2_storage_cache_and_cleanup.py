"""Comprehensive regression tests for PR 1 Wave 2:
- P0-2: bounded persisted thinking_content (8k cap), single-segment display_events dedup,
  and persist-time slimming of oversized query_files with content_sha256 + session_read_snapshots.
- P0-3: warm-cache-first lookup skips load_loop_history TOAST scan; explicit 6-column projection.
- P0-6: GeneratorExit in stream_adapter.py and model.py never awaits aclose() inline.
- P0-9: wire_prefix_cache validates tools_sha256 and reasoning_effort (fail-open on mismatch).
- P1-1: <turn_context> warm-cache fingerprint dedup (0 KV prefix mutation, preserves read_snapshots,
  re-emits when modified, restores full_text on Stage 2 compaction).
- Legacy cleanup: deduplicated empty-file soft-delete helper advances updated_at consistently.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlmodel import Session

from agent.core.chat_storage import (
    ARCHIVED_QUERY_CONTENT_NOTE,
    PERSISTED_QUERY_FILES_MAX_CHARS,
    slim_query_files_result_for_persistence,
)
from agent.core.empty_files import soft_delete_empty_file_if_unchanged
from agent.core.message_manager import (
    PERSISTED_THINKING_MAX_CHARS,
    PERSISTED_THINKING_TRUNCATION_MARKER,
    MessageManager,
    dedupe_single_content_display_events,
    normalize_assistant_display_events,
    truncate_persisted_thinking,
)
from agent.core.session_loader import SessionLoader
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop import wire_prefix_cache
from agent.loop.history import LOOP_ENGINE_METADATA_VALUE, load_loop_history
from agent.loop.turn_context import (
    AUTHOR_EDITS_UNCHANGED_MARKER,
    PROJECT_FILES_UNCHANGED_MARKER,
    build_loop_turn_context,
)
from agent.openai_agents import model as sdk_model
from agent.service import AgentService
from agent.stream_adapter import StreamAdapter
from config.datetime_utils import utcnow
from models import ChatMessage, ChatSession, File, Project, User
from services.features.file_version_service import FileVersionService, content_sha256


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
    client = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake-wave2/0")
    monkeypatch.setattr(wire_prefix_cache, "get_redis_client", lambda: client)
    wire_prefix_cache.reset_wire_prefix_cache_state()
    yield client
    wire_prefix_cache.reset_wire_prefix_cache_state()


def _seed(db_session: Session) -> tuple[User, Project, ChatSession]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"wave2-{suffix}@example.com",
        username=f"wave2_{suffix}",
        hashed_password="h",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name=f"Wave2_{suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话", is_active=True)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    return user, project, chat


# ---------------------------------------------------------------------------
# P0-2: Thinking cap, single-segment display_events dedup, query_files slimming
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_p02_persisted_thinking_capped_while_short_thinking_preserved():
    short = "短思考内容" * 10
    assert truncate_persisted_thinking(short) == short

    huge = "深度思考推理过程。" * 30_000  # 270,000 chars
    capped = truncate_persisted_thinking(huge)
    assert len(capped) <= PERSISTED_THINKING_MAX_CHARS
    assert capped.endswith(PERSISTED_THINKING_TRUNCATION_MARKER)

    normalized = normalize_assistant_display_events(
        [
            {"type": "thinking_content", "content": huge},
            {"type": "content", "content": "正式回复正文。"},
        ]
    )
    assert normalized is not None
    assert len(normalized[0]["content"]) <= PERSISTED_THINKING_MAX_CHARS
    assert normalized[0]["content"].endswith(PERSISTED_THINKING_TRUNCATION_MARKER)
    assert normalized[1] == {"type": "content", "content": "正式回复正文。"}


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_p02_live_sse_thinking_unbounded_but_persisted_thinking_capped_and_single_content_deduped(
    db_session: Session,
):
    user, project, chat = _seed(db_session)
    huge_thinking = "思" * (PERSISTED_THINKING_MAX_CHARS + 5_000)

    async def stream_with_thinking(*_args, **_kwargs):
        yield StreamEvent(type=StreamEventType.THINKING, data={"thinking": huge_thinking})
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "回复正文。"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    service = AgentService()
    with patch("agent.service.run_loop_agent_streaming", side_effect=stream_with_thinking):
        frames = [
            frame
            async for frame in service.process_stream(
                project_id=project.id,
                user_id=user.id,
                message="分析一下",
                session=db_session,
                session_id=chat.id,
            )
        ]

    # Live SSE thinking is 100% untouched (full length streamed).
    thinking_frames = [f for f in frames if "event: thinking_content" in f]
    assert len(thinking_frames) == 1
    sse_thinking = json.loads(thinking_frames[0].split("data:", 1)[1])["content"]
    assert sse_thinking == huge_thinking

    # Persisted thinking in chat_message.message_metadata is capped to PERSISTED_THINKING_MAX_CHARS.
    done_payload = json.loads([f for f in frames if "event: done" in f][0].split("data:", 1)[1])
    saved = db_session.get(ChatMessage, done_payload["assistant_message_id"])
    assert saved is not None
    meta = json.loads(saved.message_metadata or "{}")
    persisted_thinking = meta["display_events"][0]["content"]
    assert len(persisted_thinking) <= PERSISTED_THINKING_MAX_CHARS
    assert persisted_thinking.endswith(PERSISTED_THINKING_TRUNCATION_MARKER)

    # Single-segment content-only turn omits duplicate display_events.
    assert dedupe_single_content_display_events(
        [{"type": "content", "content": "单段正文"}], "单段正文"
    ) is None
    assert dedupe_single_content_display_events(
        [{"type": "content", "content": "单段正文"}],
        "单段正文",
        tool_calls=[{"name": "query_files"}],
    ) == [{"type": "content", "content": "单段正文"}]


@pytest.mark.unit
def test_p02_query_files_persist_slimming_preserves_session_read_snapshots(db_session: Session):
    user, project, chat = _seed(db_session)
    large_body = "雾港钟声响起，潮水拍打着青石阶，温梨推开了补纸斋的木门。" * 300
    assert len(large_body) > PERSISTED_QUERY_FILES_MAX_CHARS

    chapter = File(project_id=project.id, title="第1章", content=large_body, file_type="draft")
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)

    raw_result = [
        {
            "id": chapter.id,
            "title": chapter.title,
            "file_type": "draft",
            "content": large_body,
            "content_length": len(large_body),
            "updated_at": chapter.updated_at.isoformat(),
        }
    ]
    slimmed = slim_query_files_result_for_persistence(raw_result)
    assert isinstance(slimmed, list)
    assert "content" not in slimmed[0]
    assert slimmed[0]["content_sha256"] == content_sha256(large_body)
    assert slimmed[0]["archived_note"] == ARCHIVED_QUERY_CONTENT_NOTE

    # Small query_files payload (<= threshold) stays intact.
    small_result = [{"id": "s1", "title": "短卡", "file_type": "character", "content": "短设定。"}]
    assert slim_query_files_result_for_persistence(small_result) == small_result

    # Persisting slimmed query_files still lets SessionLoader.session_read_snapshots verify & carry over snapshot!
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="已读完第1章。",
            tool_calls=json.dumps(
                [
                    {
                        "id": "call-qf",
                        "name": "query_files",
                        "arguments": {"id": chapter.id},
                        "status": "success",
                        "result": slimmed,
                    }
                ],
                ensure_ascii=False,
            ),
            message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE}),
        )
    )
    db_session.commit()

    loader = SessionLoader(project.id, user.id)
    snapshots = loader.session_read_snapshots(db_session, chat_session_id=chat.id)
    assert snapshots == {chapter.id: large_body}

    # If the author edits the chapter, sha256 mismatch rejects carryover (zero stale bypass).
    chapter.content = large_body + "\n作者新加的一句。"
    db_session.add(chapter)
    db_session.commit()
    assert loader.session_read_snapshots(db_session, chat_session_id=chat.id) == {}


# ---------------------------------------------------------------------------
# P0-3 & P0-9: Warm-cache-first skips load_loop_history; tools_sha256 & reasoning_effort
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_p03_load_loop_history_projects_explicit_columns_without_reasoning_content(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    from agent.loop import history as history_mod

    user, project, chat = _seed(db_session)
    base = datetime.now(UTC)
    for i in range(4):
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"消息 {i}",
                reasoning_content="不应被 SELECT 解压的思考列" if i % 2 == 1 else None,
                message_metadata=(
                    json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE}) if i % 2 == 1 else None
                ),
                created_at=base + timedelta(seconds=i),
            )
        )
    db_session.commit()

    monkeypatch.setattr(history_mod, "_PAGE_SIZE", 2)
    sql_calls: list[tuple[str, object]] = []
    engine = db_session.get_bind()

    def _on_sql(_conn, _cursor, statement, parameters, _context, _execmany):
        sql_calls.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _on_sql)
    try:
        history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    finally:
        event.remove(engine, "before_cursor_execute", _on_sql)

    assert [item["content"] for item in history.items] == ["消息 0", "消息 1", "消息 2", "消息 3"]
    cm_selects = [
        (s, p)
        for s, p in sql_calls
        if "chat_message" in s.lower() and s.lstrip().lower().startswith("select")
    ]
    assert len(cm_selects) >= 2
    assert all("reasoning_content" not in s.lower() for s, _ in cm_selects)
    # Keyset pagination: page 2+ filters by created_at < cursor rather than incrementing SQL OFFSET.
    assert any("chat_message.created_at <" in s.lower() for s, _ in cm_selects[1:])
    for s, params in cm_selects:
        if "offset" in s.lower() and isinstance(params, (list, tuple)) and params:
            # SQLite compiles .limit(N) as "LIMIT ? OFFSET ?" with final param == 0.
            assert params[-1] == 0


@pytest.mark.unit
def test_p09_wire_prefix_cache_validates_tools_sha256_and_reasoning_effort(fake_redis):
    items = [
        {"role": "user", "content": "写第一章"},
        {"role": "assistant", "content": "第一章写好了。"},
    ]
    assert (
        wire_prefix_cache.store_wire_prefix(
            session_id="sess-p09",
            project_id="p1",
            user_id="u1",
            last_assistant_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
            tools_sha256="tools-hash-v1",
            reasoning_effort="high",
            items=items,
            history_meta={"last_prompt_tokens": 12000, "author_messages": ["写第一章"]},
            context_fingerprints={"project_files_sha256": "pf-1", "full_file_hashes": {}},
        )
        is True
    )

    hit = wire_prefix_cache.load_wire_prefix_entry(
        session_id="sess-p09",
        project_id="p1",
        user_id="u1",
        last_message_id="m1",
        system_prompt="sys",
        provider_key="minimax:MiniMax-M3.1-Flash:primary",
        tools_sha256="tools-hash-v1",
        reasoning_effort="high",
    )
    assert hit is not None
    assert hit.items == items
    assert hit.history_meta == {"last_prompt_tokens": 12000, "author_messages": ["写第一章"]}
    assert hit.context_fingerprints == {"project_files_sha256": "pf-1", "full_file_hashes": {}}

    # Mismatched tools_sha256 fails open cleanly to None.
    assert (
        wire_prefix_cache.load_wire_prefix_entry(
            session_id="sess-p09",
            project_id="p1",
            user_id="u1",
            last_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
            tools_sha256="tools-hash-v2",
            reasoning_effort="high",
        )
        is None
    )

    # Mismatched reasoning_effort fails open cleanly to None.
    assert (
        wire_prefix_cache.load_wire_prefix_entry(
            session_id="sess-p09",
            project_id="p1",
            user_id="u1",
            last_message_id="m1",
            system_prompt="sys",
            provider_key="minimax:MiniMax-M3.1-Flash:primary",
            tools_sha256="tools-hash-v1",
            reasoning_effort="low",
        )
        is None
    )


@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs")
def test_p03_warm_cache_hit_skips_load_loop_history_completely(db_session: Session, fake_redis):
    user, project, chat = _seed(db_session)
    msg_u = ChatMessage(session_id=chat.id, role="user", content="写开篇")
    msg_a = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="开篇完成了。",
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 42_000}),
    )
    db_session.add_all([msg_u, msg_a])
    db_session.commit()
    db_session.refresh(msg_a)

    service = AgentService()
    system_prompt = MessageManager(project_id=project.id, user_id=user.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=""
    )
    cached_items = [
        {"role": "user", "content": "写开篇"},
        {"role": "assistant", "content": "开篇完成了。"},
    ]
    tools_sha = wire_prefix_cache.tools_schema_sha256()
    assert wire_prefix_cache.store_wire_prefix(
        session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        last_assistant_message_id=msg_a.id,
        system_prompt=system_prompt,
        provider_key="minimax:MiniMax-M3.1-Flash:primary",
        tools_sha256=tools_sha,
        reasoning_effort="default",
        items=cached_items,
        history_meta={
            "last_prompt_tokens": 42_000,
            "compacted": False,
            "compaction_skipped_at_tokens": 0,
            "compaction_skipped_reason": None,
            "trailing_output_truncations": 0,
            "archived_call_ids": [],
            "archived_file_ids": ["f-archived-1"],
            "author_messages": ["写开篇"],
        },
    )

    with patch("agent.service.load_loop_history", side_effect=AssertionError("Must not call load_loop_history on warm hit")):
        history, _turn, _skills, _sys = service._prepare_loop_turn(
            db_session,
            project_id=project.id,
            user_id=user.id,
            chat_session_id=chat.id,
            selected_skill_ids=None,
            message="继续写下一段",
            metadata={},
            language="zh",
            expected_provider_key="minimax:MiniMax-M3.1-Flash:primary",
            expected_tools_sha256=tools_sha,
            expected_reasoning_effort="default",
        )

    assert history.items == cached_items
    assert history.last_message_id == msg_a.id
    assert history.last_prompt_tokens == 42_000
    assert history.author_messages == ["写开篇"]
    assert history.archived_file_ids == {"f-archived-1"}


# ---------------------------------------------------------------------------
# P0-6: GeneratorExit never awaits aclose() inline (no RuntimeError)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_p06_generator_exit_in_stream_adapter_and_model_never_raises_runtime_error():
    closed_events = {"adapter_upstream": False, "model_upstream": False}

    class _SuspendingUpstream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            return StreamEvent(type=StreamEventType.TEXT, data={"text": "chunk"})

        async def aclose(self):
            await asyncio.sleep(0)
            closed_events["adapter_upstream"] = True

    adapter = StreamAdapter()
    gen = adapter.process_workflow_events(_SuspendingUpstream())
    first = await gen.__anext__()
    assert first is not None
    await gen.aclose()
    await asyncio.sleep(0.01)
    assert closed_events["adapter_upstream"] is True

    class _SuspendingModelStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            return SimpleNamespace(type="response.output_text.delta", delta="hi")

        async def aclose(self):
            await asyncio.sleep(0)
            closed_events["model_upstream"] = True

    guarded = sdk_model._iter_with_stream_timeouts(
        _SuspendingModelStream(),
        first_event_timeout_s=5.0,
        idle_timeout_s=5.0,
        provider_name="minimax",
    )
    ev = await guarded.__anext__()
    assert ev.delta == "hi"
    await guarded.aclose()
    await asyncio.sleep(0.01)
    assert closed_events["model_upstream"] is True


# ---------------------------------------------------------------------------
# P1-1: <turn_context> fingerprint deduplication on warm cache turns
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_p11_turn_context_fingerprint_deduplicates_unchanged_manifest_and_focus_while_keeping_snapshots(
    db_session: Session,
):
    user, project, chat = _seed(db_session)
    versions = FileVersionService()

    focus_body = "第一章完整正文段落，雨夜雾港的灯火映在青石板上。" * 60  # > 500 chars
    focus = File(project_id=project.id, title="第1章 雾港", content=focus_body, file_type="draft")
    edited_other = File(project_id=project.id, title="第2章 来信", content="作者改后的第2章。", file_type="draft")
    db_session.add_all([focus, edited_other])
    db_session.commit()
    db_session.refresh(focus)
    db_session.refresh(edited_other)

    versions.create_version(db_session, edited_other.id, "AI初稿", change_type="create", change_source="ai")
    versions.create_version(db_session, edited_other.id, "作者改后的第2章。", change_type="edit", change_source="user")
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="写好了第2章。",
            tool_calls=json.dumps(
                [
                    {
                        "id": "wc-2",
                        "name": "write_chapter",
                        "arguments": {"chapter": 2, "title": "第2章 来信", "file_type": "draft"},
                        "status": "success",
                        "result": {"id": edited_other.id, "title": edited_other.title, "action": "created", "body_saved": True},
                    }
                ],
                ensure_ascii=False,
            ),
            message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE}),
        )
    )
    db_session.commit()

    # Turn 1 (cold): full <project_files>, full focus body, full <author_edits>.
    turn1 = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": focus.id},
        language="zh",
        chat_session_id=chat.id,
    )
    assert focus_body in turn1.text
    assert "<project_files>" in turn1.text
    assert PROJECT_FILES_UNCHANGED_MARKER not in turn1.text
    assert edited_other.id in turn1.text
    assert turn1.read_snapshots[focus.id] == focus_body
    assert turn1.full_text == ""

    # Turn 2 (warm, nothing changed): manifest, focus body, and <author_edits> are deduplicated;
    # read_snapshots[focus.id] is STILL preserved and full_text holds the un-deduped fallback!
    turn2 = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": focus.id},
        language="zh",
        chat_session_id=chat.id,
        prior_context_fingerprints=turn1.context_fingerprints,
    )
    assert PROJECT_FILES_UNCHANGED_MARKER in turn2.text
    assert AUTHOR_EDITS_UNCHANGED_MARKER in turn2.text
    assert focus_body not in turn2.text
    assert "与本会话近期上下文中已提供的全文一致" in turn2.text
    assert turn2.read_snapshots[focus.id] == focus_body
    assert focus_body in turn2.full_text

    # Turn 3 (warm, focus file edited): updated focus body is immediately re-emitted verbatim!
    updated_body = focus_body + "\n新增结尾段落。"
    focus.content = updated_body
    db_session.add(focus)
    db_session.commit()
    turn3 = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": focus.id},
        language="zh",
        chat_session_id=chat.id,
        prior_context_fingerprints=turn2.context_fingerprints,
    )
    assert updated_body in turn3.text
    assert turn3.read_snapshots[focus.id] == updated_body


# ---------------------------------------------------------------------------
# Deduplicated empty-file soft-delete helper advances updated_at consistently
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_shared_soft_delete_empty_file_advances_updated_at_and_guards_version(db_session: Session):
    _user, project, _chat = _seed(db_session)
    old_ts = utcnow().replace(tzinfo=None) - timedelta(seconds=5)
    empty_file = File(
        project_id=project.id,
        title="空白占位",
        content="",
        file_type="draft",
        updated_at=old_ts,
    )
    db_session.add(empty_file)
    db_session.commit()
    db_session.refresh(empty_file)

    # Wrong version token refuses deletion.
    deleted_bad, _ = soft_delete_empty_file_if_unchanged(
        db_session,
        file_id=empty_file.id,
        project_id=project.id,
        expected_updated_at=(old_ts + timedelta(seconds=1)).isoformat(),
    )
    assert deleted_bad is False

    # Matching version token soft-deletes and advances updated_at.
    deleted_ok, title = soft_delete_empty_file_if_unchanged(
        db_session,
        file_id=empty_file.id,
        project_id=project.id,
        expected_updated_at=empty_file.updated_at.isoformat(),
    )
    db_session.commit()
    assert deleted_ok is True
    assert title == "空白占位"

    db_session.expire_all()
    refreshed = db_session.get(File, empty_file.id)
    assert refreshed is not None
    assert refreshed.is_deleted is True
    assert refreshed.deleted_at is not None
    assert refreshed.updated_at > old_ts
