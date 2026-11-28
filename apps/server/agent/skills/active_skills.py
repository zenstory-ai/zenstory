"""
当前用户「启用中」技能的统一视图。

技能目录（L1）、`load_skill` / `read_skill_resource` 工具（L2/L3）、显式选择的
`selected_skill_ids` 都从这里取数，保证三处看到的是同一批技能、同一套优先级：
用户自建技能 > 已添加的公共技能（仅 approved）。
"""

import difflib
import json
from dataclasses import dataclass

from sqlmodel import Session, select

from config.official_skill_translations import localized_skill_text
from models import PublicSkill, SkillResource, UserAddedSkill, UserSkill

# 显式选择的技能数上限（与前端 SkillTriggerContext 一致）
MAX_SELECTED_SKILLS = 3


def parse_skill_triggers(raw: str | None) -> tuple[str, ...]:
    """安全解析技能触发词 JSON 数组；格式异常时静默退回空元组。"""
    if not raw or not isinstance(raw, str):
        return ()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return tuple(cleaned)


@dataclass(frozen=True)
class ActiveSkill:
    """A skill the current user can use in this turn."""

    # 规范 ID：自建技能为 UserSkill.id，已添加技能为 PublicSkill.id（用量统计沿用此口径）
    id: str
    name: str
    description: str
    instructions: str
    source: str  # "user" | "added"
    # 已添加技能在 GET /skills 列表里的 ID（UserAddedSkill.id）
    added_id: str | None = None
    # 触发场景提示（自建技能来自 UserSkill.triggers，公共技能来自 PublicSkill.tags）
    triggers: tuple[str, ...] = ()
    # 公共技能来源（PublicSkill.source："official" | "community"；自建技能为 None）
    public_source: str | None = None
    # 用户是否为已添加的公共技能设置了自定义名称（设置了自定义名时保留用户自定义名，不覆盖翻译）
    has_custom_name: bool = False

    @property
    def user_skill_id(self) -> str | None:
        return self.id if self.source == "user" else None

    @property
    def public_skill_id(self) -> str | None:
        return self.id if self.source == "added" else None

    @property
    def is_official(self) -> bool:
        return self.source == "added" and self.public_source == "official"

    def localized_display(self, language: str | None) -> tuple[str, str]:
        """Return ``(display_name, display_description)`` localized for ``language``."""
        if not self.is_official or self.has_custom_name:
            return self.name, self.description
        display_name, display_desc = localized_skill_text(
            source=self.public_source,
            name=self.name,
            description=self.description,
            language=language,
        )
        return display_name, display_desc or ""


def load_active_skills(session: Session, user_id: str) -> list[ActiveSkill]:
    """
    Load all active skills available to a user, in deterministic order.

    order_by 不可省：没有它时返回顺序由数据库堆序决定，PostgreSQL 上任何一次 UPDATE
    都会把元组重写到堆尾，目录顺序会在用户编辑技能后无声漂移，破坏 prompt 缓存。
    """
    skills: list[ActiveSkill] = []

    user_stmt = select(UserSkill).where(
        UserSkill.user_id == user_id,
        UserSkill.is_active,
    ).order_by(UserSkill.created_at, UserSkill.id)
    for skill in session.exec(user_stmt).all():
        skills.append(ActiveSkill(
            id=skill.id,
            name=skill.name,
            description=skill.description or "",
            instructions=skill.instructions or "",
            source="user",
            triggers=parse_skill_triggers(skill.triggers),
        ))

    added_stmt = select(UserAddedSkill, PublicSkill).join(
        PublicSkill, UserAddedSkill.public_skill_id == PublicSkill.id
    ).where(
        UserAddedSkill.user_id == user_id,
        UserAddedSkill.is_active,
        PublicSkill.status == "approved",
    ).order_by(UserAddedSkill.added_at, UserAddedSkill.id)
    for added, public in session.exec(added_stmt).all():
        # 公共技能的 tags 列承载触发词（与 api/skills.py 及 skill_package_service.py 一致）
        skills.append(ActiveSkill(
            id=public.id,
            name=added.custom_name or public.name,
            description=public.description or "",
            instructions=public.instructions or "",
            source="added",
            added_id=added.id,
            triggers=parse_skill_triggers(public.tags),
            public_source=public.source,
            has_custom_name=bool(added.custom_name),
        ))

    return skills


