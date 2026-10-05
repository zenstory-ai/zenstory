"""
Skill package service.

标准技能包（SKILL.md + references/ + assets/）的导入、导出与资源文件管理。
路由层（api/skills.py）保持轻薄，业务逻辑都在这里；解析/打包是 agent/skills/package.py
里的纯函数。永不执行技能携带的代码。
"""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.skills.active_skills import get_skill_resource, list_skill_resources
from agent.skills.package import (
    MAX_RESOURCES_PER_SKILL,
    MAX_TOTAL_RESOURCE_BYTES,
    MAX_ZIP_BYTES,
    ParsedSkill,
    SkillPackageError,
    build_skill_zip,
    export_filename,
    normalize_resource_path,
    read_skill_md_upload,
    read_skill_zip,
    serialize_skill_metadata,
    validate_resource_content,
    validate_resource_path,
)
from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import PublicSkill, SkillResource, UserAddedSkill, UserSkill
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# 上传读取上限：zip 包上限 + 1 字节，用来判定「超限」而不把超大文件整个读进内存
MAX_UPLOAD_READ_BYTES = MAX_ZIP_BYTES + 1
# 导入请求体（multipart）上限：zip 上限 + multipart 边界/表头余量。按 Content-Length 提前拒收。
MAX_IMPORT_REQUEST_BYTES = MAX_ZIP_BYTES + 64 * 1024


@dataclass
class OwnedSkill:
    """A skill the user can see: their own UserSkill, or an added PublicSkill."""

    source: str  # "user" | "added"
    user_skill: UserSkill | None = None
    added: UserAddedSkill | None = None
    public_skill: PublicSkill | None = None

    @property
    def user_skill_id(self) -> str | None:
        return self.user_skill.id if self.user_skill else None

    @property
    def public_skill_id(self) -> str | None:
        return self.public_skill.id if self.public_skill else None


# ==================== Helpers ====================


def package_error_to_api(exc: SkillPackageError) -> APIException:
    """SkillPackageError -> APIException（error_detail 携带给用户看的原因）。"""
    if exc.kind == "too_large":
        return APIException(
            status_code=413,
            error_code=ErrorCode.SKILL_PACKAGE_TOO_LARGE,
            detail=str(exc),
        )
    return APIException(
        status_code=400,
        error_code=ErrorCode.SKILL_PACKAGE_INVALID,
        detail=str(exc),
    )


