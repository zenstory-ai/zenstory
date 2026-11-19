"""Recoverable AI edit operation service regressions."""

from datetime import datetime

import pytest
from sqlmodel import select

from agent.tools.file_ops.edit import FileEditor
from api import file_edit_operations as operations_api
from main import app
from models import File, Project, User
from models.file_edit_operation import FileEditOperation
from models.file_version import CHANGE_SOURCE_USER, CHANGE_TYPE_EDIT, FileVersion
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService
from services.file_edit_operations import EditOperationConflictError, undo_edit_operation

pytestmark = pytest.mark.unit


@pytest.fixture
def operation_file(db_session, monkeypatch):
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_k: None)
    user = User(
        username="operation-owner",
        email="operation-owner@example.test",
        hashed_password="unused",
        email_verified=True,
    )
    project = Project(name="可恢复改稿", owner_id=user.id)
    file = File(
        project_id=project.id,
        title="第1章 原题",
        content="第一段原稿。\n第二段原稿。",
        file_type="draft",
        order=1,
        updated_at=datetime(2026, 1, 1),
    )
    db_session.add_all([user, project, file])
    db_session.flush()
    FileVersionService().create_version(
        db_session,
        file.id,
        file.content,
        change_type=CHANGE_TYPE_EDIT,
        change_source=CHANGE_SOURCE_USER,
        skip_quota=True,
        commit=False,
    )
    db_session.commit()
    return user, project, file


def _operation(session, operation_id: str) -> FileEditOperation:
    return session.exec(select(FileEditOperation).where(FileEditOperation.id == operation_id)).one()


def test_body_and_title_are_one_durable_operation_and_undo_atomically(db_session, operation_file):
    user, _project, file = operation_file
    result = FileEditor(db_session, user.id).replace_body(
        file.id,
        "第一段新稿。\n第二段新稿。",
        expected_updated_at=None,
        title="第1章 新题",
    )
    undo = result["undo"]
    operation = _operation(db_session, undo["operation_id"])
    assert (operation.before_title, operation.after_title) == ("第1章 原题", "第1章 新题")
    assert session_version_content(db_session, operation.before_version_id) == "第一段原稿。\n第二段原稿。"
    assert session_version_content(db_session, operation.after_version_id) == "第一段新稿。\n第二段新稿。"

    undone = undo_edit_operation(
        db_session,
        operation_id=operation.id,
        file_id=file.id,
        user_id=user.id,
    )
    db_session.refresh(file)
    assert undone["status"] == "undone"
    assert (file.content, file.title, file.order) == ("第一段原稿。\n第二段原稿。", "第1章 原题", 1)
    db_session.refresh(operation)
    assert operation.undone_at is not None and operation.undo_version_id is not None

    version_count = len(db_session.exec(select(FileVersion).where(FileVersion.file_id == file.id)).all())
    again = undo_edit_operation(
        db_session, operation_id=operation.id, file_id=file.id, user_id=user.id
    )
    assert again["already_undone"] is True
    assert len(db_session.exec(select(FileVersion).where(FileVersion.file_id == file.id)).all()) == version_count


def test_later_author_text_returns_snapshots_then_cas_resolution_preserves_it(db_session, operation_file):
    user, _project, file = operation_file
    result = FileEditor(db_session, user.id).edit_file(
        file.id, [{"op": "replace", "old": "第一段原稿。", "new": "第一段 AI 新稿。"}]
    )
    operation_id = result["undo"]["operation_id"]
    file.content += "\n作者后来追加三百字。"
    file.updated_at = datetime(2026, 1, 2)
    db_session.add(file)
    db_session.commit()

    preview = undo_edit_operation(
        db_session, operation_id=operation_id, file_id=file.id, user_id=user.id
    )
    assert preview["status"] == "needs_merge" and preview["content_conflict"] is True
    assert preview["before"]["content"] == "第一段原稿。\n第二段原稿。"
    assert preview["after"]["content"] == "第一段 AI 新稿。\n第二段原稿。"
    assert preview["current"]["content"].endswith("作者后来追加三百字。")

    resolved = "第一段原稿。\n第二段原稿。\n作者后来追加三百字。"
    done = undo_edit_operation(
        db_session,
        operation_id=operation_id,
        file_id=file.id,
        user_id=user.id,
        expected_updated_at=datetime.fromisoformat(preview["current"]["updated_at"]),
        expected_after_content_sha256=preview["current"]["content_sha256"],
        resolved_content=resolved,
        metadata_resolution="preserve",
    )
    db_session.refresh(file)
    assert done["status"] == "undone" and file.content == resolved