def skill_name_key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def duplicate_name_keys(skills: list[ActiveSkill], *, language: str | None = None) -> set[str]:
    """名称（忽略大小写与空白差异）出现不止一次的键集合。"""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for skill in skills:
        display_name = skill.localized_display(language)[0] if language else skill.name
        key = skill_name_key(display_name)
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    return duplicates


def same_name_skills(skills: list[ActiveSkill], skill: ActiveSkill) -> list[ActiveSkill]:
    """与 ``skill`` 同名（忽略大小写与空白差异）的其他技能。"""
    key = skill_name_key(skill.name)
    return [other for other in skills if other.id != skill.id and skill_name_key(other.name) == key]


def find_active_skill(skills: list[ActiveSkill], name_or_id: str) -> ActiveSkill | None:
    """
    按 ID 或显示名查找技能（官方技能同时支持英文官方展示名）。

    先匹配 ID（技能目录给同名技能标注了 id，模型据此区分），再精确匹配名称，
    最后忽略大小写/首尾空白匹配名称或官方技能的英文展示名；列表本身已按「自建 > 已添加」排序，
    只给名称且同名时自建技能胜出（调用方应提示还有同名技能）。
    """
    query = (name_or_id or "").strip()
    if not query:
        return None

    for skill in skills:
        if query in (skill.id, skill.added_id):
            return skill

    for skill in skills:
        if skill.name == query:
            return skill

    folded = skill_name_key(query)
    for skill in skills:
        if skill_name_key(skill.name) == folded:
            return skill

    for skill in skills:
        if skill.is_official and not skill.has_custom_name:
            en_name, _ = skill.localized_display("en")
            if en_name and skill_name_key(en_name) == folded:
                return skill

    return None


def suggest_skill_names(skills: list[ActiveSkill], name: str, limit: int = 10) -> list[str]:
    """找不到技能时给模型的候选名：优先相近名称，没有相近的就列出前若干个。"""
    names: list[str] = []
    for skill in skills:
        if skill.name not in names:
            names.append(skill.name)
    close = difflib.get_close_matches((name or "").strip(), names, n=limit, cutoff=0.3)
    return close or names[:limit]


def resolve_selected_skills(
    session: Session,
    user_id: str,
    skill_ids: list[str] | None,
) -> list[ActiveSkill]:
    """
    解析前端显式选择的技能 ID（UserSkill.id / UserAddedSkill.id / 已添加的 PublicSkill.id）。

    只接受属于当前用户且启用中的技能；其余 ID 静默忽略。最多 MAX_SELECTED_SKILLS 个，按请求顺序去重。
    """
    wanted = [str(item).strip() for item in (skill_ids or []) if str(item or "").strip()]
    if not wanted:
        return []

    active = load_active_skills(session, user_id)
    selected: list[ActiveSkill] = []
    seen: set[str] = set()
    for skill_id in wanted:
        match = next(
            (skill for skill in active if skill_id in (skill.id, skill.added_id)),
            None,
        )
        if match is None or match.id in seen:
            continue
        seen.add(match.id)
        selected.append(match)
        if len(selected) >= MAX_SELECTED_SKILLS:
            break
    return selected


def _resource_owner_filter(user_skill_id: str | None, public_skill_id: str | None):
    if user_skill_id:
        return SkillResource.user_skill_id == user_skill_id
    if public_skill_id:
        return SkillResource.public_skill_id == public_skill_id
    raise ValueError("Either user_skill_id or public_skill_id is required")


def list_skill_resources(
    session: Session,
    *,
    user_skill_id: str | None = None,
    public_skill_id: str | None = None,
) -> list[SkillResource]:
    """List resources of one skill, ordered by path."""
    stmt = select(SkillResource).where(
        _resource_owner_filter(user_skill_id, public_skill_id)
    ).order_by(SkillResource.path)
    return list(session.exec(stmt).all())


def get_skill_resource(
    session: Session,
    path: str,
    *,
    user_skill_id: str | None = None,
    public_skill_id: str | None = None,
) -> SkillResource | None:
    """Get one resource of a skill by path."""
    stmt = select(SkillResource).where(
        _resource_owner_filter(user_skill_id, public_skill_id),
        SkillResource.path == path,
    )
    return session.exec(stmt).first()


def list_active_skill_resources(session: Session, skill: ActiveSkill) -> list[SkillResource]:
    return list_skill_resources(
        session,
        user_skill_id=skill.user_skill_id,
        public_skill_id=skill.public_skill_id,
    )
