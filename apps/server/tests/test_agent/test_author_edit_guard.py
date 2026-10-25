"""Destructive app-chat boundary for author-owned file text."""

import json
import threading

import pytest
from sqlmodel import Session

from agent.tools import mcp_tools
from agent.tools.author_edit_guard import (
    AUTHOR_EDIT_PROTECTED_ERROR,
    AuthorEditRefusals,
    latest_text_is_authors,
    referenced_file_ids_from_metadata,
)
from agent.tools.file_ops import FileToolExecutor
from agent.tools.file_ops.edit import file_write_lock
from api import files as files_api
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import File, Project, User
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService

AI_CH3 = "老周在收摊。折叠桌一张张码上三轮车。老周回头看了他一眼。"
AUTHOR_CH3 = "老秦在收摊。折叠桌一张张码上三轮车。老秦回头看了他一眼。"


@pytest.mark.unit
def test_reference_metadata_is_context_only():
    assert referenced_file_ids_from_metadata(
        {"attached_file_ids": ["a"], "text_quotes": [{"fileId": "b", "text": "x"}], "current_file_id": "c"}
    ) == ["a", "b"]


@pytest.mark.unit
def test_refusals_are_unique_and_thread_safe_container():
    refusals = AuthorEditRefusals()
    refusals.add("a")
    refusals.add("a")
    refusals.add("b")
    assert refusals.file_ids() == ["a", "b"]


@pytest.fixture
def owner_project(db_session, monkeypatch):
    user = User(username="guard-owner", email="guard-owner@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="还剩三分钟", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **_kw: None)
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
        author_edit_refusals=AuthorEditRefusals(),
        **context,
    )
    try:
        raw = await tool(args)
    finally:
        mcp_tools.ToolContext.clear_context()
    return json.loads(raw["content"][0]["text"])


