"""Regression tests for Tier 0 zero-quality-risk MiniMax quota safety optimizations."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop.compaction import (
    SKIPPED_EMPTY_OR_TRUNCATED,
    SKIPPED_PROVIDER_ERROR,
    CompactionOutcome,
    should_compact,
    skipped_record,
)
from agent.loop.history import LoopHistory, load_loop_history
from agent.tools import mcp_tools
from agent.tools.file_ops.serialization import (
    QUERY_FILES_LARGE_LISTING_CONTENT_PREVIEW_CHARS,
    QUERY_FILES_LARGE_LISTING_REFERENCE_PREVIEW_CHARS,
    QUERY_FILES_LIST_FULL_MAX_TOTAL_CHARS,
)
from models import ChatMessage, ChatSession, File, Project, User
from services.core.auth_service import hash_password


def _make_user_and_project(db_session: Session, suffix: str) -> tuple[User, Project]:
    user = User(
        email=f"tier0_{suffix}@example.com",
        username=f"tier0_{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name=f"Tier0-{suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


def _make_req_ctx(db_session: Session, user: User, project: Project) -> mcp_tools.AgentRequestContext:
    engine = db_session.get_bind()
    return mcp_tools.AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id=None,
        create_session_func=lambda: Session(engine),
        read_snapshots={},
    )


def _history(**kwargs) -> LoopHistory:
    defaults = {
        "items": [{"role": "user", "content": "写第1章"}, {"role": "assistant", "content": "写好了。"}],
        "last_message_id": "msg-9",
        "author_messages": ["写第1章"],
    }
    defaults.update(kwargs)
    return LoopHistory(**defaults)


# ---------------------------------------------------------------------------
# 1. Compaction backoff: provider_error vs empty_or_truncated_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_provider_error_skip_does_not_back_off_while_model_output_failure_does():
    # Transient provider_error retries on the very next turn instead of waiting +100k.
    provider_err_history = _history(
        last_prompt_tokens=325_000,
        compaction_skipped_at_tokens=320_000,
        compaction_skipped_reason=SKIPPED_PROVIDER_ERROR,
    )
    assert should_compact(provider_err_history, threshold=300_000, retry_growth=100_000) is True

    # Model output failure (empty_or_truncated_summary) still applies +100k growth backoff.
    bad_summary_below = _history(
        last_prompt_tokens=380_000,
        compaction_skipped_at_tokens=320_000,
        compaction_skipped_reason=SKIPPED_EMPTY_OR_TRUNCATED,
    )
    assert should_compact(bad_summary_below, threshold=300_000, retry_growth=100_000) is False

    bad_summary_grown = _history(
        last_prompt_tokens=420_000,
        compaction_skipped_at_tokens=320_000,
        compaction_skipped_reason=SKIPPED_EMPTY_OR_TRUNCATED,
    )
    assert should_compact(bad_summary_grown, threshold=300_000, retry_growth=100_000) is True


@pytest.mark.unit
def test_load_loop_history_preserves_compaction_skipped_reason_from_db(db_session: Session):
    user, project = _make_user_and_project(db_session, "skip_reason_db")
    chat = ChatSession(user_id=user.id, project_id=project.id, title="会话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    base = datetime(2026, 11, 22, 10, 0, 0)
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="写第1章", created_at=base))
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="已写完",
            created_at=base + timedelta(seconds=1),
            message_metadata=json.dumps(
                {
                    "engine": "loop",
                    "prompt_tokens": 340_000,
                    "compaction_skipped": {"reason": SKIPPED_PROVIDER_ERROR, "at_prompt_tokens": 335_000},
                }
            ),
        )
    )
    db_session.commit()

    history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert history.compaction_skipped_at_tokens == 335_000
    assert history.compaction_skipped_reason == SKIPPED_PROVIDER_ERROR
    assert should_compact(history, threshold=300_000, retry_growth=100_000) is True

    # A subsequent empty_or_truncated_summary skip DOES trigger backoff.
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="继续", created_at=base + timedelta(seconds=2)))
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="已继续",
            created_at=base + timedelta(seconds=3),
            message_metadata=json.dumps(
                {
                    "engine": "loop",
                    "prompt_tokens": 355_000,
                    "compaction_skipped": {"reason": SKIPPED_EMPTY_OR_TRUNCATED, "at_prompt_tokens": 350_000},
                }
            ),
        )
    )
    db_session.commit()

    history2 = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert history2.compaction_skipped_at_tokens == 350_000
    assert history2.compaction_skipped_reason == SKIPPED_EMPTY_OR_TRUNCATED
    assert should_compact(history2, threshold=300_000, retry_growth=100_000) is False


# ---------------------------------------------------------------------------
# 3. Current-turn <turn_context> + user message estimate in should_compact
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_should_compact_includes_current_turn_extra_tokens():
    near_limit = _history(last_prompt_tokens=291_354)
    assert should_compact(near_limit, threshold=300_000) is False
    assert should_compact(near_limit, threshold=300_000, extra_tokens=12_647) is True
    # Brand-new session (no history items) never triggers compaction even with large extra_tokens.
    assert should_compact(LoopHistory(), threshold=300_000, extra_tokens=100_000) is False

    rec = skipped_record(near_limit, SKIPPED_EMPTY_OR_TRUNCATED)
    assert rec["at_prompt_tokens"] == 291_354


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_agent_service_compacts_before_crossing_300k_with_current_turn_context(db_session: Session):
    from agent.service import AgentService

    user, project = _make_user_and_project(db_session, "turn_ctx_compact")
    chat = ChatSession(user_id=user.id, project_id=project.id, title="会话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    base = datetime(2026, 11, 22, 11, 0, 0)
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="写第1章", created_at=base))
    db_session.add(
        ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="第1章已写完。",
            created_at=base + timedelta(seconds=1),
            message_metadata=json.dumps({"engine": "loop", "prompt_tokens": 295_000}),
        )
    )
    db_session.commit()

    compacted_record = {
        "summary": "交接摘要",
        "covers_through_message_id": "row-1",
        "kept_author_messages": ["写第1章"],
        "created_at": "2026-11-22T11:05:00",
    }
    compact_mock = AsyncMock(
        return_value=CompactionOutcome(
            record=compacted_record,
            items=[{"role": "user", "content": "写第1章"}, {"role": "user", "content": "【上下文已压缩】交接摘要"}],
        )
    )

    def fake_loop(_state, **_kwargs):
        async def _gen():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "好的，已承接。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _gen()

    # ~10k Chinese chars -> ~6,500 provider tokens; 295,000 + 6,500 > 300,000 -> must compact!
    long_prompt = "请结合以下设定续写下一章。" * 850
    with (
        patch("agent.service.compact_history", compact_mock),
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        async for _frame in AgentService().process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message=long_prompt,
            session=db_session,
            session_id=chat.id,
        ):
            pass

    compact_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# 4. Eliminate query_files(id=None, response_mode="full") multi-chapter bombs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_list_full_with_query_downgrades_to_summary_matches_and_skips_snapshots(db_session: Session):
    user, project = _make_user_and_project(db_session, "list_full_query")
    ch1 = File(
        project_id=project.id,
        title="第一章 雨夜",
        file_type="draft",
        content=("雾气弥漫在港口石阶上。" * 80) + "温梨摸出袖中青玉佩。" + ("海风渐紧。" * 80),
    )
    ch2 = File(
        project_id=project.id,
        title="青玉佩资料",
        file_type="lore",
        content="这是一份仅标题包含关键词的设定卡片，正文讲述港口历史。",
    )
    db_session.add_all([ch1, ch2])
    db_session.commit()

    req_ctx = _make_req_ctx(db_session, user, project)
    try:
        for extra_args in ({"response_mode": "full"}, {"include_content": True}):
            raw = await mcp_tools.query_files({"query": "青玉佩", **extra_args}, request_context=req_ctx)
            payload = json.loads(raw["content"][0]["text"])
            assert payload["status"] == "success"
            assert "note" in payload
            assert "query_files(ids=[...])" in payload["note"]
            rows = {row["id"]: row for row in payload["data"]}
            assert ch1.id in rows and ch2.id in rows
            for row in rows.values():
                assert "content" not in row
            assert rows[ch1.id]["match_count"] == 1
            assert "青玉佩" in rows[ch1.id]["matches"][0]
            assert "content_preview" not in rows[ch1.id]
            assert rows[ch2.id]["match_count"] == 0
            assert "content_preview" in rows[ch2.id]
            # Downgraded summary rows MUST NEVER record full-read snapshots (pending or promoted).
            req_ctx.promote_pending_reads()
            assert req_ctx.read_snapshot(ch1.id) is None
            assert req_ctx.read_snapshot(ch2.id) is None
    finally:
        req_ctx.clear()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_list_full_without_query_allows_small_pack_and_downgrades_over_25k(db_session: Session):
    user, project = _make_user_and_project(db_session, "list_full_cap")
    small_chars = [
        File(project_id=project.id, title=f"角色{i}", file_type="character", content="人物设定内容。" * 500)
        for i in range(3)
    ]  # 3 * 3,500 = 10,500 chars <= 25,000
    large_drafts = [
        File(project_id=project.id, title=f"第{i}章", file_type="draft", content="章节正文段落。" * 1_600)
        for i in range(3)
    ]  # 3 * 11,200 = 33,600 chars > 25,000
    db_session.add_all([*small_chars, *large_drafts])
    db_session.commit()

    req_ctx = _make_req_ctx(db_session, user, project)
    try:
        # Small character pack (<= 25,000 chars) keeps full content intact!
        small_raw = await mcp_tools.query_files(
            {"file_type": "character", "response_mode": "full"},
            request_context=req_ctx,
        )
        small_payload = json.loads(small_raw["content"][0]["text"])
        assert small_payload["status"] == "success"
        assert "note" not in small_payload
        assert len(small_payload["data"]) == 3
        total_small = sum(len(row["content"]) for row in small_payload["data"])
        assert total_small <= QUERY_FILES_LIST_FULL_MAX_TOTAL_CHARS

        # Multi-chapter dump (> 25,000 chars) downgrades to summary + note and NEVER records snapshots!
        big_raw = await mcp_tools.query_files(
            {"file_type": "draft", "response_mode": "full"},
            request_context=req_ctx,
        )
        big_payload = json.loads(big_raw["content"][0]["text"])
        assert big_payload["status"] == "success"
        assert "note" in big_payload and "query_files(ids=[...])" in big_payload["note"]
        assert len(big_payload["data"]) == 3
        req_ctx.promote_pending_reads()
        for row in big_payload["data"]:
            assert "content" not in row
            assert "content_preview" in row
            assert row["content_truncated"] is True
            assert "word_count" not in row
            assert req_ctx.read_snapshot(row["id"]) is None

        # Single-file query_files(id=...) remains 100% full-read (explicitly excluded from Batch 1 cap).
        mega = File(project_id=project.id, title="超长单章", file_type="draft", content="长章正文。" * 7_000)
        db_session.add(mega)
        db_session.commit()
        single_raw = await mcp_tools.query_files({"id": mega.id}, request_context=req_ctx)
        single_payload = json.loads(single_raw["content"][0]["text"])
        assert single_payload["status"] == "success"
        assert len(single_payload["data"]) == 1
        assert single_payload["data"][0]["content"] == mega.content
        req_ctx.promote_pending_reads()
        assert req_ctx.read_snapshot(mega.id) == mega.content
    finally:
        req_ctx.clear()


# ---------------------------------------------------------------------------
# 5. Selective list_summary slimming (>20 pure listing rows)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_list_summary_slims_draft_script_previews_over_20_rows_while_preserving_references_and_matches(
    db_session: Session,
):
    user, project = _make_user_and_project(db_session, "list_summary_slim")
    long_body = "甲乙丙丁戊己庚辛壬癸" * 30  # 300 chars
    files = [
        File(project_id=project.id, title=f"第{i}章", file_type="draft", content=f"线索标记{i}：" + long_body, order=i)
        for i in range(12)
    ] + [
        File(project_id=project.id, title=f"第{i}集", file_type="script", content=f"线索标记{i}：" + long_body, order=20 + i)
        for i in range(6)
    ] + [
        File(project_id=project.id, title=f"大纲{i}", file_type="outline", content=f"线索标记{i}：" + long_body, order=40 + i)
        for i in range(3)
    ] + [
        File(project_id=project.id, title=f"人物{i}", file_type="character", content=f"线索标记{i}：" + long_body, order=50 + i)
        for i in range(3)
    ] + [
        File(project_id=project.id, title=f"设定{i}", file_type="lore", content=f"线索标记{i}：" + long_body, order=60 + i)
        for i in range(3)
    ]
    db_session.add_all(files)
    db_session.commit()

    req_ctx = _make_req_ctx(db_session, user, project)
    try:
        # Pure directory listing with > 20 rows (27 rows): draft/script <= 60, outline/character/lore == 100.
        raw_large = await mcp_tools.query_files({"limit": 50}, request_context=req_ctx)
        large_rows = json.loads(raw_large["content"][0]["text"])["data"]
        assert len(large_rows) == 27
        for row in large_rows:
            if row["file_type"] in {"draft", "script"}:
                assert len(row["content_preview"]) == QUERY_FILES_LARGE_LISTING_CONTENT_PREVIEW_CHARS
            else:
                assert len(row["content_preview"]) == QUERY_FILES_LARGE_LISTING_REFERENCE_PREVIEW_CHARS
            assert row["content_truncated"] is True
            assert row["content_length"] > 300

        # Small listing (<= 20 rows) keeps full 200-char default preview.
        raw_small = await mcp_tools.query_files({"limit": 20}, request_context=req_ctx)
        small_rows = json.loads(raw_small["content"][0]["text"])["data"]
        assert len(small_rows) == 20
        for row in small_rows:
            assert len(row["content_preview"]) == 200

        # Explicit content_preview_chars override is respected even above 20 rows.
        raw_explicit = await mcp_tools.query_files(
            {"limit": 50, "content_preview_chars": 150},
            request_context=req_ctx,
        )
        explicit_rows = json.loads(raw_explicit["content"][0]["text"])["data"]
        assert all(len(row["content_preview"]) == 150 for row in explicit_rows)

        # Keyword search keeps matches intact and omits content_preview whenever matches is non-empty.
        raw_search = await mcp_tools.query_files({"query": "线索标记", "limit": 50}, request_context=req_ctx)
        search_rows = json.loads(raw_search["content"][0]["text"])["data"]
        assert len(search_rows) == 27
        for row in search_rows:
            assert row["match_count"] >= 1
            assert row["matches"] and "线索标记" in row["matches"][0]
            assert "content_preview" not in row
    finally:
        req_ctx.clear()
