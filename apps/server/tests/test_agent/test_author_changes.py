"""Unit tests for lightweight author manual-edit detection and alert rendering."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.core.session_loader import SessionLoader
from agent.tools.author_changes import (
    AI,
    AUTHOR,
    FAILED_LINE,
    FAILED_LINE_EN,
    ORIGINAL,
    pair_clip,
    render_manual_edit_alerts,
    version_actor,
)
from models import File, Project, User
from services.features.file_version_service import FileVersionService


def _seed(db_session: Session) -> tuple[User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"author-alert-{suffix}@example.com",
        username=f"author_alert_{suffix}",
        hashed_password="h",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="手改提醒测试", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


@pytest.mark.unit
def test_version_actor_classification():
    assert version_actor(SimpleNamespace(change_type="create", change_source="ai", change_summary="")) == AI
    assert version_actor(SimpleNamespace(change_type="create", change_source="user", change_summary="")) == ORIGINAL
    assert version_actor(SimpleNamespace(change_type="edit", change_source="ai", change_summary="")) == AI
    assert version_actor(SimpleNamespace(change_type="edit", change_source="user", change_summary="")) == AUTHOR
    assert (
        version_actor(
            SimpleNamespace(change_type="ai_edit", change_source="user", change_summary="AI edit (reviewed)")
        )
        == AUTHOR
    )
    assert (
        version_actor(
            SimpleNamespace(change_type="ai_edit", change_source="user", change_summary="AI polish (reviewed)")
        )
        == AI
    )
    assert version_actor(SimpleNamespace(change_type="restore", change_source="system", change_summary="")) == ORIGINAL
    assert version_actor(SimpleNamespace(change_type="restore", change_source="user", change_summary="")) == AUTHOR


@pytest.mark.unit
def test_render_manual_edit_alerts_zh_and_en():
    assert render_manual_edit_alerts([]) == ""

    zh = render_manual_edit_alerts([("f-1", "第1章 雨夜")])
    assert "<author_edits>" in zh and "</author_edits>" in zh
    assert "- 《第1章 雨夜》(id: f-1): 自你上次写入后已被作者手动修改，引用或续写前请先读取最新版。" in zh

    en = render_manual_edit_alerts([("f-1", "Chapter 1")], force_en=True)
    assert "<author_edits>" in en and "</author_edits>" in en
    assert 'manually edited by the author since your last write' in en
    assert "f-1" in en

    zh_failed = render_manual_edit_alerts([], failed=True)
    assert FAILED_LINE in zh_failed
    en_failed = render_manual_edit_alerts([], force_en=True, failed=True)
    assert FAILED_LINE_EN in en_failed


@pytest.mark.unit
def test_pair_clip_preserves_change_window():
    same_prefix = "甲" * 200
    same_suffix = "乙" * 200
    before, after, cut = pair_clip(f"{same_prefix}旧台词{same_suffix}", f"{same_prefix}新台词{same_suffix}")
    assert "旧台词" in before
    assert "新台词" in after
    assert cut is False


@pytest.mark.unit
def test_author_edits_for_files_distinguishes_manual_edits_from_pure_ai_and_author_only(db_session: Session):
    user, project = _seed(db_session)
    versions = FileVersionService()

    # 1) Author-only file (no AI version) -> NOT flagged
    author_only = File(project_id=project.id, title="作者原稿", content="作者自己写的。", file_type="draft")
    # 2) Pure AI file -> NOT flagged
    pure_ai = File(project_id=project.id, title="纯AI章", content="AI写的第一章。", file_type="draft")
    # 3) AI -> author edit -> AI edit (latest is AI, unchanged since) -> NOT flagged
    ai_then_author_then_ai = File(
        project_id=project.id, title="AI已读并续写", content="AI最终版。", file_type="draft"
    )
    # 4) AI -> author edit -> FLAGGED
    ai_then_author = File(project_id=project.id, title="作者改过", content="作者修改版。", file_type="draft")
    # 5) AI -> unversioned same-length author edit -> FLAGGED
    unversioned = File(project_id=project.id, title="无版本手改", content="林墨推门进来。", file_type="draft")

    db_session.add_all([author_only, pure_ai, ai_then_author_then_ai, ai_then_author, unversioned])
    db_session.commit()

    versions.create_version(db_session, author_only.id, author_only.content, change_type="create", change_source="user")
    versions.create_version(db_session, pure_ai.id, pure_ai.content, change_type="create", change_source="ai")

    versions.create_version(db_session, ai_then_author_then_ai.id, "初稿", change_type="create", change_source="ai")
    versions.create_version(db_session, ai_then_author_then_ai.id, "作者改", change_type="edit", change_source="user")
    versions.create_version(db_session, ai_then_author_then_ai.id, "AI最终版。", change_type="edit", change_source="ai")

    versions.create_version(db_session, ai_then_author.id, "AI初稿。", change_type="create", change_source="ai")
    versions.create_version(db_session, ai_then_author.id, "作者修改版。", change_type="edit", change_source="user")

    v_unver = versions.create_version(
        db_session, unversioned.id, "林墨推门进来。", change_type="create", change_source="ai"
    )
    unversioned.content = "陆沉推门进来。"
    unversioned.updated_at = v_unver.created_at + timedelta(milliseconds=50)
    db_session.add(unversioned)
    db_session.commit()

    loader = SessionLoader(project.id, user.id)
    block, edited_ids, _by_id = loader.author_edits_for_files(
        db_session,
        [author_only.id, pure_ai.id, ai_then_author_then_ai.id, ai_then_author.id, unversioned.id],
        {},
    )
    assert edited_ids == {ai_then_author.id, unversioned.id}
    assert ai_then_author.id in block and unversioned.id in block
    assert author_only.id not in block
    assert pure_ai.id not in block
    assert ai_then_author_then_ai.id not in block
