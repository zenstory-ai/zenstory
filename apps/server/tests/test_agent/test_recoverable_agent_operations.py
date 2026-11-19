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
    mcp_tools.ToolContext.set_context(
        None,
        user.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
        author_message=message,
        author_steering=[],
        **context,
    )
    try:
        raw = await tool(args)
    finally:
        mcp_tools.ToolContext.clear_context()
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
    assert data == {
        "id": chapter.id,
        "title": chapter.title,
        "file_type": "draft",
        "restored": False,
        "mutation_applied": True,
    }
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
                    "input": {"id": chapter.id},
                    "result": data,
                    "status": "completed",
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
    assert restored == {
        "id": chapter.id,
        "title": chapter.title,
        "restored": True,
        "already_restored": False,
        "restored_count": 1,
    }

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

    # Legacy boolean delete_file card upgrades cleanly on matching restore, rejects mismatched file_id.
    chat = ChatSession(user_id=user.id, project_id=project.id, title="旧卡片测试")
    db_session.add(chat)
    db_session.flush()
    msg = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已删",
        tool_calls=json.dumps(
            [{"id": "legacy_del", "name": "delete_file", "input": {"id": deleted_child.id}, "result": True}]
        ),
    )
    db_session.add(msg)
    db_session.commit()

    assert (
        record_delete_card_restored(
            db_session,
            user_id=user.id,
            project_id=project.id,
            file_id="wrong-id",
            message_id=msg.id,
            tool_use_id="legacy_del",
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
    assert upgraded[0]["result"] == {"id": deleted_child.id, "restored": True, "mutation_applied": True}
