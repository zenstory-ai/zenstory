"""Tests for 7-day chat_message_archive cold table storage, archival, and transparent fallbacks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, SQLModel, select

import models  # noqa: F401
from agent.core.chat_storage import ARCHIVED_QUERY_CONTENT_NOTE
from agent.loop.history import LOOP_ENGINE_METADATA_VALUE, load_loop_history
from models import (
    ChatMessage,
    ChatMessageArchive,
    ChatMessageUxSummary,
    ChatSession,
    File,
    Project,
    User,
)
from scripts.archive_old_chat_messages import build_parser
from scripts.archive_old_chat_messages import main as archive_cli_main
from services.chat_message_archival import (
    GradualArchivalReport,
    archive_chat_messages_batch,
    run_gradual_archival,
)
from services.chat_tool_cards import (
    read_delete_card_deleted_at,
    read_edit_card_before_title,
    record_delete_card_restored,
    record_edit_card_undone,
)
from services.core.auth_service import hash_password
from services.features.file_version_service import content_sha256

SERVER_DIR = Path(__file__).resolve().parents[2]
ARCHIVE_REVISION = "20261126_000000"
PREV_REVISION = "20261124_180000"
PRE_SUMMARY_REVISION = "20261123_000000"


def _seed_user_project_session(
    db_session: Session,
    *,
    password: str = "password123",
) -> tuple[User, Project, ChatSession]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"arch_{suffix}",
        email=f"arch-{suffix}@example.com",
        hashed_password=hash_password(password),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"Archive Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.flush()
    chat = ChatSession(
        user_id=user.id,
        project_id=project.id,
        title="归档测试会话",
        is_active=True,
        message_count=0,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(project)
    db_session.refresh(chat)
    return user, project, chat


def _bloated_assistant_tool_calls(
    *,
    draft_id: str,
    deleted_id: str,
    updated_at: datetime,
    deleted_at: datetime,
) -> str:
    """Build ASCII-escaped, indented tool_calls JSON simulating legacy bloated TOAST rows."""
    payload = [
        {
            "id": "call-query",
            "name": "query_files",
            "arguments": {"id": draft_id},
            "status": "success",
            "result": [
                {
                    "id": draft_id,
                    "title": "第一章 风起青萍",
                    "file_type": "draft",
                    "word_count": 3200,
                    "content": "第一章超长历史正文内容，用于验证归档压缩瘦身。" * 150,
                    "content_preview": "前两百字预览" * 20,
                    "matches": [{"line": 1, "snippet": "命中片段"}],
                    "author_edits": {"changed": True},
                    "file_metadata": '{"chapter_number": 1}',
                }
            ],
        },
        {
            "id": "call-edit",
            "name": "edit_file",
            "arguments": {"id": draft_id},
            "status": "success",
            "result": {
                "id": draft_id,
                "title": "第一章 风起青萍",
                "file_type": "draft",
                "mutation_applied": True,
                "undo": {
                    "before_version_number": 1,
                    "expected_after_updated_at": updated_at.isoformat(),
                    "expected_after_content_sha256": content_sha256("新正文"),
                    "before_title": "第一章 旧标题",
                    "undone": False,
                },
            },
        },
        {
            "id": "call-delete",
            "name": "delete_file",
            "arguments": {"id": deleted_id},
            "status": "success",
            "result": {
                "id": deleted_id,
                "title": "废弃草稿",
                "file_type": "draft",
                "mutation_applied": True,
                "deleted_at": deleted_at.isoformat(),
                "restored": False,
            },
        },
    ]
    # ensure_ascii=True + default whitespace mirrors legacy bloated storage.
    return json.dumps(payload, ensure_ascii=True, indent=2)


# ---------------------------------------------------------------------------
# 1. Core archival batch & gradual archival service tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dry_run_makes_zero_writes_and_advances_cursor_across_batches(db_session: Session) -> None:
    _user, _project, chat = _seed_user_project_session(db_session)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)

    old_ids: list[str] = []
    for idx in range(5):
        msg = ChatMessage(
            id=f"dry-old-{idx}-{uuid4().hex[:6]}",
            session_id=chat.id,
            role="assistant",
            content=f"旧消息 {idx}",
            tool_calls=json.dumps(
                [
                    {
                        "id": f"q-{idx}",
                        "name": "query_files",
                        "status": "success",
                        "result": [{"id": "f1", "title": "章", "content": "大段正文" * 80}],
                    }
                ],
                ensure_ascii=True,
                indent=2,
            ),
            message_metadata=json.dumps(
                {"engine": LOOP_ENGINE_METADATA_VALUE, "stop_reason": "end_turn"},
                ensure_ascii=True,
                indent=2,
            ),
            created_at=cutoff - timedelta(days=5, minutes=idx),
        )
        old_ids.append(msg.id)
        db_session.add(msg)

    recent = ChatMessage(
        id=f"dry-hot-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="user",
        content="近七天内热消息",
        created_at=cutoff + timedelta(hours=2),
    )
    db_session.add(recent)
    db_session.commit()

    report = run_gradual_archival(
        db_session,
        cutoff=cutoff,
        batch_size=2,
        max_batches=10,
        sleep_ms=0,
        dry_run=True,
    )

    assert report.dry_run is True
    assert report.batches_run == 3
    assert report.total_scanned == 5
    assert report.total_archived == 0
    assert report.total_deleted == 0
    assert report.total_bytes_before > report.total_bytes_after
    assert report.bytes_saved > 0
    assert report.has_more is False

    # Zero writes and zero deletes in dry-run mode.
    archived_rows = db_session.exec(
        select(ChatMessageArchive).where(ChatMessageArchive.session_id == chat.id)
    ).all()
    hot_rows = db_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == chat.id)
    ).all()
    assert len(archived_rows) == 0
    assert {row.id for row in hot_rows} == set(old_ids) | {recent.id}


@pytest.mark.integration
def test_archive_copies_compacts_verifies_deletes_old_rows_and_preserves_hot_and_ux_summary(
    db_session: Session,
) -> None:
    """Verify >7d compaction + copy-verify-delete, <=7d preservation, and FK-free UX summary survival."""
    _user, _project, chat = _seed_user_project_session(db_session)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)
    old_ts = cutoff - timedelta(days=3)
    hot_ts = cutoff + timedelta(hours=1)

    bloated_tools = _bloated_assistant_tool_calls(
        draft_id="f-draft-1",
        deleted_id="f-del-1",
        updated_at=old_ts,
        deleted_at=old_ts,
    )
    bloated_meta = json.dumps(
        {
            "engine": LOOP_ENGINE_METADATA_VALUE,
            "stop_reason": "end_turn",
            "display_events": [
                {"type": "thinking_content", "content": "这是一段中文思考内容"},
                {"type": "content", "content": "已完成修改。"},
            ],
        },
        ensure_ascii=True,
        indent=2,
    )
    old_assistant = ChatMessage(
        id=f"old-asst-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="已完成修改。",
        tool_calls=bloated_tools,
        reasoning_content="这是一段中文思考内容",
        message_metadata=bloated_meta,
        created_at=old_ts,
    )
    old_legacy_assistant = ChatMessage(
        id=f"old-legacy-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="旧链路回答",
        reasoning_content="仅存于 reasoning_content 的思考，必须保留",
        message_metadata=json.dumps({"stop_reason": "end_turn"}, ensure_ascii=True, indent=2),
        created_at=old_ts + timedelta(seconds=1),
    )
    hot_msg = ChatMessage(
        id=f"hot-msg-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="热消息",
        message_metadata=json.dumps({"stop_reason": "end_turn"}, ensure_ascii=False),
        created_at=hot_ts,
    )
    db_session.add_all([old_assistant, old_legacy_assistant, hot_msg])
    db_session.commit()

    # Confirm UX summary rows were created by the after_insert listener.
    summary_before = db_session.get(ChatMessageUxSummary, old_assistant.id)
    assert summary_before is not None
    assert summary_before.round_category == "healthy"
    assert summary_before.ai_edit_cards == 1
    assert summary_before.ai_delete_cards == 1

    batch = archive_chat_messages_batch(
        db_session,
        cutoff=cutoff,
        batch_size=50,
        dry_run=False,
    )

    assert batch.scanned == 2
    assert batch.archived == 2
    assert batch.deleted == 2
    assert batch.bytes_before > batch.bytes_after
    assert batch.has_more is False
    assert batch.oldest_remaining_at == hot_ts

    db_session.expire_all()

    # Hot table keeps only <=7d rows.
    assert db_session.get(ChatMessage, old_assistant.id) is None
    assert db_session.get(ChatMessage, old_legacy_assistant.id) is None
    assert db_session.get(ChatMessage, hot_msg.id) is not None

    # Cold table holds compacted UTF-8 rows.
    cold_main = db_session.get(ChatMessageArchive, old_assistant.id)
    assert cold_main is not None
    assert cold_main.archived_at is not None
    assert cold_main.reasoning_content is None  # duplicate cleared losslessly
    assert cold_main.message_metadata is not None
    assert "\\u" not in cold_main.message_metadata
    assert ": " not in cold_main.message_metadata
    assert cold_main.tool_calls is not None
    assert "\\u" not in cold_main.tool_calls

    parsed_tools = json.loads(cold_main.tool_calls)
    # query_files heavy content stripped; edit_file & delete_file preserved 100%.
    assert "content" not in parsed_tools[0]["result"][0]
    assert parsed_tools[0]["result"][0]["archived_note"] == ARCHIVED_QUERY_CONTENT_NOTE
    assert parsed_tools[1]["result"]["undo"]["before_version_number"] == 1
    assert parsed_tools[2]["result"]["restored"] is False

    # Legacy row without thinking_content in display_events preserves reasoning_content.
    cold_legacy = db_session.get(ChatMessageArchive, old_legacy_assistant.id)
    assert cold_legacy is not None
    assert cold_legacy.reasoning_content == "仅存于 reasoning_content 的思考，必须保留"

    # CRITICAL INVARIANT: chat_message_ux_summary rows SURVIVE archival deletion!
    summary_after = db_session.get(ChatMessageUxSummary, old_assistant.id)
    assert summary_after is not None
    assert summary_after.round_category == "healthy"
    assert summary_after.ai_edit_cards == 1
    assert summary_after.ai_delete_cards == 1


@pytest.mark.integration
def test_archive_idempotent_rerun_and_partial_verification_failure_never_deletes(
    db_session: Session,
) -> None:
    _user, _project, chat = _seed_user_project_session(db_session)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)
    old_ts = cutoff - timedelta(days=4)

    m1 = ChatMessage(
        id=f"idem-1-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="user",
        content="第一条旧消息",
        created_at=old_ts,
    )
    m2 = ChatMessage(
        id=f"idem-2-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="第二条旧消息",
        created_at=old_ts + timedelta(seconds=1),
    )
    db_session.add_all([m1, m2])
    db_session.commit()

    # 1) Partial verification failure: simulate missing m2 in verification result.
    with patch(
        "services.chat_message_archival._verify_archived_ids",
        return_value={m1.id},
    ):
        failed_batch = archive_chat_messages_batch(
            db_session, cutoff=cutoff, batch_size=10, dry_run=False
        )

    assert failed_batch.scanned == 2
    assert failed_batch.deleted == 0
    # Both hot rows MUST remain untouched when verification fails!
    db_session.expire_all()
    assert db_session.get(ChatMessage, m1.id) is not None
    assert db_session.get(ChatMessage, m2.id) is not None

    # 2) Pre-insert m1 into archive (simulating an interrupted prior run where copy
    #    committed before delete) -> idempotent re-run must upsert via ON CONFLICT DO UPDATE,
    #    verify both, and delete both hot rows cleanly.
    db_session.add(
        ChatMessageArchive(
            id=m1.id,
            session_id=chat.id,
            role=m1.role,
            content=m1.content,
            created_at=m1.created_at,
            archived_at=cutoff,
        )
    )
    db_session.commit()

    ok_batch = archive_chat_messages_batch(db_session, cutoff=cutoff, batch_size=10, dry_run=False)
    assert ok_batch.scanned == 2
    assert ok_batch.archived == 2  # m1 upserted via ON CONFLICT DO UPDATE; m2 inserted
    assert ok_batch.deleted == 2
    db_session.expire_all()
    assert db_session.get(ChatMessage, m1.id) is None
    assert db_session.get(ChatMessage, m2.id) is None
    assert db_session.get(ChatMessageArchive, m1.id) is not None
    assert db_session.get(ChatMessageArchive, m2.id) is not None

    # 3) Running again on already-empty >7d window is a clean no-op.
    empty_batch = archive_chat_messages_batch(
        db_session, cutoff=cutoff, batch_size=10, dry_run=False
    )
    assert empty_batch.scanned == 0
    assert empty_batch.archived == 0
    assert empty_batch.deleted == 0
    assert empty_batch.has_more is False


@pytest.mark.integration
def test_gradual_archival_aborts_and_cli_returns_nonzero_on_verification_failure(
    db_session: Session,
) -> None:
    """Live verification failure must abort immediately without archiving newer batches, and CLI exits 1."""
    _user, _project, chat = _seed_user_project_session(db_session)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)
    old_ts = cutoff - timedelta(days=5)

    created_ids: list[str] = []
    for idx in range(4):
        msg = ChatMessage(
            id=f"abort-{idx}-{uuid4().hex[:6]}",
            session_id=chat.id,
            role="user",
            content=f"旧消息 {idx}",
            created_at=old_ts + timedelta(minutes=idx),
        )
        created_ids.append(msg.id)
        db_session.add(msg)
    db_session.commit()

    with patch(
        "services.chat_message_archival._verify_archived_ids",
        return_value=set(),
    ):
        report = run_gradual_archival(
            db_session,
            cutoff=cutoff,
            batch_size=2,
            max_batches=5,
            sleep_ms=0,
            dry_run=False,
        )

    assert report.aborted is True
    assert report.verification_failed is True
    assert report.batches_run == 1  # aborted immediately on first failed batch
    assert report.total_deleted == 0
    # ALL 4 hot rows stay untouched (batch 2 was never archived/deleted).
    db_session.expire_all()
    remaining_ids = {
        row.id
        for row in db_session.exec(
            select(ChatMessage).where(ChatMessage.session_id == chat.id)
        ).all()
    }
    assert remaining_ids == set(created_ids)

    # CLI exit code must be 1 on aborted run and 0 on clean run.
    with patch(
        "scripts.archive_old_chat_messages.run_gradual_archival",
        return_value=GradualArchivalReport(aborted=True, verification_failed=True),
    ):
        assert archive_cli_main(["--max-batches", "1"]) == 1

    with patch(
        "scripts.archive_old_chat_messages.run_gradual_archival",
        return_value=GradualArchivalReport(aborted=False, verification_failed=False),
    ):
        assert archive_cli_main(["--max-batches", "1"]) == 0


# ---------------------------------------------------------------------------
# 2. Transparent API, feedback, tool cards, clear_session, and loop history
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_recent_feedback_clear_session_transparently_bridge_hot_and_cold(
    client: AsyncClient,
    db_session: Session,
) -> None:
    user, project, chat = _seed_user_project_session(db_session)
    base = datetime(2026, 11, 10, 10, 0, 0)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)

    # Seed 3 old messages (>7d) and 1 recent message (<=7d).
    old_u1 = ChatMessage(
        id=f"api-u1-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="user",
        content="十天前的问题",
        created_at=base,
    )
    old_a1 = ChatMessage(
        id=f"api-a1-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="十天前的回答",
        tool_calls=json.dumps(
            [
                {
                    "id": "qf-1",
                    "name": "query_files",
                    "status": "success",
                    "result": [{"id": "f1", "title": "第一章", "content": "大段正文" * 50}],
                }
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "stop_reason": "end_turn",
                "compaction": {
                    "summary": "历史摘要",
                    "covers_through_message_id": old_u1.id,
                    "kept_author_messages": ["这是一段只给模型回放的原话"],
                },
            },
            ensure_ascii=False,
        ),
        created_at=base + timedelta(minutes=1),
    )
    hot_u2 = ChatMessage(
        id=f"api-u2-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="user",
        content="今天回访的新问题",
        created_at=cutoff + timedelta(hours=3),
    )
    db_session.add_all([old_u1, old_a1, hot_u2])
    chat.message_count = 3
    db_session.commit()

    # Archive >7d messages (old_u1, old_a1 -> chat_message_archive; hot_u2 stays in chat_message).
    res = archive_chat_messages_batch(db_session, cutoff=cutoff, batch_size=50, dry_run=False)
    assert res.deleted == 2

    login = await client.post(
        "/api/auth/login",
        data={"username": user.username, "password": "password123"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # 1) GET /recent merges cold + hot chronologically and strips kept_author_messages & query content.
    recent_resp = await client.get(
        f"/api/v1/chat/session/{project.id}/recent",
        params={"limit": 10},
        headers=headers,
    )
    assert recent_resp.status_code == 200
    items = recent_resp.json()
    assert [item["id"] for item in items] == [old_u1.id, old_a1.id, hot_u2.id]
    a1_meta = json.loads(items[1]["metadata"])
    assert "kept_author_messages" not in a1_meta["compaction"]
    assert a1_meta["compaction"]["summary"] == "历史摘要"

    # 2) GET /recent?message_id=<archived_id> finds the message in chat_message_archive.
    single_resp = await client.get(
        f"/api/v1/chat/session/{project.id}/recent",
        params={"message_id": old_a1.id},
        headers=headers,
    )
    assert single_resp.status_code == 200
    single_items = single_resp.json()
    assert len(single_items) == 1
    assert single_items[0]["id"] == old_a1.id

    # 3) POST /messages/{archived_id}/feedback works on archived message AND updates UX summary.
    fb_resp = await client.post(
        f"/api/v1/chat/messages/{old_a1.id}/feedback",
        headers=headers,
        json={"vote": "down", "preset": "off_topic", "comment": "历史回复评价"},
    )
    assert fb_resp.status_code == 200
    fb_data = fb_resp.json()
    assert fb_data["message_id"] == old_a1.id
    assert fb_data["feedback"]["vote"] == "down"
    assert fb_data["feedback"]["preset"] == "off_topic"

    db_session.expire_all()
    archived_row = db_session.get(ChatMessageArchive, old_a1.id)
    assert archived_row is not None
    assert json.loads(archived_row.message_metadata or "{}")["feedback"]["vote"] == "down"
    ux_summary = db_session.get(ChatMessageUxSummary, old_a1.id)
    assert ux_summary is not None
    assert ux_summary.feedback_vote == "down"
    assert ux_summary.feedback_preset == "off_topic"

    # 4) DELETE /session/{project_id} cleans hot, cold, AND ux_summary!
    clear_resp = await client.delete(
        f"/api/v1/chat/session/{project.id}",
        headers=headers,
    )
    assert clear_resp.status_code == 200
    assert "Cleared 3 messages" in clear_resp.json()["message"]

    db_session.expire_all()
    assert db_session.exec(select(ChatMessage).where(ChatMessage.session_id == chat.id)).all() == []
    assert (
        db_session.exec(
            select(ChatMessageArchive).where(ChatMessageArchive.session_id == chat.id)
        ).all()
        == []
    )
    assert (
        db_session.exec(
            select(ChatMessageUxSummary).where(ChatMessageUxSummary.session_id == chat.id)
        ).all()
        == []
    )


@pytest.mark.integration
def test_chat_tool_cards_read_and_mutate_archived_messages_and_sync_ux_summary(
    db_session: Session,
) -> None:
    user, project, chat = _seed_user_project_session(db_session)
    old_ts = datetime(2026, 11, 10, 8, 0, 0)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)

    draft = File(
        id=f"draft-{uuid4().hex[:6]}",
        project_id=project.id,
        title="第一章 风起青萍",
        file_type="draft",
        content="新正文",
        updated_at=old_ts,
    )
    deleted = File(
        id=f"del-{uuid4().hex[:6]}",
        project_id=project.id,
        title="废弃草稿",
        file_type="draft",
        content="旧正文",
        is_deleted=True,
        deleted_at=old_ts,
    )
    db_session.add_all([draft, deleted])
    db_session.commit()

    msg = ChatMessage(
        id=f"card-arch-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="已完成修改和删除。",
        tool_calls=_bloated_assistant_tool_calls(
            draft_id=draft.id,
            deleted_id=deleted.id,
            updated_at=old_ts,
            deleted_at=old_ts,
        ),
        message_metadata=json.dumps({"stop_reason": "end_turn"}, ensure_ascii=False),
        created_at=old_ts,
    )
    db_session.add(msg)
    db_session.commit()

    archive_chat_messages_batch(db_session, cutoff=cutoff, batch_size=10, dry_run=False)
    assert db_session.get(ChatMessage, msg.id) is None
    assert db_session.get(ChatMessageArchive, msg.id) is not None

    # Read helpers fall back to chat_message_archive.
    before_title, after_title = read_edit_card_before_title(
        db_session,
        user_id=user.id,
        project_id=project.id,
        file_id=draft.id,
        message_id=msg.id,
        tool_use_id="call-edit",
        before_version_number=1,
        expected_after_updated_at=old_ts,
    )
    assert before_title == "第一章 旧标题"
    assert after_title == "第一章 风起青萍"

    deleted_at_read = read_delete_card_deleted_at(
        db_session,
        user_id=user.id,
        project_id=project.id,
        file_id=deleted.id,
        message_id=msg.id,
        tool_use_id="call-delete",
    )
    assert deleted_at_read is not None

    # Mutation helpers update chat_message_archive AND sync chat_message_ux_summary!
    assert record_edit_card_undone(
        db_session,
        user_id=user.id,
        project_id=project.id,
        file_id=draft.id,
        message_id=msg.id,
        tool_use_id="call-edit",
        before_version_number=1,
        expected_after_updated_at=old_ts,
    )
    assert record_delete_card_restored(
        db_session,
        user_id=user.id,
        project_id=project.id,
        file_id=deleted.id,
        message_id=msg.id,
        tool_use_id="call-delete",
        restored_deleted_at=old_ts,
    )

    db_session.expire_all()
    archived = db_session.get(ChatMessageArchive, msg.id)
    assert archived is not None
    calls = json.loads(archived.tool_calls or "[]")
    assert calls[1]["result"]["undo"]["undone"] is True
    assert calls[2]["result"]["restored"] is True

    summary = db_session.get(ChatMessageUxSummary, msg.id)
    assert summary is not None
    assert summary.ai_edit_undone == 1
    assert summary.ai_delete_restored == 1


@pytest.mark.integration
def test_load_loop_history_seamlessly_continues_returning_session_across_cold_and_hot(
    db_session: Session,
) -> None:
    user, project, chat = _seed_user_project_session(db_session)
    old_base = datetime(2026, 11, 5, 9, 0, 0)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)

    # 1) Entire session archived (>7d): user returns and loads history before sending a new turn.
    u1 = ChatMessage(
        id="ret-1",
        session_id=chat.id,
        role="user",
        content="设定主角叫沈砚，擅长机关术。",
        created_at=old_base,
    )
    a1 = ChatMessage(
        id="ret-2",
        session_id=chat.id,
        role="assistant",
        content="好的，已记住主角沈砚擅长机关术。",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "prompt_tokens": 1200,
                "display_events": [{"type": "content", "content": "好的，已记住主角沈砚擅长机关术。"}],
            },
            ensure_ascii=False,
        ),
        created_at=old_base + timedelta(minutes=1),
    )
    db_session.add_all([u1, a1])
    db_session.commit()

    archive_chat_messages_batch(db_session, cutoff=cutoff, batch_size=50, dry_run=False)
    assert db_session.exec(select(ChatMessage).where(ChatMessage.session_id == chat.id)).all() == []

    cold_only_history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
    )
    assert cold_only_history.last_message_id == a1.id
    assert cold_only_history.last_prompt_tokens == 1200
    assert [item["content"] for item in cold_only_history.items] == [
        "设定主角叫沈砚，擅长机关术。",
        "好的，已记住主角沈砚擅长机关术。",
    ]

    # 2) User sends a new turn (saved to hot chat_message) -> history seamlessly merges cold + hot.
    u2 = ChatMessage(
        id="ret-3",
        session_id=chat.id,
        role="user",
        content="十天后回来了，接着写第二章。",
        created_at=cutoff + timedelta(hours=1),
    )
    a2 = ChatMessage(
        id="ret-4",
        session_id=chat.id,
        role="assistant",
        content="继续承接沈砚的机关术线索写第二章。",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "prompt_tokens": 2400,
                "display_events": [
                    {"type": "content", "content": "继续承接沈砚的机关术线索写第二章。"}
                ],
            },
            ensure_ascii=False,
        ),
        created_at=cutoff + timedelta(hours=1, minutes=1),
    )
    db_session.add_all([u2, a2])
    db_session.commit()

    merged_history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
    )
    assert merged_history.last_message_id == a2.id
    assert merged_history.last_prompt_tokens == 2400
    assert [item["content"] for item in merged_history.items] == [
        "设定主角叫沈砚，擅长机关术。",
        "好的，已记住主角沈砚擅长机关术。",
        "十天后回来了，接着写第二章。",
        "继续承接沈砚的机关术线索写第二章。",
    ]

    # 3) Hot compaction whose covers_through_message_id points to an archived row in cold table!
    a3 = ChatMessage(
        id="ret-5",
        session_id=chat.id,
        role="assistant",
        content="已基于压缩摘要续写第三章。",
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "prompt_tokens": 900,
                "compaction": {
                    "summary": "沈砚在第一章展示机关术。",
                    "covers_through_message_id": a1.id,
                    "kept_author_messages": ["设定主角叫沈砚，擅长机关术。"],
                },
                "display_events": [
                    {"type": "content", "content": "已基于压缩摘要续写第三章。"}
                ],
            },
            ensure_ascii=False,
        ),
        created_at=cutoff + timedelta(hours=2),
    )
    db_session.add(a3)
    db_session.commit()

    compacted_history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
    )
    assert compacted_history.compacted is True
    assert compacted_history.items[0] == {"role": "user", "content": "设定主角叫沈砚，擅长机关术。"}
    assert "沈砚在第一章展示机关术。" in compacted_history.items[1]["content"]
    assert compacted_history.items[-1]["content"] == "已基于压缩摘要续写第三章。"


@pytest.mark.integration
def test_load_loop_history_interleaved_hot_and_cold_preserves_order_and_compaction_anchor(
    db_session: Session,
) -> None:
    """Interleaved hot+cold rows (e.g. SKIP LOCKED skip on m2 while m3 archived) stay strictly ordered."""
    user, project, chat = _seed_user_project_session(db_session)
    t1 = datetime(2026, 11, 5, 9, 0, 0)
    t2 = t1 + timedelta(minutes=1)
    t3 = t1 + timedelta(minutes=2)
    t4 = t1 + timedelta(minutes=3)

    # m1 (t1, cold), m2 (t2, hot - skipped lock!), m3 (t3, cold with compaction covering m2),
    # and duplicate m2 also in cold with stale content (hot m2 must win on dedup!).
    db_session.add(
        ChatMessageArchive(
            id="inter-1",
            session_id=chat.id,
            role="user",
            content="第一轮问题",
            created_at=t1,
            archived_at=t4,
        )
    )
    db_session.add(
        ChatMessage(
            id="inter-2",
            session_id=chat.id,
            role="assistant",
            content="热表最新版本的第二轮回答",
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "prompt_tokens": 1111,
                    "display_events": [{"type": "content", "content": "热表最新版本的第二轮回答"}],
                },
                ensure_ascii=False,
            ),
            created_at=t2,
        )
    )
    db_session.add(
        ChatMessageArchive(
            id="inter-2",
            session_id=chat.id,
            role="assistant",
            content="冷表旧副本不应被采用",
            created_at=t2,
            archived_at=t4,
        )
    )
    db_session.add(
        ChatMessageArchive(
            id="inter-3",
            session_id=chat.id,
            role="assistant",
            content="冷表更新的第三轮（带压缩点覆盖 inter-2）",
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "prompt_tokens": 3333,
                    "compaction": {
                        "summary": "已覆盖到第二轮的交接摘要。",
                        "covers_through_message_id": "inter-2",
                        "kept_author_messages": ["第一轮问题"],
                    },
                    "display_events": [
                        {"type": "content", "content": "冷表更新的第三轮（带压缩点覆盖 inter-2）"}
                    ],
                },
                ensure_ascii=False,
            ),
            created_at=t3,
            archived_at=t4,
        )
    )
    db_session.commit()

    history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
    )
    assert history.last_message_id == "inter-3"
    assert history.last_prompt_tokens == 3333
    assert history.compacted is True
    assert history.items[0] == {"role": "user", "content": "第一轮问题"}
    assert "已覆盖到第二轮的交接摘要。" in history.items[1]["content"]
    assert history.items[-1]["content"] == "冷表更新的第三轮（带压缩点覆盖 inter-2）"

    # Also verify non-compacted interleaved order: add hot m4 (t4) without compaction on a fresh session.
    _u2, proj2, chat2 = _seed_user_project_session(db_session)
    db_session.add_all(
        [
            ChatMessage(id="ord-1", session_id=chat2.id, role="user", content="t1-hot", created_at=t1),
            ChatMessageArchive(
                id="ord-2", session_id=chat2.id, role="assistant", content="t2-cold", created_at=t2, archived_at=t4
            ),
            ChatMessage(id="ord-3", session_id=chat2.id, role="user", content="t3-hot", created_at=t3),
            ChatMessageArchive(
                id="ord-4", session_id=chat2.id, role="assistant", content="t4-cold", created_at=t4, archived_at=t4
            ),
        ]
    )
    db_session.commit()

    ordered = load_loop_history(
        db_session,
        project_id=proj2.id,
        user_id=_u2.id,
        chat_session_id=chat2.id,
    )
    assert ordered.last_message_id == "ord-4"
    assert [item["content"] for item in ordered.items] == [
        "t1-hot",
        "t2-cold",
        "t3-hot",
        "t4-cold",
    ]


@pytest.mark.integration
def test_admin_list_chat_feedback_votes_includes_archived_messages_and_dedups_hot(
    db_session: Session,
) -> None:
    """list_chat_feedback_votes(days=30) must include >7d archived votes and prefer hot on duplicate IDs."""
    from datetime import UTC

    from agent.service import _project_has_ai_reply
    from services.usage.admin_ux_metrics_service import list_chat_feedback_votes

    user, project, chat = _seed_user_project_session(db_session)
    now_utc = datetime(2026, 11, 26, 8, 0, tzinfo=UTC)
    now_naive = now_utc.replace(tzinfo=None)
    old_ts = now_naive - timedelta(days=14)
    recent_ts = now_naive - timedelta(days=2)
    cutoff = now_naive - timedelta(days=7)

    old_vote_msg = ChatMessage(
        id=f"vote-old-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="十四天前的回复",
        message_metadata=json.dumps(
            {
                "stop_reason": "end_turn",
                "feedback": {
                    "vote": "down",
                    "preset": "off_topic",
                    "comment": "归档后的点踩反馈",
                    "created_at": old_ts.isoformat() + "+00:00",
                    "updated_at": old_ts.isoformat() + "+00:00",
                },
            },
            ensure_ascii=False,
        ),
        created_at=old_ts,
    )
    hot_vote_msg = ChatMessage(
        id=f"vote-hot-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="两天前的回复",
        message_metadata=json.dumps(
            {
                "stop_reason": "end_turn",
                "feedback": {
                    "vote": "up",
                    "preset": None,
                    "comment": "热表点赞评论",
                    "created_at": recent_ts.isoformat() + "+00:00",
                    "updated_at": recent_ts.isoformat() + "+00:00",
                },
            },
            ensure_ascii=False,
        ),
        created_at=recent_ts,
    )
    db_session.add_all([old_vote_msg, hot_vote_msg])
    db_session.commit()

    # Archive the 14-day-old row.
    res = archive_chat_messages_batch(db_session, cutoff=cutoff, batch_size=50, dry_run=False)
    assert res.deleted == 1

    # Simulate temporary overlap: duplicate hot_vote_msg in cold with stale comment.
    db_session.add(
        ChatMessageArchive(
            id=hot_vote_msg.id,
            session_id=chat.id,
            role="assistant",
            content="两天前的回复",
            message_metadata=json.dumps(
                {
                    "stop_reason": "end_turn",
                    "feedback": {
                        "vote": "down",
                        "preset": "stale_cold_preset",
                        "comment": "冷表旧副本",
                        "created_at": recent_ts.isoformat() + "+00:00",
                        "updated_at": recent_ts.isoformat() + "+00:00",
                    },
                },
                ensure_ascii=False,
            ),
            created_at=recent_ts,
            archived_at=now_naive,
        )
    )
    db_session.commit()

    votes = list_chat_feedback_votes(db_session, days=30, now=now_utc)
    assert votes["summary"]["up"] == 1
    assert votes["summary"]["down"] == 1
    assert votes["summary"]["total_votes"] == 2
    assert votes["summary"]["preset_counts"] == [{"preset": "off_topic", "count": 1}]
    by_id = {item["message_id"]: item for item in votes["items"]}
    assert set(by_id.keys()) == {old_vote_msg.id, hot_vote_msg.id}
    assert by_id[old_vote_msg.id]["comment"] == "归档后的点踩反馈"
    assert by_id[hot_vote_msg.id]["vote"] == "up"
    assert by_id[hot_vote_msg.id]["comment"] == "热表点赞评论"

    # Also verify _project_has_ai_reply falls back to chat_message_archive when hot is empty!
    db_session.delete(hot_vote_msg)
    db_session.commit()
    assert _project_has_ai_reply(db_session, project.id) is True


# ---------------------------------------------------------------------------
# 3. CLI parser contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_parser_defaults_and_flags() -> None:
    parser = build_parser()
    defaults = parser.parse_args([])
    assert defaults.older_than_days == 7
    assert defaults.batch_size == 500
    assert defaults.max_batches == 20
    assert defaults.sleep_ms == 100
    assert defaults.dry_run is False
    assert defaults.vacuum_analyze is False

    custom = parser.parse_args(
        [
            "--older-than-days",
            "14",
            "--batch-size",
            "250",
            "--max-batches",
            "5",
            "--sleep-ms",
            "50",
            "--dry-run",
            "--vacuum-analyze",
        ]
    )
    assert custom.older_than_days == 14
    assert custom.batch_size == 250
    assert custom.max_batches == 5
    assert custom.sleep_ms == 50
    assert custom.dry_run is True
    assert custom.vacuum_analyze is True


# ---------------------------------------------------------------------------
# 4. Alembic migration upgrade / downgrade round-trip on disposable SQLite DB
# ---------------------------------------------------------------------------


def _alembic_executable() -> str:
    candidate = Path(sys.executable).parent / "alembic"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("alembic")
    assert found, "alembic CLI not found"
    return found


def _run_alembic(db_url: str, *args: str) -> None:
    completed = subprocess.run(
        [_alembic_executable(), *args],
        cwd=SERVER_DIR,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.integration
@pytest.mark.slow
def test_alembic_chat_message_archive_migration_drops_ux_fk_and_round_trips(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'chat-archive-migration.db'}"
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    # Stamp head, then step back before 20261124_000000 and upgrade to 20261124_180000 so
    # chat_message_ux_summary is created by the REAL 20261124_000000 DDL (with unnamed inline CASCADE FK).
    _run_alembic(db_url, "stamp", "head")
    _run_alembic(db_url, "downgrade", PRE_SUMMARY_REVISION)
    _run_alembic(db_url, "upgrade", PREV_REVISION)

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "chat_message_archive" not in inspector.get_table_names()
    pre_fks = inspector.get_foreign_keys("chat_message_ux_summary")
    assert any(fk.get("referred_table") == "chat_message" for fk in pre_fks)

    # Seed user, project, session, message, and summary row BEFORE upgrade.
    now_str = datetime.utcnow().isoformat(sep=" ")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO user (id, username, email, email_verified, hashed_password, is_active, is_superuser, created_at, updated_at) "
                "VALUES ('u-mig', 'mig_user', 'mig@example.com', 1, 'hash', 1, 0, :now, :now)"
            ),
            {"now": now_str},
        )
        conn.execute(
            text(
                "INSERT INTO project (id, name, owner_id, created_at, updated_at, is_deleted, project_type) "
                "VALUES ('p-mig', 'Mig Project', 'u-mig', :now, :now, 0, 'novel')"
            ),
            {"now": now_str},
        )
        conn.execute(
            text(
                "INSERT INTO chat_session (id, user_id, project_id, title, is_active, message_count, created_at, updated_at) "
                "VALUES ('s-mig', 'u-mig', 'p-mig', 'Mig Session', 1, 1, :now, :now)"
            ),
            {"now": now_str},
        )
        conn.execute(
            text(
                "INSERT INTO chat_message (id, session_id, role, content, created_at) "
                "VALUES ('m-mig', 's-mig', 'assistant', 'hello', :now)"
            ),
            {"now": now_str},
        )
        conn.execute(
            text(
                "INSERT INTO chat_message_ux_summary "
                "(message_id, session_id, created_at, round_category, ai_edit_cards, ai_edit_undone, ai_delete_cards, ai_delete_restored) "
                "VALUES ('m-mig', 's-mig', :now, 'healthy', 1, 0, 0, 0)"
            ),
            {"now": now_str},
        )
    engine.dispose()

    # Upgrade to 20261126_000000: creates chat_message_archive and drops CASCADE FK on chat_message_ux_summary.
    _run_alembic(db_url, "upgrade", ARCHIVE_REVISION)

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "chat_message_archive" in inspector.get_table_names()
    archive_indexes = {idx["name"] for idx in inspector.get_indexes("chat_message_archive")}
    assert "ix_chat_message_archive_session_created_at_id" in archive_indexes
    assert "ix_chat_message_archive_archived_at" in archive_indexes

    archive_fks = inspector.get_foreign_keys("chat_message_archive")
    assert any(
        fk.get("referred_table") == "chat_session"
        and fk.get("options", {}).get("ondelete", "").upper() == "CASCADE"
        for fk in archive_fks
    )

    # FK on chat_message_ux_summary MUST be completely gone.
    post_fks = inspector.get_foreign_keys("chat_message_ux_summary")
    assert post_fks == []

    # With PRAGMA foreign_keys = ON, deleting chat_message MUST NOT cascade-delete chat_message_ux_summary!
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        conn.execute(text("DELETE FROM chat_message WHERE id = 'm-mig'"))
        surviving = conn.execute(
            text("SELECT COUNT(*) FROM chat_message_ux_summary WHERE message_id = 'm-mig'")
        ).scalar_one()
        assert surviving == 1
    engine.dispose()

    # Downgrade MUST refuse when chat_message_archive is non-empty (protecting cold data).
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_message_archive "
                "(id, session_id, role, content, created_at, archived_at) "
                "VALUES ('m-cold-keep', 's-mig', 'assistant', 'archived reply', :now, :now)"
            ),
            {"now": now_str},
        )
    engine.dispose()

    blocked_downgrade = subprocess.run(
        [_alembic_executable(), "downgrade", PREV_REVISION],
        cwd=SERVER_DIR,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert blocked_downgrade.returncode != 0
    assert "Refusing to drop non-empty chat_message_archive" in (
        blocked_downgrade.stdout + blocked_downgrade.stderr
    )

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "chat_message_archive" in inspector.get_table_names()
    assert "ix_chat_message_archive_session_created_at_id" in {
        idx["name"] for idx in inspector.get_indexes("chat_message_archive")
    }
    with engine.begin() as conn:
        kept = conn.execute(
            text("SELECT COUNT(*) FROM chat_message_archive WHERE id = 'm-cold-keep'")
        ).scalar_one()
        assert kept == 1
        # Now empty the archive table so empty-table downgrade round-trip can proceed.
        conn.execute(text("DELETE FROM chat_message_archive"))
    engine.dispose()

    # Downgrade back to 20261124_180000 succeeds cleanly when archive table is empty,
    # even with orphan summary row, and drops chat_message_archive.
    _run_alembic(db_url, "downgrade", PREV_REVISION)
    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "chat_message_archive" not in inspector.get_table_names()
    engine.dispose()


# ---------------------------------------------------------------------------
# 5. Wave 3 Pre-Merge Audit Hardening Regressions (P0-1, P0-2, P1-1..3, P2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.slow
def test_archive_fails_closed_when_ux_summary_fk_still_references_chat_message(
    tmp_path: Path,
) -> None:
    """P0-1: init_db() create_all() without alembic upgrade head MUST fail closed without deleting."""
    db_url = f"sqlite:///{tmp_path / 'chat-archive-fk-guard.db'}"
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    # Build a DB at PREV_REVISION (chat_message_ux_summary still has CASCADE FK to chat_message).
    _run_alembic(db_url, "stamp", "head")
    _run_alembic(db_url, "downgrade", PRE_SUMMARY_REVISION)
    _run_alembic(db_url, "upgrade", PREV_REVISION)

    # Simulate new container startup calling init_db() -> SQLModel.metadata.create_all(),
    # which creates chat_message_archive WITHOUT dropping chat_message_ux_summary's FK.
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)

    inspector = inspect(engine)
    assert "chat_message_archive" in inspector.get_table_names()
    assert any(
        fk.get("referred_table") == "chat_message"
        for fk in inspector.get_foreign_keys("chat_message_ux_summary")
    )

    cutoff = datetime(2026, 11, 19, 0, 0, 0)
    old_ts = cutoff - timedelta(days=3)
    with Session(engine) as session:
        session.exec(text("PRAGMA foreign_keys = ON"))
        _user, _project, chat = _seed_user_project_session(session)
        old_msg = ChatMessage(
            id=f"fk-guard-{uuid4().hex[:6]}",
            session_id=chat.id,
            role="assistant",
            content="旧助手消息",
            message_metadata=json.dumps({"stop_reason": "end_turn"}, ensure_ascii=False),
            created_at=old_ts,
        )
        session.add(old_msg)
        session.commit()

        for dry_run in (True, False):
            with pytest.raises(RuntimeError, match="chat_message_ux_summary"):
                archive_chat_messages_batch(
                    session, cutoff=cutoff, batch_size=10, dry_run=dry_run
                )

        session.expire_all()
        assert session.get(ChatMessage, old_msg.id) is not None
        assert session.get(ChatMessageArchive, old_msg.id) is None
        assert session.get(ChatMessageUxSummary, old_msg.id) is not None

    engine.dispose()


@pytest.mark.integration
def test_archive_backfills_missing_assistant_ux_summary_and_aborts_if_unverified(
    db_session: Session,
) -> None:
    """P0-1: un-backfilled assistant rows are backfilled atomically before hot delete, and fail closed if unverified."""
    _user, _project, chat = _seed_user_project_session(db_session)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)
    old_ts = cutoff - timedelta(days=4)

    msg = ChatMessage(
        id=f"unbackfilled-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="尚未回填 UX 汇总的历史回答",
        tool_calls=_bloated_assistant_tool_calls(
            draft_id="f-bf-1",
            deleted_id="f-bf-2",
            updated_at=old_ts,
            deleted_at=old_ts,
        ),
        message_metadata=json.dumps(
            {
                "stop_reason": "end_turn",
                "feedback": {"vote": "down", "preset": "too_long"},
            },
            ensure_ascii=False,
        ),
        created_at=old_ts,
    )
    db_session.add(msg)
    db_session.commit()

    # Delete the summary row to simulate an older un-backfilled assistant row.
    db_session.exec(
        text("DELETE FROM chat_message_ux_summary WHERE message_id = :mid"),
        params={"mid": msg.id},
    )
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(ChatMessageUxSummary, msg.id) is None

    # 1) If summary verification still reports missing, archival must abort & roll back without deleting hot row.
    with patch(
        "services.chat_message_archival._verify_ux_summary_ids",
        return_value=set(),
    ):
        failed = archive_chat_messages_batch(
            db_session, cutoff=cutoff, batch_size=10, dry_run=False
        )
    assert failed.verification_failed is True
    assert failed.deleted == 0
    db_session.expire_all()
    assert db_session.get(ChatMessage, msg.id) is not None
    assert db_session.get(ChatMessageArchive, msg.id) is None

    # 2) Normal run backfills the missing summary row inside the same transaction before deleting hot row.
    ok = archive_chat_messages_batch(db_session, cutoff=cutoff, batch_size=10, dry_run=False)
    assert ok.verification_failed is False
    assert ok.deleted == 1
    db_session.expire_all()
    assert db_session.get(ChatMessage, msg.id) is None
    assert db_session.get(ChatMessageArchive, msg.id) is not None
    backfilled = db_session.get(ChatMessageUxSummary, msg.id)
    assert backfilled is not None
    assert backfilled.round_category == "healthy"
    assert backfilled.feedback_vote == "down"
    assert backfilled.feedback_preset == "too_long"
    assert backfilled.ai_edit_cards == 1
    assert backfilled.ai_delete_cards == 1


@pytest.mark.integration
def test_load_active_session_recent_messages_exact_boundary_hot_equals_limit_with_cold(
    db_session: Session,
) -> None:
    """P0-2: when hot == limit and cold >= 1, cold MUST be probed and has_more MUST be True."""
    from api.chat import load_active_session_recent_messages

    user, project, chat = _seed_user_project_session(db_session)
    base = datetime(2026, 11, 10, 12, 0, 0)
    archived_at = datetime(2026, 11, 20, 0, 0, 0)

    # Seed 3 cold archived messages (t0..t2) and 3 hot messages (t3..t5).
    cold_ids = [f"b-cold-{i}-{uuid4().hex[:6]}" for i in range(3)]
    hot_ids = [f"b-hot-{i}-{uuid4().hex[:6]}" for i in range(3)]
    for idx, cid in enumerate(cold_ids):
        db_session.add(
            ChatMessageArchive(
                id=cid,
                session_id=chat.id,
                role="user" if idx % 2 == 0 else "assistant",
                content=f"cold-{idx}",
                created_at=base + timedelta(minutes=idx),
                archived_at=archived_at,
            )
        )
    for idx, hid in enumerate(hot_ids):
        db_session.add(
            ChatMessage(
                id=hid,
                session_id=chat.id,
                role="user" if idx % 2 == 0 else "assistant",
                content=f"hot-{idx}",
                created_at=base + timedelta(days=10, minutes=idx),
            )
        )
    db_session.commit()

    # Exact boundary: hot == limit (3 == 3), cold == 3 >= 1 -> MUST probe cold and return has_more=True!
    sess_resp, msgs, has_more = load_active_session_recent_messages(
        db_session,
        project_id=project.id,
        user_id=user.id,
        limit=3,
    )
    assert sess_resp is not None and sess_resp.id == chat.id
    assert [m.id for m in msgs] == hot_ids
    assert has_more is True

    # Spanning hot + cold with older cold still remaining (limit=5, total=6) -> has_more=True.
    _, msgs_5, has_more_5 = load_active_session_recent_messages(
        db_session,
        project_id=project.id,
        user_id=user.id,
        limit=5,
    )
    assert [m.id for m in msgs_5] == [*cold_ids[1:], *hot_ids]
    assert has_more_5 is True

    # Exact total == limit (limit=6, total=6) -> has_more=False.
    _, msgs_6, has_more_6 = load_active_session_recent_messages(
        db_session,
        project_id=project.id,
        user_id=user.id,
        limit=6,
    )
    assert [m.id for m in msgs_6] == [*cold_ids, *hot_ids]
    assert has_more_6 is False

    # Hot-only exact boundary (remove cold rows, hot == limit == 3) -> has_more=False.
    db_session.exec(
        text("DELETE FROM chat_message_archive WHERE session_id = :sid"),
        params={"sid": chat.id},
    )
    db_session.commit()
    _, msgs_hot_only, has_more_hot_only = load_active_session_recent_messages(
        db_session,
        project_id=project.id,
        user_id=user.id,
        limit=3,
    )
    assert [m.id for m in msgs_hot_only] == hot_ids
    assert has_more_hot_only is False


@pytest.mark.integration
def test_clear_session_deletes_hot_before_cold_to_prevent_ghost_resurrection(
    db_session: Session,
) -> None:
    """P1-3: clear_session MUST delete chat_message BEFORE chat_message_archive."""
    from sqlalchemy import event

    from api.chat import clear_session

    user, project, chat = _seed_user_project_session(db_session)
    now = datetime(2026, 11, 20, 12, 0, 0)
    db_session.add(
        ChatMessage(
            id=f"clr-hot-{uuid4().hex[:6]}",
            session_id=chat.id,
            role="user",
            content="hot",
            created_at=now,
        )
    )
    db_session.add(
        ChatMessageArchive(
            id=f"clr-cold-{uuid4().hex[:6]}",
            session_id=chat.id,
            role="assistant",
            content="cold",
            created_at=now - timedelta(days=10),
            archived_at=now,
        )
    )
    db_session.commit()

    delete_statements: list[str] = []

    def _capture_deletes(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        normalized = " ".join(statement.strip().lower().split())
        if normalized.startswith("delete from "):
            delete_statements.append(normalized)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture_deletes)
    try:
        result = clear_session(project_id=project.id, session=db_session, current_user=user)
    finally:
        event.remove(engine, "before_cursor_execute", _capture_deletes)

    assert result["success"] is True
    hot_idx = next(
        idx for idx, sql in enumerate(delete_statements) if sql.startswith("delete from chat_message ")
    )
    cold_idx = next(
        idx
        for idx, sql in enumerate(delete_statements)
        if sql.startswith("delete from chat_message_archive ")
    )
    assert hot_idx < cold_idx, f"Expected hot delete before cold delete, got: {delete_statements}"


@pytest.mark.integration
def test_chat_feedback_service_locks_hot_then_falls_back_to_locked_cold(
    db_session: Session,
) -> None:
    """P1-3: ChatFeedbackService MUST use FOR UPDATE + populate_existing on hot, then locked cold fallback."""
    from sqlalchemy.dialects import postgresql

    from services.chat_feedback_service import chat_feedback_service

    user, _project, chat = _seed_user_project_session(db_session)
    now = datetime(2026, 11, 20, 12, 0, 0)
    cold_msg = ChatMessageArchive(
        id=f"fb-lock-cold-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="已归档的回复",
        message_metadata=json.dumps({"stop_reason": "end_turn"}, ensure_ascii=False),
        created_at=now - timedelta(days=10),
        archived_at=now,
    )
    db_session.add(cold_msg)
    db_session.commit()

    seen_locked_queries: list[tuple[str, bool]] = []
    orig_exec = db_session.exec

    def _spy_exec(statement, *args, **kwargs):
        compiled = str(
            statement.compile(dialect=postgresql.dialect())
        )
        if "FROM chat_message " in compiled or "FROM chat_message_archive " in compiled:
            exec_opts = getattr(statement, "_execution_options", {})
            seen_locked_queries.append((compiled, bool(exec_opts.get("populate_existing"))))
        return orig_exec(statement, *args, **kwargs)

    with patch.object(db_session, "exec", side_effect=_spy_exec):
        res = chat_feedback_service.submit_feedback(
            session=db_session,
            current_user=user,
            message_id=cold_msg.id,
            vote="up",
            preset="helpful",
            comment="并发归档后仍可安全反馈",
        )

    assert res["message_id"] == cold_msg.id
    assert res["feedback"]["vote"] == "up"
    assert len(seen_locked_queries) == 2
    hot_sql, hot_pop = seen_locked_queries[0]
    cold_sql, cold_pop = seen_locked_queries[1]
    assert "FROM chat_message " in hot_sql and "FOR UPDATE" in hot_sql and hot_pop is True
    assert "FROM chat_message_archive " in cold_sql and "FOR UPDATE" in cold_sql and cold_pop is True


@pytest.mark.unit
def test_migration_postgres_lock_timeout_and_partial_feedback_index() -> None:
    """P1-1 & P1-2: PG migration sets lock_timeout=5s and creates/drops partial feedback index matching database.py."""
    import importlib.util
    import types
    from unittest.mock import MagicMock

    import database as db_module

    # Partial index DDL in database.py must include the cold feedback partial index.
    assert any(
        "ix_chat_message_archive_assistant_feedback_created_at" in sql
        and "strpos(message_metadata, '\"feedback\"') > 0" in sql
        for sql in db_module.POSTGRES_PERFORMANCE_INDEX_SQL
    )

    migration_path = (
        SERVER_DIR / "alembic" / "versions" / "20261126_000000_add_chat_message_archive.py"
    )
    fake_op = MagicMock()
    fake_alembic = types.ModuleType("alembic")
    fake_alembic.op = fake_op  # type: ignore[attr-defined]

    spec = importlib.util.spec_from_file_location("mig_20261126", migration_path)
    assert spec is not None and spec.loader is not None
    mig = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"alembic": fake_alembic}):
        spec.loader.exec_module(mig)

    executed_ops: list[tuple[str, str]] = []

    fake_bind = MagicMock()
    fake_bind.dialect.name = "postgresql"
    fake_bind.execute.return_value.scalar_one.return_value = 0

    fake_inspector = MagicMock()
    fake_inspector.has_table.side_effect = lambda name: name == "chat_message_ux_summary"
    fake_inspector.get_foreign_keys.return_value = [
        {
            "name": "chat_message_ux_summary_message_id_fkey",
            "referred_table": "chat_message",
            "constrained_columns": ["message_id"],
        }
    ]
    fake_inspector.get_indexes.return_value = []

    fake_op.get_bind.return_value = fake_bind
    fake_op.execute.side_effect = lambda stmt: executed_ops.append(("execute", str(stmt)))
    fake_op.drop_constraint.side_effect = lambda name, table, **kw: executed_ops.append(
        ("drop_constraint", name)
    )
    fake_op.create_table.side_effect = lambda name, *a, **kw: executed_ops.append(
        ("create_table", name)
    )
    fake_op.create_index.side_effect = lambda name, table, cols: executed_ops.append(
        ("create_index", name)
    )
    fake_op.drop_index.side_effect = lambda name, **kw: executed_ops.append(("drop_index", name))
    fake_op.drop_table.side_effect = lambda name: executed_ops.append(("drop_table", name))

    with patch.object(mig.sa, "inspect", return_value=fake_inspector):
        mig.upgrade()

    assert executed_ops[0] == ("execute", "SET LOCAL lock_timeout = '5s'")
    assert ("drop_constraint", "chat_message_ux_summary_message_id_fkey") in executed_ops
    assert any(
        kind == "execute" and "ix_chat_message_archive_assistant_feedback_created_at" in sql
        for kind, sql in executed_ops
    )

    # PG downgrade on non-empty archive table refuses BEFORE dropping any index or table.
    executed_ops.clear()
    fake_bind.execute.return_value.scalar_one.return_value = 42
    fake_inspector.has_table.side_effect = lambda name: name in {
        "chat_message_archive",
        "chat_message_ux_summary",
        "chat_message",
    }
    fake_inspector.get_indexes.return_value = [
        {"name": "ix_chat_message_archive_session_created_at_id"},
        {"name": "ix_chat_message_archive_archived_at"},
        {"name": "ix_chat_message_archive_assistant_feedback_created_at"},
    ]
    with patch.object(mig.sa, "inspect", return_value=fake_inspector):
        with pytest.raises(RuntimeError, match="Refusing to drop non-empty chat_message_archive"):
            mig.downgrade()
    assert executed_ops == [("execute", "SET LOCAL lock_timeout = '5s'")]

    # PG downgrade on empty archive table drops partial feedback index, standard indexes, and table.
    executed_ops.clear()
    fake_bind.execute.return_value.scalar_one.return_value = 0
    with patch.object(mig.sa, "inspect", return_value=fake_inspector):
        mig.downgrade()
    assert executed_ops[0] == ("execute", "SET LOCAL lock_timeout = '5s'")
    assert ("drop_index", "ix_chat_message_archive_assistant_feedback_created_at") in executed_ops
    assert ("drop_table", "chat_message_archive") in executed_ops


@pytest.mark.integration
def test_archive_warns_and_preserves_hot_edits_on_conflict_and_vacuum_analyzes_archive_table(
    db_session: Session,
) -> None:
    """M4 + P2 hygiene: ON CONFLICT DO UPDATE preserves newer hot edits (feedback/undo), warns, and ANALYZE archive."""
    from unittest.mock import MagicMock

    from services.chat_message_archival import _run_postgres_vacuum_analyze

    _user, _project, chat = _seed_user_project_session(db_session)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)
    old_ts = cutoff - timedelta(days=3)

    newer_meta = json.dumps(
        {
            "stop_reason": "end_turn",
            "feedback": {"vote": "down", "preset": "off_topic", "comment": "热表新反馈"},
        },
        ensure_ascii=False,
    )
    newer_tools = json.dumps(
        [
            {
                "id": "call-edit-m4",
                "name": "edit_file",
                "status": "success",
                "result": {"id": "f-m4", "undo": {"before_version_number": 1, "undone": True}},
            }
        ],
        ensure_ascii=False,
    )
    msg = ChatMessage(
        id=f"dup-warn-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="热表新内容",
        tool_calls=newer_tools,
        message_metadata=newer_meta,
        created_at=old_ts,
    )
    db_session.add(msg)
    db_session.add(
        ChatMessageArchive(
            id=msg.id,
            session_id=chat.id,
            role="assistant",
            content="冷表旧内容",
            tool_calls=json.dumps(
                [
                    {
                        "id": "call-edit-m4",
                        "name": "edit_file",
                        "status": "success",
                        "result": {"id": "f-m4", "undo": {"before_version_number": 1, "undone": False}},
                    }
                ],
                ensure_ascii=False,
            ),
            message_metadata=json.dumps({"stop_reason": "end_turn"}, ensure_ascii=False),
            created_at=old_ts,
            archived_at=cutoff,
        )
    )
    db_session.commit()

    with patch("services.chat_message_archival.log_with_context") as mock_log:
        batch = archive_chat_messages_batch(db_session, cutoff=cutoff, batch_size=10, dry_run=False)

    assert batch.scanned == 1
    assert batch.archived == 1
    assert batch.deleted == 1
    assert any(
        call.args[1] == 30 and "ON CONFLICT DO UPDATE" in call.args[2]
        for call in mock_log.call_args_list
    )

    # Newer hot content, feedback, and undo state MUST overwrite the stale archive row.
    db_session.expire_all()
    assert db_session.get(ChatMessage, msg.id) is None
    archived = db_session.get(ChatMessageArchive, msg.id)
    assert archived is not None
    assert archived.content == "热表新内容"
    assert json.loads(archived.message_metadata or "{}")["feedback"]["comment"] == "热表新反馈"
    assert json.loads(archived.tool_calls or "[]")[0]["result"]["undo"]["undone"] is True

    # Verify PostgreSQL --vacuum-analyze executes BOTH VACUUM ANALYZE chat_message and ANALYZE chat_message_archive.
    executed_sql: list[str] = []
    fake_conn = MagicMock()
    fake_conn.execute.side_effect = lambda stmt: executed_sql.append(str(stmt))
    fake_engine = MagicMock()
    fake_engine.connect.return_value.execution_options.return_value.__enter__.return_value = fake_conn
    fake_bind = MagicMock()
    fake_bind.dialect.name = "postgresql"
    fake_bind.engine = fake_engine
    fake_session = MagicMock(spec=Session)
    fake_session.get_bind.return_value = fake_bind

    assert _run_postgres_vacuum_analyze(fake_session) is True
    assert executed_sql == [
        "VACUUM ANALYZE chat_message",
        "ANALYZE chat_message_archive",
    ]


# ---------------------------------------------------------------------------
# 6. PR #488 Review Follow-up Regressions (H1, M1, M2, M3, L2, Codecov)
# ---------------------------------------------------------------------------


def _capture_archive_selects(db_session: Session):
    """Context manager recording all SELECT statements touching chat_message_archive."""
    import contextlib

    from sqlalchemy import event

    @contextlib.contextmanager
    def _ctx():
        archive_selects: list[str] = []

        def _listener(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
            normalized = " ".join(statement.strip().lower().split())
            if normalized.startswith("select ") and "from chat_message_archive" in normalized:
                archive_selects.append(normalized)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", _listener)
        try:
            yield archive_selects
        finally:
            event.remove(engine, "before_cursor_execute", _listener)

    return _ctx()


def _is_full_row_cold_scan(sql: str) -> bool:
    """True when a SELECT against chat_message_archive fetches heap/TOAST payload columns."""
    return any(
        col_name in sql
        for col_name in (
            "chat_message_archive.content",
            "chat_message_archive.tool_calls",
            "chat_message_archive.message_metadata",
            "chat_message_archive.reasoning_content",
        )
    )


@pytest.mark.integration
def test_h1_load_loop_history_lazy_cold_watermark_runs_zero_full_cold_scans(
    db_session: Session,
) -> None:
    """H1: hot-only and hot-compacted multi-week sessions execute ZERO full-row cold scans."""
    # 1) Hot-only session: 0 full-row cold scans.
    user, project, hot_chat = _seed_user_project_session(db_session)
    base = datetime(2026, 11, 25, 10, 0, 0)
    db_session.add_all(
        [
            ChatMessage(id="h1-hot-u1", session_id=hot_chat.id, role="user", content="热问题", created_at=base),
            ChatMessage(
                id="h1-hot-a1",
                session_id=hot_chat.id,
                role="assistant",
                content="热回答",
                message_metadata=json.dumps(
                    {
                        "engine": LOOP_ENGINE_METADATA_VALUE,
                        "prompt_tokens": 500,
                        "display_events": [{"type": "content", "content": "热回答"}],
                    },
                    ensure_ascii=False,
                ),
                created_at=base + timedelta(minutes=1),
            ),
        ]
    )
    db_session.commit()

    with _capture_archive_selects(db_session) as hot_only_sql:
        hot_history = load_loop_history(
            db_session,
            project_id=project.id,
            user_id=user.id,
            chat_session_id=hot_chat.id,
        )
    assert [item["content"] for item in hot_history.items] == ["热问题", "热回答"]
    assert [s for s in hot_only_sql if _is_full_row_cold_scan(s)] == []

    # 2) Active multi-week session with cold rows AND a hot compaction anchor strictly newer
    #    than the cold watermark: 0 full-row cold scans!
    user2, proj2, multi_chat = _seed_user_project_session(db_session)
    old_t = datetime(2026, 11, 1, 8, 0, 0)
    hot_t = datetime(2026, 11, 25, 12, 0, 0)
    db_session.add_all(
        [
            ChatMessageArchive(
                id="h1-mw-c1",
                session_id=multi_chat.id,
                role="user",
                content="三周前的冷问题",
                created_at=old_t,
                archived_at=old_t + timedelta(days=8),
            ),
            ChatMessageArchive(
                id="h1-mw-c2",
                session_id=multi_chat.id,
                role="assistant",
                content="三周前的冷回答",
                created_at=old_t + timedelta(minutes=1),
                archived_at=old_t + timedelta(days=8),
            ),
            ChatMessage(
                id="h1-mw-h1",
                session_id=multi_chat.id,
                role="user",
                content="昨天的问题",
                created_at=hot_t,
            ),
            ChatMessage(
                id="h1-mw-h2",
                session_id=multi_chat.id,
                role="assistant",
                content="今天的压缩后回复",
                message_metadata=json.dumps(
                    {
                        "engine": LOOP_ENGINE_METADATA_VALUE,
                        "prompt_tokens": 900,
                        "compaction": {
                            "summary": "热表压缩摘要",
                            "covers_through_message_id": "h1-mw-h1",
                            "kept_author_messages": ["昨天的问题"],
                        },
                        "display_events": [{"type": "content", "content": "今天的压缩后回复"}],
                    },
                    ensure_ascii=False,
                ),
                created_at=hot_t + timedelta(minutes=1),
            ),
        ]
    )
    db_session.commit()

    with _capture_archive_selects(db_session) as compacted_sql:
        compacted_history = load_loop_history(
            db_session,
            project_id=proj2.id,
            user_id=user2.id,
            chat_session_id=multi_chat.id,
        )
    assert compacted_history.compacted is True
    assert compacted_history.last_message_id == "h1-mw-h2"
    assert compacted_history.items[-1]["content"] == "今天的压缩后回复"
    # Index-only watermark checked once; ZERO full-row cold scans!
    assert len(compacted_sql) == 1
    assert [s for s in compacted_sql if _is_full_row_cold_scan(s)] == []


@pytest.mark.integration
def test_m1_session_loader_written_files_and_read_snapshots_lazy_cold_fallback(
    db_session: Session,
) -> None:
    """M1: SessionLoader.session_written_file_ids and session_read_snapshots lazily read archived sessions."""
    from agent.core.session_loader import SessionLoader
    from models.file_version import FileVersion

    user, project, chat = _seed_user_project_session(db_session)
    old_ts = datetime(2026, 11, 10, 9, 0, 0)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)

    edited_content = "归档后依旧未改动的正文"
    streamed_content = "流式创建的正文内容"
    f_edit = File(
        id=f"m1-edit-{uuid4().hex[:6]}",
        project_id=project.id,
        title="第一章",
        file_type="draft",
        content=edited_content,
        updated_at=old_ts,
    )
    f_stream = File(
        id=f"m1-stream-{uuid4().hex[:6]}",
        project_id=project.id,
        title="第二章",
        file_type="draft",
        content=streamed_content,
        updated_at=old_ts + timedelta(seconds=30),
    )
    db_session.add_all([f_edit, f_stream])
    db_session.flush()
    db_session.add(
        FileVersion(
            file_id=f_stream.id,
            project_id=project.id,
            version_number=1,
            content=streamed_content,
            change_summary="AI 创建章节",
            change_source="ai",
        )
    )

    old_asst = ChatMessage(
        id=f"m1-asst-{uuid4().hex[:6]}",
        session_id=chat.id,
        role="assistant",
        content="已写完两章。",
        tool_calls=json.dumps(
            [
                {
                    "id": "call-m1-edit",
                    "name": "edit_file",
                    "arguments": {"id": f_edit.id},
                    "status": "success",
                    "result": {
                        "id": f_edit.id,
                        "mutation_applied": True,
                        "undo": {
                            "before_version_number": 1,
                            "expected_after_updated_at": old_ts.isoformat(),
                            "expected_after_content_sha256": content_sha256(edited_content),
                            "undone": False,
                        },
                    },
                },
                {
                    "id": "call-m1-create",
                    "name": "write_chapter",
                    "arguments": {"chapter": 2, "title": "第二章"},
                    "status": "success",
                    "result": {
                        "id": f_stream.id,
                        "action": "created",
                        "awaiting_body": True,
                        "body_saved": True,
                    },
                },
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps(
            {"engine": LOOP_ENGINE_METADATA_VALUE, "stop_reason": "end_turn"},
            ensure_ascii=False,
        ),
        created_at=old_ts + timedelta(minutes=1),
    )
    db_session.add(old_asst)
    db_session.commit()

    # Archive the entire session (>7d).
    res = archive_chat_messages_batch(db_session, cutoff=cutoff, batch_size=50, dry_run=False)
    assert res.deleted == 1

    loader = SessionLoader(project_id=project.id, user_id=user.id)

    # 1) session_written_file_ids finds both archived files.
    written_ids = loader.session_written_file_ids(
        db_session,
        chat_session_id=chat.id,
        limit=10,
        file_types=("draft",),
    )
    assert set(written_ids) == {f_edit.id, f_stream.id}

    # 2) session_read_snapshots recovers both undo_write and streamed_create snapshots from archive.
    snapshots = loader.session_read_snapshots(
        db_session,
        chat_session_id=chat.id,
        limit=10,
    )
    assert snapshots == {
        f_edit.id: edited_content,
        f_stream.id: streamed_content,
    }

    # 3) Add a hot message with a compaction anchor & hot write: lazy watermark avoids full-row cold scans!
    f_hot = File(
        id=f"m1-hot-{uuid4().hex[:6]}",
        project_id=project.id,
        title="第三章",
        file_type="draft",
        content="热表新章节",
        updated_at=cutoff + timedelta(hours=1),
    )
    db_session.add(f_hot)
    db_session.add(
        ChatMessage(
            id=f"m1-hot-asst-{uuid4().hex[:6]}",
            session_id=chat.id,
            role="assistant",
            content="热表已写第三章",
            tool_calls=json.dumps(
                [
                    {
                        "id": "call-m1-hot",
                        "name": "edit_file",
                        "arguments": {"id": f_hot.id},
                        "status": "success",
                        "result": {
                            "id": f_hot.id,
                            "mutation_applied": True,
                            "undo": {
                                "before_version_number": 1,
                                "expected_after_updated_at": (cutoff + timedelta(hours=1)).isoformat(),
                                "expected_after_content_sha256": content_sha256("热表新章节"),
                                "undone": False,
                            },
                        },
                    }
                ],
                ensure_ascii=False,
            ),
            message_metadata=json.dumps(
                {
                    "engine": LOOP_ENGINE_METADATA_VALUE,
                    "compaction": {
                        "summary": "压缩点",
                        "covers_through_message_id": old_asst.id,
                        "kept_author_messages": [],
                    },
                },
                ensure_ascii=False,
            ),
            created_at=cutoff + timedelta(hours=2),
        )
    )
    db_session.commit()

    with _capture_archive_selects(db_session) as written_sql:
        top1_written = loader.session_written_file_ids(
            db_session,
            chat_session_id=chat.id,
            limit=1,
            file_types=("draft",),
        )
    assert top1_written == [f_hot.id]
    assert [s for s in written_sql if _is_full_row_cold_scan(s)] == []

    with _capture_archive_selects(db_session) as snap_sql:
        hot_compacted_snaps = loader.session_read_snapshots(
            db_session,
            chat_session_id=chat.id,
            limit=10,
        )
    assert hot_compacted_snaps == {f_hot.id: "热表新章节"}
    assert [s for s in snap_sql if _is_full_row_cold_scan(s)] == []


@pytest.mark.integration
def test_m2_suggest_service_recent_chat_history_lazy_cold_fallback(
    db_session: Session,
) -> None:
    """M2: SuggestService._get_recent_chat_history falls back lazily to chat_message_archive."""
    from agent.suggest_service import CHAT_HISTORY_LIMIT, SuggestService

    user, project, chat = _seed_user_project_session(db_session)
    old_ts = datetime(2026, 11, 5, 9, 0, 0)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)

    db_session.add_all(
        [
            ChatMessage(
                id="m2-old-1",
                session_id=chat.id,
                role="user",
                content="十天前我想写玄幻大纲",
                created_at=old_ts,
            ),
            ChatMessage(
                id="m2-old-2",
                session_id=chat.id,
                role="assistant",
                content="好的，这是玄幻三幕大纲。",
                created_at=old_ts + timedelta(minutes=1),
            ),
        ]
    )
    db_session.commit()

    res = archive_chat_messages_batch(db_session, cutoff=cutoff, batch_size=50, dry_run=False)
    assert res.deleted == 2

    svc = SuggestService()
    archived_history = svc._get_recent_chat_history(db_session, project.id, user.id)
    assert archived_history == [
        {"role": "user", "content": "十天前我想写玄幻大纲"},
        {"role": "assistant", "content": "好的，这是玄幻三幕大纲。"},
    ]

    # Add CHAT_HISTORY_LIMIT hot rows strictly newer than cold -> ZERO full-row cold scans!
    for idx in range(CHAT_HISTORY_LIMIT):
        db_session.add(
            ChatMessage(
                id=f"m2-hot-{idx}",
                session_id=chat.id,
                role="user" if idx % 2 == 0 else "assistant",
                content=f"热对话-{idx}",
                created_at=cutoff + timedelta(hours=1, minutes=idx),
            )
        )
    db_session.commit()

    with _capture_archive_selects(db_session) as suggest_sql:
        hot_history = svc._get_recent_chat_history(db_session, project.id, user.id)
    assert [m["content"] for m in hot_history] == [f"热对话-{i}" for i in range(CHAT_HISTORY_LIMIT)]
    assert [s for s in suggest_sql if _is_full_row_cold_scan(s)] == []


@pytest.mark.integration
def test_m3_load_active_session_recent_messages_interleaved_hot_cold_never_hides_newer_cold(
    db_session: Session,
) -> None:
    """M3: Older skipped hot rows cannot hide newer cold rows in /recent, and strictly-newer hot skips full cold scans."""
    from api.chat import load_active_session_recent_messages

    user, project, chat = _seed_user_project_session(db_session)
    t0 = datetime(2026, 11, 10, 12, 0, 0)
    archived_at = datetime(2026, 11, 20, 0, 0, 0)

    # Simulate SKIP LOCKED leaving 3 older hot rows (t0, t1, t2) while newer cold rows (t3, t4)
    # were archived, plus 1 fresh hot row (t5).
    db_session.add_all(
        [
            ChatMessage(id="m3-h-old-0", session_id=chat.id, role="user", content="h0", created_at=t0),
            ChatMessage(id="m3-h-old-1", session_id=chat.id, role="assistant", content="h1", created_at=t0 + timedelta(minutes=1)),
            ChatMessage(id="m3-h-old-2", session_id=chat.id, role="user", content="h2", created_at=t0 + timedelta(minutes=2)),
            ChatMessageArchive(
                id="m3-c-new-3",
                session_id=chat.id,
                role="assistant",
                content="c3",
                created_at=t0 + timedelta(minutes=3),
                archived_at=archived_at,
            ),
            ChatMessageArchive(
                id="m3-c-new-4",
                session_id=chat.id,
                role="user",
                content="c4",
                created_at=t0 + timedelta(minutes=4),
                archived_at=archived_at,
            ),
            ChatMessage(id="m3-h-new-5", session_id=chat.id, role="assistant", content="h5", created_at=t0 + timedelta(minutes=5)),
        ]
    )
    db_session.commit()

    # Case 1: len(hot) == 4 == limit + 1 (limit=3), yet c3 & c4 are NEWER than h0..h2!
    _, msgs_3, has_more_3 = load_active_session_recent_messages(
        db_session,
        project_id=project.id,
        user_id=user.id,
        limit=3,
    )
    assert [m.id for m in msgs_3] == ["m3-c-new-3", "m3-c-new-4", "m3-h-new-5"]
    assert has_more_3 is True

    # Case 2: len(hot) == 3 == limit (delete h0 so hot=[h5, h2, h1]), still interleaved with c3, c4!
    db_session.exec(text("DELETE FROM chat_message WHERE id = 'm3-h-old-0'"))
    db_session.commit()
    _, msgs_3b, has_more_3b = load_active_session_recent_messages(
        db_session,
        project_id=project.id,
        user_id=user.id,
        limit=3,
    )
    assert [m.id for m in msgs_3b] == ["m3-c-new-3", "m3-c-new-4", "m3-h-new-5"]
    assert has_more_3b is True

    # Case 3: Strictly newer hot rows (limit=2, hot has 3 rows > all cold) -> ZERO full-row cold scans!
    db_session.exec(text("DELETE FROM chat_message WHERE id IN ('m3-h-old-1', 'm3-h-old-2')"))
    db_session.add_all(
        [
            ChatMessage(id="m3-h-new-6", session_id=chat.id, role="user", content="h6", created_at=t0 + timedelta(minutes=6)),
            ChatMessage(id="m3-h-new-7", session_id=chat.id, role="assistant", content="h7", created_at=t0 + timedelta(minutes=7)),
        ]
    )
    db_session.commit()

    with _capture_archive_selects(db_session) as recent_sql:
        _, msgs_2, has_more_2 = load_active_session_recent_messages(
            db_session,
            project_id=project.id,
            user_id=user.id,
            limit=2,
        )
    assert [m.id for m in msgs_2] == ["m3-h-new-6", "m3-h-new-7"]
    assert has_more_2 is True
    assert [s for s in recent_sql if _is_full_row_cold_scan(s)] == []


@pytest.mark.unit
def test_l2_cli_guards_older_than_days_and_formatting_helpers() -> None:
    """L2: CLI rejects --older-than-days 0 and validates positive/non-negative flags."""
    from scripts.archive_old_chat_messages import _format_batch_line, _format_summary_line
    from services.chat_message_archival import ArchiveBatchResult

    parser = build_parser()
    for bad_args in (
        ["--older-than-days", "0"],
        ["--older-than-days", "-1"],
        ["--batch-size", "0"],
        ["--max-batches", "0"],
        ["--sleep-ms", "-1"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(bad_args)

    batch_res = ArchiveBatchResult(
        scanned=5,
        archived=5,
        deleted=5,
        bytes_before=2000,
        bytes_after=800,
        has_more=False,
    )
    assert batch_res.bytes_saved == 1200
    report = GradualArchivalReport(
        dry_run=False,
        batches_run=1,
        total_scanned=5,
        total_archived=5,
        total_deleted=5,
        total_bytes_before=2000,
        total_bytes_after=800,
        oldest_remaining_at=datetime(2026, 11, 20, 0, 0, 0),
    )
    batch_line = _format_batch_line(1, batch_res, report)
    summary_line = _format_summary_line(report)
    assert "bytes_saved=1200" in batch_line
    assert "oldest_remaining_at=2026-11-20T00:00:00" in summary_line


@pytest.mark.unit
def test_archival_service_and_archive_model_edge_branches(db_session: Session) -> None:
    """Cover validation guards, tz-aware cutoff, malformed JSON, empty helpers, generic/PG dialect paths, and callbacks."""
    from datetime import UTC
    from unittest.mock import MagicMock

    from models.chat_message_archive import has_chat_message_archive_table
    from services.chat_message_archival import (
        ArchiveBatchResult,
        _assert_ux_summary_fk_removed,
        _compact_json_utf8,
        _delete_hot_rows,
        _ensure_assistant_ux_summaries,
        _insert_ux_summary_rows_idempotent,
        _resolve_cutoff_naive,
        _run_postgres_vacuum_analyze,
        _upsert_archive_rows,
    )

    # has_chat_message_archive_table defensive branches
    assert has_chat_message_archive_table(None) is False
    assert has_chat_message_archive_table(object()) is False
    no_dialect_conn = MagicMock(dialect=None, engine=None)
    assert has_chat_message_archive_table(no_dialect_conn) is False

    # _compact_json_utf8 malformed JSON passthrough & _resolve_cutoff_naive branches
    assert _compact_json_utf8("{invalid-json") == "{invalid-json"
    tz_cutoff = datetime(2026, 11, 19, 8, 0, 0, tzinfo=UTC)
    assert _resolve_cutoff_naive(older_than_days=7, cutoff=tz_cutoff) == datetime(2026, 11, 19, 8, 0, 0)
    resolved_default = _resolve_cutoff_naive(older_than_days=7, cutoff=None)
    assert resolved_default.tzinfo is None

    # Empty input no-op guards
    assert _upsert_archive_rows(db_session, []) == (0, 0)
    assert _insert_ux_summary_rows_idempotent(db_session, []) == 0

    # Validation errors on archive_chat_messages_batch & run_gradual_archival
    with pytest.raises(ValueError, match="older_than_days"):
        archive_chat_messages_batch(db_session, older_than_days=-1)
    with pytest.raises(ValueError, match="batch_size"):
        archive_chat_messages_batch(db_session, batch_size=0)
    with patch("services.chat_message_archival.has_chat_message_archive_table", return_value=False):
        with pytest.raises(RuntimeError, match="chat_message_archive"):
            archive_chat_messages_batch(db_session)

    with pytest.raises(ValueError, match="older_than_days"):
        run_gradual_archival(db_session, older_than_days=-1)
    with pytest.raises(ValueError, match="batch_size"):
        run_gradual_archival(db_session, batch_size=0)
    with pytest.raises(ValueError, match="max_batches"):
        run_gradual_archival(db_session, max_batches=0)
    with pytest.raises(ValueError, match="sleep_ms"):
        run_gradual_archival(db_session, sleep_ms=-1)

    # _assert_ux_summary_fk_removed / _ensure_assistant_ux_summaries when summary table is absent
    fake_insp = MagicMock()
    fake_insp.has_table.return_value = False
    with patch("services.chat_message_archival.sa_inspect", return_value=fake_insp):
        _assert_ux_summary_fk_removed(MagicMock())
    with patch("services.chat_message_archival.has_chat_message_ux_summary_table", return_value=False):
        assert _ensure_assistant_ux_summaries(db_session, []) == (True, 0, 0)

    # _run_postgres_vacuum_analyze returns False on non-PG (SQLite)
    assert _run_postgres_vacuum_analyze(db_session) is False

    # _delete_hot_rows fallback when driver returns rowcount=None
    fake_del_sess = MagicMock(spec=Session)
    fake_del_sess.exec.return_value.rowcount = None
    assert _delete_hot_rows(fake_del_sess, ["id-1", "id-2"]) == 2

    # Generic (non-PG, non-SQLite) and PG dialect branches in _upsert_archive_rows & _insert_ux_summary_rows_idempotent
    sample_archive_row = {
        "id": "m-dialect-1",
        "session_id": "s-1",
        "role": "assistant",
        "content": "hi",
        "tool_calls": None,
        "tool_call_id": None,
        "reasoning_content": None,
        "message_metadata": None,
        "created_at": datetime(2026, 11, 1, 0, 0, 0),
        "archived_at": datetime(2026, 11, 20, 0, 0, 0),
    }
    sample_summary_row = {
        "message_id": "m-dialect-1",
        "session_id": "s-1",
        "created_at": datetime(2026, 11, 1, 0, 0, 0),
        "round_category": "healthy",
        "abnormal_reason": None,
        "feedback_vote": None,
        "feedback_preset": None,
        "ai_edit_cards": 0,
        "ai_edit_undone": 0,
        "ai_delete_cards": 0,
        "ai_delete_restored": 0,
    }

    # PostgreSQL dialect branch (rowcount=1 and rowcount=-1 fallback)
    pg_sess = MagicMock(spec=Session)
    pg_sess.get_bind.return_value.dialect.name = "postgresql"
    pg_sess.exec.return_value.rowcount = 1
    with patch("services.chat_message_archival._verify_archived_ids", return_value=set()):
        assert _upsert_archive_rows(pg_sess, [sample_archive_row]) == (1, 0)
    pg_sess.exec.return_value.rowcount = -1
    with patch("services.chat_message_archival._verify_archived_ids", return_value={"m-dialect-1"}):
        assert _upsert_archive_rows(pg_sess, [sample_archive_row]) == (1, 1)
    pg_sess.exec.return_value.rowcount = 1
    assert _insert_ux_summary_rows_idempotent(pg_sess, [sample_summary_row]) == 1

    # Generic fallback dialect branch (update existing + insert missing)
    gen_sess = MagicMock(spec=Session)
    gen_sess.get_bind.return_value.dialect.name = "mysql"
    row_new = {**sample_archive_row, "id": "m-dialect-2"}
    with patch("services.chat_message_archival._verify_archived_ids", return_value={"m-dialect-1"}):
        assert _upsert_archive_rows(gen_sess, [sample_archive_row, row_new]) == (2, 1)
    gen_sess.exec.return_value.all.return_value = ["m-dialect-1"]
    row_sum_new = {**sample_summary_row, "message_id": "m-dialect-2"}
    assert _insert_ux_summary_rows_idempotent(gen_sess, [sample_summary_row, row_sum_new]) == 1

    # PG FOR UPDATE SKIP LOCKED branch in archive_chat_messages_batch
    pg_live_sess = MagicMock(spec=Session)
    pg_live_sess.get_bind.return_value.dialect.name = "postgresql"
    pg_live_sess.exec.return_value.all.return_value = []
    pg_live_sess.exec.return_value.one.return_value = None
    with (
        patch("services.chat_message_archival.has_chat_message_archive_table", return_value=True),
        patch("services.chat_message_archival._assert_ux_summary_fk_removed"),
    ):
        pg_empty = archive_chat_messages_batch(pg_live_sess, cutoff=datetime(2026, 11, 19), dry_run=False)
    assert pg_empty.scanned == 0

    # run_gradual_archival progress_callback, inter-batch sleep_ms > 0, and vacuum_analyze=True
    progress_calls: list[int] = []
    batch_seq = [
        ArchiveBatchResult(scanned=2, archived=2, deleted=2, has_more=True),
        ArchiveBatchResult(scanned=1, archived=1, deleted=1, has_more=False),
    ]
    with (
        patch("services.chat_message_archival.archive_chat_messages_batch", side_effect=batch_seq),
        patch("services.chat_message_archival.time.sleep") as mock_sleep,
        patch("services.chat_message_archival._run_postgres_vacuum_analyze", return_value=True) as mock_vac,
    ):
        rep = run_gradual_archival(
            db_session,
            batch_size=2,
            max_batches=5,
            sleep_ms=25,
            dry_run=False,
            vacuum_analyze=True,
            progress_callback=lambda b, _r, _rep: progress_calls.append(b),
        )
    assert progress_calls == [1, 2]
    mock_sleep.assert_called_once_with(0.025)
    mock_vac.assert_called_once()
    assert rep.vacuum_analyzed is True
    assert rep.total_deleted == 3


@pytest.mark.unit
def test_compact_archived_tool_calls_slims_read_and_search_files_without_touching_skills():
    from services.chat_message_archival import compact_archived_tool_calls

    raw = json.dumps(
        [
            {
                "id": "call-read",
                "name": "read_files",
                "status": "success",
                "result": [
                    {
                        "id": "f-1",
                        "title": "第一章",
                        "file_type": "draft",
                        "content": "超长正文" * 200,
                    }
                ],
            },
            {
                "id": "call-search",
                "name": "search_files",
                "status": "success",
                "result": {
                    "query": "雾港",
                    "files": [
                        {
                            "id": "f-1",
                            "title": "第一章",
                            "matches": [{"snippet": "雾港" * 40}],
                        }
                    ],
                },
            },
            {
                "id": "call-skill",
                "name": "load_skill",
                "status": "success",
                "result": {"skill_id": "sk-1", "skill_name": "短篇节奏", "instructions_chars": 1200},
            },
        ],
        ensure_ascii=True,
        indent=2,
    )
    compacted = compact_archived_tool_calls(raw)
    assert compacted is not None
    parsed = json.loads(compacted)
    assert "content" not in parsed[0]["result"][0]
    assert parsed[0]["result"][0]["archived_note"] == ARCHIVED_QUERY_CONTENT_NOTE
    assert "matches" not in parsed[1]["result"]["files"][0]
    assert parsed[1]["result"]["files"][0]["archived_note"] == ARCHIVED_QUERY_CONTENT_NOTE
    # Skill cards are preserved byte-for-byte semantically.
    assert parsed[2]["result"] == {"skill_id": "sk-1", "skill_name": "短篇节奏", "instructions_chars": 1200}
