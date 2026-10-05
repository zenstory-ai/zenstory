"""
技能正文的 token 预算。

技能正文最长 5 万字符（约 6 万 tokens），显式选择的技能会进 system prompt，被每一轮
工具迭代、每个角色重复发送；`load_skill` / `read_skill_resource` 的结果也会留在对话里
反复重发。所以一次请求里交给模型的技能内容（显式选择区块 + 技能工具返回）合计不超过
``SKILL_CONTENT_TOKEN_BUDGET``，超出部分截断，并提示模型用
``read_skill_resource(path="SKILL.md", offset=...)`` 分段读取。

token 数用 ``agent.utils.token_utils.estimate_text_tokens`` 估算，与 prompt 账本同一口径。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from agent.context.budget import truncate_text_to_tokens
from agent.utils.token_utils import estimate_text_tokens

# 一次请求里技能内容的 token 总上限（显式选择区块 + load_skill + read_skill_resource）
SKILL_CONTENT_TOKEN_BUDGET = 12_000
# 其中显式选择区块（进 system prompt）最多占用的部分，给本轮的 load_skill 留出余量
SELECTED_SKILLS_TOKEN_BUDGET = 8_000

# read_skill_resource 用这个伪路径分段读取技能正文（SKILL.md 的正文部分）
SKILL_BODY_PATH = "SKILL.md"


@dataclass(frozen=True)
class SkillTextSegment:
    """从 offset 开始、落在 token 预算内的一段文本。"""

    text: str
    offset: int
    # 还有后续内容时为下一段的起始字符偏移，读完为 None
    next_offset: int | None
    total_chars: int
    tokens: int

    @property
    def truncated(self) -> bool:
        return self.next_offset is not None


def take_segment(text: str, offset: int, max_tokens: int) -> SkillTextSegment:
    """
    从 ``offset`` 起截取不超过 ``max_tokens`` 的一段。

    截断时至少前进 1 个字符，保证模型按 next_offset 续读一定能读完。
    """
    total = len(text)
    start = min(max(int(offset or 0), 0), total)
    rest = text[start:]
    if not rest:
        return SkillTextSegment(text="", offset=start, next_offset=None, total_chars=total, tokens=0)

    rest_tokens = estimate_text_tokens(rest)
    if rest_tokens <= max_tokens:
        return SkillTextSegment(
            text=rest, offset=start, next_offset=None, total_chars=total, tokens=rest_tokens
        )
    if max_tokens <= 0:
        return SkillTextSegment(text="", offset=start, next_offset=start, total_chars=total, tokens=0)

    clipped = truncate_text_to_tokens(rest, max_tokens, suffix="")
    end = start + max(len(clipped), 1)
    piece = text[start:end]
    return SkillTextSegment(
        text=piece,
        offset=start,
        next_offset=end if end < total else None,
        total_chars=total,
        tokens=estimate_text_tokens(piece),
    )


def split_selected_budget(texts: list[str], budget: int = SELECTED_SKILLS_TOKEN_BUDGET) -> list[SkillTextSegment]:
    """
    把显式选择区块的预算分给多个技能正文（返回顺序与输入一致）。

    从最短的开始分：每个技能先拿「剩余预算 / 剩余技能数」，短技能用不完的部分留给后面的长技能。
    """
    segments: list[SkillTextSegment | None] = [None] * len(texts)
    order = sorted(range(len(texts)), key=lambda index: len(texts[index]))
    remaining = max(budget, 0)
    for position, index in enumerate(order):
        share = remaining // (len(order) - position)
        segment = take_segment(texts[index], 0, share)
        segments[index] = segment
        remaining -= segment.tokens
    return [segment for segment in segments if segment is not None]


class SkillContentBudget:
    """一次请求内共享的技能内容 token 余额（工具可能在多个工作线程里并发调用）。"""

    def __init__(self, total: int = SKILL_CONTENT_TOKEN_BUDGET, used: int = 0) -> None:
        self._total = total
        self._used = max(used, 0)
        self._lock = threading.Lock()

    @property
    def total(self) -> int:
        return self._total

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(self._total - self._used, 0)

    def take(self, text: str, offset: int = 0) -> SkillTextSegment:
        """按当前余额截取一段并记账。"""
        with self._lock:
            segment = take_segment(text, offset, max(self._total - self._used, 0))
            self._used += segment.tokens
            return segment
