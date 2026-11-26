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
    # P0-1: Cold DB replay (default allow_slimmed_read_sha256=False) MUST NOT grant
    # read_snapshots carryover for persist-slimmed entries lacking full content in model history.
    assert loader.session_read_snapshots(db_session, chat_session_id=chat.id) == {}

    # Warm wire_prefix_cache hit (allow_slimmed_read_sha256=True) confirms full text is still
    # in model context and verifies via content_sha256 + updated_at.
    snapshots = loader.session_read_snapshots(
        db_session, chat_session_id=chat.id, allow_slimmed_read_sha256=True
    )
    assert snapshots == {chapter.id: large_body}

    # If the author edits the chapter, sha256 mismatch rejects carryover even on warm hit (zero stale bypass).
    chapter.content = large_body + "\n作者新加的一句。"
    db_session.add(chapter)
    db_session.commit()
    assert loader.session_read_snapshots(
        db_session, chat_session_id=chat.id, allow_slimmed_read_sha256=True
    ) == {}


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


# ---------------------------------------------------------------------------
# Review P1-1 / P1-2 / P2-2 regressions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_author_edits_strict_ai_followed_by_reviewed_base_version_is_detected(db_session: Session):
    """P1-1(a): v1=create/ai -> v2=ai_edit('AI edit (reviewed)', force_base=True) must NOT suppress
    the manual-edit alert (ai_fv must join max_strict_ai_ver, not the reviewed row)."""
    from services.features.file_version_service import FileVersionService

    user, project, _chat = _seed(db_session)
    versions = FileVersionService()

    f_diff_len = File(project_id=project.id, title="审查改长章", file_type="draft", content="AI原文。")
    f_same_len = File(project_id=project.id, title="审查同长章", file_type="draft", content="林墨推门。")
    db_session.add_all([f_diff_len, f_same_len])
    db_session.commit()

    versions.create_version(db_session, f_diff_len.id, "AI原文。", change_type="create", change_source="ai")
    versions.create_version(
        db_session,
        f_diff_len.id,
        "作者审查后改写的新正文。",
        change_type="ai_edit",
        change_source="user",
        change_summary="AI edit (reviewed)",
        force_base=True,
    )
    f_diff_len.content = "作者审查后改写的新正文。"

    versions.create_version(db_session, f_same_len.id, "林墨推门。", change_type="create", change_source="ai")
    versions.create_version(
        db_session,
        f_same_len.id,
        "陆沉推门。",
        change_type="ai_edit",
        change_source="user",
        change_summary="AI edit (reviewed)",
        force_base=True,
    )
    f_same_len.content = "陆沉推门。"
    db_session.add_all([f_diff_len, f_same_len])
    db_session.commit()

    block, edited_ids, _by_id = SessionLoader(project.id, user.id).author_edits_for_files(
        db_session, [f_diff_len.id, f_same_len.id], {}
    )
    assert edited_ids == {f_diff_len.id, f_same_len.id}
    assert f_diff_len.id in block and f_same_len.id in block


