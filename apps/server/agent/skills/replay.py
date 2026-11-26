"""
Server-side cold-replay rehydration for persisted skill tool cards.

持久化在 `ChatMessage.tool_calls` / `display_events` 里的技能工具结果是前端安全口径
（`stream_adapter._client_safe_skill_payload`）：只保留元数据，绝不落库官方技能或自定义技能正文，
也不发给浏览器。其中 `load_skill` 与 `read_skill_resource` 卡片均携带 `skill_id`（`read_skill_resource`
在 `offset > 0` 时一并保留 `offset`，并支持从配对的 `call_arguments["offset"]` 回退读取以兼容旧卡片）。

当活跃会话的 Redis `wire_prefix_cache` 冷启动/失效、需要从数据库重建跨轮历史交给模型时，
`agent.loop.history.load_loop_history` 对最近若干用户轮次内的技能工具调用本模块的
`rehydrate_skill_tool_result`，在服务端按当前用户仍启用中的技能完整回填正文（不做人为 token 截断），
避免模型因历史卡片缺失正文而白白重调 `load_skill` / `read_skill_resource`：

- 仅当 `skill_id` 仍存在于调用方传入的 `active_skills_by_id`（由 `load_active_skills` 构建，
  天然强制当前用户归属、`is_active == True` 与 `PublicSkill.status == "approved"`）时才回填。
- 技能被删除、停用、下架/驳回，资源文件已删除，或 `call_arguments` / `offset` 非法或越界时返回 `None`，
  让调用方体面回退到既有的 `_SKILL_CONTENT_NOT_KEPT` 提示。
- 本模块只服务于服务端模型历史回放，严禁用于浏览器 SSE 帧或聊天历史 HTTP 响应，
  官方技能正文对前端/API 依然保持 100% 隐藏。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from sqlmodel import Session

from .active_skills import ActiveSkill, get_skill_resource, list_active_skill_resources
from .content_budget import SKILL_BODY_PATH
from .package import normalize_resource_path


def index_active_skills(skills: Iterable[ActiveSkill]) -> dict[str, ActiveSkill]:
    """按规范 `skill.id` 建立当前启用中技能的索引表。"""
    return {skill.id: skill for skill in skills}


def _parse_call_arguments(call_arguments: Any) -> dict[str, Any] | None:
    """解析历史 `tool_calls[i]["arguments"]`（dict 或 JSON 字符串）；无效输入返回 None。"""
    if call_arguments is None:
        return {}
    if isinstance(call_arguments, dict):
        return call_arguments
    if isinstance(call_arguments, str):
        try:
            parsed = json.loads(call_arguments)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
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
    call_arguments: Any = None,
) -> dict[str, Any] | None:
    """
    将持久化的客户端安全技能结果卡片重新水合为模型可读的完整工具结果 `data` 字典。

    Args:
        active_skills_by_id: 当前用户启用中技能的索引（`{skill.id: ActiveSkill}`）。
        session: 只读查询技能资源所用的数据库会话。
        tool_name: 工具名，仅处理 `"load_skill"` 与 `"read_skill_resource"`。
        result_data: 持久化卡片中的 `result` 字典（`{skill_id, skill_name, path?, offset?, size?}`）。
        call_arguments: 可选的配对工具入参（dict 或 JSON 字符串，对应 `ChatMessage.tool_calls[i]["arguments"]`）；
            当 `read_skill_resource` 的 `result_data` 未显式携带 `offset` 时从中读取 `offset`，
            避免静默回退为 `offset=0`。

    Returns:
        水合后的完整结果字典（`truncated: False`）；无法安全水合时返回 `None`，
        调用方应回退到 `_SKILL_CONTENT_NOT_KEPT`。
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

    if tool_name == "load_skill":
        resources = [
            {"path": resource.path, "size": resource.size}
            for resource in list_active_skill_resources(session, skill)
        ]
        return {
            "skill_id": skill.id,
            "skill_name": skill.name,
            "source": skill.source,
            "instructions": skill.instructions,
            "truncated": False,
            "resources": resources,
        }

    # tool_name == "read_skill_resource"
    raw_path = result_data.get("path")
    if not isinstance(raw_path, str):
        return None
    path = normalize_resource_path(raw_path)
    if not path:
        return None

    raw_offset = result_data.get("offset")
    if raw_offset is None:
        parsed_args = _parse_call_arguments(call_arguments)
        if parsed_args is None:
            return None
        raw_offset = parsed_args.get("offset")
    offset = _parse_offset(raw_offset)
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

    rehydrated: dict[str, Any] = {
        "skill_id": skill.id,
        "skill_name": skill.name,
        "path": resolved_path,
        "content": content[offset:],
        "truncated": False,
    }
    if offset > 0:
        rehydrated["offset"] = offset
    return rehydrated
