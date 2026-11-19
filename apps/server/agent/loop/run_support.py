"""单循环 agent 一轮里用到的收尾说明、插话追加轮提示、正文字数与事件整理。"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from agent.constants import CONTENT_FILE_TYPES
from agent.core.author_facing_text import AgentTextShaper
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.openai_agents.events import parse_mcp_tool_payload
from agent.tools.mcp_tools import ToolContext
from utils.logger import get_logger
from utils.text_metrics import count_words

logger = get_logger(__name__)

# 插话追加轮提示：openai-agents 不支持向进行中的 run 注入消息，运行期间到达的
# steering 只能在 run 结束后补一轮才能在本次请求内生效；引导内容本身已作为
# 用户消息追加进会话历史，这里只引导模型去看它。
STEERING_FOLLOWUP_CONTEXT = (
    "[系统提醒] 用户在生成过程中发来了新的引导消息（见对话历史末尾的用户消息）。"
    "请在已完成工作的基础上按用户最新引导继续调整或补充；"
    "如果引导无需改动已完成内容，请简要回应说明。"
)


WRAP_UP_TEXT = "\n\n这一轮的时间快用完了，先停在这里；回复「继续」我接着做。"
WRAP_UP_TEXT_EN = "\n\nThis round is almost out of time, so I'm stopping here. Reply \"continue\" and I'll pick it up."
# 今天的 AI 消息已经用完（本轮已计入）：回复「继续」发不出去，只说停在哪里；恢复时间由前端额度卡给出。
WRAP_UP_TEXT_NO_MESSAGES_LEFT = "\n\n这一轮的时间快用完了，先停在这里，已经写好的内容都保留了。"
WRAP_UP_TEXT_NO_MESSAGES_LEFT_EN = "\n\nThis round is almost out of time, so I'm stopping here; everything written so far is kept."
# 正文已落库、回复停在 </file> 上且待办仅剩常规自查时撞上收尾：如实告知已保存，不发暂停卡也不叫作者回复「继续」。
WRAP_UP_SELF_CHECK_SKIPPED_TEXT = "\n\n{named}已保存；这一轮时间快到了，没有再做一轮自查。"
WRAP_UP_SELF_CHECK_SKIPPED_FALLBACK = "\n\n正文已保存；这一轮时间快到了，没有再做一轮自查。"
WRAP_UP_SELF_CHECK_SKIPPED_TEXT_EN = (
    "\n\n{named} saved; this round ran short of time before an extra self-check pass."
)
WRAP_UP_SELF_CHECK_SKIPPED_FALLBACK_EN = (
    "\n\nThe text is saved; this round ran short of time before an extra self-check pass."
)


def wrap_up_self_check_skipped_text(english: bool, titles: list[str]) -> str:
    """正文已落库、停在 </file> 上且待办仅剩常规自查时撞上收尾说明。"""
    cleaned = [t.strip() for t in titles if isinstance(t, str) and t.strip()]
    if not cleaned:
        return WRAP_UP_SELF_CHECK_SKIPPED_FALLBACK_EN if english else WRAP_UP_SELF_CHECK_SKIPPED_FALLBACK
    if english:
        named = ", ".join(f'"{t}"' for t in cleaned)
        return WRAP_UP_SELF_CHECK_SKIPPED_TEXT_EN.format(named=named)
    named = "、".join(f"《{t}》" for t in cleaned)
    return WRAP_UP_SELF_CHECK_SKIPPED_TEXT.format(named=named)


# 轮与轮之间收尾的暂停卡（与 runner 时限收尾同一张卡：limit=wall_clock，前端给「继续完成」按钮）。
# reason 进历史里的状态卡摘要，供下一轮的模型看；作者看到的卡片文案由前端 i18n 给出。
WRAP_UP_CARD_REASON = "这一轮的时间快用完了，没有再开始下一步。任务可能还没做完，回复「继续」就能接着做。"
WRAP_UP_CARD_REASON_EN = (
    "This round is almost out of time, so the next step was not started. "
    "The task may not be finished; reply \"continue\" to pick it up."
)


def _ai_messages_left_today(user_id: str) -> bool | None:
    """今天还能不能再发一条 AI 消息（每日条数，本轮已预扣）；读不到返回 None。"""
    from database import create_session
    from services.quota_service import quota_service

    try:
        with create_session() as session:
            allowed, _used, _limit = quota_service.check_ai_conversation_quota(session, user_id)
            return allowed
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Quota read for the wrap-up note failed: {type(exc).__name__}")
        return None


async def wrap_up_text(english: bool) -> str:
    """收尾说明：确知今天的消息已用完时不叫作者回复「继续」；读不到额度时保持原说明。"""
    user_id = ToolContext.get_user_id()
    messages_left = await asyncio.to_thread(_ai_messages_left_today, user_id) if user_id else None
    if messages_left is False:
        return WRAP_UP_TEXT_NO_MESSAGES_LEFT_EN if english else WRAP_UP_TEXT_NO_MESSAGES_LEFT
    return WRAP_UP_TEXT_EN if english else WRAP_UP_TEXT


# 续跑说明里附上的实际字数是程序事实（编辑器统计）；文案逐字沿用。
BODY_WORD_COUNT_HEADER = "[本轮正文字数]"
_BODY_WORD_COUNT_NOTE = (
    "以上是编辑器统计的实际字数；交接说明和对话里写的字数是写作时的估计，"
    "回复作者时只报这里的真实字数，不要为了凑近近似字数调用 edit_file 反复增删。"
)


def collect_body_word_counts(file_ids: list[str]) -> list[tuple[str, int]] | None:
    """本请求写过的正文类文件（draft / script）落库后的编辑器字数，按写入顺序。"""
    project_id = ToolContext.get_project_id()
    if not project_id or not file_ids:
        return None

    from sqlmodel import select

    from models import File

    with ToolContext.short_lived_session() as session:
        rows = session.exec(
            select(File.id, File.title, File.content).where(
                File.project_id == project_id,
                File.id.in_(file_ids),
                File.file_type.in_(CONTENT_FILE_TYPES),
                File.is_deleted.is_(False),
            )
        ).all()
    by_id = {row.id: (row.title or "", count_words(row.content or "")) for row in rows}
    return [by_id[file_id] for file_id in file_ids if file_id in by_id] or None


def read_body_tail(file_id: str, chars: int) -> tuple[str, str] | None:
    """这个文件落库后的（标题，正文最后 chars 个字符）；文件不在当前作品里时返回 None。"""
    project_id = ToolContext.get_project_id()
    if not project_id or not file_id:
        return None

    from models import File

    with ToolContext.short_lived_session() as session:
        file = session.get(File, file_id)
        if file is None or file.project_id != project_id or file.is_deleted:
            return None
        content = (file.content or "").rstrip()
        return file.title or "", content[-chars:]


def format_body_word_counts(counts: list[tuple[str, int]] | None) -> str:
    if not counts:
        return ""
    lines = "\n".join(f"- 《{title}》{words} 字" if title else f"- {words} 字" for title, words in counts)
    return f"\n\n{BODY_WORD_COUNT_HEADER}\n{lines}\n{_BODY_WORD_COUNT_NOTE}"


CUT_BODY_WORD_COUNT_HEADER = "[截断处已保存的字数]"
_CUT_BODY_WORD_COUNT_NOTE = (
    "这是截断处落库正文的字数，续写之后会变；回复作者时这一章的字数报最后一次 edit_file 结果里的 new_word_count，"
    "不要报这里的数。"
)


def format_cut_body_word_counts(counts: list[tuple[str, int]] | None) -> str:
    """被输出上限截断、待续写的正文在截断处的字数：标明是截断时的数，最终字数以续写的 edit_file 结果为准。"""
    if not counts:
        return ""
    lines = "\n".join(f"- 《{title}》{words} 字" if title else f"- {words} 字" for title, words in counts)
    return f"\n\n{CUT_BODY_WORD_COUNT_HEADER}\n{lines}\n{_CUT_BODY_WORD_COUNT_NOTE}"


def tool_result_failed(result: Any) -> bool:
    """TOOL_RESULT 事件的 result（MCP 文本载荷）是否是 status=error 的失败结果。"""
    payload = parse_mcp_tool_payload(result)
    return isinstance(payload, dict) and payload.get("status") == "error"


async def shape_agent_text(
    events: AsyncIterator[StreamEvent],
    shaper: AgentTextShaper,
) -> AsyncIterator[StreamEvent]:
    """一个 agent run 的事件流：TEXT 经 AgentTextShaper 整理后再交给循环（P3-16）。

    非文字事件（思考过程除外）结束当前这段文字：先把攒着的文字发出去（紧接着是工具调用
    且攒着的是英文过程句时丢掉），再发这个事件。
    """
    async for event in events:
        if event.type == StreamEventType.TEXT:
            text = shaper.feed(str(event.data.get("text") or ""))
            if text:
                yield StreamEvent(type=StreamEventType.TEXT, data={**event.data, "text": text})
            continue
        if event.type != StreamEventType.THINKING:
            rest = shaper.end_segment(before_tool_call=event.type == StreamEventType.TOOL_USE)
            if rest:
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": rest})
        yield event
    rest = shaper.end_segment(before_tool_call=False)
    if rest:
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": rest})