@pytest.mark.unit
def test_author_edits_diff_ai_version_followed_by_identical_user_save_not_flagged(db_session: Session):
    """P1-1(b): v1=create/user -> v2=edit/ai (diff, is_base_version=False) -> v3=edit/user (identical)
    must reconstruct v2's full text instead of comparing against raw diff, and initial batched SQL
    must not project file_version.content."""
    from services.features.file_version_service import FileVersionService

    user, project, _chat = _seed(db_session)
    versions = FileVersionService()

    f_noop = File(project_id=project.id, title="无变更保存章", file_type="draft", content="初稿第一段。\n第二段。")
    f_changed = File(project_id=project.id, title="真实手改章", file_type="draft", content="初稿第一段。\n第二段。")
    db_session.add_all([f_noop, f_changed])
    db_session.commit()

    # v1 (base, user) -> v2 (diff, ai) -> v3 (identical, user)
    versions.create_version(db_session, f_noop.id, "初稿第一段。\n第二段。", change_type="create", change_source="user")
    v2_noop = versions.create_version(
        db_session, f_noop.id, "AI改写第一段。\n第二段。", change_type="edit", change_source="ai"
    )
    assert v2_noop.is_base_version is False
    versions.create_version(
        db_session, f_noop.id, "AI改写第一段。\n第二段。", change_type="edit", change_source="user"
    )
    f_noop.content = "AI改写第一段。\n第二段。"

    # v1 (base, user) -> v2 (diff, ai) -> v3 (different length, user)
    versions.create_version(db_session, f_changed.id, "初稿第一段。\n第二段。", change_type="create", change_source="user")
    v2_changed = versions.create_version(
        db_session, f_changed.id, "AI改写第一段。\n第二段。", change_type="edit", change_source="ai"
    )
    assert v2_changed.is_base_version is False
    versions.create_version(
        db_session, f_changed.id, "作者大幅扩展后的第一段内容。\n第二段。", change_type="edit", change_source="user"
    )
    f_changed.content = "作者大幅扩展后的第一段内容。\n第二段。"
    db_session.add_all([f_noop, f_changed])
    db_session.commit()

    executed_sql: list[str] = []
    engine = db_session.get_bind()

    def _on_sql(_conn, _cursor, statement, _params, _context, _execmany):
        executed_sql.append(statement)

    event.listen(engine, "before_cursor_execute", _on_sql)
    try:
        # First check only f_changed: distinct length -> 0 TOAST content reads!
        _b1, edited_changed, _ = SessionLoader(project.id, user.id).author_edits_for_files(
            db_session, [f_changed.id], {}
        )
    finally:
        event.remove(engine, "before_cursor_execute", _on_sql)

    assert edited_changed == {f_changed.id}
    fv_sql = [s.lower() for s in executed_sql if "file_version" in s.lower()]
    assert len(fv_sql) == 1
    assert ".content" not in fv_sql[0]

    # Now check both: f_noop reconstructs diff v2 == snap.content -> NOT flagged; f_changed -> flagged.
    block, edited_ids, _ = SessionLoader(project.id, user.id).author_edits_for_files(
        db_session, [f_noop.id, f_changed.id], {}
    )
    assert edited_ids == {f_changed.id}
    assert f_noop.id not in block
    assert f_changed.id in block


@pytest.mark.unit
def test_p1_2_turn_mutation_evicts_fingerprints_so_author_revert_emits_full_v1(db_session: Session):
    """P1-2: Turn 1 starts with focus F=V1, AI edits F->V2, author reverts F->V1 before Turn 2.
    Because Turn 1 mutated F, stored fingerprints evict F (and clear project_files/author_edits),
    so Turn 2 warm-cache context emits full V1 instead of a stale unchanged pointer."""
    from agent.service import _applied_file_mutations_for_fingerprints

    user, project, chat = _seed(db_session)
    v1_body = "【第1章原稿V1】" + ("林墨站在雨夜的巷口等待消息。" * 70)
    focus = File(project_id=project.id, title="第1章", file_type="draft", content=v1_body)
    db_session.add(focus)
    db_session.commit()

    # Turn 1: sends focus F=V1 in full
    turn1 = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": focus.id},
        language="zh",
        chat_session_id=chat.id,
    )
    assert v1_body in turn1.text
    assert focus.id in turn1.context_fingerprints["full_file_hashes"]

    # Turn 1 runs edit_file(focus.id) -> V2; service evicts mutated file_id + clears manifest/author_edits
    tool_calls = [
        {
            "id": "call_edit_1",
            "name": "edit_file",
            "arguments": {"file_id": focus.id, "edits": [{"op": "append", "text": "新加一句"}]},
            "status": "success",
            "result": {"id": focus.id, "edits_applied": 1},
        }
    ]
    mutated_ids, any_mutation = _applied_file_mutations_for_fingerprints(tool_calls)
    assert mutated_ids == {focus.id}
    assert any_mutation is True

    stored_hashes = dict(turn1.context_fingerprints["full_file_hashes"])
    for fid in mutated_ids:
        stored_hashes.pop(fid, None)
    cached_after_turn1 = {
        "project_files_sha256": "" if any_mutation else turn1.context_fingerprints["project_files_sha256"],
        "full_file_hashes": stored_hashes,
        "author_edits_sha256": "" if any_mutation else turn1.context_fingerprints["author_edits_sha256"],
    }

    # Author reverts F back to V1 before Turn 2 (DB has V1 again).
    turn2 = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": focus.id},
        language="zh",
        chat_session_id=chat.id,
        prior_context_fingerprints=cached_after_turn1,
    )
    # Turn 2 MUST emit full V1 and full <project_files> (no stale unchanged pointer!).
    assert v1_body in turn2.text
    assert PROJECT_FILES_UNCHANGED_MARKER not in turn2.text
    assert turn2.read_snapshots[focus.id] == v1_body


