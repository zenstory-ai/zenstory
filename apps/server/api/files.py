"""
File management API endpoints.

Provides REST API for the unified File model used by the AI agent.
"""
import contextlib
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, UploadFile
from fastapi import File as FastAPIFile
from pydantic import BaseModel, ConfigDict, Field
from services.auth import get_current_active_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only
from sqlmodel import Session, col, select

from config.datetime_utils import normalize_datetime_to_utc, utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from core.project_access import verify_project_ownership
from database import get_session
from models import (
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
    File,
    User,
)
from models.file_version import (
    CHANGE_SOURCE_AI,
    CHANGE_SOURCE_SYSTEM,
    CHANGE_SOURCE_USER,
    CHANGE_TYPE_AI_EDIT,
    CHANGE_TYPE_AUTO_SAVE,
    CHANGE_TYPE_CREATE,
    CHANGE_TYPE_EDIT,
    CHANGE_TYPE_RESTORE,
)
from services.features.activation_event_service import activation_event_service
from services.file_tree_rules import ParentNotFoundError, validate_parent_assignment
from utils.logger import get_logger, log_with_context
from utils.text_metrics import count_words
from utils.title_sequence import (
    build_sequence_sort_key,
    extract_title_first_sequence_number,
    resolve_persisted_sequence_order,
)

logger = get_logger(__name__)


router = APIRouter(prefix="/api/v1", tags=["files"])


# ==================== Material Upload Constants ====================
MATERIAL_MAX_CHARS = 200_000  # Maximum 20万字
MATERIAL_MAX_BYTES = 2_000_000  # 2MB hard byte cap
ALLOWED_EXTENSIONS = {".txt"}  # Only .txt allowed
MATERIAL_AUTO_SPLIT_TRIGGER_CHARS = 20_000  # Auto-split long uploads above this size
MATERIAL_AUTO_SPLIT_MAX_CHARS = 20_000  # Keep each generated snippet manageable

# ==================== Draft Upload Constants ====================
DRAFT_MAX_CHARS = 500_000            # 单文件最大 50 万字
DRAFT_MAX_BYTES = 5_000_000          # 单文件 5MB
DRAFT_MAX_FILES = 20                 # 单次最多 20 个文件
ALLOWED_DRAFT_EXTENSIONS = {".txt", ".md"}

CHAPTER_HEADING_PATTERN = re.compile(
    r"^\s*(?:"
    r"第[零一二三四五六七八九十百千万\d]+[章节回]\s*[：:]?\s*.*"
    r"|卷[零一二三四五六七八九十百千万\d]+\s*[：:]?\s*.*"
    r"|序章\s*[：:]?\s*.*"
    r"|楔子\s*[：:]?\s*.*"
    r"|番外\s*[：:]?\s*.*"
    r"|prologue\s*[：:]?\s*.*"
    r"|epilogue\s*[：:]?\s*.*"
    r"|chapter\s+\d+\s*[：:]?\s*.*"
    r"|\d+[\.、]\s*[^\d\s].*"
    r")\s*$",
    re.IGNORECASE,
)

VALID_CHANGE_TYPES = {
    CHANGE_TYPE_CREATE,
    CHANGE_TYPE_EDIT,
    CHANGE_TYPE_AI_EDIT,
    CHANGE_TYPE_RESTORE,
    CHANGE_TYPE_AUTO_SAVE,
}
VALID_CHANGE_SOURCES = {
    CHANGE_SOURCE_USER,
    CHANGE_SOURCE_AI,
    CHANGE_SOURCE_SYSTEM,
}