def test_stale_merge_resolution_and_later_title_are_never_overwritten(db_session, operation_file):
    user, _project, file = operation_file
    result = FileEditor(db_session, user.id).replace_body(
        file.id, "AI 正文。", expected_updated_at=None, title="第1章 AI 标题"
    )
    operation_id = result["undo"]["operation_id"]
    file.content = "AI 正文。\n作者追加。"
    file.title = "第1章 作者新标题"
    file.updated_at = datetime(2026, 1, 2)
    db_session.add(file)
    db_session.commit()

    preview = undo_edit_operation(
        db_session, operation_id=operation_id, file_id=file.id, user_id=user.id
    )
    assert preview["status"] == "needs_merge"
    assert preview["metadata_conflict"] is True
    stale_time = datetime.fromisoformat(preview["current"]["updated_at"])
    file.content += "\n更新于冲突窗口。"
    file.updated_at = datetime(2026, 1, 3)
    db_session.add(file)
    db_session.commit()
    with pytest.raises(EditOperationConflictError, match="stale_write"):
        undo_edit_operation(
            db_session,
            operation_id=operation_id,
            file_id=file.id,
            user_id=user.id,
            expected_updated_at=stale_time,
            expected_after_content_sha256=preview["current"]["content_sha256"],
            resolved_content="第一段原稿。\n第二段原稿。\n作者追加。",
            metadata_resolution="preserve",
        )
    db_session.refresh(file)
    assert file.title == "第1章 作者新标题"
    assert file.content.endswith("更新于冲突窗口。")

    current = undo_edit_operation(
        db_session, operation_id=operation_id, file_id=file.id, user_id=user.id
    )
    done = undo_edit_operation(
        db_session,
        operation_id=operation_id,
        file_id=file.id,
        user_id=user.id,
        expected_updated_at=datetime.fromisoformat(current["current"]["updated_at"]),
        expected_after_content_sha256=current["current"]["content_sha256"],
        resolved_content="",
        metadata_resolution="preserve",
    )
    db_session.refresh(file)
    assert done["status"] == "undone"
    assert file.content == ""
    assert file.title == "第1章 作者新标题"


def test_rename_only_operation_keeps_body_and_can_be_undone(db_session, operation_file):
    user, _project, file = operation_file
    body = file.content
    initial_versions = db_session.exec(select(FileVersion).where(FileVersion.file_id == file.id)).all()
    assert len(initial_versions) == 1
    renamed = FileEditor(db_session, user.id).rename_with_undo(file.id, "第1章 新标题")
    operation = _operation(db_session, renamed["undo"]["operation_id"])
    assert operation.before_version_id == operation.after_version_id == initial_versions[0].id
    assert len(db_session.exec(select(FileVersion).where(FileVersion.file_id == file.id)).all()) == 1
    db_session.refresh(file)
    assert file.content == body and file.title == "第1章 新标题"
    undo_edit_operation(
        db_session,
        operation_id=renamed["undo"]["operation_id"],
        file_id=file.id,
        user_id=user.id,
    )
    db_session.refresh(file)
    assert file.content == body and file.title == "第1章 原题"
    assert len(db_session.exec(select(FileVersion).where(FileVersion.file_id == file.id)).all()) == 1


def test_undo_rename_preserves_later_author_order_without_a_conflict(db_session, operation_file):
    user, _project, file = operation_file
    result = FileEditor(db_session, user.id).rename_with_undo(file.id, "第1章 新标题")
    file.order = 8
    file.updated_at = datetime(2026, 1, 2)
    db_session.add(file)
    db_session.commit()
    done = undo_edit_operation(
        db_session, operation_id=result["undo"]["operation_id"], file_id=file.id, user_id=user.id
    )
    db_session.refresh(file)
    assert done["status"] == "undone"
    assert (file.title, file.order) == ("第1章 原题", 8)