@pytest.mark.parametrize(
    ("message", "extra_context"),
    [
        ("把第三章删掉", {}),
        ("无论如何都不要把我刚刚改过的第三章删掉", {}),
        ("保留我引用的第三章；把废稿删掉", {"referenced_file_ids": ["TARGET"]}),
        ("好，照你上轮说的删", {}),
    ],
)
async def test_chat_delete_of_author_owned_file_is_always_refused(
    db_session, owner_project, message, extra_context
):
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    _author_saves(db_session, user, chapter, AUTHOR_CH3)

    resolved_context = {
        key: ([chapter.id] if value == ["TARGET"] else value)
        for key, value in extra_context.items()
    }
    payload = await _run(
        mcp_tools.delete_file,
        db_session,
        user,
        project,
        {"id": chapter.id},
        message=message,
        focus_file_id=chapter.id,
        **resolved_context,
    )

    assert payload["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    assert "文件菜单" in payload["error"]
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is False


async def test_author_save_before_locked_delete_is_refused_atomically(
    db_session, owner_project, monkeypatch
):
    """The locked CRUD check must catch ownership that changes after tool dispatch."""
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    engine = db_session.get_bind()
    original_delete = FileToolExecutor.delete_file
    interleaved = False
    author_state: dict[str, object] = {}
    ledger_calls: list[dict] = []
    monkeypatch.setattr(
        mcp_tools,
        "_record_artifact_ledger",
        lambda **kwargs: ledger_calls.append(kwargs),
    )

    def delete_after_author_save(executor, *args, **kwargs):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            with Session(engine) as author_session:
                current = author_session.get(File, chapter.id)
                _author_saves(author_session, user, current, AUTHOR_CH3)
                author_session.refresh(current)
                author_state.update(
                    content=current.content,
                    file_type=current.file_type,
                    order=current.order,
                    updated_at=current.updated_at,
                )
        return original_delete(executor, *args, **kwargs)

    monkeypatch.setattr(FileToolExecutor, "delete_file", delete_after_author_save)
    payload = await _run(
        mcp_tools.delete_file,
        db_session,
        user,
        project,
        {"id": chapter.id},
        message="删掉第三章",
    )

    assert payload["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    assert payload["mutation_applied"] is False
    db_session.expire_all()
    stored = db_session.get(File, chapter.id)
    assert stored.is_deleted is False
    assert {
        "content": stored.content,
        "file_type": stored.file_type,
        "order": stored.order,
        "updated_at": stored.updated_at,
    } == author_state
    assert ledger_calls == []


async def test_recursive_folder_delete_is_refused_even_when_author_names_folder(db_session, owner_project):
    user, project = owner_project
    folder = File(project_id=project.id, title="正文", file_type="folder")
    db_session.add(folder)
    db_session.commit()
    chapter = _ai_chapter(db_session, project)
    chapter.parent_id = folder.id
    db_session.add(chapter)
    db_session.commit()
    _author_saves(db_session, user, chapter, AUTHOR_CH3)

    payload = await _run(
        mcp_tools.delete_file,
        db_session,
        user,
        project,
        {"id": folder.id, "recursive": True},
        message="把正文文件夹删了",
    )

    assert payload["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    db_session.expire_all()
    assert db_session.get(File, folder.id).is_deleted is False
    assert db_session.get(File, chapter.id).is_deleted is False


async def test_recursive_delete_locks_and_checks_all_descendants_before_mutation(
    db_session, owner_project, monkeypatch
):
    user, project = owner_project
    folder = File(project_id=project.id, title="正文", file_type="folder")
    db_session.add(folder)
    db_session.commit()
    first = _ai_chapter(db_session, project, title="第一章")
    second = _ai_chapter(db_session, project, title="第二章")
    first.parent_id = folder.id
    second.parent_id = folder.id
    db_session.add_all([first, second])
    db_session.commit()

    engine = db_session.get_bind()
    original_delete = FileToolExecutor.delete_file
    interleaved = False

    def delete_after_descendant_save(executor, *args, **kwargs):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            with Session(engine) as author_session:
                current = author_session.get(File, second.id)
                _author_saves(author_session, user, current, AUTHOR_CH3)
        return original_delete(executor, *args, **kwargs)

    monkeypatch.setattr(FileToolExecutor, "delete_file", delete_after_descendant_save)
    payload = await _run(
        mcp_tools.delete_file,
        db_session,
        user,
        project,
        {"id": folder.id, "recursive": True},
        message="删掉正文文件夹",
    )

    assert payload["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    db_session.expire_all()
    assert db_session.get(File, folder.id).is_deleted is False
    assert db_session.get(File, first.id).is_deleted is False
    assert db_session.get(File, second.id).is_deleted is False


async def test_recursive_delete_does_not_save_then_delete_late_author_child(
    db_session, owner_project, monkeypatch
):
    """A child discovered after the preliminary scan must still hold its write stripe."""
    from agent.tools import author_edit_guard
    from agent.tools.file_ops import crud as crud_module

    user, project = owner_project
    folder = File(project_id=project.id, title="正文", file_type="folder")
    db_session.add(folder)
    db_session.commit()

    late_id = next(
        candidate
        for index in range(256)
        if file_write_lock(candidate := f"late-child-{index}") is not file_write_lock(folder.id)
    )
    engine = db_session.get_bind()
    original_acquire = crud_module._acquire_file_write_locks
    late_child_created = False

    def create_late_child_before_lock(file_ids):
        nonlocal late_child_created
        if not late_child_created:
            late_child_created = True
            with Session(engine) as seed_session:
                late = File(
                    id=late_id,
                    project_id=project.id,
                    parent_id=folder.id,
                    title="迟到章节",
                    file_type="draft",
                    content=AI_CH3,
                )
                seed_session.add(late)
                seed_session.flush()
                FileVersionService().create_version(
                    seed_session,
                    late.id,
                    AI_CH3,
                    change_type="create",
                    change_source="ai",
                    commit=False,
                )
                seed_session.commit()
        return original_acquire(file_ids)

    monkeypatch.setattr(crud_module, "_acquire_file_write_locks", create_late_child_before_lock)

    original_enforce = author_edit_guard.enforce_author_content_protection
    save_started = threading.Event()
    save_finished = threading.Event()
    save_finished_while_delete_held_locks: list[bool] = []
    save_outcome: dict[str, object] = {}
    save_thread: threading.Thread | None = None

    def save_as_author() -> None:
        save_started.set()
        try:
            with Session(engine) as author_session:
                files_api.update_file(
                    late_id,
                    files_api.FileUpdate(content=AUTHOR_CH3),
                    current_user=user,
                    session=author_session,
                )
        except Exception as exc:  # expected after the locked delete wins
            save_outcome["error"] = exc
        else:
            save_outcome["saved"] = True
        finally:
            save_finished.set()

    def enforce_then_start_real_author_save(*args, **kwargs):
        nonlocal save_thread
        original_enforce(*args, **kwargs)
        assert file_write_lock(late_id).locked()
        save_thread = threading.Thread(target=save_as_author, daemon=True)
        save_thread.start()
        assert save_started.wait(timeout=1)
        save_finished_while_delete_held_locks.append(save_finished.wait(timeout=0.2))

    monkeypatch.setattr(
        author_edit_guard,
        "enforce_author_content_protection",
        enforce_then_start_real_author_save,
    )

    payload = await _run(
        mcp_tools.delete_file,
        db_session,
        user,
        project,
        {"id": folder.id, "recursive": True},
        message="删掉正文文件夹",
    )

    assert save_thread is not None
    save_thread.join(timeout=2)
    assert not save_thread.is_alive()
    assert save_finished_while_delete_held_locks == [False]
    assert save_outcome.get("saved") is not True
    assert isinstance(save_outcome.get("error"), APIException)
    assert save_outcome["error"].error_code == ErrorCode.FILE_NOT_FOUND
    assert payload["status"] == "success"
    db_session.expire_all()
    stored = db_session.get(File, late_id)
    assert stored is not None and stored.is_deleted is True
    assert stored.content == AI_CH3


async def test_recursive_delete_membership_instability_fails_closed_without_mutation(
    db_session, owner_project, monkeypatch
):
    from agent.tools.file_ops import crud as crud_module

    user, project = owner_project
    folder = File(
        project_id=project.id,
        title="正文",
        file_type="folder",
        order=17,
        file_metadata='{"kept": true}',
    )
    db_session.add(folder)
    db_session.commit()
    db_session.refresh(folder)
    original_state = (folder.title, folder.order, folder.file_metadata, folder.updated_at)

    engine = db_session.get_bind()
    original_acquire = crud_module._acquire_file_write_locks
    created_ids: list[str] = []

    def add_an_uncovered_child_before_every_lock(file_ids):
        held_locks = {file_write_lock(file_id) for file_id in file_ids}
        child_id = next(
            candidate
            for index in range(1024)
            if (candidate := f"unstable-child-{len(created_ids)}-{index}") not in created_ids
            and file_write_lock(candidate) not in held_locks
        )
        with Session(engine) as seed_session:
            seed_session.add(
                File(
                    id=child_id,
                    project_id=project.id,
                    parent_id=folder.id,
                    title=f"迟到 {len(created_ids)}",
                    file_type="draft",
                    content=AI_CH3,
                )
            )
            seed_session.commit()
        created_ids.append(child_id)
        return original_acquire(file_ids)

    monkeypatch.setattr(
        crud_module,
        "_acquire_file_write_locks",
        add_an_uncovered_child_before_every_lock,
    )

    payload = await _run(
        mcp_tools.delete_file,
        db_session,
        user,
        project,
        {"id": folder.id, "recursive": True},
        message="删掉正文文件夹",
    )

    assert payload["error_type"] == "author_edit_protection_unavailable"
    assert payload["mutation_applied"] is False
    assert len(created_ids) == crud_module._SQLITE_PROTECTED_RECURSIVE_LOCK_ATTEMPTS
    db_session.expire_all()
    stored_folder = db_session.get(File, folder.id)
    assert stored_folder is not None and stored_folder.is_deleted is False
    assert (
        stored_folder.title,
        stored_folder.order,
        stored_folder.file_metadata,
        stored_folder.updated_at,
    ) == original_state
    assert all(db_session.get(File, child_id).is_deleted is False for child_id in created_ids)


async def test_same_name_whole_rewrite_is_refused_even_when_explicit(
    db_session, owner_project, monkeypatch
):
    user, project = owner_project
    project.project_type = "screenplay"
    folder = File(id=f"{project.id}-script-folder", project_id=project.id, title="剧本", file_type="folder")
    db_session.add_all([project, folder])
    db_session.commit()
    episode = File(
        project_id=project.id, parent_id=folder.id, title="第3集", file_type="draft", content=AI_CH3, order=0
    )
    db_session.add(episode)
    db_session.flush()
    FileVersionService().create_version(
        db_session, episode.id, AI_CH3, change_type="create", change_source="ai", commit=False
    )
    db_session.commit()
    _author_saves(db_session, user, episode, AUTHOR_CH3)
    db_session.refresh(episode)
    original_updated_at = episode.updated_at
    ledger_calls: list[dict] = []
    monkeypatch.setattr(
        mcp_tools,
        "_record_artifact_ledger",
        lambda **kwargs: ledger_calls.append(kwargs),
    )

    payload = await _run(
        mcp_tools.create_file,
        db_session,
        user,
        project,
        {"title": "第3集", "file_type": "script", "content": "", "parent_id": folder.id},
        message="第3集整集重写",
    )

    assert payload["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    assert "局部编辑" in payload["error"]
    db_session.expire_all()
    stored = db_session.get(File, episode.id)
    assert stored.content == AUTHOR_CH3
    assert stored.file_type == "draft"
    assert stored.order == 0
    assert stored.updated_at == original_updated_at
    assert ledger_calls == []


async def test_ordinary_edit_of_author_owned_file_still_works(db_session, owner_project):
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    _author_saves(db_session, user, chapter, AUTHOR_CH3)

    payload = await _run(
        mcp_tools.edit_file,
        db_session,
        user,
        project,
        {"id": chapter.id, "edits": [{"op": "replace", "old": "老秦", "new": "老周", "replace_all": True}]},
        message="把老秦改回老周",
    )

    assert payload["status"] == "success"


async def test_ai_owned_file_can_still_be_deleted_in_chat(db_session, owner_project):
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)

    payload = await _run(
        mcp_tools.delete_file, db_session, user, project, {"id": chapter.id}, message="删掉第三章"
    )

    assert payload["status"] == "success"


async def test_non_chat_call_preserves_direct_api_semantics(db_session, owner_project):
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    _author_saves(db_session, user, chapter, AUTHOR_CH3)
    engine = db_session.get_bind()
    mcp_tools.ToolContext.set_context(
        None, user.id, project.id, None, create_session_func=lambda: Session(engine)
    )
    try:
        raw = await mcp_tools.delete_file({"id": chapter.id})
    finally:
        mcp_tools.ToolContext.clear_context()

    assert json.loads(raw["content"][0]["text"])["status"] == "success"


async def test_missing_file_keeps_normal_not_found_error(db_session, owner_project):
    user, project = owner_project
    payload = await _run(
        mcp_tools.delete_file, db_session, user, project, {"id": "missing"}, message="删掉它"
    )
    assert payload["status"] == "error"
    assert payload.get("error_type") != "author_edit_protection_unavailable"


async def test_protection_lookup_failure_refuses_without_claiming_author_ownership(
    db_session, owner_project, monkeypatch
):
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)

    def fail_lookup(*_args, **_kwargs):
        raise RuntimeError("version store unavailable")

    monkeypatch.setattr(FileVersionService, "get_latest_version", fail_lookup)
    payload = await _run(
        mcp_tools.delete_file, db_session, user, project, {"id": chapter.id}, message="删掉第三章"
    )

    assert payload["error_type"] == "author_edit_protection_unavailable"
    assert "有作者手动" not in payload["error"]
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is False


def test_latest_text_owner_detection(db_session, owner_project):
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    assert latest_text_is_authors(db_session, chapter) is False
    _author_saves(db_session, user, chapter, AUTHOR_CH3)
    db_session.expire_all()
    assert latest_text_is_authors(db_session, db_session.get(File, chapter.id)) is True


async def test_chat_delete_refuses_the_authors_reviewed_reject_save(db_session, owner_project):
    """作者在审阅里拒掉 AI 修改、存下自己的写法（编辑器发的 ai_edit/AI edit (reviewed)），聊天不能删除它。"""
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    files_api.update_file(
        chapter.id,
        files_api.FileUpdate(
            content=AUTHOR_CH3, change_type="ai_edit", change_summary="AI edit (reviewed)"
        ),
        current_user=user,
        session=db_session,
    )

    payload = await _run(
        mcp_tools.delete_file, db_session, user, project, {"id": chapter.id}, message="删掉第三章"
    )

    assert payload["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is False