def _split_content_by_length(content: str, max_chars: int = MATERIAL_AUTO_SPLIT_MAX_CHARS) -> list[str]:
    """Split long content into chunks while preferring newline boundaries."""
    if len(content) <= max_chars:
        return [content]

    chunks: list[str] = []
    start = 0
    total_length = len(content)

    while start < total_length:
        end = min(start + max_chars, total_length)

        if end < total_length:
            # Prefer cutting at a recent newline for better readability.
            search_start = start + int(max_chars * 0.6)
            newline_pos = content.rfind("\n", search_start, end)
            if newline_pos > start:
                end = newline_pos + 1

        piece = content[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end

    return chunks


def _extract_chapter_segments(content: str) -> list[tuple[str, str]]:
    """
    Split content by chapter-like headings.

    Returns list of (chapter_heading, chapter_content_with_heading).
    If no reliable chapter structure is found, returns an empty list.
    """
    lines = content.splitlines()
    segments: list[tuple[str, str]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped and CHAPTER_HEADING_PATTERN.match(stripped):
            if current_lines:
                segment_content = "\n".join(current_lines).strip()
                if segment_content:
                    segments.append((current_title or "", segment_content))
            current_title = stripped
            current_lines = [raw_line]
            continue

        current_lines.append(raw_line)

    if current_lines:
        segment_content = "\n".join(current_lines).strip()
        if segment_content:
            segments.append((current_title or "", segment_content))

    # Require at least 2 chapter headings to treat as structured chapters.
    titled_count = sum(1 for title, _ in segments if title)
    if titled_count < 2:
        return []

    return segments


def _truncate_title(title: str, max_length: int = 80) -> str:
    normalized = " ".join((title or "").split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 1].rstrip() + "…"


def _build_upload_snippets(base_title: str, content: str) -> list[tuple[str, str]]:
    """
    Build snippet list (title, content) for uploaded material.

    Strategy:
    1. Short content -> keep as a single snippet.
    2. Long content -> try chapter-based split first.
    3. If no chapter structure -> split by length.
    """
    normalized_base_title = _truncate_title(base_title.strip() or "material")

    if len(content) <= MATERIAL_AUTO_SPLIT_TRIGGER_CHARS:
        return [(normalized_base_title, content)]

    chapter_segments = _extract_chapter_segments(content)
    snippets: list[tuple[str, str]] = []

    if chapter_segments:
        for chapter_index, (chapter_heading, chapter_content) in enumerate(chapter_segments, start=1):
            chapter_label = _truncate_title(chapter_heading or f"Part {chapter_index}", 60)
            chapter_title_base = f"{normalized_base_title} - {chapter_label}"

            sub_chunks = _split_content_by_length(chapter_content, MATERIAL_AUTO_SPLIT_MAX_CHARS)
            if len(sub_chunks) == 1:
                snippets.append((chapter_title_base, sub_chunks[0]))
            else:
                total_sub = len(sub_chunks)
                for sub_index, sub_chunk in enumerate(sub_chunks, start=1):
                    snippets.append(
                        (f"{chapter_title_base} ({sub_index}/{total_sub})", sub_chunk)
                    )
    else:
        parts = _split_content_by_length(content, MATERIAL_AUTO_SPLIT_MAX_CHARS)
        if len(parts) == 1:
            snippets.append((normalized_base_title, parts[0]))
        else:
            total_parts = len(parts)
            for idx, part in enumerate(parts, start=1):
                snippets.append((f"{normalized_base_title} ({idx}/{total_parts})", part))

    return snippets or [(normalized_base_title, content)]


def _strip_heading_line(content: str, heading: str) -> str:
    """Remove the heading line from content, preserving body indentation."""
    lines = content.split("\n")
    if lines and lines[0].strip() == heading:
        remaining = lines[1:]
        while remaining and not remaining[0].strip():
            remaining.pop(0)
        return "\n".join(remaining)
    return content


def _build_draft_chapters(base_title: str, content: str) -> list[tuple[str, str]]:
    """
    Build draft chapter list from uploaded novel text.
    Splits by chapter headings ONLY — no length-based splitting.
    No chapters = entire content as a single draft.
    """
    normalized = _truncate_title(base_title.strip() or "draft")

    chapter_segments = _extract_chapter_segments(content)

    if not chapter_segments:
        # Even without splitting, strip a lone chapter heading if present
        lines = content.splitlines()
        if lines and lines[0].strip() and CHAPTER_HEADING_PATTERN.match(lines[0].strip()):
            heading = lines[0].strip()
            body = _strip_heading_line(content, heading)
            return [(normalized, body)]
        return [(normalized, content)]

    chapters = []
    for heading, chapter_content in chapter_segments:
        label = _truncate_title(heading or "章节", 60)
        body = _strip_heading_line(chapter_content, heading)
        chapters.append((f"{normalized} - {label}", body))

    return chapters


# Request/Response schemas
class FileCreate(BaseModel):
    """Request body for creating a file."""
    title: str
    file_type: str = "document"
    content: str = ""
    parent_id: str | None = None
    order: int = 0
    metadata: dict | None = None


class FileUpdate(BaseModel):
    """Request body for updating a file."""
    title: str | None = None
    content: str | None = None
    word_count: int | None = Field(
        default=None,
        ge=0,
        description="Optional precomputed word count for draft/script content updates.",
    )
    parent_id: str | None = None
    order: int | None = None
    metadata: dict | None = None
    change_type: Literal["create", "edit", "ai_edit", "restore", "auto_save"] | None = None
    change_source: Literal["user", "ai", "system"] | None = Field(
        default=None,
        description=(
            "Deprecated client hint retained for compatibility. Authenticated "
            "PUT versions are attributed to the user; use change_type for intent."
        ),
    )
    change_summary: str | None = None
    skip_version: bool = False
    base_updated_at: datetime | None = Field(
        default=None,
        description=(
            "乐观并发令牌：客户端加载这份正文时拿到的 updated_at。"
            "服务端发现文件在此之后被改过（例如 agent 的 edit_file），则返回 409，"
            "避免编辑器的防抖自动保存用陈旧整篇快照静默覆盖 AI 的编辑结果。"
        ),
    )


class MoveFileRequest(BaseModel):
    """Request body for moving a file to a new parent."""
    target_parent_id: str | None = None


class ReorderFilesRequest(BaseModel):
    """Request body for reordering files within a parent."""
    ordered_ids: list[str]


class FileResponse(BaseModel):
    """Response model for a file."""
    id: str
    project_id: str
    title: str
    content: str
    file_type: str
    parent_id: str | None
    order: int
    file_metadata: str | None
    created_at: datetime
    updated_at: datetime
    version_quota_exceeded: bool = Field(
        default=False,
        description=(
            "本次保存因版本额度已满而未生成版本快照。正文本身已保存成功——"
            "版本额度限制的是历史深度，不应该把用户的正文保存整体判负。"
        ),
    )

    model_config = ConfigDict(from_attributes=True)


def _resolve_change_type(change_type: str | None) -> str:
    if change_type is None:
        return CHANGE_TYPE_EDIT
    if change_type not in VALID_CHANGE_TYPES:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail=f"Invalid change_type: {change_type}",
        )
    return change_type


def _resolve_change_source(change_source: str | None) -> str:
    if change_source is None:
        return CHANGE_SOURCE_USER
    if change_source not in VALID_CHANGE_SOURCES:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail=f"Invalid change_source: {change_source}",
        )
    return change_source


def _load_file_for_write(session: Session, file_id: str) -> File | None:
    """
    读取待写入的文件行，语义与 agent 侧 edit_file/update_file 保持一致。

    - PostgreSQL：`FOR NO KEY UPDATE` 行锁，与 agent 的 edit_file 互斥（用 key_share
      而非独占锁，才不会挡住版本快照那条独立 session 的外键插入）；
    - SQLite：没有行锁，改用 populate_existing 强制重新 SELECT，绕开 session
      identity map 里可能已经陈旧的副本；互斥由外层的条带写锁提供。
    """
    from database import is_postgres

    if is_postgres:
        return session.exec(
            select(File).where(File.id == file_id).with_for_update(key_share=True)
        ).first()
    return session.get(File, file_id, populate_existing=True)