@pytest.mark.unit
def test_p2_2_query_result_full_file_ids_covers_dict_containers_and_slimmed_sha():
    """P2-2: query_result_full_file_ids handles dict containers (data/files/items/results)
    and optional slimmed content_sha256 entries consistently."""
    from agent.loop.history import query_result_full_file_ids

    payload = {
        "data": [
            {"id": "f-raw", "file_type": "draft", "content": "完整正文"},
            {"id": "f-slim", "file_type": "draft", "content_sha256": "abc123", "archived_note": "..."},
            {"id": "f-partial", "file_type": "draft", "content": "局部", "content_partial": True},
            {"id": "f-folder", "file_type": "folder", "content": "ignored"},
        ],
        "files": [{"file_id": "f-nested", "file_type": "outline", "content_sha256": "def456"}],
    }
    assert query_result_full_file_ids(payload) == ["f-raw"]
    assert query_result_full_file_ids(payload, include_slimmed_sha256=True) == ["f-raw", "f-slim", "f-nested"]


# ---------------------------------------------------------------------------
# Wave 3 Audit P0-1 & P0-2 regressions
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs")
def test_w3_p0_1_prepare_loop_turn_cold_vs_warm_persist_slimmed_read_snapshots(
    db_session: Session, fake_redis
):
    """P0-1: Cold DB replay never grants read_snapshots for persist-slimmed query_files;
    warm wire_prefix_cache hit with full wire content grants it; a warm entry whose own wire
    items contain only archived_note (cold->warm hop) marks the file archived so it is dropped."""
    user, project, chat = _seed(db_session)
    large_body = "雾港钟声响起，潮水拍打着青石阶，温梨推开了补纸斋的木门。" * 300
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
    msg_u = ChatMessage(session_id=chat.id, role="user", content="看看第1章")
    msg_a = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已读完第1章。",
        tool_calls=json.dumps(
            [
                {
                    "id": "call-qf-1",
                    "name": "query_files",
                    "arguments": {"id": chapter.id},
                    "status": "success",
                    "result": slimmed,
                }
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "prompt_tokens": 12_000}),
    )
    db_session.add_all([msg_u, msg_a])
    db_session.commit()
    db_session.refresh(msg_a)

    service = AgentService()
    system_prompt = MessageManager(project_id=project.id, user_id=user.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=""
    )
    tools_sha = wire_prefix_cache.tools_schema_sha256()

    # 1) Cold DB replay (Redis empty): no snapshot carryover for persist-slimmed entry!
    _hist_cold, turn_cold, _sk, _sp = service._prepare_loop_turn(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
        selected_skill_ids=None,
        message="修改第1章",
        metadata={},
        language="zh",
        expected_provider_key="minimax:MiniMax-M3.1-Flash:primary",
        expected_tools_sha256=tools_sha,
        expected_reasoning_effort="default",
    )
    assert chapter.id not in turn_cold.read_snapshots

    # 2) Warm wire_prefix_cache hit with full wire content: carryover succeeds!
    full_wire_items = [
        {"role": "user", "content": "看看第1章"},
        {"type": "function_call", "call_id": "call-qf-1", "name": "query_files", "arguments": json.dumps({"id": chapter.id})},
        {
            "type": "function_call_output",
            "call_id": "call-qf-1",
            "output": json.dumps({"status": "success", "data": raw_result}, ensure_ascii=False),
        },
        {"role": "assistant", "content": "已读完第1章。"},
    ]
    assert wire_prefix_cache.store_wire_prefix(
        session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        last_assistant_message_id=msg_a.id,
        system_prompt=system_prompt,
        provider_key="minimax:MiniMax-M3.1-Flash:primary",
        tools_sha256=tools_sha,
        reasoning_effort="default",
        items=full_wire_items,
        history_meta={
            "last_prompt_tokens": 12_000,
            "compacted": False,
            "compaction_skipped_at_tokens": 0,
            "compaction_skipped_reason": None,
            "trailing_output_truncations": 0,
            "archived_call_ids": [],
            "archived_file_ids": [],
            "author_messages": ["看看第1章"],
        },
    )
    hist_warm, turn_warm, _sk, _sp = service._prepare_loop_turn(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
        selected_skill_ids=None,
        message="修改第1章",
        metadata={},
        language="zh",
        expected_provider_key="minimax:MiniMax-M3.1-Flash:primary",
        expected_tools_sha256=tools_sha,
        expected_reasoning_effort="default",
    )
    assert turn_warm.read_snapshots.get(chapter.id) == large_body
    assert chapter.id not in hist_warm.archived_file_ids

    # 3) Cold->warm hop where wire items themselves only hold archived_note:
    #    chapter.id is recorded in hist.archived_file_ids so service drops its snapshot!
    slimmed_wire_items = [
        {"role": "user", "content": "看看第1章"},
        {"type": "function_call", "call_id": "call-qf-1", "name": "query_files", "arguments": json.dumps({"id": chapter.id})},
        {
            "type": "function_call_output",
            "call_id": "call-qf-1",
            "output": json.dumps({"status": "success", "data": slimmed}, ensure_ascii=False),
        },
        {"role": "assistant", "content": "已读完第1章。"},
    ]
    assert wire_prefix_cache.store_wire_prefix(
        session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        last_assistant_message_id=msg_a.id,
        system_prompt=system_prompt,
        provider_key="minimax:MiniMax-M3.1-Flash:primary",
        tools_sha256=tools_sha,
        reasoning_effort="default",
        items=slimmed_wire_items,
        history_meta={
            "last_prompt_tokens": 12_000,
            "compacted": False,
            "compaction_skipped_at_tokens": 0,
            "compaction_skipped_reason": None,
            "trailing_output_truncations": 0,
            "archived_call_ids": [],
            "archived_file_ids": [],
            "author_messages": ["看看第1章"],
        },
    )
    hist_hop, _turn_hop, _sk, _sp = service._prepare_loop_turn(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
        selected_skill_ids=None,
        message="修改第1章",
        metadata={},
        language="zh",
        expected_provider_key="minimax:MiniMax-M3.1-Flash:primary",
        expected_tools_sha256=tools_sha,
        expected_reasoning_effort="default",
    )
    assert chapter.id in hist_hop.archived_file_ids


