"""Chat edits and deletion use recoverable operations, independent of authorship."""

import json

import pytest
from sqlmodel import Session, select

from agent.tools import mcp_tools
from api import files as files_api
from models import File, Project, User
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

    monkeypatch.setattr(FileVersionService, "create_version", fail_backup)

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
async def test_author_saved_or_unversioned_file_moves_to_one_recoverable_batch(db_session, owner_project, has_history):
    user, project = owner_project
    if has_history:
        chapter = _ai_chapter(db_session, project)
        _author_saves(db_session, user, chapter, AUTHOR_CH3)
    else:
        chapter = File(project_id=project.id, title="作者原稿", content=AUTHOR_CH3)
        db_session.add(chapter)
        db_session.commit()
    result = await _run(mcp_tools.delete_file, db_session, user, project, {"id": chapter.id}, message="删掉这份废稿")
    assert result["status"] == "success"
    data = result["data"]
    assert data["batch_id"] and data["mutation_applied"] is True
    assert {item["id"] for item in data["targets"]} == {chapter.id}
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is True
    assert db_session.get(File, chapter.id).content == AUTHOR_CH3


async def test_two_named_files_are_one_atomic_batch(db_session, owner_project):
    user, project = owner_project
    first = _ai_chapter(db_session, project, title="第1章 废稿")
    second = _ai_chapter(db_session, project, title="第2章 废稿")
    result = await _run(
        mcp_tools.delete_file, db_session, user, project,
        {"ids": [first.id, second.id, first.id]}, message="删掉这两章废稿",
    )
    assert result["status"] == "success"
    assert result["data"]["count"] == 2
    assert {item["id"] for item in result["data"]["targets"]} == {first.id, second.id}
    from models.file_deletion_batch import FileDeletionBatch

    assert len(db_session.exec(select(FileDeletionBatch).where(FileDeletionBatch.project_id == project.id)).all()) == 1


async def test_split_calls_share_the_five_leaf_limit(db_session, owner_project):
    user, project = owner_project
    chapters = [_ai_chapter(db_session, project, title=f"第{i}章 废稿") for i in range(1, 7)]
    mcp_tools.ToolContext.set_context(db_session, user.id, project.id, None, author_message="删除这些废稿")
    try:
        first = json.loads((await mcp_tools.delete_file({"ids": [row.id for row in chapters[:3]]}))["content"][0]["text"])
        second = json.loads((await mcp_tools.delete_file({"ids": [row.id for row in chapters[3:]]}))["content"][0]["text"])
    finally:
        mcp_tools.ToolContext.clear_context()
    assert first["data"]["mutation_applied"] is True
    assert second["data"]["confirmation_required"] is True
    assert second["data"]["mutation_applied"] is False
    db_session.expire_all()
    assert all(db_session.get(File, row.id).is_deleted for row in chapters[:3])
    assert not any(db_session.get(File, row.id).is_deleted for row in chapters[3:])


@pytest.mark.parametrize("first_scope", ["folder", "six_leaves"])
async def test_unapplied_preview_does_not_block_author_narrowed_single_leaf_delete(
    db_session, owner_project, first_scope
):
    user, project = owner_project
    folder = File(project_id=project.id, title="废弃卷", file_type="folder")
    db_session.add(folder)
    db_session.flush()
    leaves = [File(project_id=project.id, parent_id=folder.id, title=f"废稿{i}", content=AUTHOR_CH3) for i in range(6)]
    db_session.add_all(leaves)
    db_session.commit()
    first_args = {"id": folder.id} if first_scope == "folder" else {"ids": [row.id for row in leaves]}
    mcp_tools.ToolContext.set_context(db_session, user.id, project.id, None, author_message="删除这批废稿")
    try:
        first = json.loads((await mcp_tools.delete_file(first_args))["content"][0]["text"])
        narrowed = json.loads((await mcp_tools.delete_file({"id": leaves[0].id}))["content"][0]["text"])
    finally:
        mcp_tools.ToolContext.clear_context()
    assert first["data"]["confirmation_required"] is True
    assert first["data"]["mutation_applied"] is False
    assert narrowed["data"]["mutation_applied"] is True
    db_session.expire_all()
    assert db_session.get(File, leaves[0].id).is_deleted is True
    assert all(not db_session.get(File, row.id).is_deleted for row in [folder, *leaves[1:]])


