"""Chat edits and deletion use existing file history and soft-delete restore, independent of authorship."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from agent.loop.history import load_loop_history
from agent.tools import mcp_tools
from api import files as files_api
from core.error_handler import APIException
from models import ChatMessage, ChatSession, File, Project, User
from services.chat_tool_cards import record_delete_card_restored
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService

AI_CH3 = "老周在收摊。折叠桌一张张码上三轮车。老周回头看了他一眼。"
AUTHOR_CH3 = "老秦在收摊。折叠桌一张张码上三轮车。老秦回头看了他一眼。"


@pytest.fixture
def owner_project(db_session, monkeypatch):
    user = User(username="guard-owner", email="guard-owner@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="还剩三分钟", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    return user, project


def _ai_chapter(session, project, title="第3章 三十年", content=AI_CH3):
    file = File(project_id=project.id, title=title, content=content, file_type="draft")
    session.add(file)
    session.flush()
    FileVersionService().create_version(
        session, file.id, content, change_type="create", change_source="ai", commit=False
    )
    session.commit()
    session.refresh(file)
    return file


def _author_saves(session, user, file, content):
    files_api.update_file(
        file.id,
        files_api.FileUpdate(content=content),
        current_user=user,
        session=session,
    )


async def _run(tool, session, user, project, args, *, message, **context):
    engine = session.get_bind()
    req_ctx = mcp_tools.AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id=None,
        create_session_func=lambda: Session(engine),
        author_message=message,
        author_steering=[],
        **context,
    )
    try:
        raw = await tool(args, request_context=req_ctx)
    finally:
        req_ctx.clear()
    return json.loads(raw["content"][0]["text"])


async def test_edit_reports_unbacked_original_as_unapplied_typed_error(db_session, owner_project, monkeypatch):
    """原稿没能进历史时 edit_file 拒绝：结果明确一处没改，模型可以同轮重试。"""
    from sqlalchemy import text

    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    chapter.content = AI_CH3 + "作者新加、没进历史的一段。\n"
    db_session.add(chapter)
    db_session.commit()

    def fail_backup(_self, session, *_args, **_kwargs):
        session.exec(text("SELECT * FROM table_that_does_not_exist_for_backup_test"))

    monkeypatch.setattr(FileVersionService, "backup_unversioned_content", fail_backup)

    payload = await _run(
        mcp_tools.edit_file,
        db_session,
        user,
        project,
        {"id": chapter.id, "edits": [{"op": "append", "text": "AI 续写。\n"}]},
        message="接着写",
    )

    assert payload["status"] == "error"
    assert payload["error_type"] == "pre_write_history_unavailable"
    assert payload["mutation_applied"] is False
    assert payload["edits_applied"] == 0


@pytest.mark.parametrize("has_history", [True, False])
async def test_author_saved_or_unversioned_file_soft_deletes_and_restores_from_card(
    db_session, owner_project, has_history
):
    user, project = owner_project
    if has_history:
        chapter = _ai_chapter(db_session, project)
        _author_saves(db_session, user, chapter, AUTHOR_CH3)
    else:
        chapter = File(project_id=project.id, title="作者原稿", content=AUTHOR_CH3, file_type="draft")
        db_session.add(chapter)
        db_session.commit()

    result = await _run(mcp_tools.delete_file, db_session, user, project, {"id": chapter.id}, message="删掉这份废稿")
    assert result["status"] == "success"
    data = result["data"]
    assert data["id"] == chapter.id
    assert data["title"] == chapter.title
    assert data["file_type"] == "draft"
    assert data["restored"] is False
    assert data["mutation_applied"] is True
    assert isinstance(data.get("deleted_at"), str)
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is True
    assert db_session.get(File, chapter.id).content == AUTHOR_CH3

    chat = ChatSession(user_id=user.id, project_id=project.id, title="删除恢复测试")
    db_session.add(chat)
    db_session.flush()
    user_msg = ChatMessage(session_id=chat.id, role="user", content="删掉这份废稿")
    assistant_msg = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已删除，可在卡片上恢复。",
        tool_calls=json.dumps(
            [
                {
                    "id": "call_del_1",
                    "name": "delete_file",
                    "arguments": json.dumps({"id": chapter.id}, ensure_ascii=False),
                    "result": data,
                    "status": "success",
                }
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps(
            {
                "engine": "loop",
                "display_events": [
                    {"type": "tool_call", "data": {"tool_use_id": "call_del_1"}},
                    {"type": "content", "content": "已删除，可在卡片上恢复。"},
                ],
            },
            ensure_ascii=False,
        ),
    )
    db_session.add_all([user_msg, assistant_msg])
    db_session.commit()

    restored = files_api.restore_file(
        chapter.id,
        request=files_api.RestoreFileRequest(chat_message_id=assistant_msg.id, tool_use_id="call_del_1"),
        current_user=user,
        session=db_session,
    )
    assert restored["id"] == chapter.id
    assert restored["title"] == chapter.title
    assert restored["restored"] is True
    assert restored["already_restored"] is False
    assert restored["restored_count"] == 1
    assert restored["file_ids"] == [chapter.id]

    # Idempotent repeat call keeps already_restored=True without error.
    repeat = files_api.restore_file(
        chapter.id,
        request=files_api.RestoreFileRequest(chat_message_id=assistant_msg.id, tool_use_id="call_del_1"),
        current_user=user,
        session=db_session,
    )
    assert repeat["already_restored"] is True
    assert repeat["restored_count"] == 0

    db_session.expire_all()
    reloaded = db_session.get(File, chapter.id)
    assert reloaded.is_deleted is False
    assert reloaded.deleted_at is None
    assert reloaded.content == AUTHOR_CH3

    saved_calls = json.loads(db_session.get(ChatMessage, assistant_msg.id).tool_calls)
    assert saved_calls[0]["result"]["restored"] is True

    history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
    )
    outputs = [item for item in history.items if item.get("type") == "function_call_output"]
    assert len(outputs) == 1
    replayed = json.loads(outputs[0]["output"])
    assert replayed["data"]["restored"] is True


def test_folder_restore_recovers_only_same_timestamp_descendants(db_session, owner_project, monkeypatch):
    from agent.tools.file_ops import crud as crud_module

    user, project = owner_project
    folder = File(project_id=project.id, title="废弃卷", file_type="folder")
    db_session.add(folder)
    db_session.flush()

    earlier_stamp = datetime(2026, 11, 18, 10, 0, 0, tzinfo=UTC)
    folder_stamp = earlier_stamp + timedelta(minutes=5)

    old_deleted = File(
        project_id=project.id,
        parent_id=folder.id,
        title="更早删掉的草稿",
        file_type="draft",
        content="旧废稿",
        is_deleted=True,
        deleted_at=earlier_stamp,
    )
    live_child = File(
        project_id=project.id,
        parent_id=folder.id,
        title="随卷一起删的章节",
        file_type="draft",
        content="保留正文",
    )
    nested_folder = File(project_id=project.id, parent_id=folder.id, title="子目录", file_type="folder")
    db_session.add_all([old_deleted, live_child, nested_folder])
    db_session.flush()
    nested_leaf = File(
        project_id=project.id,
        parent_id=nested_folder.id,
        title="子目录设定",
        file_type="lore",
        content="设定内容",
    )
    db_session.add(nested_leaf)
    db_session.commit()

    monkeypatch.setattr(crud_module, "utcnow", lambda: folder_stamp)
    assert crud_module.FileCRUD(db_session, user.id).delete_file(folder.id, recursive=True) is True

    restored = files_api.restore_file(folder.id, request=None, current_user=user, session=db_session)
    assert restored["restored_count"] == 4

    db_session.expire_all()
    assert db_session.get(File, folder.id).is_deleted is False
    assert db_session.get(File, live_child.id).is_deleted is False
    assert db_session.get(File, nested_folder.id).is_deleted is False
    assert db_session.get(File, nested_leaf.id).is_deleted is False
    assert db_session.get(File, old_deleted.id).is_deleted is True


def test_restore_file_rejects_missing_parent_duplicate_title_and_legacy_card_mismatch(db_session, owner_project):
    user, project = owner_project
    parent = File(project_id=project.id, title="原目录", file_type="folder")
    db_session.add(parent)
    db_session.flush()
    deleted_child = File(
        project_id=project.id,
        parent_id=parent.id,
        title="第1章 雨夜",
        file_type="draft",
        content="原稿",
        is_deleted=True,
        deleted_at=datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC),
    )
    db_session.add(deleted_child)
    db_session.commit()

    # Deleted parent -> 409 conflict
    parent.is_deleted = True
    parent.deleted_at = datetime(2026, 11, 18, 12, 5, 0, tzinfo=UTC)
    db_session.add(parent)
    db_session.commit()
    with pytest.raises(APIException) as missing_parent:
        files_api.restore_file(deleted_child.id, request=None, current_user=user, session=db_session)
    assert missing_parent.value.status_code == 409

    # Live parent with duplicate sibling title -> 409 conflict
    parent.is_deleted = False
    parent.deleted_at = None
    duplicate = File(project_id=project.id, parent_id=parent.id, title="第1章 雨夜", file_type="draft", content="新稿")
    db_session.add_all([parent, duplicate])
    db_session.commit()
    with pytest.raises(APIException) as duplicate_title:
        files_api.restore_file(deleted_child.id, request=None, current_user=user, session=db_session)
    assert duplicate_title.value.status_code == 409

    # Missing file -> 404
    with pytest.raises(APIException) as not_found:
        files_api.restore_file("nonexistent-id", request=None, current_user=user, session=db_session)
    assert not_found.value.status_code == 404

    # Legacy boolean delete_file card with persisted MessageManager `arguments` JSON string upgrades cleanly,
    # while failed / unapplied cards are never mutated to restored=True.
    chat = ChatSession(user_id=user.id, project_id=project.id, title="旧卡片测试")
    db_session.add(chat)
    db_session.flush()
    msg = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已删",
        tool_calls=json.dumps(
            [
                {
                    "id": "failed_del",
                    "name": "delete_file",
                    "arguments": json.dumps({"id": deleted_child.id}),
                    "status": "error",
                    "result": {"error": "not found", "error_type": "not_found"},
                },
                {
                    "id": "noop_del",
                    "name": "delete_file",
                    "arguments": {"id": deleted_child.id},
                    "status": "success",
                    "result": {"mutation_applied": False, "data": False},
                },
                {
                    "id": "legacy_del",
                    "name": "delete_file",
                    "arguments": json.dumps({"id": deleted_child.id}),
                    "status": "success",
                    "result": True,
                },
            ]
        ),
    )
    db_session.add(msg)
    db_session.commit()

    for bad_tool_id, bad_file_id in (("failed_del", deleted_child.id), ("noop_del", deleted_child.id), ("legacy_del", "wrong-id")):
        assert (
            record_delete_card_restored(
                db_session,
                user_id=user.id,
                project_id=project.id,
                file_id=bad_file_id,
                message_id=msg.id,
                tool_use_id=bad_tool_id,
            )
            is False
        )
    assert (
        record_delete_card_restored(
            db_session,
            user_id=user.id,
            project_id=project.id,
            file_id=deleted_child.id,
            message_id=msg.id,
            tool_use_id="legacy_del",
        )
        is True
    )
    upgraded = json.loads(db_session.get(ChatMessage, msg.id).tool_calls)
    assert "restored" not in upgraded[0]["result"]
    assert "restored" not in upgraded[1]["result"]
    assert upgraded[2]["result"] == {"id": deleted_child.id, "restored": True, "mutation_applied": True}

    from services.chat_tool_cards import read_delete_card_deleted_at

    assert read_delete_card_deleted_at(db_session, user_id=user.id, project_id=project.id, file_id=deleted_child.id, message_id="missing", tool_use_id="x") is None
    assert read_delete_card_deleted_at(db_session, user_id=user.id, project_id=project.id, file_id=deleted_child.id, message_id=msg.id, tool_use_id="failed_del") is None
    assert read_delete_card_deleted_at(db_session, user_id=user.id, project_id=project.id, file_id=deleted_child.id, message_id=msg.id, tool_use_id="noop_del") is None

    # Already-live file with a legacy boolean card still persists restored: True via restore_file,
    # while a card with a mismatched deleted_at is left untouched.
    live_file = _ai_chapter(db_session, project, title="第2章 已存活")
    legacy_live_msg = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="旧卡",
        tool_calls=json.dumps(
            [
                {
                    "id": "legacy_live_del",
                    "name": "delete_file",
                    "arguments": json.dumps({"id": live_file.id}),
                    "status": "success",
                    "result": True,
                },
                {
                    "id": "mismatch_stamp_del",
                    "name": "delete_file",
                    "arguments": json.dumps({"id": live_file.id}),
                    "status": "success",
                    "result": {"id": live_file.id, "deleted_at": "2026-11-19T00:00:00+00:00", "restored": False},
                },
            ]
        ),
    )
    db_session.add(legacy_live_msg)
    db_session.commit()
    assert (
        record_delete_card_restored(
            db_session,
            user_id=user.id,
            project_id=project.id,
            file_id=live_file.id,
            message_id=legacy_live_msg.id,
            tool_use_id="mismatch_stamp_del",
            restored_deleted_at=datetime(2026, 11, 19, 1, 0, 0, tzinfo=UTC),
        )
        is False
    )
    already = files_api.restore_file(
        live_file.id,
        request=files_api.RestoreFileRequest(chat_message_id=legacy_live_msg.id, tool_use_id="legacy_live_del"),
        current_user=user,
        session=db_session,
    )
    assert already["already_restored"] is True
    assert json.loads(db_session.get(ChatMessage, legacy_live_msg.id).tool_calls)[0]["result"]["restored"] is True


def test_restored_delete_card_keeps_written_file_in_session_author_edits_and_recovers_repaired_root_children(
    db_session, owner_project, monkeypatch
):
    from agent.core.session_loader import SessionLoader
    from agent.tools.file_ops import crud as crud_module

    user, project = owner_project
    root_folder = File(id=f"{project.id}-draft-folder", project_id=project.id, title="正文", file_type="folder")
    db_session.add(root_folder)
    db_session.flush()
    chapter = File(
        project_id=project.id,
        parent_id=root_folder.id,
        title="第1章 钟楼",
        file_type="draft",
        content=AI_CH3,
    )
    db_session.add(chapter)
    db_session.commit()

    del_stamp = datetime(2026, 11, 19, 6, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(crud_module, "utcnow", lambda: del_stamp)
    assert crud_module.FileCRUD(db_session, user.id).delete_file(root_folder.id, recursive=True) is True

    # Simulate canonical root folder auto-repair before user clicks Restore on the chat card.
    repaired_root = db_session.get(File, root_folder.id)
    repaired_root.is_deleted = False
    repaired_root.deleted_at = None
    db_session.add(repaired_root)
    db_session.commit()

    chat = ChatSession(user_id=user.id, project_id=project.id, title="根修复恢复与手改追踪")
    db_session.add(chat)
    db_session.flush()
    msg1 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已写第一章",
        tool_calls=json.dumps([{"id": "w1", "name": "write_chapter", "status": "success", "result": {"id": chapter.id, "action": "created", "body_saved": True}}]),
    )
    msg2 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已删目录",
        tool_calls=json.dumps(
            [
                {
                    "id": "d1",
                    "name": "delete_file",
                    "arguments": json.dumps({"id": root_folder.id, "recursive": True}),
                    "status": "success",
                    "result": {
                        "id": root_folder.id,
                        "title": "正文",
                        "file_type": "folder",
                        "deleted_at": del_stamp.isoformat(),
                        "restored": False,
                        "mutation_applied": True,
                    },
                }
            ]
        ),
    )
    db_session.add_all([msg1, msg2])
    db_session.commit()

    restored = files_api.restore_file(
        root_folder.id,
        request=files_api.RestoreFileRequest(chat_message_id=msg2.id, tool_use_id="d1"),
        current_user=user,
        session=db_session,
    )
    assert restored["restored_count"] == 1
    assert restored["file_ids"] == [chapter.id]
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is False

    # Now delete chapter directly, verify SessionLoader excludes it while deleted, then restore and verify it returns.
    direct_del_stamp = datetime(2026, 11, 19, 6, 5, 0, tzinfo=UTC)
    monkeypatch.setattr(crud_module, "utcnow", lambda: direct_del_stamp)
    assert crud_module.FileCRUD(db_session, user.id).delete_file(chapter.id, recursive=False) is True
    msg3 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已删章节",
        tool_calls=json.dumps(
            [
                {
                    "id": "d2",
                    "name": "delete_file",
                    "arguments": json.dumps({"id": chapter.id}),
                    "status": "success",
                    "result": {
                        "id": chapter.id,
                        "title": chapter.title,
                        "file_type": "draft",
                        "deleted_at": direct_del_stamp.isoformat(),
                        "restored": False,
                        "mutation_applied": True,
                    },
                }
            ]
        ),
    )
    db_session.add(msg3)
    db_session.commit()
    loader = SessionLoader(project.id, user.id)
    assert loader.session_written_file_ids(db_session, chat_session_id=chat.id, limit=10) == []

    files_api.restore_file(
        chapter.id,
        request=files_api.RestoreFileRequest(chat_message_id=msg3.id, tool_use_id="d2"),
        current_user=user,
        session=db_session,
    )
    # Unapplied/false delete cards must also never hide a written file in SessionLoader.
    msg4 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="删除未生效",
        tool_calls=json.dumps(
            [
                {"id": "d3", "name": "delete_file", "arguments": json.dumps({"id": chapter.id}), "status": "success", "result": False},
                {"id": "d4", "name": "delete_file", "arguments": json.dumps({"id": chapter.id}), "status": "success", "result": {"id": chapter.id, "mutation_applied": False, "data": False}},
            ]
        ),
    )
    db_session.add(msg4)
    db_session.commit()
    assert loader.session_written_file_ids(db_session, chat_session_id=chat.id, limit=10) == [chapter.id]


async def test_delete_file_refuses_pending_chapter_replacement_and_billing_clears_deleted_targets(db_session, owner_project):
    from agent.core.events import tool_call_event, tool_result_event
    from agent.core.stream_billing import StreamBillingTracker

    user, project = owner_project
    chapter = _ai_chapter(db_session, project, title="第1章 钟楼")

    engine = db_session.get_bind()
    req_ctx = mcp_tools.AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id=None,
        create_session_func=lambda: Session(engine),
        author_message="重写第一章",
        author_steering=[],
    )
    try:
        armed = json.loads(
            (await mcp_tools.write_chapter({"chapter": 1, "title": "第1章 钟楼", "file_type": "draft"}, request_context=req_ctx))["content"][0]["text"]
        )
        assert armed["status"] == "success" and armed["data"]["action"] == "replaced"

        refused = json.loads((await mcp_tools.delete_file({"id": chapter.id}, request_context=req_ctx))["content"][0]["text"])
        assert refused["status"] == "error"
        assert refused["error_type"] == "chapter_replace_pending"
        assert refused["mutation_applied"] is False
    finally:
        req_ctx.clear_pending_empty_file()
        req_ctx.clear()

    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is False

    tracker = StreamBillingTracker()
    tracker.observe(tool_result_event("write_chapter", "success", {"id": "ch-1", "action": "replaced", "original_content_length": 100}).to_sse())
    assert tracker.file_body_pending() is True
    tracker.observe(tool_call_event("delete_file", {"id": "ch-1"}, tool_use_id="del-ch-1").to_sse())
    tracker.observe(tool_result_event("delete_file", "success", {"id": "ch-1", "mutation_applied": True}, tool_use_id="del-ch-1").to_sse())
    assert tracker.file_body_pending() is False


def test_replace_body_undo_preserves_author_manual_rename_and_skips_sibling_title_collision(db_session, owner_project):
    from agent.tools.file_ops import FileToolExecutor
    from api import versions as versions_api

    user, project = owner_project
    chapter = _ai_chapter(db_session, project, title="剑来 - 第1章 惊蛰", content="第一章原稿正文。")

    replaced = FileToolExecutor(session=db_session, user_id=user.id).replace_body(
        chapter.id,
        "第一章重写后正文。",
        expected_updated_at=None,
        title="剑来 - 第1章 风起",
    )
    undo = replaced["undo"]
    assert undo["before_title"] == "剑来 - 第1章 惊蛰"

    chat = ChatSession(user_id=user.id, project_id=project.id, title="整章改名撤销保护")
    db_session.add(chat)
    db_session.flush()
    msg = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已重写第一章",
        tool_calls=json.dumps(
            [
                {
                    "id": "wc-1",
                    "name": "write_chapter",
                    "arguments": {"chapter": 1, "title": "第1章 风起", "file_type": "draft"},
                    "status": "success",
                    "result": {
                        "id": chapter.id,
                        "action": "replaced",
                        "body_saved": True,
                        "mutation_applied": True,
                        "title": replaced["title"],
                        "undo": undo,
                    },
                }
            ],
            ensure_ascii=False,
        ),
    )
    db_session.add(msg)
    db_session.commit()

    # Author manually renames chapter after AI rewrite without changing content:
    # conditional undo via SHA-256 restores body but MUST NOT clobber the author's manual title!
    files_api.update_file(
        chapter.id,
        files_api.FileUpdate(title="剑来 - 第1章 作者定稿名"),
        current_user=user,
        session=db_session,
    )
    resp = versions_api.rollback_to_version(
        chapter.id,
        undo["before_version_number"],
        current_user=user,
        session=db_session,
        rollback_request=versions_api.RollbackRequest(
            expected_updated_at=datetime.fromisoformat(undo["expected_after_updated_at"]),
            expected_after_content_sha256=undo["expected_after_content_sha256"],
            chat_message_id=msg.id,
            tool_use_id="wc-1",
        ),
    )
    assert resp.success is True
    db_session.expire_all()
    after_author_rename_undo = db_session.get(File, chapter.id)
    assert after_author_rename_undo.content == "第一章原稿正文。"
    assert after_author_rename_undo.title == "剑来 - 第1章 作者定稿名"

    # Second scenario: sibling collision on before_title does not block content rollback.
    chapter2 = _ai_chapter(db_session, project, title="剑来 - 第2章 听潮", content="第二章原稿正文。")
    replaced2 = FileToolExecutor(session=db_session, user_id=user.id).replace_body(
        chapter2.id,
        "第二章重写后正文。",
        expected_updated_at=None,
        title="剑来 - 第2章 破浪",
    )
    undo2 = replaced2["undo"]
    sibling = File(project_id=project.id, title="剑来 - 第2章 听潮", content="同名兄弟稿", file_type="draft")
    msg2 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已重写第二章",
        tool_calls=json.dumps(
            [
                {
                    "id": "wc-2",
                    "name": "write_chapter",
                    "arguments": {"chapter": 2, "title": "第2章 破浪", "file_type": "draft"},
                    "status": "success",
                    "result": {
                        "id": chapter2.id,
                        "action": "replaced",
                        "body_saved": True,
                        "mutation_applied": True,
                        "title": replaced2["title"],
                        "undo": undo2,
                    },
                }
            ],
            ensure_ascii=False,
        ),
    )
    db_session.add_all([sibling, msg2])
    db_session.commit()

    resp2 = versions_api.rollback_to_version(
        chapter2.id,
        undo2["before_version_number"],
        current_user=user,
        session=db_session,
        rollback_request=versions_api.RollbackRequest(
            expected_updated_at=datetime.fromisoformat(undo2["expected_after_updated_at"]),
            expected_after_content_sha256=undo2["expected_after_content_sha256"],
            chat_message_id=msg2.id,
            tool_use_id="wc-2",
        ),
    )
    assert resp2.success is True
    db_session.expire_all()
    after_clash_undo = db_session.get(File, chapter2.id)
    assert after_clash_undo.content == "第二章原稿正文。"
    assert after_clash_undo.title == "剑来 - 第2章 破浪"

    # Third scenario: spoofed cross-project or failed card with before_title cannot rename file on rollback.
    chapter3 = _ai_chapter(db_session, project, title="剑来 - 第3章 原名", content="第三章原稿正文。")
    edited3 = FileToolExecutor(session=db_session, user_id=user.id).edit_file(
        chapter3.id,
        [{"op": "replace", "old": "原稿", "new": "精修"}],
    )
    undo3 = edited3["undo"]
    other_project = Project(name="无关作品", owner_id=user.id)
    db_session.add(other_project)
    db_session.flush()
    other_chat = ChatSession(user_id=user.id, project_id=other_project.id, title="无关会话")
    db_session.add(other_chat)
    db_session.flush()
    spoof_msg = ChatMessage(
        session_id=other_chat.id,
        role="assistant",
        content="伪造卡片",
        tool_calls=json.dumps(
            [
                {
                    "id": "spoof-1",
                    "name": "write_chapter",
                    "status": "success",
                    "result": {
                        "id": chapter3.id,
                        "title": "剑来 - 第3章 原名",
                        "undo": {**undo3, "before_title": "被篡改的标题"},
                    },
                }
            ],
            ensure_ascii=False,
        ),
    )
    failed_msg = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="失败卡片",
        tool_calls=json.dumps(
            [
                {
                    "id": "failed-1",
                    "name": "write_chapter",
                    "status": "error",
                    "result": {
                        "id": chapter3.id,
                        "title": "剑来 - 第3章 原名",
                        "undo": {**undo3, "before_title": "失败卡标题"},
                    },
                }
            ],
            ensure_ascii=False,
        ),
    )
    db_session.add_all([spoof_msg, failed_msg])
    db_session.commit()

    from services.chat_tool_cards import read_edit_card_before_title

    assert read_edit_card_before_title(
        db_session,
        user_id=user.id,
        project_id=project.id,
        file_id=chapter3.id,
        message_id=spoof_msg.id,
        tool_use_id="spoof-1",
        before_version_number=undo3["before_version_number"],
        expected_after_updated_at=datetime.fromisoformat(undo3["expected_after_updated_at"]),
    ) == (None, None)
    assert read_edit_card_before_title(
        db_session,
        user_id=user.id,
        project_id=project.id,
        file_id=chapter3.id,
        message_id=failed_msg.id,
        tool_use_id="failed-1",
        before_version_number=undo3["before_version_number"],
        expected_after_updated_at=datetime.fromisoformat(undo3["expected_after_updated_at"]),
    ) == (None, None)

    resp3 = versions_api.rollback_to_version(
        chapter3.id,
        undo3["before_version_number"],
        current_user=user,
        session=db_session,
        rollback_request=versions_api.RollbackRequest(
            expected_updated_at=datetime.fromisoformat(undo3["expected_after_updated_at"]),
            expected_after_content_sha256=undo3["expected_after_content_sha256"],
            chat_message_id=spoof_msg.id,
            tool_use_id="spoof-1",
        ),
    )
    assert resp3.success is True and resp3.undo_recorded is False
    db_session.expire_all()
    assert db_session.get(File, chapter3.id).title == "剑来 - 第3章 原名"