@pytest.mark.unit
def test_w3_p0_2_path_a_query_files_read_updates_fingerprints_preventing_stale_revert_pointer(
    db_session: Session,
):
    """P0-2 Path A: Focus Ch1(V1) -> author edits V2 -> Turn 2 focuses Ch2 & reads Ch1(V2) via
    query_files (no write tools) -> author reverts Ch1 to V1 -> Turn 3 MUST emit full V1."""
    from agent.service import _applied_file_mutations_for_fingerprints, _read_file_hashes_for_fingerprints

    user, project, chat = _seed(db_session)
    v1_body = "【第1章V1】" + ("雾港的灯塔在夜雨里闪烁，温梨站在栈桥尽头。" * 60)
    v2_body = "【第1章V2】" + ("黎明前的海面一片死寂，温梨拆开了那封旧信。" * 60)
    ch2_body = "【第2章】" + ("补纸斋的木门轻轻推开，老周正在研墨。" * 60)
    ch1 = File(project_id=project.id, title="第1章", file_type="draft", content=v1_body)
    ch2 = File(project_id=project.id, title="第2章", file_type="draft", content=ch2_body)
    db_session.add_all([ch1, ch2])
    db_session.commit()

    # Turn 1: Focus Ch1(V1) -> full_file_hashes[ch1.id] = sha(V1)
    turn1 = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": ch1.id},
        language="zh",
        chat_session_id=chat.id,
    )
    assert v1_body in turn1.text
    assert turn1.context_fingerprints["full_file_hashes"][ch1.id] == content_sha256(v1_body)

    # Author edits Ch1 -> V2; Turn 2 focuses Ch2 and AI reads Ch1(V2) via query_files (NO write tools!)
    ch1.content = v2_body
    db_session.add(ch1)
    db_session.commit()
    turn2 = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": ch2.id},
        language="zh",
        chat_session_id=chat.id,
        prior_context_fingerprints=turn1.context_fingerprints,
    )
    # Turn 2 only executed a read-only query_files(id=ch1) returning V2 (no write tools).
    turn2_tool_calls = [
        {
            "id": "call_q_v2",
            "name": "query_files",
            "arguments": {"id": ch1.id},
            "status": "success",
            "result": [{"id": ch1.id, "title": "第1章", "file_type": "draft", "content": v2_body}],
        }
    ]
    read_hashes, read_ids = _read_file_hashes_for_fingerprints(turn2_tool_calls)
    mutated_ids, any_mutation = _applied_file_mutations_for_fingerprints(turn2_tool_calls)
    assert mutated_ids == set() and any_mutation is False
    assert read_ids == {ch1.id}
    assert read_hashes[ch1.id] == content_sha256(v2_body)

    cached_after_turn2 = {
        "project_files_sha256": turn2.context_fingerprints["project_files_sha256"],
        "full_file_hashes": dict(turn2.context_fingerprints["full_file_hashes"]),
        "author_edits_sha256": turn2.context_fingerprints["author_edits_sha256"],
    }
    for fid in read_ids:
        if fid in read_hashes:
            cached_after_turn2["full_file_hashes"][fid] = read_hashes[fid]
        else:
            cached_after_turn2["full_file_hashes"].pop(fid, None)

    # Author reverts Ch1 back to V1 before Turn 3!
    ch1.content = v1_body
    db_session.add(ch1)
    db_session.commit()

    turn3 = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": ch1.id},
        language="zh",
        chat_session_id=chat.id,
        prior_context_fingerprints=cached_after_turn2,
    )
    # Model last saw V2 in Turn 2, so Turn 3 MUST emit full V1 instead of a stale unchanged pointer!
    assert v1_body in turn3.text
    assert "与本会话近期上下文中已提供的全文一致" not in turn3.text


