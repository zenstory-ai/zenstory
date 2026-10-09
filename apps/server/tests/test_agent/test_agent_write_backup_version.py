"""AI overwrites back up unversioned body text as a system version first."""

from datetime import datetime

import pytest
from fastapi import BackgroundTasks
from sqlmodel import select

from agent.tools.file_ops import FileCRUD, FileEditor
from api import files as files_api
from models import File, FileVersion, Project, User
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService, get_file_version_service


@pytest.fixture
def chapter(db_session, monkeypatch):
    user = User(username="backup-owner", email="backup-owner@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="备份测试", owner_id=user.id)
    file = File(project_id=project.id, title="第3章", file_type="draft", content="AI 初稿。\n")
    db_session.add_all([user, project, file])
    db_session.flush()
    get_file_version_service().create_initial_version(db_session, file)
    db_session.commit()
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr("services.llama_index.schedule_index_upsert", lambda **_kw: None)
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **_kw: None)
    return user, project, file


def _versions(session, file_id):
    session.expire_all()
    return list(
        session.exec(
            select(FileVersion).where(FileVersion.file_id == file_id).order_by(FileVersion.version_number)
        ).all()
    )


def _manual_save_without_version(session, user, file, content):
    """正文变了但历史里没有的手动保存。

    小改动不再走 skip_version 跳过版本（见 2026-10-09-user-versions-coalesce-by-time-window），
    现在留下「没进历史的正文」的真实路径是：最新版本不能合并（这里是 system 基线），
    且用户版本额度已满，PUT 照常保存正文、只回 version_quota_exceeded。
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (False, 10, 10))
        response = files_api.update_file(
            file.id,
            files_api.FileUpdate(content=content),
            BackgroundTasks(),
            current_user=user,
            session=session,
        )
    assert response.version_quota_exceeded is True


def _content_at(session, file_id, version_number):
    return FileVersionService().get_content_at_version(session, file_id, version_number)


@pytest.mark.parametrize("writer", ["update_file", "edit_file"])
def test_ai_overwrite_after_unversioned_manual_edit_keeps_manual_text_in_history(db_session, chapter, writer):
    user, _, file = chapter
    manual = "作者手改的第三章原稿。\n"
    _manual_save_without_version(db_session, user, file, manual)
    assert [v.version_number for v in _versions(db_session, file.id)] == [1]

    if writer == "update_file":
        FileCRUD(db_session, user.id).update_file(file.id, content="AI 整篇重写。\n")
    else:
        FileEditor(db_session, user.id).edit_file(
            file.id, [{"op": "replace", "old": "作者手改的第三章原稿。", "new": "AI 改写的第三章。"}]
        )

    versions = _versions(db_session, file.id)
    backup = versions[1]
    assert backup.change_source == "system"
    assert backup.change_type == "edit"
    assert backup.change_summary == "Before AI edit"
    assert _content_at(db_session, file.id, backup.version_number) == manual
    # 备份之后才是 AI 自己的版本，内容是 AI 写入后的正文。
    assert versions[2].change_source == "ai"
    assert _content_at(db_session, file.id, versions[2].version_number) == db_session.get(File, file.id).content


def test_edit_undo_anchors_on_the_backup_of_unversioned_text(db_session, chapter):
    user, _, file = chapter
    _manual_save_without_version(db_session, user, file, "手改稿。\n")

    result = FileEditor(db_session, user.id).edit_file(file.id, [{"op": "append", "text": "AI 续写。\n"}])

    # 以前历史最新版和当前正文不一致时没有撤销锚点；现在锚在刚建的备份上。
    assert result["undo"]["before_version_number"] == 2
    assert _content_at(db_session, file.id, 2) == "手改稿。\n"


@pytest.mark.parametrize("writer", ["update_file", "edit_file"])
def test_no_backup_when_body_matches_latest_version(db_session, chapter, writer):
    user, _, file = chapter
    if writer == "update_file":
        FileCRUD(db_session, user.id).update_file(file.id, content="AI 整篇重写。\n")
    else:
        FileEditor(db_session, user.id).edit_file(file.id, [{"op": "append", "text": "续写。\n"}])

    versions = _versions(db_session, file.id)
    assert [v.change_source for v in versions] == ["system", "ai"]
    assert all(v.change_summary != "Before AI edit" for v in versions)


def test_backup_does_not_use_user_version_quota(db_session, chapter, monkeypatch):
    user, _, file = chapter
    _manual_save_without_version(db_session, user, file, "额度已满时的手改稿。\n")
    # 额度已满：任何走用户额度闸门的版本都会被拒绝。
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (False, 10, 10))

    FileCRUD(db_session, user.id).update_file(file.id, content="AI 重写。\n")

    backup = next(v for v in _versions(db_session, file.id) if v.change_summary == "Before AI edit")
    assert _content_at(db_session, file.id, backup.version_number) == "额度已满时的手改稿。\n"
    assert FileVersionService().get_version_count(db_session, file.id, change_source="user") == 0


def test_backup_failure_does_not_block_the_ai_write(db_session, chapter, monkeypatch):
    user, _, file = chapter
    _manual_save_without_version(db_session, user, file, "手改稿。\n")
    real_create = FileVersionService.create_version

    def failing_backup(self, *args, **kwargs):
        if kwargs.get("change_summary") == "Before AI edit":
            raise RuntimeError("backup storage unavailable")
        return real_create(self, *args, **kwargs)

    monkeypatch.setattr(FileVersionService, "create_version", failing_backup)

    FileCRUD(db_session, user.id).update_file(file.id, content="AI 重写。\n")

    db_session.expire_all()
    assert db_session.get(File, file.id).content == "AI 重写。\n"
    assert [v.change_source for v in _versions(db_session, file.id)] == ["system", "ai"]


def _manual_save(session, user, file, content):
    return files_api.update_file(
        file.id,
        files_api.FileUpdate(content=content),
        BackgroundTasks(),
        current_user=user,
        session=session,
    )


def test_coalesced_manual_saves_are_already_history_so_ai_write_adds_no_backup(db_session, chapter):
    """时间窗合并改写的是最新用户版本，正文始终等于历史头；AI 写入前无需再备份。"""
    user, _, file = chapter
    _manual_save(db_session, user, file, "手改第一稿。\n")
    _manual_save(db_session, user, file, "手改第二稿。\n")  # 窗口内，合并进上一版

    FileCRUD(db_session, user.id).update_file(file.id, content="AI 重写。\n")

    versions = _versions(db_session, file.id)
    assert [v.change_source for v in versions] == ["system", "user", "ai"]
    assert all(v.change_summary != "Before AI edit" for v in versions)
    assert _content_at(db_session, file.id, 2) == "手改第二稿。\n"


def test_undo_of_ai_write_after_unversioned_text_backs_up_once(db_session, chapter):
    """AI 写入前的备份和恢复前的备份是同一条规则：撤销 AI 修改不会把同一份正文再备份一次，
    回滚响应里的版本号就是实际新建的 restore 版本。"""
    user, _, file = chapter
    _manual_save_without_version(db_session, user, file, "手改稿。\n")
    result = FileEditor(db_session, user.id).edit_file(file.id, [{"op": "append", "text": "AI 续写。\n"}])
    anchor = result["undo"]["before_version_number"]

    _, restore_version, quota_exceeded = FileVersionService().rollback_to_version(
        db_session,
        file.id,
        anchor,
        user_id=user.id,
        expected_updated_at=datetime.fromisoformat(result["undo"]["expected_after_updated_at"]),
    )

    versions = _versions(db_session, file.id)
    summaries = [v.change_summary for v in versions]
    assert summaries.count("Before AI edit") == 1
    assert not any((s or "").startswith("Before restoring") for s in summaries)
    assert quota_exceeded is False
    assert restore_version.version_number == versions[-1].version_number
    assert versions[-1].change_type == "restore"
    assert db_session.get(File, file.id).content == "手改稿。\n"