async def test_confirming_delete_card_persists_receipt_for_next_loop_turn(db_session, owner_project):
    from agent.loop.history import load_loop_history
    from models import ChatMessage, ChatSession
    from services.chat_tool_cards import record_delete_card_confirmed
    from services.file_trash import FileTrashService

    user, project = owner_project
    folder = File(project_id=project.id, title="废弃卷", file_type="folder")
    db_session.add(folder)
    db_session.flush()
    leaf = File(project_id=project.id, parent_id=folder.id, title="废稿一", content=AUTHOR_CH3)
    chat_session = ChatSession(id="loop-del-session", project_id=project.id, user_id=user.id, title="Chat")
    db_session.add_all([leaf, chat_session])
    db_session.commit()

    preview_result = await _run(mcp_tools.delete_file, db_session, user, project, {"id": folder.id}, message="删掉废弃卷")
    preview = preview_result["data"]["preview"]
    db_session.add_all([
        ChatMessage(id="u-1", session_id=chat_session.id, role="user", content="删掉废弃卷"),
        ChatMessage(
            id="a-1",
            session_id=chat_session.id,
            role="assistant",
            content="请确认删除范围。",
            tool_calls=json.dumps([{
                "id": "call-del-1",
                "name": "delete_file",
                "status": "success",
                "arguments": {"id": folder.id},
                "result": preview_result["data"],
            }], ensure_ascii=False),
            message_metadata=json.dumps({
                "engine": "loop",
                "display_events": [
                    {"type": "tool_call", "tool_call_index": 0},
                    {"type": "content", "content": "请确认删除范围。"},
                ],
            }, ensure_ascii=False),
        ),
    ])
    db_session.commit()

    deletion = FileTrashService(db_session, user_id=user.id, project_id=project.id).delete(
        preview["root_ids"],
        recursive=preview["recursive"],
        source="agent_confirmed",
        expected_target_hash=preview["target_hash"],
    )
    assert record_delete_card_confirmed(
        db_session,
        user_id=user.id,
        project_id=project.id,
        message_id="a-1",
        tool_use_id="call-del-1",
        expected_target_hash=preview["target_hash"],
        deleted_result=deletion,
    ) is True

    history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat_session.id,
    )
    outputs = [item for item in history.items if item.get("type") == "function_call_output"]
    assert len(outputs) == 1
    replayed = json.loads(outputs[0]["output"])["data"]
    assert replayed["confirmation_required"] is False
    assert replayed["mutation_applied"] is True
    assert replayed["batch_id"] == deletion["batch_id"]


async def test_folder_returns_one_exact_confirmation_without_deleting(db_session, owner_project):
    user, project = owner_project
    folder = File(project_id=project.id, title="废弃卷", file_type="folder")
    db_session.add(folder)
    db_session.flush()
    live = File(project_id=project.id, parent_id=folder.id, title="作者章节", content=AUTHOR_CH3)
    old_deleted = File(project_id=project.id, parent_id=folder.id, title="早就删除的稿", is_deleted=True)
    db_session.add_all([live, old_deleted])
    db_session.commit()
    result = await _run(mcp_tools.delete_file, db_session, user, project, {"id": folder.id, "recursive": "false"}, message="这一卷不要了")
    data = result["data"]
    assert data["confirmation_required"] is True and data["mutation_applied"] is False
    assert data["preview"]["recursive"] is True
    assert {item["id"] for item in data["preview"]["targets"]} == {folder.id, live.id}
    db_session.expire_all()
    assert db_session.get(File, folder.id).is_deleted is False
    assert db_session.get(File, live.id).is_deleted is False


@pytest.mark.parametrize("args", [
    {}, {"id": "x", "ids": ["x"]}, {"ids": []}, {"ids": "x"}, {"ids": [None]},
    {"ids": [f"file-{i}" for i in range(101)]},
])
async def test_delete_validates_one_structured_target_list(db_session, owner_project, args):
    user, project = owner_project
    result = await _run(mcp_tools.delete_file, db_session, user, project, args, message="删除废稿")
    assert result["status"] == "error"


async def test_mixed_project_batch_does_not_delete_the_valid_member(db_session, owner_project):
    user, project = owner_project
    own = _ai_chapter(db_session, project)
    other_project = Project(name="同作者另一本书", owner_id=user.id)
    db_session.add(other_project)
    db_session.commit()
    other = _ai_chapter(db_session, other_project)
    result = await _run(
        mcp_tools.delete_file, db_session, user, project,
        {"ids": [own.id, other.id]}, message="删掉这两份废稿",
    )
    assert result["status"] == "error"
    db_session.expire_all()
    assert not db_session.get(File, own.id).is_deleted
    assert not db_session.get(File, other.id).is_deleted