def _assert_not_stale_write(file: File, base_updated_at: datetime | None) -> None:
    """
    乐观并发校验：客户端提交的是「整篇正文快照」，纯加锁挡不住丢更新——
    锁只保证串行，陈旧快照照样会在拿到锁之后原样覆盖别人刚写完的内容。

    因此当客户端带了加载时的 updated_at、而服务端的 updated_at 已经更新
    （典型场景：用户编辑器 3 秒防抖自动保存待发时，agent 的 edit_file 先落库），
    直接返回 409 并把服务端当前正文回带，交给前端刷新/合并。
    """
    if base_updated_at is None:
        return

    current = normalize_datetime_to_utc(file.updated_at)
    base = normalize_datetime_to_utc(base_updated_at)
    if current <= base:
        return

    raise APIException(
        error_code=ErrorCode.RESOURCE_CONFLICT,
        status_code=409,
        detail={
            "reason": "stale_write",
            "file_id": file.id,
            "current_content": file.content or "",
            "current_updated_at": current.isoformat(),
            "base_updated_at": base.isoformat(),
        },
    )


def _validate_parent_assignment(
    session: Session,
    project_id: str,
    parent_id: str | None,
    *,
    moving_file_id: str | None = None,
) -> str | None:
    """
    Validate parent assignment invariants for file hierarchy operations.

    - parent must exist, be active, and belong to project
    - parent must be a folder
    - moving file cannot be assigned into its own descendant chain

    规则本身不在这里实现：唯一权威版本在 services/file_tree_rules.py，
    Agent 工具层（agent/tools/file_ops/crud.py）复用的是同一份。本函数只负责
    把 ValueError 翻译成 REST 契约要求的 error_code——历史上两个入口各写一遍
    校验，agent 那份漏了「parent 必须是 folder」，才有了 AI 写的章节在文件树里
    消失的缺陷。
    """
    try:
        return validate_parent_assignment(
            session,
            project_id,
            parent_id,
            moving_file_id=moving_file_id,
        )
    except ParentNotFoundError as exc:
        raise APIException(
            error_code=ErrorCode.FILE_NOT_FOUND,
            status_code=400,
        ) from exc
    except ValueError as exc:
        # 非 folder / 成环：沿用既有的 VALIDATION_ERROR，保持前端与既有测试不变
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
        ) from exc


def _ensure_material_folder(session: Session, project_id: str) -> File:
    """
    Ensure the canonical material folder exists for upload workflows.

    Legacy non-novel projects may not have this folder pre-created.
    """
    # Heuristic language detection: project has no explicit language field.
    # We infer from existing root folder titles to avoid creating "Materials" in
    # Chinese projects (or vice-versa).
    en_root_folder_markers = (
        "Characters",
        "Concept",
        "Drafts",
        "Scripts",
        "Scenes",
        "Episode Outlines",
        "World Building",
    )

    material_folder_id = f"{project_id}-material-folder"
    material_folder = session.get(File, material_folder_id)

    if material_folder:
        if material_folder.project_id != project_id or material_folder.file_type != "folder":
            raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400)

        if material_folder.is_deleted:
            material_folder.is_deleted = False
            material_folder.deleted_at = None
            material_folder.updated_at = utcnow()
            session.add(material_folder)
            session.commit()
            session.refresh(material_folder)

        return material_folder

    has_en_root_folder = (
        session.exec(
            select(File.id)
            .where(File.project_id == project_id)
            .where(File.file_type == "folder")
            .where(File.parent_id.is_(None))
            .where(File.is_deleted.is_(False))
            .where(File.title.in_(en_root_folder_markers))
            .limit(1)
        ).first()
        is not None
    )

    material_title = "Materials" if has_en_root_folder else "素材"

    material_folder = File(
        id=material_folder_id,
        project_id=project_id,
        title=material_title,
        file_type="folder",
        parent_id=None,
        order=2,
    )
    session.add(material_folder)
    try:
        session.commit()
    except IntegrityError as err:
        # Concurrent create: another request created the folder first.
        session.rollback()
        existing = session.get(File, material_folder_id)
        if not existing or existing.is_deleted:
            raise
        if existing.project_id != project_id or existing.file_type != "folder":
            raise APIException(
                error_code=ErrorCode.VALIDATION_ERROR,
                status_code=400,
            ) from err
        return existing

    session.refresh(material_folder)
    return material_folder


def _ensure_draft_folder(session: Session, project_id: str) -> File:
    """Ensure the canonical draft folder exists for upload workflows."""
    draft_folder_id = f"{project_id}-draft-folder"
    draft_folder = session.get(File, draft_folder_id)

    if draft_folder:
        if draft_folder.project_id != project_id or draft_folder.file_type != "folder":
            raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400)

        if draft_folder.is_deleted:
            draft_folder.is_deleted = False
            draft_folder.deleted_at = None
            draft_folder.updated_at = utcnow()
            session.add(draft_folder)
            session.commit()
            session.refresh(draft_folder)

        return draft_folder

    # Check for script-folder fallback (screenplay projects use "script" instead of "draft")
    script_folder_id = f"{project_id}-script-folder"
    script_folder = session.get(File, script_folder_id)
    if (
        script_folder
        and script_folder.project_id == project_id
        and script_folder.file_type == "folder"
    ):
        if script_folder.is_deleted:
            script_folder.is_deleted = False
            session.add(script_folder)
            session.commit()
            session.refresh(script_folder)
        return script_folder

    # Detect language from existing root folders
    en_markers = ("Characters", "Concept", "Drafts", "Scripts", "Scenes", "Episode Outlines", "World Building")
    has_en_root = (
        session.exec(
            select(File.id)
            .where(File.project_id == project_id)
            .where(File.file_type == "folder")
            .where(File.parent_id.is_(None))
            .where(File.is_deleted.is_(False))
            .where(File.title.in_(en_markers))
            .limit(1)
        ).first()
        is not None
    )

    draft_folder = File(
        id=draft_folder_id,
        project_id=project_id,
        title="Drafts" if has_en_root else "正文",
        file_type="folder",
        parent_id=None,
        order=3,
    )
    session.add(draft_folder)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.get(File, draft_folder_id)
        if not existing or existing.is_deleted:
            raise
        if existing.project_id != project_id or existing.file_type != "folder":
            raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400) from None
        return existing

    session.refresh(draft_folder)
    return draft_folder


