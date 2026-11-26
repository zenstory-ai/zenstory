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

    # Enforce SQLite foreign keys on this connection to prove deleting from chat_message
    # cannot CASCADE-delete chat_message_ux_summary.
    db_session.exec(text("PRAGMA foreign_keys = ON"))

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
    #    committed before delete) -> idempotent re-run must skip conflict, verify both,
    #    and delete both hot rows cleanly.
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
    assert ok_batch.archived == 1  # only m2 was newly inserted; m1 hit ON CONFLICT DO NOTHING
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

    # Downgrade back to 20261124_180000 succeeds cleanly even with orphan summary row,
    # and drops chat_message_archive.
    _run_alembic(db_url, "downgrade", PREV_REVISION)
    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "chat_message_archive" not in inspector.get_table_names()
    engine.dispose()