def parse_json_object(value: str | None) -> dict[str, Any]:
    """Parse a JSON object column; malformed data degrades to {}."""
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_json_str_list(value: str | None) -> list[str]:
    """Parse a JSON array column; malformed data degrades to []."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item is not None]


def _not_found(detail: str = "Skill not found") -> APIException:
    return APIException(status_code=404, error_code=ErrorCode.NOT_FOUND, detail=detail)


def resolve_owned_skill(session: Session, user_id: str, skill_id: str) -> OwnedSkill:
    """
    Resolve a skill id as returned by GET /skills for the current user.

    自建技能用 UserSkill.id；已添加的公共技能用 UserAddedSkill.id。
    不属于当前用户的一律 404（不泄露存在性）。
    """
    user_skill = session.exec(
        select(UserSkill).where(UserSkill.id == skill_id, UserSkill.user_id == user_id)
    ).first()
    if user_skill is not None:
        return OwnedSkill(source="user", user_skill=user_skill)

    # 与 load_active_skills 口径一致：已添加的公共技能只认 approved 的（下架/驳回的不可再读）。
    row = session.exec(
        select(UserAddedSkill, PublicSkill)
        .join(PublicSkill, UserAddedSkill.public_skill_id == PublicSkill.id)
        .where(
            UserAddedSkill.id == skill_id,
            UserAddedSkill.user_id == user_id,
            PublicSkill.status == "approved",
        )
    ).first()
    if row is not None:
        added, public = row
        return OwnedSkill(source="added", added=added, public_skill=public)

    raise _not_found()


def _require_own_skill(owned: OwnedSkill) -> UserSkill:
    if owned.user_skill is None:
        raise APIException(
            status_code=403,
            error_code=ErrorCode.NOT_AUTHORIZED,
            detail="Added public skills are read-only",
        )
    return owned.user_skill


def _to_parsed_skill(owned: OwnedSkill) -> tuple[ParsedSkill, str]:
    """Convert a stored skill into ParsedSkill for export; returns (skill, id for slug fallback)."""
    if owned.user_skill is not None:
        record = owned.user_skill
        name = record.name
        triggers = parse_json_str_list(record.triggers)
        category = None
        skill_id = record.id
    else:
        assert owned.public_skill is not None and owned.added is not None
        record = owned.public_skill
        name = owned.added.custom_name or record.name
        # 公共技能的 tags 就是它的触发词（分享时由 triggers 复制而来）
        triggers = parse_json_str_list(record.tags)
        category = record.category
        skill_id = record.id

    stored = parse_json_object(record.skill_metadata)
    metadata = stored.get("metadata")
    allowed_tools = stored.get("allowed_tools")
    skill = ParsedSkill(
        name=name,
        description=record.description or "",
        instructions=record.instructions or "",
        triggers=triggers,
        category=category,
        license=stored.get("license") if isinstance(stored.get("license"), str) else None,
        compatibility=(
            stored.get("compatibility") if isinstance(stored.get("compatibility"), str) else None
        ),
        allowed_tools=[str(tool) for tool in allowed_tools] if isinstance(allowed_tools, list) else [],
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )
    return skill, skill_id


def _touch_user_skill(session: Session, user_skill: UserSkill) -> None:
    user_skill.updated_at = utcnow()
    session.add(user_skill)


# ==================== Import / Export ====================


def ensure_upload_size(size: int | None) -> None:
    """
    上传包大小上限（与扩展名无关，一律 413）。

    路由层在读文件之前先用 UploadFile.size 调一次，读的时候只读 MAX_UPLOAD_READ_BYTES，
    解析前再按实际读到的字节数调一次；没有 Content-Length 的分块上传也会落到这里。
    """
    if size is not None and size > MAX_ZIP_BYTES:
        raise SkillPackageError(
            f"技能包不能超过 {MAX_ZIP_BYTES // 1024 // 1024} MiB", kind="too_large"
        )


@dataclass
class ParsedUpload:
    """An uploaded skill package parsed and validated, not yet stored."""

    skill: ParsedSkill
    resources: list[tuple[str, str]]
    warnings: list[str]
    skill_metadata: str


def parse_skill_upload(filename: str | None, data: bytes) -> ParsedUpload:
    """
    Parse and validate an uploaded `.zip` package or bare `.md` SKILL.md.

    纯 CPU 工作、不碰数据库，路由层用 asyncio.to_thread 调用，避免阻塞事件循环。

    Raises:
        APIException: 400/413 for invalid or oversized packages
    """
    lowered = (filename or "").lower()
    try:
        ensure_upload_size(len(data))
        if lowered.endswith(".zip") or (not lowered.endswith(".md") and data[:4] == b"PK\x03\x04"):
            skill, resources, warnings = read_skill_zip(data)
        elif lowered.endswith(".md"):
            skill = read_skill_md_upload(data)
            resources, warnings = [], []
        else:
            raise SkillPackageError("只支持导入 .zip 技能包或 .md 文件")
        skill_metadata = serialize_skill_metadata(skill)
    except SkillPackageError as exc:
        raise package_error_to_api(exc) from exc
    return ParsedUpload(skill=skill, resources=resources, warnings=warnings, skill_metadata=skill_metadata)


def import_skill_package(
    session: Session,
    user_id: str,
    filename: str | None,
    data: bytes,
    parsed: ParsedUpload | None = None,
) -> tuple[UserSkill, list[str]]:
    """
    Import a `.zip` skill package or a bare `.md` SKILL.md as a new UserSkill.

    Args:
        parsed: 已由 parse_skill_upload 解析好的结果（路由层在线程池里解析后传入）；
            为 None 时在当前线程解析 filename / data。

    Returns:
        (created UserSkill, warnings about dropped files)
    """
    upload = parsed if parsed is not None else parse_skill_upload(filename, data)
    skill, resources, warnings = upload.skill, upload.resources, upload.warnings

    user_skill = UserSkill(
        user_id=user_id,
        name=skill.name,
        description=skill.description or None,
        triggers=json.dumps(skill.triggers),
        instructions=skill.instructions,
        skill_metadata=upload.skill_metadata,
    )
    session.add(user_skill)
    session.flush()

    for path, content in resources:
        session.add(SkillResource(
            user_skill_id=user_skill.id,
            path=path,
            content=content,
            size=len(content.encode("utf-8")),
        ))

    session.commit()
    session.refresh(user_skill)

    log_with_context(
        logger, 20, "Skill package imported",
        user_id=user_id,
        skill_id=user_skill.id,
        resource_count=len(resources),
        warning_count=len(warnings),
    )
    return user_skill, warnings


def export_skill_package(session: Session, user_id: str, skill_id: str) -> tuple[str, bytes]:
    """Export an own or added skill as a standard skill zip. Returns (filename, zip bytes)."""
    owned = resolve_owned_skill(session, user_id, skill_id)
    skill, canonical_id = _to_parsed_skill(owned)
    resources = [
        (resource.path, resource.content)
        for resource in list_skill_resources(
            session,
            user_skill_id=owned.user_skill_id,
            public_skill_id=owned.public_skill_id,
        )
    ]
    data = build_skill_zip(skill, resources, skill_id=canonical_id)
    log_with_context(
        logger, 20, "Skill package exported",
        user_id=user_id,
        skill_id=skill_id,
        resource_count=len(resources),
    )
    return export_filename(skill, skill_id=canonical_id), data


# ==================== Resources ====================


def list_resources(session: Session, user_id: str, skill_id: str) -> list[SkillResource]:
    owned = resolve_owned_skill(session, user_id, skill_id)
    return list_skill_resources(
        session,
        user_skill_id=owned.user_skill_id,
        public_skill_id=owned.public_skill_id,
    )


def get_resource(session: Session, user_id: str, skill_id: str, path: str) -> SkillResource:
    owned = resolve_owned_skill(session, user_id, skill_id)
    resource = get_skill_resource(
        session,
        normalize_resource_path(path),
        user_skill_id=owned.user_skill_id,
        public_skill_id=owned.public_skill_id,
    )
    if resource is None:
        raise _not_found("Skill resource not found")
    return resource


def upsert_resource(
    session: Session,
    user_id: str,
    skill_id: str,
    path: str,
    content: str,
) -> SkillResource:
    """
    Create or replace one resource of the user's own skill (validated like import).

    数量/总大小限额是「读现有资源 → 判断 → 写入」，需要串行化：先锁住父技能行
    （PostgreSQL 上 SELECT ... FOR UPDATE；SQLite 不支持行锁，写事务本身串行）。
    并发插入同一路径撞上唯一约束时返回 409，而不是 500。
    """
    user_skill = _require_own_skill(resolve_owned_skill(session, user_id, skill_id))
    try:
        normalized_path = validate_resource_path(path)
        size = validate_resource_content(content)
    except SkillPackageError as exc:
        raise package_error_to_api(exc) from exc

    locked = session.exec(
        select(UserSkill).where(UserSkill.id == user_skill.id).with_for_update()
    ).first()
    if locked is None:
        raise _not_found()
    user_skill = locked

    existing_resources = list_skill_resources(session, user_skill_id=user_skill.id)
    existing = next((item for item in existing_resources if item.path == normalized_path), None)
    others_total = sum(item.size for item in existing_resources if item is not existing)

    if existing is None and len(existing_resources) >= MAX_RESOURCES_PER_SKILL:
        raise package_error_to_api(SkillPackageError(
            f"资源文件不能超过 {MAX_RESOURCES_PER_SKILL} 个", kind="too_large"
        ))
    if others_total + size > MAX_TOTAL_RESOURCE_BYTES:
        raise package_error_to_api(SkillPackageError(
            f"资源文件总大小不能超过 {MAX_TOTAL_RESOURCE_BYTES // 1024} KiB", kind="too_large"
        ))

    if existing is None:
        resource = SkillResource(
            user_skill_id=user_skill.id,
            path=normalized_path,
            content=content,
            size=size,
        )
    else:
        resource = existing
        resource.content = content
        resource.size = size
        resource.updated_at = utcnow()

    session.add(resource)
    _touch_user_skill(session, user_skill)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise APIException(
            status_code=409,
            error_code=ErrorCode.RESOURCE_CONFLICT,
            detail=f"资源 {normalized_path} 正在被另一个请求修改，请重试",
        ) from exc
    session.refresh(resource)

    log_with_context(
        logger, 20, "Skill resource saved",
        user_id=user_id,
        skill_id=skill_id,
        path=normalized_path,
        size=size,
    )
    return resource


def delete_resource(session: Session, user_id: str, skill_id: str, path: str) -> None:
    """Delete one resource of the user's own skill."""
    user_skill = _require_own_skill(resolve_owned_skill(session, user_id, skill_id))
    resource = get_skill_resource(session, normalize_resource_path(path), user_skill_id=user_skill.id)
    if resource is None:
        raise _not_found("Skill resource not found")

    session.delete(resource)
    _touch_user_skill(session, user_skill)
    session.commit()
    log_with_context(logger, 20, "Skill resource deleted", user_id=user_id, skill_id=skill_id, path=resource.path)