# ==================== File CRUD ====================

@router.get("/projects/{project_id}/files", response_model=list[FileResponse])
def get_files(
    project_id: str,
    file_type: str | None = Query(None, description="Filter by file type"),
    parent_id: str | None = Query(None, description="Filter by parent ID"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Get all files for a project.

    Optionally filter by file_type and/or parent_id.
    Returns files in order.
    """
    # Check project ownership
    verify_project_ownership(project_id, current_user, session)

    query = (
        select(File)
        .options(
            load_only(
                File.id,
                File.project_id,
                File.title,
                File.content,
                File.file_type,
                File.parent_id,
                File.order,
                File.file_metadata,
                File.created_at,
                File.updated_at,
            )
        )
        .where(File.project_id == project_id, File.is_deleted.is_(False))
    )

    if file_type:
        query = query.where(File.file_type == file_type)

    if parent_id is not None:
        query = query.where(File.parent_id == parent_id)

    query = query.order_by(File.order.asc(), col(File.created_at).desc())  # type: ignore[attr-defined]

    return session.exec(query).all()


def _rebuild_vector_index_task(project_id: str) -> None:
    """Background task: rebuild vector index for a project."""
    from sqlmodel import Session

    from database import sync_engine
    from services.llama_index import get_llama_index_service

    with Session(sync_engine) as s:
        svc = get_llama_index_service()
        _ = svc.index_project(s, project_id)


@router.post("/projects/{project_id}/vector-index/rebuild")
def rebuild_vector_index(
    project_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Rebuild vector index in background (fire-and-forget)."""
    verify_project_ownership(project_id, current_user, session)

    background_tasks.add_task(_rebuild_vector_index_task, project_id)
    return {"message": "Vector index rebuild queued", "project_id": project_id}


@router.get("/files/{file_id}", response_model=FileResponse)
def get_file(
    file_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Get a specific file by ID."""
    file = session.get(File, file_id)
    if not file or file.is_deleted:
        raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

    # Check project ownership
    verify_project_ownership(file.project_id, current_user, session)

    return file


@router.post("/projects/{project_id}/files", response_model=FileResponse)
def create_file(
    project_id: str,
    file_data: FileCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Create a new file in a project."""
    # Check project ownership
    verify_project_ownership(project_id, current_user, session)

    normalized_parent_id = _validate_parent_assignment(
        session,
        project_id,
        file_data.parent_id,
    )

    # Serialize metadata
    metadata_str = json.dumps(file_data.metadata) if file_data.metadata else None

    # Infer order when caller did not explicitly provide one.
    # NOTE: `FileCreate.order` has a default (0). We must use `model_fields_set`
    # to distinguish "explicitly set to 0" vs "omitted".
    sequence_number = extract_title_first_sequence_number(
        file_data.title,
        file_data.metadata,
    )
    resolved_order: int
    if "order" in file_data.model_fields_set:
        resolved_order = resolve_persisted_sequence_order(
            file_data.order,
            title=file_data.title,
            metadata=file_data.metadata,
            file_type=file_data.file_type,
        )
    elif sequence_number is not None:
        resolved_order = sequence_number
    else:
        # Append to the end of siblings (stable insertion).
        max_order = session.exec(
            select(func.max(File.order)).where(
                File.project_id == project_id,
                File.parent_id == normalized_parent_id,
                File.is_deleted.is_(False),
            )
        ).one()
        resolved_order = int(max_order or 0)
        # If there are existing siblings, place after the current max.
        if max_order is not None:
            resolved_order += 1

    file = File(
        project_id=project_id,
        title=file_data.title,
        content=file_data.content,
        file_type=file_data.file_type,
        parent_id=normalized_parent_id,
        order=resolved_order,
        file_metadata=metadata_str,
    )

    session.add(file)
    session.commit()
    session.refresh(file)

    # Fire-and-forget vector index upsert
    try:
        from services.llama_index import schedule_index_upsert

        extra_metadata = file_data.metadata or {}
        if file.parent_id:
            extra_metadata = {**extra_metadata, "parent_id": file.parent_id}

        background_tasks.add_task(
            schedule_index_upsert,
            project_id=project_id,
            entity_type=file.file_type,
            entity_id=file.id,
            title=file.title,
            content=file.content or "",
            extra_metadata=extra_metadata,
            user_id=current_user.id,
        )
    except Exception as e:
        # Indexing should never block file CRUD - log and continue
        log_with_context(
            logger,
            logging.DEBUG,
            "Vector index upsert failed (continuing)",
            error=str(e),
            project_id=project_id,
            file_id=file.id,
            operation="create_file_index",
        )

    return file


@router.put("/files/{file_id}", response_model=FileResponse)
def update_file(
    file_id: str,
    file_data: FileUpdate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Update an existing file."""
    # 请求参数校验必须先于任何写入：change_type/change_source 非法时若等到正文
    # commit 之后才抛 400，就又是一次「内容其实已保存却提示失败」的误导性失败。
    change_type = _resolve_change_type(file_data.change_type)
    # Validate the deprecated hint for backward compatibility, but never use
    # client input as the trusted actor.
    _resolve_change_source(file_data.change_source)
    # 这是带 Bearer token 的用户侧 PUT；客户端可以描述 AI 审阅意图，但不能决定
    # 持久化版本的可信来源。否则伪造 ai/system 会同时绕过配额闸门和 user 行计数。
    # AI 审阅语义由 change_type=ai_edit 保留，版本来源与 POST /versions 一样钉死 user。
    change_source = CHANGE_SOURCE_USER
    change_summary = file_data.change_summary or "File updated"

    from agent.tools.file_ops.edit import file_write_lock
    from database import is_postgres

    # 用户保存必须和 agent 的 edit_file / update_file 走同一条串行化通道。
    # 上一轮只把 agent↔agent 序列化了，agent↔用户这条更常见的路径没有任何互斥。
    # SQLite 没有行锁，用同一组进程内条带锁；PG 上互斥由 _load_file_for_write
    # 里的 FOR NO KEY UPDATE 行锁提供，不再叠加进程内锁（与 agent 侧一致）。
    lock_ctx = contextlib.nullcontext() if is_postgres else file_write_lock(file_id)

    content_changed = False
    version_quota_exceeded = False

    with lock_ctx:
        file = _load_file_for_write(session, file_id)
        if not file or file.is_deleted:
            raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

        # Check project ownership
        verify_project_ownership(file.project_id, current_user, session)

        # 陈旧写入检测：必须在拿到锁、读到最新行之后做，否则校验的是过期快照。
        _assert_not_stale_write(file, file_data.base_updated_at)

        # Update fields
        if file_data.title is not None:
            file.title = file_data.title

        if file_data.content is not None:
            if file_data.content != file.content:
                content_changed = True
            file.content = file_data.content

        if "parent_id" in file_data.model_fields_set:
            file.parent_id = _validate_parent_assignment(
                session,
                file.project_id,
                file_data.parent_id,
                moving_file_id=file.id,
            )

        existing_metadata: dict[str, Any] = {}
        if file.file_metadata:
            try:
                parsed = json.loads(file.file_metadata)
                if isinstance(parsed, dict):
                    existing_metadata = parsed
            except Exception:
                existing_metadata = {}

        updated_metadata: dict[str, Any] | None = None

        if file_data.metadata is not None:
            updated_metadata = {**existing_metadata, **file_data.metadata}

        if content_changed and file.file_type in {"draft", "script"}:
            if updated_metadata is None:
                updated_metadata = dict(existing_metadata)

            # Don't override explicit user-provided metadata payload.
            user_supplied_word_count = (
                file_data.metadata is not None
                and isinstance(file_data.metadata, dict)
                and "word_count" in file_data.metadata
            )
            if not user_supplied_word_count:
                resolved_word_count = (
                    int(file_data.word_count)
                    if file_data.word_count is not None
                    else count_words(file.content)
                )
                updated_metadata["word_count"] = max(0, resolved_word_count)

        if updated_metadata is not None:
            file.file_metadata = json.dumps(updated_metadata)

        if (
            file_data.order is not None
            or file_data.title is not None
            or file_data.metadata is not None
        ):
            effective_metadata = updated_metadata if updated_metadata is not None else existing_metadata
            effective_raw_order = file_data.order if file_data.order is not None else file.order
            file.order = resolve_persisted_sequence_order(
                effective_raw_order,
                title=file.title,
                metadata=effective_metadata,
                file_type=file.file_type,
            )

        file.updated_at = utcnow()

        # 版本额度预检必须发生在 commit 之前。
        # 旧顺序是「先 commit 正文，再 create_version」，配额超限时 402 原样上抛，
        # 前端提示「保存失败」而正文其实已经落库——典型的误导性失败。
        # 现在的口径：per-file 版本额度限制的是历史深度，不该把用户的正文保存
        # 整体判负；超限就跳过版本快照，并在响应里显式告诉前端。
        from services.file_version import get_file_version_service

        file_version_service = get_file_version_service()
        create_version_needed = content_changed and not file_data.skip_version
        if create_version_needed:
            has_quota, used_versions, max_versions = (
                file_version_service.check_user_version_quota(
                    session,
                    file.id,
                    current_user.id,
                )
            )
            if not has_quota:
                version_quota_exceeded = True
                create_version_needed = False
                log_with_context(
                    logger,
                    logging.INFO,
                    "File version quota reached, saving content without a version snapshot",
                    user_id=current_user.id,
                    file_id=file.id,
                    used_versions=used_versions,
                    max_versions=max_versions,
                    operation="update_file_version_quota",
                )

        if create_version_needed:
            try:
                # A savepoint preserves the product contract that snapshot
                # failures do not block content saves, while the outer locked
                # transaction still commits content and a successful snapshot
                # atomically.
                with session.begin_nested():
                    file_version_service.create_version(
                        session=session,
                        file_id=file.id,
                        new_content=file.content,
                        change_type=change_type,
                        change_source=change_source,
                        change_summary=change_summary,
                        user_id=current_user.id,
                        # Quota was checked above while holding the same file lock.
                        skip_quota=True,
                        quota_source=CHANGE_SOURCE_USER,
                        commit=False,
                    )
            except Exception as e:
                log_with_context(
                    logger,
                    logging.WARNING,
                    "Failed to create file version after content update",
                    error=str(e),
                    file_id=file.id,
                    operation="update_file_create_version",
                )

        session.commit()
        session.refresh(file)

    if content_changed and file.file_type in {"draft", "script"}:
        try:
            from services.infra.dashboard_cache import dashboard_cache

            dashboard_cache.bump_project_version(
                user_id=current_user.id,
                project_id=file.project_id,
            )
        except Exception as e:
            log_with_context(
                logger,
                logging.DEBUG,
                "Failed to bump dashboard cache version after file update (continuing)",
                error=str(e),
                error_type=type(e).__name__,
                user_id=current_user.id,
                project_id=file.project_id,
                file_id=file.id,
                operation="update_file_bump_dashboard_cache_version",
            )

    if content_changed:
        try:
            if change_source == CHANGE_SOURCE_USER:
                activation_event_service.record_once(
                    session,
                    user_id=current_user.id,
                    event_name=ACTIVATION_EVENT_FIRST_FILE_SAVED,
                    project_id=file.project_id,
                    event_metadata={
                        "file_id": file.id,
                        "file_type": file.file_type,
                    },
                )

            if change_type == CHANGE_TYPE_AI_EDIT:
                activation_event_service.record_once(
                    session,
                    user_id=current_user.id,
                    event_name=ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
                    project_id=file.project_id,
                    event_metadata={
                        "file_id": file.id,
                        "file_type": file.file_type,
                    },
                )
        except Exception as e:
            log_with_context(
                logger,
                logging.WARNING,
                "Failed to record activation event on file update",
                error=str(e),
                file_id=file.id,
                project_id=file.project_id,
                user_id=current_user.id,
            )

    # Fire-and-forget vector index upsert
    try:
        from services.llama_index import schedule_index_upsert

        extra_metadata = {}
        if file.file_metadata:
            try:
                extra_metadata = json.loads(file.file_metadata)
            except Exception as e:
                # JSON parse failure - use empty metadata
                log_with_context(
                    logger,
                    logging.DEBUG,
                    "Failed to parse file metadata (using empty)",
                    error=str(e),
                    file_id=file.id,
                )
                extra_metadata = {}
        if file.parent_id:
            extra_metadata = {**extra_metadata, "parent_id": file.parent_id}

        background_tasks.add_task(
            schedule_index_upsert,
            project_id=file.project_id,
            entity_type=file.file_type,
            entity_id=file.id,
            title=file.title,
            content=file.content or "",
            extra_metadata=extra_metadata,
            user_id=current_user.id,
        )
    except Exception as e:
        # Indexing should never block file CRUD - log and continue
        log_with_context(
            logger,
            logging.DEBUG,
            "Vector index upsert failed (continuing)",
            error=str(e),
            file_id=file.id,
            operation="update_file_index",
        )

    response = FileResponse.model_validate(file)
    response.version_quota_exceeded = version_quota_exceeded
    return response


@router.delete("/files/{file_id}")
def delete_file(
    file_id: str,
    background_tasks: BackgroundTasks,
    recursive: bool = Query(False, description="Delete children recursively"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Delete a file."""
    log_with_context(
        logger,
        logging.INFO,
        "Deleting file",
        user_id=current_user.id,
        file_id=file_id,
        recursive=recursive,
    )

    file = session.get(File, file_id)
    if not file:
        log_with_context(
            logger,
            logging.WARNING,
            "Attempted to delete non-existent file",
            user_id=current_user.id,
            file_id=file_id,
        )
        raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

    # Check project ownership
    verify_project_ownership(file.project_id, current_user, session)

    deleted_files: list[File] = []

    if recursive:
        # Delete all children recursively
        deleted_files = _delete_recursive(session, file)
    else:
        deleted_files = [file]
        # Soft delete: mark as deleted instead of removing from database
        file.is_deleted = True
        file.deleted_at = utcnow()

    session.commit()

    log_with_context(
        logger,
        logging.INFO,
        "File deleted successfully",
        user_id=current_user.id,
        file_id=file_id,
        deleted_count=len(deleted_files),
        recursive=recursive,
    )

    # Fire-and-forget vector index delete
    try:
        if background_tasks is None:
            # Shouldn't happen in FastAPI, but keep safe
            from services.llama_index import schedule_index_delete
            for f in deleted_files:
                schedule_index_delete(
                    project_id=f.project_id,
                    entity_type=f.file_type,
                    entity_id=f.id,
                    user_id=current_user.id,
                )
        else:
            from services.llama_index import schedule_index_delete
            for f in deleted_files:
                background_tasks.add_task(
                    schedule_index_delete,
                    project_id=f.project_id,
                    entity_type=f.file_type,
                    entity_id=f.id,
                    user_id=current_user.id,
                )
    except Exception as e:
        log_with_context(
            logger,
            logging.WARNING,
            "Failed to schedule vector index delete",
            file_id=file_id,
            deleted_count=len(deleted_files),
            error=str(e),
        )

    return {"message": "File deleted successfully"}


@router.post("/files/{file_id}/move", response_model=FileResponse)
def move_file(
    file_id: str,
    request: MoveFileRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Move a file to a new parent folder.

    - Validates that the new parent exists and is a folder
    - Prevents moving a folder into its own descendants
    - Updates parent_id and timestamps
    """
    file = session.get(File, file_id)
    if not file or file.is_deleted:
        raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

    # Check project ownership
    verify_project_ownership(file.project_id, current_user, session)

    file.parent_id = _validate_parent_assignment(
        session,
        file.project_id,
        request.target_parent_id,
        moving_file_id=file.id,
    )
    file.updated_at = utcnow()

    session.add(file)
    session.commit()
    session.refresh(file)

    log_with_context(
        logger,
        logging.INFO,
        "File moved successfully",
        user_id=current_user.id,
        file_id=file_id,
        new_parent_id=file.parent_id,
    )

    return file


@router.post("/projects/{project_id}/files/reorder")
def reorder_files(
    project_id: str,
    request: ReorderFilesRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Reorder files within a parent folder.

    Updates the 'order' field of files based on the provided ordered_ids list.
    All files must belong to the same project and have the same parent.
    """
    # Check project ownership
    verify_project_ownership(project_id, current_user, session)

    if not request.ordered_ids:
        return {"message": "No files to reorder"}

    # Get all files and validate
    files_to_update = []
    parent_id = None

    for idx, file_id in enumerate(request.ordered_ids):
        file = session.get(File, file_id)
        if not file or file.is_deleted:
            raise APIException(
                error_code=ErrorCode.FILE_NOT_FOUND,
                status_code=400
            )

        if file.project_id != project_id:
            raise APIException(
                error_code=ErrorCode.VALIDATION_ERROR,
                status_code=400
            )

        # All files should have the same parent
        if parent_id is None:
            parent_id = file.parent_id
        elif file.parent_id != parent_id:
            raise APIException(
                error_code=ErrorCode.VALIDATION_ERROR,
                status_code=400
            )

        files_to_update.append((file, idx))

    # Update order
    for file, new_order in files_to_update:
        file.order = resolve_persisted_sequence_order(
            new_order,
            title=file.title,
            metadata=file.get_metadata(),
            file_type=file.file_type,
        )
        file.updated_at = utcnow()
        session.add(file)

    session.commit()

    log_with_context(
        logger,
        logging.INFO,
        "Files reordered successfully",
        user_id=current_user.id,
        project_id=project_id,
        parent_id=parent_id,
        file_count=len(files_to_update),
    )

    return {"message": "Files reordered successfully", "count": len(files_to_update)}


def _delete_recursive(session: Session, file: File) -> list[File]:
    """Delete a file and all its children recursively.

    Returns a list of File objects that were deleted (including the root file).
    Uses soft delete: marks files as deleted instead of removing from database.
    """
    deleted: list[File] = []

    # Get all children that are not already deleted
    children = session.exec(
        select(File).where(
            File.parent_id == file.id,
            File.is_deleted.is_(False)
        )
    ).all()

    # Delete children first
    for child in children:
        deleted.extend(_delete_recursive(session, child))

    # Soft delete this file
    deleted.append(file)
    file.is_deleted = True
    file.deleted_at = utcnow()

    return deleted


# 成环检测原先在这里另写了一份 `_is_descendant`，与 agent 侧的实现同语义不同代码。
# 现已统一到 services/file_tree_rules.is_descendant_of，由
# _validate_parent_assignment 间接调用，本文件不再保留副本。


# ==================== File Tree ====================

@router.get("/projects/{project_id}/file-tree")
def get_file_tree(
    project_id: str,
    include_content: bool = Query(
        False,
        description="Whether to include full file content in tree nodes (default: false)",
    ),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Get the complete file tree for a project.

    Returns a hierarchical structure with folders and their contents.
    Files are sorted by chapter number (extracted from title) then by creation date.
    """
    # Check project ownership
    verify_project_ownership(project_id, current_user, session)

    def sort_key(item: dict):
        """
        Sort key: first by effective order, then by sequence number, then by created_at.

        `order` historically defaulted to 0 for many agent-created files. In that
        case, we treat `order == 0` as "unset" and fall back to parsing the
        episode/chapter sequence from the title for a more intuitive ordering.
        For chapter-like draft/outline/script files, title sequence is treated as
        canonical ordering. We also normalize obvious LLM typos such as 580 for
        "第58章".
        """
        effective_order, seq_num = build_sequence_sort_key(
            item.get("order", 0),
            title=item.get("title", ""),
            metadata=item.get("metadata"),
            file_type=item.get("file_type"),
        )
        created_at = item.get("created_at", "")
        return (effective_order, seq_num, created_at)

    # Get all files for the project (exclude soft-deleted)
    file_query = select(File).where(
        File.project_id == project_id, File.is_deleted.is_(False)
    )
    if not include_content:
        file_query = file_query.options(
            load_only(
                File.id,
                File.title,
                File.file_type,
                File.parent_id,
                File.order,
                File.file_metadata,
                File.created_at,
            )
        )

    files = session.exec(file_query).all()

    # Build tree structure
    # Optimize: Use explicit loop to avoid repeated method calls
    file_dict: dict[str, dict[str, Any]] = {}
    for f in files:
        # Parse metadata inline to avoid method call overhead
        metadata = None
        if f.file_metadata:
            try:
                metadata = json.loads(f.file_metadata)
            except Exception:
                metadata = {}

        node_data = {
            "id": f.id,
            "title": f.title,
            "file_type": f.file_type,
            "parent_id": f.parent_id,
            "order": f.order,
            "created_at": f.created_at.isoformat() if f.created_at else "",
            "content": f.content if include_content else "",
            "metadata": metadata,
            "children": []
        }

        file_dict[f.id] = node_data

    # Build hierarchy
    root_items = []
    for _file_id, file_data in file_dict.items():
        parent_id = file_data["parent_id"]
        if parent_id and parent_id in file_dict:
            file_dict[parent_id]["children"].append(file_data)
        else:
            root_items.append(file_data)

    # Sort children of each folder
    for file_data in file_dict.values():
        if file_data["children"]:
            file_data["children"].sort(key=sort_key)

    # Sort root items
    root_items.sort(key=sort_key)

    return {"tree": root_items}


# ==================== Material Upload ====================

@router.post("/projects/{project_id}/files/upload", response_model=FileResponse)
async def upload_material(
    project_id: str,
    file: UploadFile = FastAPIFile(...),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Upload a .txt file as a material (snippet) in the project.

    Constraints:
    - Only .txt files allowed
    - Maximum 200,000 characters (约 20 万字)
    - File will be created as snippet(s) under the material folder
    - Long uploads (>2万字) are auto-split (chapter-aware when possible)
    """
    # 1. Check project ownership
    verify_project_ownership(project_id, current_user, session)

    # 2. Validate file extension
    if not file.filename:
        raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400)

    _, ext = os.path.splitext(file.filename)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise APIException(
            error_code=ErrorCode.FILE_TYPE_INVALID,
            status_code=400
        )

    # 3. Read file content with a hard byte cap (streaming)
    try:
        content_bytes = bytearray()
        total_bytes = 0
        while True:
            chunk = await file.read(8192)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > MATERIAL_MAX_BYTES:
                raise APIException(
                    error_code=ErrorCode.FILE_TOO_LARGE,
                    status_code=400
                )
            content_bytes.extend(chunk)

        raw_bytes = bytes(content_bytes)
        # Try UTF-8 first, then fall back to other encodings
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content = raw_bytes.decode("gbk")
            except UnicodeDecodeError:
                content = raw_bytes.decode("utf-8", errors="ignore")
    except Exception as e:
        if isinstance(e, APIException):
            raise
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400
        ) from e

    # 4. Validate content length
    char_count = len(content)
    if char_count > MATERIAL_MAX_CHARS:
        raise APIException(
            error_code=ErrorCode.FILE_CONTENT_TOO_LONG,
            status_code=400
        )

    # 5. Ensure material folder exists (legacy non-novel projects may miss it)
    material_folder = _ensure_material_folder(session, project_id)

    # 6. Build snippet list (single or multiple)
    base_title = os.path.splitext(file.filename)[0]
    snippets = _build_upload_snippets(base_title, content)
    split_total = len(snippets)

    created_files: list[File] = []
    for index, (snippet_title, snippet_content) in enumerate(snippets, start=1):
        metadata = {
            "source": "upload",
            "original_filename": file.filename,
            "char_count": len(snippet_content),
        }
        if split_total > 1:
            metadata["split"] = {
                "index": index,
                "total": split_total,
                "trigger_chars": MATERIAL_AUTO_SPLIT_TRIGGER_CHARS,
            }

        created_file = File(
            project_id=project_id,
            title=snippet_title,
            content=snippet_content,
            file_type="snippet",
            parent_id=material_folder.id,
            order=index - 1,
            file_metadata=json.dumps(metadata),
        )
        session.add(created_file)
        created_files.append(created_file)

    session.commit()
    for created_file in created_files:
        session.refresh(created_file)

    # Keep backward-compatible response model: return the first created snippet.
    return created_files[0]


class UploadDraftsResponse(BaseModel):
    files: list[FileResponse]
    total: int
    errors: list[str]


@router.post("/projects/{project_id}/files/upload-drafts", response_model=UploadDraftsResponse)
async def upload_drafts(
    project_id: str,
    files: list[UploadFile] = FastAPIFile(...),
    parent_id: str | None = Form(None),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """
    Upload .txt/.md files as draft(s) in the project.
    Auto-splits by chapter headings. No length-based splitting.
    """
    # 1. Check project ownership
    verify_project_ownership(project_id, current_user, session)

    # 2. Validate file count
    if len(files) > DRAFT_MAX_FILES:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
        )

    # 3. Resolve target folder
    if parent_id:
        target_folder = session.get(File, parent_id)
        if not target_folder or target_folder.project_id != project_id or target_folder.file_type != "folder":
            raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400)
    else:
        target_folder = _ensure_draft_folder(session, project_id)

    created_files: list[File] = []
    errors: list[str] = []
    order_index = 0

    # Determine max existing order under target folder
    max_order_result = session.exec(
        select(File.order).where(File.parent_id == target_folder.id, File.is_deleted.is_(False))
    ).all()
    next_order = max(max_order_result) + 1 if max_order_result else 0

    for upload_file in files:
        try:
            # 4a. Validate filename
            if not upload_file.filename:
                errors.append(f"filename_missing: {ErrorCode.VALIDATION_ERROR}")
                continue

            # 4b. Validate file extension
            _, ext = os.path.splitext(upload_file.filename)
            if ext.lower() not in ALLOWED_DRAFT_EXTENSIONS:
                errors.append(f"{upload_file.filename}: {ErrorCode.FILE_TYPE_INVALID}")
                continue

            # 4c. Read file content with hard byte cap (streaming)
            content_bytes = bytearray()
            total_bytes = 0
            oversized = False
            while True:
                chunk = await upload_file.read(8192)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > DRAFT_MAX_BYTES:
                    oversized = True
                    break
                content_bytes.extend(chunk)

            if oversized:
                # Drain remaining bytes for proper multipart parsing
                while await upload_file.read(65536):
                    pass
                errors.append(f"{upload_file.filename}: {ErrorCode.FILE_TOO_LARGE}")
                continue

            # 4d. Decode content
            raw_bytes = bytes(content_bytes)
            try:
                if raw_bytes.startswith(b'\xff\xfe') or raw_bytes.startswith(b'\xfe\xff'):
                    content = raw_bytes.decode("utf-16")
                else:
                    content = raw_bytes.decode("utf-8")
                content = content.lstrip('\ufeff')
            except UnicodeDecodeError:
                try:
                    content = raw_bytes.decode("gb18030")
                except UnicodeDecodeError:
                    content = raw_bytes.decode("utf-8", errors="replace")

            # 4e. Validate content length
            char_count = len(content)
            if char_count > DRAFT_MAX_CHARS:
                errors.append(f"{upload_file.filename}: {ErrorCode.FILE_CONTENT_TOO_LONG}")
                continue

            # 4f. Build chapters and create files
            base_title = os.path.splitext(upload_file.filename)[0]
            chapters = _build_draft_chapters(base_title, content)

            for chapter_title, chapter_content in chapters:
                word_count = count_words(chapter_content)

                metadata = {
                    "source": "upload",
                    "original_filename": upload_file.filename,
                    "char_count": len(chapter_content),
                    "word_count": word_count,
                }

                resolved_order = resolve_persisted_sequence_order(
                    next_order + order_index,
                    title=chapter_title,
                    metadata=metadata,
                    file_type="draft",
                )

                draft = File(
                    project_id=project_id,
                    title=chapter_title,
                    content=chapter_content,
                    file_type="draft",
                    parent_id=target_folder.id,
                    order=resolved_order,
                    file_metadata=json.dumps(metadata),
                )
                session.add(draft)
                created_files.append(draft)
                order_index += 1

        except Exception as e:
            logger.warning(f"Error processing upload file {getattr(upload_file, 'filename', '?')}: {e}")
            errors.append(f"{getattr(upload_file, 'filename', '未知文件')}: {ErrorCode.VALIDATION_ERROR}")

    # 5. Commit all created files
    if created_files:
        session.commit()
        for f in created_files:
            session.refresh(f)

        # 6. Background tasks: vector index + dashboard cache
        for f in created_files:
            try:
                from services.llama_index import schedule_index_upsert

                background_tasks.add_task(
                    schedule_index_upsert,
                    project_id=project_id,
                    entity_type=f.file_type,
                    entity_id=f.id,
                    content=f.content,
                    title=f.title,
                    extra_metadata={"parent_id": f.parent_id} if f.parent_id else {},
                )
            except Exception:
                pass

        try:
            from services.infra.dashboard_cache import dashboard_cache

            dashboard_cache.bump_project_version(user_id=current_user.id, project_id=project_id)
        except Exception:
            pass

    return UploadDraftsResponse(
        files=[FileResponse.model_validate(f) for f in created_files],
        total=len(created_files),
        errors=errors,
    )
