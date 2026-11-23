"""ChatMessage 存储去重（reasoning_content）与压缩前历史 tool_calls 归档回归测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from agent.core.chat_storage import (
    ARCHIVED_QUERY_CONTENT_NOTE,
    archive_historical_tool_calls_json,
    archive_pre_compaction_tool_calls,
    archive_pre_compaction_tool_calls_safe,
)
from agent.core.message_manager import MessageManager
from agent.core.session_loader import SessionLoader
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop.history import LOOP_ENGINE_METADATA_VALUE, load_loop_history
from agent.service import AgentService
from models import ChatMessage, ChatSession, File, Project, User
from scripts.compact_historical_chat_messages import (
    build_parser,
    compact_historical_chat_messages,
)
from services.chat_tool_cards import record_delete_card_restored, record_edit_card_undone
from services.features.file_version_service import content_sha256


def _seed_project(db_session: Session) -> tuple[User, Project, ChatSession]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"slim_{suffix}",
        email=f"slim-{suffix}@example.com",
        hashed_password="unused",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    project = Project(name=f"Slim {suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.flush()
    chat = ChatSession(user_id=user.id, project_id=project.id, title="测试会话", is_active=True)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(project)
    db_session.refresh(chat)
    return user, project, chat


# ---------------------------------------------------------------------------
# 1. reasoning_content deduplication & thinking-only persistence
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_message_manager_dedups_reasoning_when_display_events_have_thinking(db_session: Session) -> None:
    user, project, chat = _seed_project(db_session)
    manager = MessageManager(project_id=project.id, user_id=user.id)

    msg_id = manager._save_messages_with_session(
        db_session,
        chat.id,
        "帮我梳理第二章",
        "好的，下面是第二章脉络。",
        reasoning_content="先读大纲再核对人物动线",
        assistant_display_events=[
            {"type": "thinking_content", "content": "先读大纲再核对人物动线"},
            {"type": "content", "content": "好的，下面是第二章脉络。"},
        ],
        assistant_extra_metadata={"engine": LOOP_ENGINE_METADATA_VALUE},
    )

    assert msg_id is not None
    saved = db_session.get(ChatMessage, msg_id)
    assert saved is not None
    assert saved.reasoning_content is None
    metadata = json.loads(saved.message_metadata or "{}")
    assert metadata["display_events"] == [
        {"type": "thinking_content", "content": "先读大纲再核对人物动线"},
        {"type": "content", "content": "好的，下面是第二章脉络。"},
    ]


@pytest.mark.unit
def test_thinking_only_turn_persists_with_reasoning_content_none(db_session: Session) -> None:
    """仅输出 thinking_content、正文为空的助手轮次仍必须落库，且不双写 reasoning_content。"""
    user, project, chat = _seed_project(db_session)
    manager = MessageManager(project_id=project.id, user_id=user.id)

    msg_id = manager._save_messages_with_session(
        db_session,
        chat.id,
        "思考一下结局反转",
        "",
        reasoning_content=None,
        assistant_display_events=[
            {"type": "thinking_content", "content": "反转要照应第一章埋下的怀表"},
        ],
        assistant_extra_metadata={"engine": LOOP_ENGINE_METADATA_VALUE},
    )

    assert msg_id is not None
    saved = db_session.get(ChatMessage, msg_id)
    assert saved is not None
    assert saved.content == ""
    assert saved.reasoning_content is None
    metadata = json.loads(saved.message_metadata or "{}")
    assert metadata["display_events"] == [
        {"type": "thinking_content", "content": "反转要照应第一章埋下的怀表"}
    ]


@pytest.mark.unit
def test_legacy_rows_without_thinking_display_events_keep_reasoning_content(db_session: Session) -> None:
    """无 display_events 或 display_events 不含 thinking_content 的旧调用方照常保留 reasoning_content。"""
    user, project, chat = _seed_project(db_session)
    manager = MessageManager(project_id=project.id, user_id=user.id)

    msg_id = manager._save_messages_with_session(
        db_session,
        chat.id,
        "旧链路消息",
        "旧链路正文",
        reasoning_content="旧链路思考内容",
        assistant_display_events=None,
    )

    assert msg_id is not None
    saved = db_session.get(ChatMessage, msg_id)
    assert saved is not None
    assert saved.reasoning_content == "旧链路思考内容"


@pytest.mark.asyncio
@pytest.mark.usefixtures("writing_prompt_configs")
async def test_agent_service_loop_thinking_only_turn_persists_without_duplicating_reasoning(
    db_session: Session,
) -> None:
    """端到端走 AgentService.process_stream：仅 thinking_content 的单循环轮次正常落库且 reasoning_content=None。"""
    user, project, chat = _seed_project(db_session)

    async def fake_loop_stream(_state, **_kwargs):
        yield StreamEvent(type=StreamEventType.THINKING, data={"thinking": "先检查第三章人物立场"})
        yield StreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={"stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}},
        )

    service = AgentService()
    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop_stream):
        frames = [
            frame
            async for frame in service.process_stream(
                session=db_session,
                project_id=project.id,
                message="分析一下第三章",
                user_id=user.id,
                session_id=chat.id,
            )
        ]

    assert any("thinking_content" in frame for frame in frames)
    assistant_rows = db_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == chat.id, ChatMessage.role == "assistant")
    ).all()
    assert len(assistant_rows) == 1
    row = assistant_rows[0]
    assert row.content == ""
    assert row.reasoning_content is None
    metadata = json.loads(row.message_metadata or "{}")
    assert metadata["display_events"] == [
        {"type": "thinking_content", "content": "先检查第三章人物立场"}
    ]


# ---------------------------------------------------------------------------
# 2. archive_historical_tool_calls_json unit behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_archive_historical_tool_calls_json_slims_only_query_and_parallel_query_tasks() -> None:
    edit_card = {
        "id": "call-edit",
        "name": "edit_file",
        "arguments": {"id": "f-draft", "edits": [{"op": "replace", "old": "甲", "new": "乙"}]},
        "status": "success",
        "result": {
            "id": "f-draft",
            "title": "第一章",
            "file_type": "draft",
            "content": "绝不能被修改的 edit_file 字段",
            "undo": {
                "before_version_number": 1,
                "expected_after_updated_at": "2026-11-23T08:00:00+00:00",
                "expected_after_content_sha256": "abc123",
                "undone": False,
            },
            "details": [{"op": "replace", "old_preview": "甲", "new_preview": "乙"}],
        },
        "error": None,
    }
    write_chapter_card = {
        "id": "call-write",
        "name": "write_chapter",
        "arguments": {"chapter": 2, "title": "第二章"},
        "status": "success",
        "result": {
            "id": "f-ch2",
            "title": "第二章",
            "file_type": "draft",
            "action": "replaced",
            "body_saved": True,
            "content": "write_chapter 不应被归档改写",
            "undo": {
                "before_version_number": 2,
                "expected_after_updated_at": "2026-11-23T08:01:00+00:00",
                "expected_after_content_sha256": "def456",
                "before_title": "旧第二章",
            },
        },
        "error": None,
    }
    delete_card = {
        "id": "call-del",
        "name": "delete_file",
        "arguments": {"id": "f-old"},
        "status": "success",
        "result": {
            "id": "f-old",
            "title": "废稿",
            "file_type": "draft",
            "mutation_applied": True,
            "deleted_at": "2026-11-23T08:02:00+00:00",
            "restored": False,
        },
        "error": None,
    }
    create_card = {
        "id": "call-create",
        "name": "create_file",
        "arguments": {"title": "人物卡", "file_type": "character"},
        "status": "success",
        "result": {"id": "f-char", "title": "人物卡", "file_type": "character", "content": "角色全文保留"},
        "error": None,
    }
    update_project_card = {
        "id": "call-proj",
        "name": "update_project",
        "arguments": {"notes": ["保持冷硬文风"]},
        "status": "success",
        "result": {"updated_fields": ["notes"], "notes_added": ["保持冷硬文风"]},
        "error": None,
    }

    calls = [
        {
            "id": "call-query-list",
            "name": "query_files",
            "arguments": {"ids": ["f-1", "f-2"]},
            "status": "success",
            "result": [
                {
                    "id": "f-1",
                    "title": "总纲",
                    "file_type": "outline",
                    "content": "超长总纲正文" * 200,
                    "content_preview": "前200字预览",
                    "matches": ["命中1", "命中2"],
                    "match_count": 2,
                    "author_edits": "作者手改差异",
                    "file_metadata": '{"chapter_number":1}',
                    "content_range": {"start": 0, "end": 1000, "total_chars": 5000},
                    "word_count": 1200,
                },
                {
                    "id": "f-2",
                    "title": "设定集",
                    "file_type": "lore",
                    "status": "ok",
                    "error": None,
                    "error_type": None,
                    "user_message": None,
                    "count": 1,
                    "content_preview": "设定预览",
                },
            ],
            "error": None,
        },
        {
            "id": "call-parallel",
            "name": "parallel_execute",
            "arguments": {"tasks": []},
            "status": "success",
            "result": {
                "total_tasks": 2,
                "completed": 2,
                "failed": 0,
                "tasks": [
                    {
                        "index": 0,
                        "type": "query_files",
                        "description": "读取卷纲",
                        "params": {"id": "f-1"},
                        "status": "completed",
                        "result": {
                            "status": "success",
                            "count": 1,
                            "data": [
                                {
                                    "id": "f-1",
                                    "title": "总纲",
                                    "file_type": "outline",
                                    "content": "并行读取的大正文" * 200,
                                    "matches": ["片段"],
                                    "match_count": 1,
                                    "author_edits": "diff",
                                    "file_metadata": "{}",
                                    "content_range": {"start": 0, "end": 100},
                                }
                            ],
                        },
                    },
                    {
                        "index": 1,
                        "type": "edit_file",
                        "description": "改第一章",
                        "params": {"id": "f-draft"},
                        "status": "completed",
                        "result": {
                            "status": "success",
                            "data": {
                                "id": "f-draft",
                                "title": "第一章",
                                "content": "parallel edit_file 子任务不准动",
                                "undo": {"before_version_number": 3},
                            },
                        },
                    },
                ],
            },
            "error": None,
        },
        edit_card,
        write_chapter_card,
        delete_card,
        create_card,
        update_project_card,
    ]

    raw = json.dumps(calls, ensure_ascii=False)
    archived_raw = archive_historical_tool_calls_json(raw)
    assert archived_raw is not None
    assert archived_raw != raw
    assert len(archived_raw) < len(raw)

    archived = json.loads(archived_raw)
    # 1) query_files list kept as list, heavy keys removed, metadata + archived_note preserved
    q_items = archived[0]["result"]
    assert isinstance(q_items, list) and len(q_items) == 2
    for key in ("content", "content_preview", "matches", "author_edits", "file_metadata", "content_range"):
        assert key not in q_items[0]
        assert key not in q_items[1]
    assert q_items[0]["id"] == "f-1"
    assert q_items[0]["title"] == "总纲"
    assert q_items[0]["file_type"] == "outline"
    assert q_items[0]["match_count"] == 2
    assert q_items[0]["archived_note"] == ARCHIVED_QUERY_CONTENT_NOTE
    assert q_items[1]["id"] == "f-2"
    assert q_items[1]["count"] == 1
    assert q_items[1]["archived_note"] == ARCHIVED_QUERY_CONTENT_NOTE

    # 2) parallel_execute query_files subtask slimmed; edit_file subtask untouched
    p_tasks = archived[1]["result"]["tasks"]
    pq_item = p_tasks[0]["result"]["data"][0]
    for key in ("content", "content_preview", "matches", "author_edits", "file_metadata", "content_range"):
        assert key not in pq_item
    assert pq_item["id"] == "f-1"
    assert pq_item["title"] == "总纲"
    assert pq_item["match_count"] == 1
    assert pq_item["archived_note"] == ARCHIVED_QUERY_CONTENT_NOTE
    assert p_tasks[0]["result"]["status"] == "success"
    assert p_tasks[0]["result"]["count"] == 1
    assert p_tasks[1] == calls[1]["result"]["tasks"][1]

    # 3) Write/undo/restore tools 100% untouched
    assert archived[2] == edit_card
    assert archived[3] == write_chapter_card
    assert archived[4] == delete_card
    assert archived[5] == create_card
    assert archived[6] == update_project_card

    # 4) Idempotency: re-running returns the exact same string
    assert archive_historical_tool_calls_json(archived_raw) == archived_raw


# ---------------------------------------------------------------------------
# 3. Automatic archival on compaction save + safety for post-compaction rows
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_compaction_save_archives_only_pre_compaction_rows_and_preserves_replay_snapshots_and_cards(
    db_session: Session,
) -> None:
    user, project, chat = _seed_project(db_session)
    base = datetime(2026, 11, 20, 10, 0, 0)

    draft1 = File(
        id="draft-1",
        project_id=project.id,
        title="第一章",
        file_type="draft",
        content="第一章最新正文",
        updated_at=base + timedelta(minutes=1),
    )
    draft2 = File(
        id="draft-2",
        project_id=project.id,
        title="第二章",
        file_type="draft",
        content="第二章最新正文",
        updated_at=base + timedelta(minutes=5),
    )
    deleted_file = File(
        id="draft-del",
        project_id=project.id,
        title="旧废稿",
        file_type="draft",
        content="旧废稿正文",
        is_deleted=True,
        deleted_at=base + timedelta(minutes=2),
    )
    db_session.add_all([draft1, draft2, deleted_file])
    db_session.commit()

    # Pre-compaction turn 1: query_files + edit_file (with undo anchor) + delete_file
    u1 = ChatMessage(id="m-01", session_id=chat.id, role="user", content="读第一章并改一下", created_at=base)
    a1 = ChatMessage(
        id="m-02",
        session_id=chat.id,
        role="assistant",
        content="已经读完并改好了第一章。",
        created_at=base + timedelta(minutes=1),
        tool_calls=json.dumps(
            [
                {
                    "id": "q1",
                    "name": "query_files",
                    "arguments": {"id": draft1.id},
                    "status": "success",
                    "result": [
                        {
                            "id": draft1.id,
                            "title": "第一章",
                            "file_type": "draft",
                            "content": "第一章很长的旧正文" * 100,
                            "updated_at": (base + timedelta(minutes=1)).isoformat(),
                        }
                    ],
                },
                {
                    "id": "e1",
                    "name": "edit_file",
                    "arguments": {"id": draft1.id},
                    "status": "success",
                    "result": {
                        "id": draft1.id,
                        "title": "第一章",
                        "file_type": "draft",
                        "mutation_applied": True,
                        "undo": {
                            "before_version_number": 1,
                            "expected_after_updated_at": (base + timedelta(minutes=1)).isoformat(),
                            "expected_after_content_sha256": content_sha256(draft1.content),
                        },
                    },
                },
                {
                    "id": "d1",
                    "name": "delete_file",
                    "arguments": {"id": deleted_file.id},
                    "status": "success",
                    "result": {
                        "id": deleted_file.id,
                        "title": "旧废稿",
                        "file_type": "draft",
                        "mutation_applied": True,
                        "deleted_at": (base + timedelta(minutes=2)).isoformat(),
                        "restored": False,
                    },
                },
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE}, ensure_ascii=False),
    )

    # Pre-compaction turn 2 (exact covers_through boundary): query_files
    u2 = ChatMessage(
        id="m-03",
        session_id=chat.id,
        role="user",
        content="再读一遍第一章",
        created_at=base + timedelta(minutes=2),
    )
    a2 = ChatMessage(
        id="m-04",
        session_id=chat.id,
        role="assistant",
        content="复核完毕。",
        created_at=base + timedelta(minutes=3),
        tool_calls=json.dumps(
            [
                {
                    "id": "q2",
                    "name": "query_files",
                    "arguments": {"id": draft1.id},
                    "status": "success",
                    "result": [
                        {
                            "id": draft1.id,
                            "title": "第一章",
                            "file_type": "draft",
                            "content": "第一章再次读取全文" * 100,
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE}, ensure_ascii=False),
    )
    db_session.add_all([u1, a1, u2, a2])
    chat.message_count = 4
    db_session.commit()

    # Turn 3 saves a compaction record covering through a2 (m-04) AND has its own post-compaction query_files!
    manager = MessageManager(project_id=project.id, user_id=user.id)
    a3_id = manager._save_messages_with_session(
        db_session,
        chat.id,
        "接着看第二章",
        "已经读完第二章。",
        tool_calls=[
            {
                "id": "q3",
                "name": "query_files",
                "arguments": {"id": draft2.id},
                "status": "success",
                "result": [
                    {
                        "id": draft2.id,
                        "title": "第二章",
                        "file_type": "draft",
                        "content": draft2.content,
                        "updated_at": draft2.updated_at.isoformat(),
                    }
                ],
            }
        ],
        reasoning_content="压缩后新轮思考",
        assistant_display_events=[
            {"type": "history_compacted", "data": {}},
            {"type": "thinking_content", "content": "压缩后新轮思考"},
            {"type": "tool_call", "tool_call_index": 0},
            {"type": "content", "content": "已经读完第二章。"},
        ],
        user_created_at=base + timedelta(minutes=4),
        assistant_extra_metadata={
            "engine": LOOP_ENGINE_METADATA_VALUE,
            "compaction": {
                "summary": "已完成第一章修改并删除旧废稿。",
                "covers_through_message_id": a2.id,
                "kept_author_messages": ["再读一遍第一章"],
                "created_at": (base + timedelta(minutes=4)).isoformat(),
            },
        },
    )
    assert a3_id is not None

    db_session.expire_all()
    a1_after = db_session.get(ChatMessage, a1.id)
    a2_after = db_session.get(ChatMessage, a2.id)
    a3_after = db_session.get(ChatMessage, a3_id)
    assert a1_after is not None and a2_after is not None and a3_after is not None

    # Pre-compaction rows a1 & a2: query_files content stripped, archived_note added
    a1_calls = json.loads(a1_after.tool_calls or "[]")
    assert "content" not in a1_calls[0]["result"][0]
    assert a1_calls[0]["result"][0]["archived_note"] == ARCHIVED_QUERY_CONTENT_NOTE
    assert a1_calls[0]["result"][0]["title"] == "第一章"
    # edit_file & delete_file on a1 remain 100% untouched
    assert a1_calls[1]["result"]["undo"]["before_version_number"] == 1
    assert a1_calls[2]["result"]["restored"] is False

    a2_calls = json.loads(a2_after.tool_calls or "[]")
    assert "content" not in a2_calls[0]["result"][0]
    assert a2_calls[0]["result"][0]["archived_note"] == ARCHIVED_QUERY_CONTENT_NOTE

    # Post-compaction row a3: query_files stays 100% full, reasoning_content deduped to None
    assert a3_after.reasoning_content is None
    a3_calls = json.loads(a3_after.tool_calls or "[]")
    assert a3_calls[0]["result"][0]["content"] == draft2.content
    assert "archived_note" not in a3_calls[0]["result"][0]

    # Cold DB history replay works cleanly
    history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert history.compacted is True
    assert any(draft2.content in json.dumps(item, ensure_ascii=False) for item in history.items)

    # SessionLoader.session_read_snapshots still carries post-compaction draft2 full snapshot!
    loader = SessionLoader(project.id, user.id)
    snapshots = loader.session_read_snapshots(db_session, chat_session_id=chat.id)
    assert snapshots == {draft2.id: draft2.content}

    # chat_tool_cards undo/restore on archived pre-compaction row a1 still works!
    assert record_edit_card_undone(
        db_session,
        user_id=user.id,
        project_id=project.id,
        file_id=draft1.id,
        message_id=a1.id,
        tool_use_id="e1",
        before_version_number=1,
        expected_after_updated_at=base + timedelta(minutes=1),
    )
    assert record_delete_card_restored(
        db_session,
        user_id=user.id,
        project_id=project.id,
        file_id=deleted_file.id,
        message_id=a1.id,
        tool_use_id="d1",
        restored_deleted_at=base + timedelta(minutes=2),
    )


@pytest.mark.unit
def test_archive_pre_compaction_tool_calls_safe_fails_open(db_session: Session) -> None:
    """归档内部异常绝不向上抛出，也不破坏主保存链路。"""
    with patch(
        "agent.core.chat_storage.archive_pre_compaction_tool_calls",
        side_effect=RuntimeError("synthetic archive error"),
    ):
        archived = archive_pre_compaction_tool_calls_safe(
            db_session,
            chat_session_id="missing-session",
            covers_through_message_id="missing-msg",
        )
    assert archived == 0


@pytest.mark.unit
def test_archive_pre_compaction_respects_same_timestamp_id_boundary(db_session: Session) -> None:
    """同微秒时间戳时严格按 (created_at, id) 边界：id <= anchor 归档，id > anchor 保留全文。"""
    _user, _project, chat = _seed_project(db_session)
    same_ts = datetime(2026, 11, 21, 12, 0, 0)

    def _q_call(body: str) -> str:
        return json.dumps(
            [{"id": "q", "name": "query_files", "arguments": {"id": "f"}, "status": "success", "result": [{"id": "f", "title": "章", "file_type": "draft", "content": body}]}],
            ensure_ascii=False,
        )

    m_before = ChatMessage(id="msg-a", session_id=chat.id, role="assistant", content="a", created_at=same_ts, tool_calls=_q_call("正文A"))
    m_anchor = ChatMessage(id="msg-b", session_id=chat.id, role="assistant", content="b", created_at=same_ts, tool_calls=_q_call("正文B"))
    m_after = ChatMessage(id="msg-c", session_id=chat.id, role="assistant", content="c", created_at=same_ts, tool_calls=_q_call("正文C"))
    db_session.add_all([m_before, m_anchor, m_after])
    db_session.commit()

    archived = archive_pre_compaction_tool_calls(
        db_session,
        chat_session_id=chat.id,
        covers_through_message_id=m_anchor.id,
    )
    assert archived == 2

    db_session.expire_all()
    assert "content" not in json.loads(db_session.get(ChatMessage, "msg-a").tool_calls)[0]["result"][0]
    assert "content" not in json.loads(db_session.get(ChatMessage, "msg-b").tool_calls)[0]["result"][0]
    assert json.loads(db_session.get(ChatMessage, "msg-c").tool_calls)[0]["result"][0]["content"] == "正文C"


# ---------------------------------------------------------------------------
# 4. Backfill script: --dry-run, --apply, active vs compacted vs >7d idle, idempotency
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_backfill_script_dry_run_apply_and_idempotency(db_session: Session) -> None:
    user, project, active_chat = _seed_project(db_session)
    compacted_chat = ChatSession(user_id=user.id, project_id=project.id, title="已压缩会话", is_active=False)
    idle_chat = ChatSession(user_id=user.id, project_id=project.id, title="静置会话", is_active=False)
    db_session.add_all([compacted_chat, idle_chat])
    db_session.commit()

    now = datetime(2026, 11, 23, 12, 0, 0)

    def _heavy_query_tools(text: str) -> str:
        return json.dumps(
            [
                {
                    "id": "q1",
                    "name": "query_files",
                    "arguments": {"id": "f1"},
                    "status": "success",
                    "result": [{"id": "f1", "title": "第一章", "file_type": "draft", "content": text}],
                },
                {
                    "id": "e1",
                    "name": "edit_file",
                    "arguments": {"id": "f1"},
                    "status": "success",
                    "result": {"id": "f1", "title": "第一章", "undo": {"before_version_number": 1}},
                },
            ],
            ensure_ascii=False,
        )

    thinking_meta = json.dumps(
        {"engine": LOOP_ENGINE_METADATA_VALUE, "display_events": [{"type": "thinking_content", "content": "思考内容"}]},
        ensure_ascii=False,
    )
    legacy_no_display_meta = json.dumps({"stop_reason": "end_turn"}, ensure_ascii=False)

    # 1) Active session (< 7d old, no compaction):
    #    - tool_calls MUST stay 100% full
    #    - duplicate reasoning_content (with display_events thinking_content) cleared
    #    - legacy row without display_events keeps reasoning_content
    active_dup = ChatMessage(
        id="act-1",
        session_id=active_chat.id,
        role="assistant",
        content="活跃回答",
        created_at=now - timedelta(hours=2),
        reasoning_content="重复思考正文" * 20,
        tool_calls=_heavy_query_tools("活跃会话查询正文" * 50),
        message_metadata=thinking_meta,
    )
    active_legacy = ChatMessage(
        id="act-2",
        session_id=active_chat.id,
        role="assistant",
        content="旧行回答",
        created_at=now - timedelta(hours=1),
        reasoning_content="旧行唯一思考备份",
        tool_calls=_heavy_query_tools("活跃旧行查询正文" * 50),
        message_metadata=legacy_no_display_meta,
    )

    # 2) Active session WITH compaction:
    #    - pre-compaction row (<= covers_through) tool_calls archived
    #    - post-compaction row (> covers_through) tool_calls kept 100% full
    comp_pre = ChatMessage(
        id="cmp-1",
        session_id=compacted_chat.id,
        role="assistant",
        content="压缩前回答",
        created_at=now - timedelta(hours=5),
        reasoning_content="压缩前重复思考" * 10,
        tool_calls=_heavy_query_tools("压缩前长正文" * 50),
        message_metadata=thinking_meta,
    )
    comp_post = ChatMessage(
        id="cmp-2",
        session_id=compacted_chat.id,
        role="assistant",
        content="压缩后回答",
        created_at=now - timedelta(hours=1),
        reasoning_content=None,
        tool_calls=_heavy_query_tools("压缩后活跃正文" * 50),
        message_metadata=json.dumps(
            {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "compaction": {
                    "summary": "交接摘要",
                    "covers_through_message_id": "cmp-1",
                    "kept_author_messages": [],
                },
            },
            ensure_ascii=False,
        ),
    )

    # 3) Idle session (> 7d since latest message, no compaction):
    #    - tool_calls archived AND duplicate reasoning_content cleared
    idle_msg = ChatMessage(
        id="idl-1",
        session_id=idle_chat.id,
        role="assistant",
        content="静置会话回答",
        created_at=now - timedelta(days=10),
        reasoning_content="静置重复思考" * 10,
        tool_calls=_heavy_query_tools("静置会话旧正文" * 50),
        message_metadata=thinking_meta,
    )

    db_session.add_all([active_dup, active_legacy, comp_pre, comp_post, idle_msg])
    db_session.commit()

    bind = db_session.get_bind()

    # Parser defaults to dry_run=True
    parsed_default = build_parser().parse_args([])
    assert parsed_default.dry_run is True
    assert parsed_default.batch_size == 200
    assert parsed_default.sleep_ms == 100
    assert parsed_default.idle_days == 7
    assert build_parser().parse_args(["--apply"]).dry_run is False

    # Step 1: --dry-run mutates NOTHING, reports exact counts & bytes
    dry_report = compact_historical_chat_messages(
        lambda: Session(bind),
        dry_run=True,
        batch_size=2,
        sleep_ms=0,
        idle_days=7,
        now=now,
    )
    assert dry_report.dry_run is True
    assert dry_report.scanned == 5
    assert dry_report.rows_updated == 3  # act-1 (reasoning), cmp-1 (both), idl-1 (both)
    assert dry_report.reasoning_cleared == 3  # act-1, cmp-1, idl-1
    assert dry_report.tool_calls_archived == 2  # cmp-1, idl-1
    assert dry_report.total_bytes_saved > 0

    db_session.expire_all()
    assert db_session.get(ChatMessage, "act-1").reasoning_content is not None
    assert "content" in json.loads(db_session.get(ChatMessage, "cmp-1").tool_calls)[0]["result"][0]
    assert "content" in json.loads(db_session.get(ChatMessage, "idl-1").tool_calls)[0]["result"][0]

    # Step 2: --apply mutates ONLY eligible rows/columns
    apply_report = compact_historical_chat_messages(
        lambda: Session(bind),
        dry_run=False,
        batch_size=2,
        sleep_ms=0,
        idle_days=7,
        now=now,
    )
    assert apply_report.dry_run is False
    assert apply_report.rows_updated == 3
    assert apply_report.reasoning_cleared == 3
    assert apply_report.tool_calls_archived == 2
    assert apply_report.total_bytes_saved == dry_report.total_bytes_saved

    db_session.expire_all()
    # Active session: act-1 cleared reasoning only, kept full tool_calls; act-2 untouched
    got_act1 = db_session.get(ChatMessage, "act-1")
    got_act2 = db_session.get(ChatMessage, "act-2")
    assert got_act1.reasoning_content is None
    assert "活跃会话查询正文" in json.loads(got_act1.tool_calls)[0]["result"][0]["content"]
    assert got_act2.reasoning_content == "旧行唯一思考备份"
    assert "活跃旧行查询正文" in json.loads(got_act2.tool_calls)[0]["result"][0]["content"]

    # Compacted session: cmp-1 archived + reasoning cleared + edit_file intact; cmp-2 full
    got_cmp1 = db_session.get(ChatMessage, "cmp-1")
    got_cmp2 = db_session.get(ChatMessage, "cmp-2")
    assert got_cmp1.reasoning_content is None
    cmp1_calls = json.loads(got_cmp1.tool_calls)
    assert "content" not in cmp1_calls[0]["result"][0]
    assert cmp1_calls[0]["result"][0]["archived_note"] == ARCHIVED_QUERY_CONTENT_NOTE
    assert cmp1_calls[1]["result"]["undo"]["before_version_number"] == 1
    assert "压缩后活跃正文" in json.loads(got_cmp2.tool_calls)[0]["result"][0]["content"]

    # Idle session: idl-1 archived + reasoning cleared + edit_file intact
    got_idl1 = db_session.get(ChatMessage, "idl-1")
    assert got_idl1.reasoning_content is None
    idl1_calls = json.loads(got_idl1.tool_calls)
    assert "content" not in idl1_calls[0]["result"][0]
    assert idl1_calls[0]["result"][0]["archived_note"] == ARCHIVED_QUERY_CONTENT_NOTE
    assert idl1_calls[1]["result"]["undo"]["before_version_number"] == 1

    # Step 3: Re-running --apply is 100% idempotent (0 updates, 0 bytes saved)
    rerun_report = compact_historical_chat_messages(
        lambda: Session(bind),
        dry_run=False,
        batch_size=2,
        sleep_ms=0,
        idle_days=7,
        now=now,
    )
    assert rerun_report.rows_updated == 0
    assert rerun_report.reasoning_cleared == 0
    assert rerun_report.tool_calls_archived == 0
    assert rerun_report.total_bytes_saved == 0


@pytest.mark.unit
def test_backfill_progress_line_is_numeric_only_without_pii_or_text(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """回填脚本输出仅含计数与节省字节，绝不打印消息正文、会话 id 或 PII。"""
    from scripts.compact_historical_chat_messages import main

    user, _project, chat = _seed_project(db_session)
    secret_prose = "绝密未发表小说正文_DO_NOT_PRINT"
    db_session.add(
        ChatMessage(
            id="secret-msg-1",
            session_id=chat.id,
            role="assistant",
            content=secret_prose,
            created_at=datetime(2026, 1, 1, 0, 0, 0),
            reasoning_content="绝密思考_SECRET_REASONING",
            tool_calls=json.dumps(
                [
                    {
                        "id": "q1",
                        "name": "query_files",
                        "arguments": {"id": "f1"},
                        "status": "success",
                        "result": [{"id": "f1", "title": "绝密标题", "file_type": "draft", "content": secret_prose}],
                    }
                ],
                ensure_ascii=False,
            ),
            message_metadata=json.dumps(
                {"display_events": [{"type": "thinking_content", "content": "绝密思考_SECRET_REASONING"}]},
                ensure_ascii=False,
            ),
        )
    )
    db_session.commit()

    bind = db_session.get_bind()
    with patch("database.sync_engine", bind):
        rc = main(["--dry-run", "--batch-size", "10", "--sleep-ms", "0", "--idle-days", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[summary] mode=dry_run" in out
    assert "total_bytes_saved=" in out
    for forbidden in (secret_prose, "SECRET_REASONING", "绝密标题", chat.id, user.email, "secret-msg-1"):
        assert forbidden not in out


@pytest.mark.unit
def test_backfill_skips_compaction_archival_when_anchor_row_is_missing(db_session: Session) -> None:
    """活跃会话压缩点指向已删除行时，与 loop/history.py 一致退回完整历史、不归档其 tool_calls。"""
    _user, _project, chat = _seed_project(db_session)
    now = datetime(2026, 11, 23, 12, 0, 0)
    db_session.add_all(
        [
            ChatMessage(
                id="orphan-pre",
                session_id=chat.id,
                role="assistant",
                content="早期回复",
                created_at=now - timedelta(hours=3),
                tool_calls=json.dumps(
                    [
                        {
                            "id": "q1",
                            "name": "query_files",
                            "arguments": {"id": "f1"},
                            "status": "success",
                            "result": [{"id": "f1", "title": "第一章", "file_type": "draft", "content": "保留正文"}],
                        }
                    ],
                    ensure_ascii=False,
                ),
            ),
            ChatMessage(
                id="orphan-comp",
                session_id=chat.id,
                role="assistant",
                content="压缩回复",
                created_at=now - timedelta(hours=1),
                message_metadata=json.dumps(
                    {
                        "engine": LOOP_ENGINE_METADATA_VALUE,
                        "compaction": {
                            "summary": "摘要",
                            "covers_through_message_id": "deleted-anchor-id",
                            "kept_author_messages": [],
                        },
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
    )
    db_session.commit()

    bind = db_session.get_bind()
    report = compact_historical_chat_messages(
        lambda: Session(bind),
        dry_run=False,
        batch_size=10,
        sleep_ms=0,
        idle_days=7,
        now=now,
    )
    assert report.tool_calls_archived == 0
    db_session.expire_all()
    assert json.loads(db_session.get(ChatMessage, "orphan-pre").tool_calls)[0]["result"][0]["content"] == "保留正文"