def delete_user_skill_resources(session: Session, user_skill_id: str) -> None:
    """删除技能的全部资源（不提交）。SQLite 可能没开外键约束，不能只靠 ON DELETE CASCADE。"""
    for resource in list_skill_resources(session, user_skill_id=user_skill_id):
        session.delete(resource)


def list_public_skill_resources(session: Session, public_skill_id: str) -> list[SkillResource]:
    """公共技能的全部资源（不论审核状态），供管理员审核原文。"""
    return list_skill_resources(session, public_skill_id=public_skill_id)


def copy_resources_to_public_skill(
    session: Session,
    user_skill_id: str,
    public_skill_id: str,
) -> int:
    """分享到公共库时复制资源（不提交）。Returns copied count."""
    resources = list_skill_resources(session, user_skill_id=user_skill_id)
    for resource in resources:
        session.add(SkillResource(
            public_skill_id=public_skill_id,
            path=resource.path,
            content=resource.content,
            size=resource.size,
        ))
    return len(resources)


def get_share_statuses(session: Session, user_skills: list[UserSkill]) -> dict[str, str]:
    """
    自建技能分享出去的公共副本的审核状态：{user_skill_id: status}。

    驳回时 admin 会清掉 shared_skill_id（作者可改后重投），所以这里只会看到
    pending / approved / unpublished。
    """
    links = {
        skill.id: skill.shared_skill_id
        for skill in user_skills
        if skill.is_shared and skill.shared_skill_id
    }
    if not links:
        return {}
    rows = session.exec(
        select(PublicSkill.id, PublicSkill.status).where(PublicSkill.id.in_(list(links.values())))
    ).all()
    status_by_public_id = dict(rows)
    return {
        user_skill_id: status_by_public_id[public_id]
        for user_skill_id, public_id in links.items()
        if public_id in status_by_public_id
    }


def count_resources(
    session: Session,
    *,
    user_skill_ids: list[str] | None = None,
    public_skill_ids: list[str] | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Batch resource counts: ({user_skill_id: n}, {public_skill_id: n})."""
    user_counts: dict[str, int] = {}
    public_counts: dict[str, int] = {}

    if user_skill_ids:
        rows = session.exec(
            select(SkillResource.user_skill_id, func.count())
            .where(SkillResource.user_skill_id.in_(user_skill_ids))
            .group_by(SkillResource.user_skill_id)
        ).all()
        user_counts = {owner_id: int(count) for owner_id, count in rows if owner_id}

    if public_skill_ids:
        rows = session.exec(
            select(SkillResource.public_skill_id, func.count())
            .where(SkillResource.public_skill_id.in_(public_skill_ids))
            .group_by(SkillResource.public_skill_id)
        ).all()
        public_counts = {owner_id: int(count) for owner_id, count in rows if owner_id}

    return user_counts, public_counts
