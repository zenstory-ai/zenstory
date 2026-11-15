"""
Agent 子系统共享常量与小工具。

这里集中定义"哪些 file_type 算正文""哪些 file_type 该进文件清单"，
以及把 LLM 传来的松散布尔值收敛成真正 bool 的 `coerce_bool`。

之所以要集中：这些判断此前以字符串字面量的形式散落在 crud.py / assembler.py /
mcp_tools.py / stream_adapter.py 等多处，各处集合互不一致（有的漏 script，
有的漏 document），同一不变量在不同入口给出不同答案，正是历史缺陷的来源。
新增/修改文件类型时只改这里。
"""

from typing import Any

from models.file_model import (
    FILE_TYPE_CHARACTER,
    FILE_TYPE_DOCUMENT,
    FILE_TYPE_DRAFT,
    FILE_TYPE_LORE,
    FILE_TYPE_OUTLINE,
    FILE_TYPE_SCRIPT,
    FILE_TYPE_SNIPPET,
)

__all__ = [
    "CONTENT_FILE_TYPES",
    "INVENTORY_FILE_TYPES",
    "coerce_bool",
    "should_offload_session_work",
]


# 承载"正文"的文件类型：这些文件的 content 是要写给读者看的成稿正文，
# 因此适用整篇流式写入、覆盖保护等针对正文的规则。
# 注意 folder 不承载任何内容；outline/character/lore/snippet 是辅助资料而非正文；
# document 是默认兜底类型、语义不确定，同样不按正文对待。
CONTENT_FILE_TYPES: tuple[str, ...] = (
    FILE_TYPE_DRAFT,
    FILE_TYPE_SCRIPT,
)


# 应出现在"项目文件清单"（喂给 LLM 的 inventory 上下文）里的文件类型：
# 除 folder 之外的全部实体文件类型。
# script（短剧正文）与 document（create_file 的默认类型）此前被清单遗漏，
# 导致 Agent 看不见自己刚创建的文件而重复创建，这里必须包含。
INVENTORY_FILE_TYPES: tuple[str, ...] = (
    FILE_TYPE_OUTLINE,
    FILE_TYPE_DRAFT,
    FILE_TYPE_CHARACTER,
    FILE_TYPE_LORE,
    FILE_TYPE_SNIPPET,
    FILE_TYPE_SCRIPT,
    FILE_TYPE_DOCUMENT,
)


# 字符串形态的真值/假值字面量（比较前统一 strip + lower）
_TRUE_LITERALS = frozenset({"true", "1", "yes", "y", "on", "t"})
_FALSE_LITERALS = frozenset({"false", "0", "no", "n", "off", "f", "null", "none", ""})


def coerce_bool(value: Any, default: bool = False) -> bool:
    """把 LLM 传来的松散值转换成真正的 bool。

    为什么需要它：工具调用走的是 `strict_json_schema=False`，
    JSON Schema 里写的 `"type": "boolean"` 在运行时**没有任何约束力**，
    模型完全可能把布尔参数序列化成字符串。于是 `recursive="false"`、
    `force="0"`、`overwrite=""` 这类值会原样抵达工具函数，
    而 Python 的朴素真值判断（`if recursive:` 或 `bool("false")`）
    会把非空字符串 `"false"` / `"0"` 判成 True——语义直接反转，
    历史上造成过"只删一个文件"变成递归删除整棵子树。

    转换规则：
    - bool：原样返回。
    - int/float（不含 bool）：0 → False，非 0 → True。
    - str：strip + lower 后，命中真值字面量
      ("true"/"1"/"yes"/"y"/"on"/"t") → True；
      命中假值字面量 ("false"/"0"/"no"/"n"/"off"/"f"/"null"/"none"/"") → False；
      其余无法判定 → default。
    - None → False（视为"未提供"，等价于假）。
    - 其他类型（list/dict/对象等）无法判定 → default。

    Args:
        value: 待转换的原始值（通常直接来自 LLM 的工具参数）。
        default: 无法判定时返回的兜底值，默认 False（保守，不误触发破坏性行为）。

    Returns:
        转换后的 bool。
    """
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    if isinstance(value, (int, float)):
        # bool 已在上面返回；这里只处理真正的数字
        return value != 0

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_LITERALS:
            return True
        if normalized in _FALSE_LITERALS:
            return False
        return default

    # list/dict/自定义对象等：语义不明，不做猜测
    return default


def should_offload_session_work(session: Any) -> bool:
    """Only offload when running against PostgreSQL production-style sessions."""
    bind = session.get_bind() if hasattr(session, "get_bind") else None
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    return dialect_name == "postgresql"
