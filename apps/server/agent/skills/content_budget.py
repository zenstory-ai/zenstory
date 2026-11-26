"""
单轮技能加载门禁与正文读取辅助。

技能正文与资源文件在入库/上传时已有硬上限（正文 ≤ 50,000 字符、单资源 ≤ 64 KiB、
技能包资源合计 ≤ 256 KiB），跨轮上下文体积交由轮间压缩控制，运行时不再对技能正文
或资源文件做人为 token 截断。

本模块保留核心安全护栏：单次请求内显式 `selected_skills` 与 `load_skill` / `read_skill_resource`
合计最多读取 ``MAX_LOADED_SKILLS_PER_REQUEST``（3）个不同技能（已选技能预登记进门禁后仍可继续读其资源），
防批量倾倒官方技能；同时支持 `read_skill_resource` 的向后兼容 `offset` 切片读取。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from agent.utils.token_utils import estimate_text_tokens

# 一次请求里显式 selected_skills 与 load_skill / read_skill_resource 合计最多读取的不同技能数。
# 一个任务正常用 1–2 个技能，一次读十几个是异常；已准入技能重复读取（读多个资源或按 offset 读取）不另计。
MAX_LOADED_SKILLS_PER_REQUEST = 3

# read_skill_resource 用这个伪路径读取技能正文（SKILL.md 的正文部分）
SKILL_BODY_PATH = "SKILL.md"


@dataclass(frozen=True)
class SkillTextSegment:
    """从 offset 开始的完整文本切片（不做人为 token 截断）。"""

    text: str
    offset: int
    next_offset: int | None
    total_chars: int
    tokens: int

    @property
    def truncated(self) -> bool:
        return self.next_offset is not None


def take_segment(text: str, offset: int = 0) -> SkillTextSegment:
    """从 ``offset`` 起返回剩余完整文本，不做人为 token 截断。"""
    total = len(text)
    start = min(max(int(offset or 0), 0), total)
    rest = text[start:]
    if not rest:
        return SkillTextSegment(text="", offset=start, next_offset=None, total_chars=total, tokens=0)
    return SkillTextSegment(
        text=rest,
        offset=start,
        next_offset=None,
        total_chars=total,
        tokens=estimate_text_tokens(rest),
    )


class SkillContentBudget:
    """一次请求内共享的技能加载门禁与用量统计（工具可能在多个工作线程里并发调用）。"""

    def __init__(self, used: int = 0) -> None:
        self._used = max(used, 0)
        self._loaded_skill_ids: set[str] = set()
        self._lock = threading.Lock()

    @property
    def used_tokens(self) -> int:
        with self._lock:
            return self._used

    def take(self, text: str, offset: int = 0) -> SkillTextSegment:
        """返回从 offset 起的完整文本并记录估算 token 数（不截断）。"""
        segment = take_segment(text, offset)
        with self._lock:
            self._used += segment.tokens
        return segment

    def admit_skill(self, skill_id: str) -> bool:
        """本次请求还能不能读这个技能：已读过的照常放行，新技能最多 MAX_LOADED_SKILLS_PER_REQUEST 个。"""
        with self._lock:
            if skill_id in self._loaded_skill_ids:
                return True
            if len(self._loaded_skill_ids) >= MAX_LOADED_SKILLS_PER_REQUEST:
                return False
            self._loaded_skill_ids.add(skill_id)
            return True
