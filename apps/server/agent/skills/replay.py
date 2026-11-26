"""
Server-side cold-replay rehydration for persisted skill tool cards.

持久化在 `ChatMessage.tool_calls` / `display_events` 里的技能工具结果是前端安全口径
（`stream_adapter._client_safe_skill_payload`）：只保存 `skill_id`、`skill_name`、
`path`、`offset`、`size` 等元数据，绝不落库官方技能或自定义技能正文，也不发给浏览器。

当活跃会话的 Redis `wire_prefix_cache` 冷启动/失效、需要从数据库重建跨轮历史交给模型时，
调用本模块的 `rehydrate_skill_tool_result` 可在服务端按当前用户仍启用中的技能重新回填正文，
避免模型因历史卡片缺失正文而白白重调 `load_skill` / `read_skill_resource`：

- 仅当 `skill_id` 仍存在于调用方传入的 `active_skills_by_id`（由 `load_active_skills` 构建，
  天然强制当前用户归属、`is_active == True` 与 `PublicSkill.status == "approved"`）时才回填。
- 技能被删除、停用、下架/驳回，资源文件已删除，`offset` 越界，或回放预算耗尽时返回 `None`，
  让调用方体面回退到既有的 `_SKILL_CONTENT_NOT_KEPT` 提示。
- 本模块只服务于服务端模型历史回放，严禁用于浏览器 SSE 帧或聊天历史 HTTP 响应，
  官方技能正文对前端/API 依然保持 100% 隐藏。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlmodel import Session

from .active_skills import ActiveSkill, get_skill_resource, list_active_skill_resources
from .content_budget import (
    SKILL_BODY_PATH,
    SKILL_CONTENT_TOKEN_BUDGET,
    SkillContentBudget,
    SkillTextSegment,
)
from .package import normalize_resource_path

# 冷回放整段历史可重新注入的技能内容总 token 上限（与单轮请求预算对齐）
SKILL_REPLAY_TOKEN_BUDGET = SKILL_CONTENT_TOKEN_BUDGET


def index_active_skills(skills: Iterable[ActiveSkill]) -> dict[str, ActiveSkill]:
    """按规范 `skill.id` 建立当前启用中技能的索引表。"""
    return {skill.id: skill for skill in skills}


def _segment_fields(segment: SkillTextSegment, *, skill_id: str, path: str) -> dict[str, Any]:
    """与 `mcp_tools._segment_fields` 口径一致的分段续读元信息。"""
    if not segment.truncated:
        return {"truncated": False}
    return {
        "truncated": True,
        "offset": segment.offset,
        "next_offset": segment.next_offset,
        "total_chars": segment.total_chars,
        "continue_hint": (
            f"内容过长，本次只返回了第 {segment.offset}–{segment.next_offset} 个字符（共 {segment.total_chars}）。"
            f'需要后续内容时调用 read_skill_resource(name="{skill_id}", path="{path}", '
            f"offset={segment.next_offset}) 分段读取。"
        ),
    }


def _coerce_budget(remaining_budget: SkillContentBudget | int) -> SkillContentBudget | None:
    if isinstance(remaining_budget, SkillContentBudget):
        return remaining_budget if remaining_budget.remaining > 0 else None
    if isinstance(remaining_budget, int) and not isinstance(remaining_budget, bool):
        return SkillContentBudget(total=remaining_budget) if remaining_budget > 0 else None
    return None


def _parse_offset(raw_offset: Any) -> int | None:
    if raw_offset is None:
        return 0
    if isinstance(raw_offset, bool) or not isinstance(raw_offset, int) or raw_offset < 0:
        return None
    return raw_offset


def rehydrate_skill_tool_result(
    active_skills_by_id: Mapping[str, ActiveSkill],
    session: Session,
    tool_name: str,
    result_data: Any,
    *,
    remaining_budget: SkillContentBudget | int,
) -> dict[str, Any] | None:
    """
    将持久化的客户端安全技能结果卡片重新水合为模型可读的工具结果 `data` 字典。

    Args:
        active_skills_by_id: 当前用户启用中技能的索引（`{skill.id: ActiveSkill}`）。
        session: 只读查询技能资源所用的数据库会话。
        tool_name: 工具名，仅处理 `"load_skill"` 与 `"read_skill_resource"`。
        result_data: 持久化卡片中的 `result` 字典（`{skill_id, skill_name, path?, offset?, size?}`）。
        remaining_budget: 回放共享的 `SkillContentBudget`（原地扣减余额），或单次整数 token 预算。

    Returns:
        水合后的结果字典；无法安全水合或预算耗尽时返回 `None`，调用方应回退到 `_SKILL_CONTENT_NOT_KEPT`。
    """
    if tool_name not in ("load_skill", "read_skill_resource"):
        return None
    if not isinstance(result_data, dict):
        return None

    raw_skill_id = result_data.get("skill_id")
    if not isinstance(raw_skill_id, str):
        return None
    skill_id = raw_skill_id.strip()
    if not skill_id:
        return None

    skill = active_skills_by_id.get(skill_id)
    if skill is None:
        return None

    budget = _coerce_budget(remaining_budget)
    if budget is None:
        return None

    if tool_name == "load_skill":
        segment = budget.take(skill.instructions, 0)
        resources = [
            {"path": resource.path, "size": resource.size}
            for resource in list_active_skill_resources(session, skill)
        ]
        return {
            "skill_id": skill.id,
            "skill_name": skill.name,
            "source": skill.source,
            "instructions": segment.text,
            **_segment_fields(segment, skill_id=skill.id, path=SKILL_BODY_PATH),
            "resources": resources,
        }

    # tool_name == "read_skill_resource"
    raw_path = result_data.get("path")
    if not isinstance(raw_path, str):
        return None
    path = normalize_resource_path(raw_path)
    if not path:
        return None

    offset = _parse_offset(result_data.get("offset"))
    if offset is None:
        return None

    if path == SKILL_BODY_PATH:
        content = skill.instructions
        resolved_path = SKILL_BODY_PATH
    else:
        resource = get_skill_resource(
            session,
            path,
            user_skill_id=skill.user_skill_id,
            public_skill_id=skill.public_skill_id,
        )
        if resource is None:
            return None
        content = resource.content
        resolved_path = resource.path

    if offset > len(content):
        return None

    segment = budget.take(content, offset)
    rehydrated: dict[str, Any] = {
        "skill_id": skill.id,
        "skill_name": skill.name,
        "path": resolved_path,
        "content": segment.text,
        **_segment_fields(segment, skill_id=skill.id, path=resolved_path),
    }
    if offset > 0 and "offset" not in rehydrated:
        rehydrated["offset"] = offset
    return rehydrated