@pytest.mark.unit
def test_w3_p0_2_path_b_author_edits_second_edit_and_read_never_emit_unchanged(
    db_session: Session,
):
    """P0-2 Path B: (1) author_edits_sha256 hashes structured (file_id, updated_at, version_number,
    char_count) so a second author edit V2 -> V3 never emits <author_edits unchanged="true"/>;
    (2) reading an alerted file via query_files clears author_edits_sha256 even with no write tool."""
    from agent.service import _read_file_hashes_for_fingerprints

    user, project, chat = _seed(db_session)
    versions = FileVersionService()

    focus = File(project_id=project.id, title="第1章", content="聚焦章内容。", file_type="draft")
    ch2 = File(project_id=project.id, title="第2章", content="AI初稿。", file_type="draft")
    db_session.add_all([focus, ch2])
    db_session.commit()
    db_session.refresh(focus)
    db_session.refresh(ch2)

    versions.create_version(db_session, ch2.id, "AI初稿。", change_type="create", change_source="ai")
    v2_text = "作者第一次手改V2。"
    ch2.content = v2_text
    ch2.updated_at = utcnow().replace(tzinfo=None) + timedelta(seconds=5)
    db_session.add(ch2)
    db_session.commit()
    versions.create_version(db_session, ch2.id, v2_text, change_type="edit", change_source="user")

    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="写完了第2章。",
            tool_calls=json.dumps(
                [
                    {
                        "id": "wc-ch2",
                        "name": "write_chapter",
                        "arguments": {"chapter": 2, "title": "第2章", "file_type": "draft"},
                        "status": "success",
                        "result": {"id": ch2.id, "title": "第2章", "action": "created", "body_saved": True},
                    }
                ],
                ensure_ascii=False,
            ),
            message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE}),
        )
    )
    db_session.commit()

    turn1 = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": focus.id},
        language="zh",
        chat_session_id=chat.id,
    )
    assert ch2.id in turn1.text
    assert ch2.id in turn1.context_fingerprints["_author_edits_file_ids"]
    sha_v2 = turn1.context_fingerprints["author_edits_sha256"]
    assert sha_v2

    # Subcase 1: Author edits Ch2 a second time (V2 -> V3). Rendered 1-line alert text is identical,
    # yet structured state tuple (updated_at, version_number, char_count) changed -> MUST NOT emit unchanged!
    v3_text = "作者第二次手改V3（新增冲突情节）。"
    ch2.content = v3_text
    ch2.updated_at = utcnow().replace(tzinfo=None) + timedelta(seconds=15)
    db_session.add(ch2)
    db_session.commit()
    versions.create_version(db_session, ch2.id, v3_text, change_type="edit", change_source="user")

    turn2_after_second_edit = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": focus.id},
        language="zh",
        chat_session_id=chat.id,
        prior_context_fingerprints=turn1.context_fingerprints,
    )
    assert AUTHOR_EDITS_UNCHANGED_MARKER not in turn2_after_second_edit.text
    assert ch2.id in turn2_after_second_edit.text
    assert turn2_after_second_edit.context_fingerprints["author_edits_sha256"] != sha_v2

    # Subcase 2: AI reads alerted Ch2 during Turn 2 (no write tools) -> service clears author_edits_sha256!
    turn2_reads = [
        {
            "id": "q-ch2",
            "name": "query_files",
            "arguments": {"id": ch2.id},
            "status": "success",
            "result": [{"id": ch2.id, "title": "第2章", "file_type": "draft", "content": v3_text}],
        }
    ]
    _r_hashes, read_ids = _read_file_hashes_for_fingerprints(turn2_reads)
    alerted_ids = set(turn2_after_second_edit.context_fingerprints["_author_edits_file_ids"])
    assert bool(read_ids & alerted_ids) is True
    cleared_fp = {
        "project_files_sha256": turn2_after_second_edit.context_fingerprints["project_files_sha256"],
        "full_file_hashes": dict(turn2_after_second_edit.context_fingerprints["full_file_hashes"]),
        "author_edits_sha256": "",
    }
    turn3 = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": focus.id},
        language="zh",
        chat_session_id=chat.id,
        prior_context_fingerprints=cleared_fp,
    )
    assert AUTHOR_EDITS_UNCHANGED_MARKER not in turn3.text
    assert ch2.id in turn3.text