@pytest.mark.parametrize("metadata_resolution", ["preserve", "restore"])
@pytest.mark.parametrize("body_state", ["rename_only", "already_restored", "unchanged_after"])
def test_title_conflict_resolution_cannot_replace_body_outside_a_content_conflict(
    db_session, operation_file, metadata_resolution, body_state
):
    user, _project, file = operation_file
    before_body = file.content
    if body_state == "rename_only":
        changed = FileEditor(db_session, user.id).rename_with_undo(file.id, "第1章 AI 标题")
        current_body = before_body + "\n作者后来追加。"
    else:
        changed = FileEditor(db_session, user.id).replace_body(
            file.id, "AI 改写正文。", expected_updated_at=None, title="第1章 AI 标题"
        )
        current_body = before_body if body_state == "already_restored" else "AI 改写正文。"
    file.content = current_body
    file.title = "第1章 作者后改标题"
    file.updated_at = datetime(2026, 1, 2)
    db_session.add(file)
    db_session.commit()
    operation_id = changed["undo"]["operation_id"]
    preview = undo_edit_operation(
        db_session, operation_id=operation_id, file_id=file.id, user_id=user.id
    )
    assert preview["status"] == "needs_merge"
    assert preview["content_conflict"] is False and preview["metadata_conflict"] is True
    result = undo_edit_operation(
        db_session, operation_id=operation_id, file_id=file.id, user_id=user.id,
        expected_updated_at=datetime.fromisoformat(preview["current"]["updated_at"]),
        expected_after_content_sha256=preview["current"]["content_sha256"],
        resolved_content="与本次撤销无关的旧正文。",
        metadata_resolution=metadata_resolution,
    )
    db_session.refresh(file)
    assert result["status"] == "undone"
    assert file.content == (current_body if body_state == "rename_only" else before_body)
    assert file.title == ("第1章 原题" if metadata_resolution == "restore" else "第1章 作者后改标题")


def test_manually_cleared_body_gets_an_exact_blank_before_anchor(db_session, operation_file):
    user, _project, file = operation_file
    file.content = ""
    db_session.add(file)
    db_session.commit()
    edited = FileEditor(db_session, user.id).edit_file(
        file.id, [{"op": "append", "text": "AI 从空稿开始。"}]
    )
    operation = _operation(db_session, edited["undo"]["operation_id"])
    assert session_version_content(db_session, operation.before_version_id) == ""
    assert session_version_content(db_session, operation.after_version_id) == "AI 从空稿开始。"


def test_referenced_user_version_is_not_coalesced(db_session, operation_file):
    user, _project, file = operation_file
    result = FileEditor(db_session, user.id).edit_file(file.id, [{"op": "append", "text": "\nAI。"}])
    operation = _operation(db_session, result["undo"]["operation_id"])
    before = db_session.get(FileVersion, operation.before_version_id)
    assert before is not None
    # Exercise the explicit invariant directly; an operation anchor is immutable
    # even if it otherwise looks like a recent coalescible user save.
    file.content = session_version_content(db_session, before.id)
    assert FileVersionService().find_coalescible_user_version(
        db_session, file, pre_save_content=file.content, now=before.created_at
    ) is None


async def test_undo_api_matches_the_frontend_contract(
    client, db_session, operation_file, monkeypatch
):
    user, _project, file = operation_file
    monkeypatch.setitem(
        app.dependency_overrides,
        operations_api.get_current_active_user,
        lambda: user,
    )
    result = FileEditor(db_session, user.id).edit_file(
        file.id, [{"op": "append", "text": "\nAI 追加。"}]
    )
    operation_id = result["undo"]["operation_id"]

    removed_list = await client.get(f"/api/v1/files/{file.id}/edit-operations")
    assert removed_list.status_code == 404

    undone = await client.post(
        f"/api/v1/files/{file.id}/edit-operations/{operation_id}/undo"
    )
    assert undone.status_code == 200
    assert undone.json()["status"] == "undone"
    assert undone.json()["operation_id"] == operation_id


def session_version_content(session, version_id: str) -> str:
    version = session.get(FileVersion, version_id)
    assert version is not None
    return FileVersionService().get_content_at_version(session, version.file_id, version.version_number)
