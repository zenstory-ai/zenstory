"""
Agent nodes for the writing workflow.

Implements the graph-facing streaming agent entrypoint and output evaluation
helpers. The model/tool loop is provided by agent.openai_agents.
"""

import re
import unicodedata
from collections.abc import AsyncIterator
from typing import Any, NamedTuple

from agent.core.stream_errors import classify_stream_exception, log_stream_exception
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.state import WritingState
from agent.openai_agents.runner import run_openai_agents_streaming_agent
from agent.prompts.subagents import (
    HOOK_DESIGNER_PROMPT,
    PLANNER_PROMPT,
    QUALITY_REVIEWER_PROMPT,
    WRITER_PROMPT,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

FAST_MODE_DIRECTIVE = "本轮为快速模式：写完直接结束，不要交接给 quality_reviewer 审稿。"


# =============================================================================
# Streaming Agent Implementation
# =============================================================================

async def run_streaming_agent(
    state: WritingState,
    agent_type: str,
    get_steering_messages: Any | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    Generic streaming agent that yields events in real-time.

    Args:
        state: Current workflow state
        agent_type: Type of agent (planner/writer/quality_reviewer)
        get_steering_messages: Optional async callback to retrieve steering messages

    Yields:
        StreamEvent objects for real-time streaming
    """
    # Specialized prompts from subagents.py
    specialized_prompts = {
        "planner": PLANNER_PROMPT,
        "hook_designer": HOOK_DESIGNER_PROMPT,
        "writer": WRITER_PROMPT,
        "quality_reviewer": QUALITY_REVIEWER_PROMPT,
    }

    log_with_context(logger, 20, f"Streaming {agent_type} started")

    base_prompt = state.get("system_prompt", "")

    # Combine base prompt with specialized agent prompt
    specialized = specialized_prompts.get(agent_type, "")
    if base_prompt and specialized:
        system_prompt = f"{base_prompt}\n\n## 当前角色：{agent_type}\n\n{specialized}"
    elif specialized:
        system_prompt = specialized
    else:
        system_prompt = base_prompt

    # 用户范围约束（由 writing_graph 按路由结果生成）：放在系统提示末尾，
    # 本轮每个 agent（含交接后的 writer / 审稿人）都能看到，且不会像改写
    # user_message 那样在会话历史里多出一条重复的用户消息。
    scope_directive = str(state.get("scope_directive") or "").strip()
    if scope_directive:
        system_prompt = f"{system_prompt}\n\n## 本轮用户范围约束 [最高优先级]\n\n{scope_directive}"

    # 快速模式（前端「更快出结果」）：WRITER_PROMPT 的送审规则在这一轮不适用。
    # writing_graph 也会丢弃 writer → quality_reviewer 的交接，这里先让模型知道，
    # 免得它白白调用一次 handoff_to_agent。
    if (
        str(state.get("generation_mode") or "").strip().lower() == "fast"
        and agent_type != "quality_reviewer"
    ):
        system_prompt = f"{system_prompt}\n\n## 本轮生成模式\n\n{FAST_MODE_DIRECTIVE}"

    try:
        async for event in run_openai_agents_streaming_agent(
            state=state,
            agent_type=agent_type,
            system_prompt=system_prompt,
            get_steering_messages=get_steering_messages,
        ):
            yield event

        log_with_context(logger, 20, f"Streaming {agent_type} completed")

    except Exception as e:
        info = classify_stream_exception(e)
        log_stream_exception(logger, f"Streaming {agent_type} error", e, info, agent_type=agent_type)
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data=info.as_event_data(error_type=type(e).__name__),
        )


# =============================================================================
# Clarification Detection
# =============================================================================


class ClarificationResult(NamedTuple):
    """澄清检测结果。"""
    needs_clarification: bool
    confidence: float  # 0.0 - 1.0
    reason: str


class OutputEvaluationResult(NamedTuple):
    """Structured output evaluation result."""

    complete_score: float
    clarification_score: float
    consistency_score: float
    should_complete: bool
    should_clarify: bool
    reason: str


def evaluate_agent_output(content: str, agent_type: str = "unknown") -> OutputEvaluationResult:
    """
    Evaluate output quality signals for workflow decisions.

    Priority order:
    1) Explicit completion marker ([TASK_COMPLETE])
    2) Conservative completion heuristics
    """
    _ = agent_type
    text = (content or "").strip()
    if not text:
        return OutputEvaluationResult(
            complete_score=0.0,
            clarification_score=0.0,
            consistency_score=0.0,
            should_complete=False,
            should_clarify=False,
            reason="empty_content",
        )

    lowered = text.lower()

    # Explicit [TASK_COMPLETE] marker is the ONLY completion signal.
    # Chinese substring heuristics (任务已完成, 已完成, etc.) are intentionally
    # removed: they produced false positives whenever those phrases appeared
    # mid-text.  Mirror the clarification approach: structured signal only.
    has_complete_marker = lowered.endswith("[task_complete]")
    complete_score = 1.0 if has_complete_marker else 0.0
    clarification_score = 0.0

    should_clarify = False
    should_complete = has_complete_marker and not should_clarify

    # Lightweight consistency score for observability/debugging
    consistency_score = max(0.0, 1.0 - abs(complete_score - clarification_score))

    reason = "explicit_complete_marker" if has_complete_marker else "insufficient_signal"

    return OutputEvaluationResult(
        complete_score=complete_score,
        clarification_score=clarification_score,
        consistency_score=consistency_score,
        should_complete=should_complete,
        should_clarify=should_clarify,
        reason=reason,
    )


def detect_clarification_needed(content: str, agent_type: str = "unknown") -> ClarificationResult:
    """
    Clarification must be triggered only by structured `request_clarification` tool calls.
    Text heuristics and marker fallbacks are intentionally disabled.

    Args:
        content: Agent 输出内容（保留参数用于兼容，不参与判定）
        agent_type: Agent 类型，用于特殊处理某些 agent 的输出格式

    Returns:
        ClarificationResult with needs_clarification flag, confidence, and reason
    """
    _ = content
    # quality_reviewer 的输出是审查报告，不应触发澄清检测
    if agent_type == "quality_reviewer":
        return ClarificationResult(
            needs_clarification=False,
            confidence=0.0,
            reason="quality_reviewer_exempt",
        )

    return ClarificationResult(
        needs_clarification=False,
        confidence=0.0,
        reason="structured_tool_required",
    )


# =============================================================================
# Question-to-user Detection
# =============================================================================

# 选项列表行：「- xxx」「* xxx」「• xxx」「1. xxx」「2、xxx」「(3) xxx」「（4）xxx」「A. xxx」
# 「-」「*」后必须跟空白，否则 `**加粗**` 行会被误当成列表项。
# 数字编号后的 ASCII「.」不能紧跟数字（「1.甜宠路线」算列表项，「1.5万字…」不算）；
# 字母编号后的 ASCII「.」必须跟空白（否则「e.g. …」会被当成列表项）。
# 「、」「)」「）」后都不要求空白。
_LIST_ITEM_RE = re.compile(
    r"^\s*(?:[-*•·]\s+|[(（]?\d{1,2}(?:\.(?!\d)\s*|[、)）]\s*)|[(（]?[A-Ha-h](?:\.\s+|[、)）]\s*))\S"
)
_QUESTION_MARKS = ("?", "？")


def _strip_trailing_decorations(line: str) -> str:
    """去掉行尾的空白、markdown 强调符与 emoji/符号，保留引号和括号。

    引号刻意不剥：以 `“你是谁？”` 收尾的是角色台词，不是在问用户。
    """
    chars = list(line.rstrip())
    while chars:
        ch = chars[-1]
        category = unicodedata.category(ch)
        if ch.isspace() or ch in "*_~`" or category.startswith("S") or category in {"Mn", "Cf"}:
            chars.pop()
            continue
        break
    return "".join(chars)


def ends_with_question_to_user(tail_text: str) -> bool:
    """判断 agent 最后一段对话文本是否以向用户提问收尾。

    只看「最后一次工具调用之后」的文本（调用方负责截取），并且保守判定：
    1) 最后一个非空行不是列表项，且以问号结尾；或
    2) 末尾是一段列表，且列表每一条都是问句，或列表前一行是问句
       （「你更倾向哪个方向？」+ 选项）。

    以下情况一律不算提问：文本里还有未闭合的 `<file>` 块（那是正文，不是对话）；
    问句在引号内（台词）；问号出现在中间而结尾是陈述句。
    结构化的 request_clarification 仍是首选信号；这里只用来兜住模型直接用
    文字发问、却被计划交接/自动质检门越过用户继续往下跑的情况。
    """
    text = (tail_text or "").strip()
    if not text:
        return False

    lowered = text.lower()
    if "</file>" in lowered:
        text = text[lowered.rfind("</file>") + len("</file>"):].strip()
        lowered = text.lower()
    if "<file" in lowered:
        return False

    if text.lower().endswith("[task_complete]"):
        text = text[: -len("[task_complete]")].strip()

    lines = [line for line in (raw.rstrip() for raw in text.splitlines()) if line.strip()]
    if not lines:
        return False

    def _is_question(line: str) -> bool:
        return _strip_trailing_decorations(line).endswith(_QUESTION_MARKS)

    if not _LIST_ITEM_RE.match(lines[-1]):
        return _is_question(lines[-1])

    # 末尾是列表：大纲要点里常有「悬念：谁在暗中观察？」这种以问号结尾的条目，
    # 所以不能只看最后一条。只有两种列表算在问用户：
    # 至少两条且每一条都是问句（逐条提问；只有一条时多半是要点里的设问），
    # 或引出列表的那一行是问句（提问 + 选项）。
    idx = len(lines) - 1
    while idx >= 0 and _LIST_ITEM_RE.match(lines[idx]):
        idx -= 1
    list_items = lines[idx + 1:]
    if len(list_items) >= 2 and all(_is_question(item) for item in list_items):
        return True
    return idx >= 0 and _is_question(lines[idx])


# =============================================================================
# Task Completion Detection
# =============================================================================


class TaskCompleteResult(NamedTuple):
    """任务完成检测结果。"""
    is_complete: bool
    confidence: float  # 0.0 - 1.0
    reason: str


def detect_task_complete(content: str, _agent_type: str = "unknown") -> TaskCompleteResult:
    """
    检测 Agent 输出是否标记任务完成。

    检测策略：检查输出是否以 [TASK_COMPLETE] 标记结尾。
    使用严格的末尾检测，只有标记在最后才触发。

    Args:
        content: Agent 输出内容
        agent_type: Agent 类型

    Returns:
        TaskCompleteResult with is_complete flag, confidence, and reason
    """
    evaluation = evaluate_agent_output(content, agent_type=_agent_type)
    if evaluation.should_complete:
        return TaskCompleteResult(
            is_complete=True,
            confidence=evaluation.complete_score,
            reason=evaluation.reason,
        )

    return TaskCompleteResult(
        is_complete=False,
        confidence=evaluation.complete_score,
        reason=evaluation.reason,
    )
