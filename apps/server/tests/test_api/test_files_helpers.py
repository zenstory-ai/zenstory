from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlmodel import Session

import api.files as files_module
from api.files import (
    FileCreate,
    FileUpdate,
    _build_upload_snippets,
    _ensure_material_folder,
    _extract_chapter_segments,
    _resolve_change_source,
    _resolve_change_type,
    _split_content_by_length,
    _truncate_title,
    _validate_parent_assignment,
)
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import File, Project


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (FileCreate, {"title": "chapter\x00one"}),
        (FileCreate, {"title": "chapter", "content": "body\x00text"}),
        (FileUpdate, {"title": "chapter\x00one"}),
        (FileUpdate, {"content": "body\x00text"}),
    ],
)
def test_file_request_schemas_reject_embedded_nul(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_file_request_schemas_preserve_supported_text_and_optional_updates():
    title = "第 1 章'; DROP TABLE files; --"
    content = "第一行\nSecond line 🌙"

    created = FileCreate(title=title, content=content)
    updated = FileUpdate(title=title, content=content)

    assert created.title == updated.title == title
    assert created.content == updated.content == content
    assert FileUpdate().title is None
    assert FileUpdate().content is None


def test_split_content_by_length_prefers_newline_boundaries():
    chunks = _split_content_by_length("12345\n67890\nabcde", max_chars=10)

    assert len(chunks) == 2
    assert all(len(chunk) <= 10 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == "1234567890abcde"


def test_extract_chapter_segments_requires_multiple_titled_sections():
    structured = "第一章 开始\n内容A\n第二章 继续\n内容B"
    unstructured = "只有一章\n内容A"

    assert len(_extract_chapter_segments(structured)) == 2
    assert _extract_chapter_segments(unstructured) == []


def test_build_upload_snippets_and_title_truncation(monkeypatch):
    content = "第一章 开始\n" + ("A" * 30) + "\n第二章 继续\n" + ("B" * 30)
    monkeypatch.setattr(files_module, "MATERIAL_AUTO_SPLIT_TRIGGER_CHARS", 20)
    monkeypatch.setattr(files_module, "MATERIAL_AUTO_SPLIT_MAX_CHARS", 80)
    snippets = _build_upload_snippets("  A   very   long   title  ", content)

    assert snippets[0][0].startswith("A very long title - 第一章 开始")
    assert len(snippets) == 2
    assert _truncate_title("x" * 90, max_length=10) == "xxxxxxxxx…"


def test_resolve_change_type_and_source_raise_validation_errors():
    assert _resolve_change_type(None) == "edit"
    assert _resolve_change_source(None) == "user"

    with pytest.raises(APIException) as change_type_exc:
        _resolve_change_type("bad-type")
    with pytest.raises(APIException) as change_source_exc:
        _resolve_change_source("bad-source")

    assert change_type_exc.value.error_code == ErrorCode.VALIDATION_ERROR
    assert change_source_exc.value.error_code == ErrorCode.VALIDATION_ERROR


def _create_project(db_session: Session, name: str = "Files helper project") -> Project:
    project = Project(name=name, owner_id="owner-1")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


def test_validate_parent_assignment_rejects_invalid_targets(db_session: Session):
    project = _create_project(db_session)
    other_project = _create_project(db_session, "Other project")
    valid_folder = File(project_id=project.id, title="Folder", file_type="folder")
    deleted_folder = File(project_id=project.id, title="Deleted", file_type="folder", is_deleted=True)
    non_folder = File(project_id=project.id, title="Draft", file_type="draft", content="draft")
    foreign_folder = File(project_id=other_project.id, title="Foreign", file_type="folder")
    db_session.add_all([valid_folder, deleted_folder, non_folder, foreign_folder])
    db_session.commit()

    assert _validate_parent_assignment(db_session, project.id, valid_folder.id) == valid_folder.id

    for parent_id, error_code in (
        (deleted_folder.id, ErrorCode.FILE_NOT_FOUND),
        (foreign_folder.id, ErrorCode.FILE_NOT_FOUND),
        (non_folder.id, ErrorCode.VALIDATION_ERROR),
    ):
        with pytest.raises(APIException) as exc:
            _validate_parent_assignment(db_session, project.id, parent_id)
        assert exc.value.error_code == error_code

    # 成环检测：用真实的父子链断言，不再 monkeypatch 已被删除的 _is_descendant。
    # （校验规则现在唯一实现在 services/file_tree_rules，打桩本模块的私有函数
    # 已经影响不到它，也验证不出任何东西。）
    child_folder = File(
        project_id=project.id,
        title="Child",
        file_type="folder",
        parent_id=valid_folder.id,
    )
    db_session.add(child_folder)
    db_session.commit()

    with pytest.raises(APIException) as descendant_exc:
        _validate_parent_assignment(
            db_session, project.id, child_folder.id, moving_file_id=valid_folder.id
        )
    assert descendant_exc.value.error_code == ErrorCode.VALIDATION_ERROR

    # 把文件夹挂到它自己下面同样必须被拒
    with pytest.raises(APIException) as self_exc:
        _validate_parent_assignment(
            db_session, project.id, valid_folder.id, moving_file_id=valid_folder.id
        )
    assert self_exc.value.error_code == ErrorCode.VALIDATION_ERROR


def test_ensure_material_folder_restores_deleted_and_uses_english_title(db_session: Session):
    deleted_project = _create_project(db_session, "Deleted material project")
    deleted_folder = File(
        id=f"{deleted_project.id}-material-folder",
        project_id=deleted_project.id,
        title="素材",
        file_type="folder",
        is_deleted=True,
    )
    db_session.add(deleted_folder)
    db_session.commit()

    restored = _ensure_material_folder(db_session, deleted_project.id)
    assert restored.id == deleted_folder.id
    assert restored.is_deleted is False
    assert restored.deleted_at is None

    english_project = _create_project(db_session, "English material project")
    db_session.add(
        File(
            project_id=english_project.id,
            title="Characters",
            file_type="folder",
        )
    )
    db_session.commit()

    material_folder = _ensure_material_folder(db_session, english_project.id)
    assert material_folder.id == f"{english_project.id}-material-folder"
    assert material_folder.title == "Materials"
