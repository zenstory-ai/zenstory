"""按作者这一轮的要求收窄工作流：笼统的要求先问清楚、规划没落进文件时补写、
作者已经要正文时规划师的提问不截停交接。

2026-10-09 新用户审计：
- P2-16：发「帮我优化一下」，AI 不问优化哪里，一次改了三集。
- P2-13：作者要的「前十章大纲」只贴在对话里，大纲目录里没有这份规划。
- P2-21：作者首条就要「…大纲，然后直接写第一章」，规划师按提示词在结尾问「要我按这份
  大纲开始写第一章吗？」，图把它当成在等作者回答，计划序列里的 writer 被清掉，第一章没写。
"""

from __future__ import annotations

import re
from typing import Any

from utils.title_sequence import parse_chinese_number

# --------------------------------------------------------------- 笼统的要求

# 整句只有「（帮我）优化 / 润色 / 改改（一下）」，没说改哪里、想要什么效果。
_VAGUE_EDIT_RE = re.compile(
    r"^(?:请|麻烦|你)?(?:能不能|可不可以|可以)?(?:再)?(?:帮我|帮忙|给我|替我)?(?:再)?(?:整体|全部|都)?"
    r"(?:优化|润色|修改|修|改|完善|提升|调整|打磨|改进|精修|美化|加强)"
    r"(?:一下|下|一遍|一改|改|改改)?(?:吧|呗|啊|呀|哈|吗|嘛)?$"
)
_VAGUE_STRIP_RE = re.compile(r"[\s，。！？!?,.~～、…:：;；]+")

# 带着选中文本发来的消息（service 拼进本轮用户消息）：选中的就是要改的地方。
_SELECTED_TEXT_MARKERS: tuple[str, ...] = ("选中的文本:", "Selected text:")

SCOPE_DIRECTIVE_CLARIFY_FIRST = (
    "作者这次的要求很笼统，没说改哪部分、想达到什么效果。本轮不修改任何文件（写文件工具会被拒绝），"
    "也不要交接给其他角色：先看一眼相关内容，然后用一句话问作者想改哪部分、想要什么效果，"
    "给 2–3 个具体选项（例如某一章或某一集的开头 / 台词 / 节奏 / 结尾钩子，或上一轮提过的建议），"
    "让作者能直接回复。不要先动手改、再问。"
)


def is_vague_edit_request(message: str) -> bool:
    """「帮我优化一下」「改改」「润色一下吧」这类没说改哪里、想要什么效果的整句要求。"""
    normalized = _VAGUE_STRIP_RE.sub("", message or "")
    if not normalized or len(normalized) > 16:
        return False
    return bool(_VAGUE_EDIT_RE.match(normalized))


# 上一轮收尾提问里「提议修改」的说法。
_PROPOSED_EDIT_WORDS: tuple[str, ...] = (
    "改",
    "优化",
    "润色",
    "调整",
    "精简",
    "加强",
    "打磨",
    "修一下",
    "修正",
    "重写",
    "收紧",
    "压缩",
    "完善",
)
_PROPOSAL_SPLIT_RE = re.compile(r"(?:\n|还是|或者|或是|；|;)")
_PREVIOUS_QUESTION_TAIL_LINES = 4


def previous_question_offers_one_edit(reply_text: str) -> bool:
    """上一轮 AI 收尾的提问 / 选项里，是不是只提了一处修改。

    「要我继续写第5章，还是先把第4章开头改紧凑一点？」只有一处：作者回「帮我优化一下」
    「改改」就是答应这一处，不用再问。选项里有两处以上修改（「1. 把开头改紧 2. 优化第3章
    对话」）或一处都没有（「要我继续写第5章吗？」）时，这句笼统的话没说清改哪里。
    """
    text = reply_text or ""
    close = text.lower().rfind("</file>")
    if close != -1:
        text = text[close + len("</file>"):]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    tail = "\n".join(lines[-_PREVIOUS_QUESTION_TAIL_LINES:])
    proposals = [
        part for part in _PROPOSAL_SPLIT_RE.split(tail) if any(word in part for word in _PROPOSED_EDIT_WORDS)
    ]
    return len(proposals) == 1


def has_selected_text(user_message: str) -> bool:
    return any(marker in (user_message or "") for marker in _SELECTED_TEXT_MARKERS)


def clarify_first_route() -> dict[str, Any]:
    """笼统要求的路由：writer 单独一轮、不交接、不写文件，只问清楚。"""
    return {
        "current_agent": "writer",
        "workflow_plan": "quick",
        "workflow_agents": [],
        "routing_metadata": {
            "agent_type": "writer",
            "workflow_type": "quick",
            "reason": "clarify_vague_request",
            "confidence": 1.0,
            "write_content": None,
            "read_only": False,
            "scope": "",
            "clarify_first": True,
        },
    }


# ------------------------------------------------- 规划师提问不截停已要求的正文

PLANNING_AGENTS: frozenset[str] = frozenset({"planner", "hook_designer"})

PLANNED_WRITER_AFTER_QUESTION_NOTE = (
    "规划师在结尾向作者提了问题，但作者本轮已经要求直接写正文：不要再问，"
    "按规划里推荐的（没有推荐就按第一个）方案写；在给作者的回复里用一句话说明按哪个方案写的，"
    "作者想换可以直接说。"
)

SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING = (
    "作者本轮已经要求写正文：规划 / 设计完成后直接交接 writer 写正文，"
    "结尾不要问作者要不要开始写。"
)


def planning_question_keeps_writer(
    agent_type: str | None,
    routing_metadata: dict[str, Any] | None,
    workflow_agents: list[str],
) -> bool:
    """规划类角色以提问收尾时，计划序列里的 writer 还要不要留着。

    作者本轮已经明确要正文（write_content=True）时留着：作者的话就是答案，
    再让作者回一句「写吧」要多花一条额度。write_content 为 False / None（只要规划、
    旧格式路由）时照旧清空，等作者回答。
    """
    return (
        agent_type in PLANNING_AGENTS
        and isinstance(routing_metadata, dict)
        and routing_metadata.get("write_content") is True
        and "writer" in workflow_agents
    )


PLANNED_HANDOFF_SCOPE_NOTE = (
    "上一位已经完成的部分（见已完成列表）不要重做，也不要修改刚交付的文件；"
    "只补作者要求的范围里还没完成的部分，不追加作者没要的产出（如分镜表、拍摄执行版、额外文档）。"
    "作者要的已经全部交付时，用一句话确认后直接结束。"
)


# --------------------------------------------------- 规划只贴在对话里、没落进文件

_PLAN_LINE_RE = re.compile(
    r"^\s*(?:[-*•·]|\d+[.、)）]|[（(]?\d+[)）])?\s*\**\s*第\s*(\d+|[零〇一二两三四五六七八九十百千]+)\s*([章集回幕])"
)
CHAT_PLAN_MIN_UNITS = 5
CHAT_PLAN_MIN_CHARS = 200

# 作者要的是一份规划（要落进大纲文件）：「前十章大纲」「分集规划」。
_PLAN_DELIVERABLE_WORDS: tuple[str, ...] = (
    "大纲", "细纲", "章纲", "分章", "分集", "规划", "梗概", "目录", "outline",
)
# 只想先聊聊：「前十章大纲你怎么看」「先讨论一下规划」，规划贴在对话里就够了。
_DISCUSSION_WORDS: tuple[str, ...] = (
    "聊聊", "聊一下", "聊一聊", "讨论", "说说", "商量", "怎么看", "建议", "意见", "想法", "觉得",
)
_PRODUCE_WORDS: tuple[str, ...] = ("写", "做", "出", "列", "整理", "生成", "给我", "存", "保存")


def request_asks_for_a_plan(message: str) -> bool:
    """作者这一轮是不是要一份规划交付物（大纲 / 分章 / 分集规划），而不是只想聊聊。"""
    text = re.sub(r"\s+", "", message or "").lower()
    if not any(word in text for word in _PLAN_DELIVERABLE_WORDS):
        return False
    only_discussing = any(word in text for word in _DISCUSSION_WORDS) and not any(
        word in text for word in _PRODUCE_WORDS
    )
    return not only_discussing


def chat_only_plan_units(tail_text: str) -> list[int]:
    """收尾文本里逐行列出的章 / 集号（至少 CHAT_PLAN_MIN_UNITS 个才算一份规划）。

    只看最后一个 ``</file>`` 之后的文字（写进文件的正文不算），还有没闭合的
    ``<file>`` 时不判断。
    """
    text = tail_text or ""
    lowered = text.lower()
    close = lowered.rfind("</file>")
    if close != -1:
        text = text[close + len("</file>"):]
        lowered = lowered[close + len("</file>"):]
    if "<file" in lowered:
        return []
    if len(text.strip()) < CHAT_PLAN_MIN_CHARS:
        return []
    numbers: list[int] = []
    for line in text.splitlines():
        match = _PLAN_LINE_RE.match(line)
        if not match:
            continue
        token = match.group(1)
        number = int(token) if token.isdigit() else parse_chinese_number(token.replace("两", "二"))
        if number and number not in numbers:
            numbers.append(number)
    return numbers if len(numbers) >= CHAT_PLAN_MIN_UNITS else []


def outline_covers_units(outline_contents: list[str], units: list[int]) -> bool:
    """本轮写过的大纲文件是否已经覆盖了对话里列的大部分章 / 集。"""
    if not units:
        return True
    for content in outline_contents:
        covered = 0
        for number in units:
            if re.search(rf"第\s*{number}\s*[章集回幕]", content or ""):
                covered += 1
        if covered * 5 >= len(units) * 4:
            return True
    return False


def chat_plan_correction_context(units: list[int]) -> str:
    """补写规划文件那一轮的交接说明。"""
    span = f"第{units[0]}–{units[-1]}" if len(units) > 1 else f"第{units[0]}"
    return (
        f"[系统提醒] 你刚才在对话里列出的分章 / 分集规划（{span}）还没有写进文件，"
        "作者之后在大纲目录里找不到它。请用 create_file 在大纲目录新建一个大纲文件"
        "（标题如「前十章大纲」，已有同类大纲文件时用 edit_file 补进去），"
        "再用 <file>…</file> 把这份规划完整写进去；写完只用一句话告诉作者写在哪个文件，"
        "不要在对话里再贴一遍，也不要写正文。"
    )
