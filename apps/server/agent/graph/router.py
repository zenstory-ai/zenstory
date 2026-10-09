"""
Router node for the writing workflow.

Uses DeepSeek's OpenAI-compatible Chat Completions API to classify user
intent and route to the appropriate writing agent.
"""

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from agent.core.deepseek_client import get_deepseek_client
from agent.graph.state import WritingState
from agent.openai_agents.events import parse_json_object
from agent.openai_agents.model import DEEPSEEK_WRITING_MODEL
from agent.prompts.subagents import ROUTER_PROMPT
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# Valid agent types for routing
AgentType = Literal["planner", "hook_designer", "writer", "quality_reviewer"]

# Workflow type definitions
WorkflowType = Literal["quick", "standard", "full", "hook_focus", "review_only"]

# Workflow type to agent sequence mapping
# 每个工作流定义了初始 Agent 之后的 Agent 序列（上限）。实际计划交接序列由
# plan_workflow_agents() 按用户范围（write_content / read_only）裁剪。
WORKFLOW_AGENTS: dict[str, list[str]] = {
    "quick": [],  # writer only (review is triggered explicitly or via auto-review gate)
    "standard": ["writer"],  # planner -> writer
    "full": ["hook_designer", "writer"],  # planner -> hook_designer -> writer
    "hook_focus": ["writer"],  # hook_designer -> writer
    "review_only": [],  # quality_reviewer only
}

# 用户范围（scope）摘要的硬上限：它会进系统提示和交接包，只需一句话。
# ROUTER_PROMPT 要求模型写在 40 字以内（提示目标）；这里按 80 字截断，
# 给模型偶尔超出留余量，同时防止异常长的输出灌进每个 agent 的系统提示。
MAX_SCOPE_CHARS = 80


class RouterDecision(BaseModel):
    """Structured routing decision with schema validation.

    write_content / read_only / scope 是对**用户本轮请求**的事实判断，
    与 workflow_type（协作路径形状）分开问：
    - write_content: 用户是否要求本轮产出/改写正文。False 时计划序列里的
      writer 会被剔除（只要大纲/人设/设定/爽点思路时不再自动写正文）；
      None 表示模型没给出（旧格式输出），沿用 workflow_type 的原始序列。
    - read_only: 用户明确要求不改文件（只回答/只分析/只看看）。为 True 时
      不安排任何计划交接，图也不会交接给有写权限的 agent。
    - scope: 用户明确限定的交付范围（如“只写第1章”），没有则为空。
    """

    agent_type: AgentType
    workflow_type: WorkflowType
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    write_content: bool | None = None
    read_only: bool = False
    scope: str = ""


def plan_workflow_agents(decision: RouterDecision) -> list[str]:
    """按用户范围裁剪 workflow_type 的计划交接序列。

    计划交接是「agent 没显式交接时也照走」的兜底路径，它一旦超出用户的
    请求范围就会凭空写出正文（只要大纲却被自动交给 writer 写章节）。
    因此只在用户确实要求写正文时保留 writer；明确只读时整条序列清空。
    """
    planned = list(WORKFLOW_AGENTS.get(decision.workflow_type, []))
    if decision.read_only:
        return []
    if decision.write_content is False:
        planned = [agent for agent in planned if agent != "writer"]
    return planned


async def router_node(state: WritingState) -> dict:
    """
    Route user message to appropriate agent based on intent.

    Uses DeepSeek Chat Completions to classify the user's intent and determine
    which agent (planner/writer/quality_reviewer) should handle the request.
    Also plans the workflow path for multi-agent collaboration.

    Args:
        state: Current workflow state containing user_message

    Returns:
        Dict with current_agent, workflow_plan, workflow_agents, routing_metadata
    """
    user_message = state.get("router_message") or state.get("user_message", "")

    log_with_context(
        logger,
        20,  # INFO
        "Router node processing message",
        message_preview=user_message[:100] if user_message else "",
    )

    if not user_message:
        log_with_context(logger, 30, "Empty user message, defaulting to writer")
        return {
            "current_agent": "writer",
            "workflow_plan": "quick",
            "workflow_agents": [],
            "routing_metadata": {
                "agent_type": "writer",
                "workflow_type": "quick",
                "reason": "empty_user_message",
                "confidence": 0.0,
            },
        }

    response: dict[str, Any] | None = None
    try:
        # Route via DeepSeek Chat Completions + tolerant JSON parser. (An SDK
        # output_type=RouterDecision path was evaluated and removed: DeepSeek
        # rejects response_format json_schema, so it could never succeed.)
        # 有上一轮助手回复时，把它的结尾和落库路由一起交给路由器（见
        # build_router_user_message）；第一轮仍只发用户原话，与改动前完全一致。
        response = await _route_with_deepseek_chat(build_router_user_message(state))
        decision = _parse_router_response(response)
        workflow_agents = plan_workflow_agents(decision)

        log_with_context(
            logger,
            20,  # INFO
            "Router determined agent and workflow",
            agent_type=decision.agent_type,
            workflow_type=decision.workflow_type,
            workflow_agents=workflow_agents,
            write_content=decision.write_content,
            read_only=decision.read_only,
            confidence=decision.confidence,
            user_message_preview=user_message[:50],
        )

        return {
            "current_agent": decision.agent_type,
            "workflow_plan": decision.workflow_type,
            "workflow_agents": workflow_agents,
            "routing_metadata": decision.model_dump(),
            # 路由这次 LLM 调用的用量随决策一起上报，由 writing_graph 放进
            # ROUTER_DECIDED 事件，最终汇入 stream_adapter 的 usage 累加器。
            "routing_usage": response.get("usage"),
        }

    except Exception as e:
        from core.error_handler import APIException
        from services.usage.cost_budget import COST_LIMIT_CODE, UNAVAILABLE_CODE
        if isinstance(e, APIException) and e.error_code in {COST_LIMIT_CODE, UNAVAILABLE_CODE}:
            raise
        log_with_context(
            logger,
            40,  # ERROR
            "Router error, defaulting to writer with quick workflow",
            error=str(e),
            error_type=type(e).__name__,
        )
        # Default to writer on error (review is triggered explicitly or via auto-review gate)
        fallback: dict[str, Any] = {
            "current_agent": "writer",
            "workflow_plan": "quick",
            "workflow_agents": [],
            "routing_metadata": {
                "agent_type": "writer",
                "workflow_type": "quick",
                "reason": "router_fallback",
                "confidence": 0.0,
            },
        }
        # 模型已经答复、只是解析失败：这次调用的用量照样上报到对话用量里。
        if response is not None and response.get("usage"):
            fallback["routing_usage"] = response["usage"]
        return fallback


def _int_usage_field(source: object, name: str) -> int:
    """读取 usage 上的整数字段，缺失/None/bool/非法一律按 0 处理。"""
    value = getattr(source, name, None)
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _extract_usage(response: object) -> dict[str, int] | None:
    """把 Chat Completions 的 usage 归一化成**规范键**。

    路由这次调用同样烧真金白银（deepseek-flash 的 reasoning token 尤其多），
    却一直没有进任何统计口径——整轮用量因此系统性偏低。

    键名必须与 openai_agents/runner.py 的 `_usage_dict_from_result` 完全一致
    （input_tokens / output_tokens / cache_read_tokens / total_tokens）：
    stream_adapter._merge_usage 是**按字面键**逐一相加的，两族键名并存时只有
    total_tokens 会被真正相加，而下游 writing_stats_service 取 input_tokens 时
    已经被 runner 填过，router 的 prompt_tokens 会被静默丢弃——于是
    total_tokens ≠ input + output + cache，计价口径自相矛盾。

    cache_read_tokens 取自 OpenAI 兼容字段 prompt_tokens_details.cached_tokens，
    它是 prompt_tokens 的**子集**；writing_stats_service 的公式是
    input * 输入价 + cache_read * 缓存价（相加），所以必须从 input_tokens 里
    把命中缓存的部分扣掉，否则同一批 token 被重复计价。
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    prompt_tokens = _int_usage_field(usage, "prompt_tokens")
    cached_tokens = _int_usage_field(
        getattr(usage, "prompt_tokens_details", None), "cached_tokens"
    )
    cached_tokens = max(0, min(cached_tokens, prompt_tokens))

    extracted = {
        "input_tokens": prompt_tokens - cached_tokens,
        "output_tokens": _int_usage_field(usage, "completion_tokens"),
        "cache_read_tokens": cached_tokens,
        "total_tokens": _int_usage_field(usage, "total_tokens"),
    }
    # 保持「没有真实用量时返回 None」的既有契约：全 0 视为没拿到 usage。
    non_zero = {key: value for key, value in extracted.items() if value}
    return non_zero or None


async def _route_with_deepseek_chat(user_message: str) -> dict[str, Any]:
    """Route using DeepSeek's OpenAI-compatible Chat Completions endpoint."""
    from agent.tools.mcp_tools import ToolContext
    from services.usage.cost_budget import budget_attribution
    from services.usage.llm_usage_service import LLMUsageAttribution

    client = get_deepseek_client()
    with budget_attribution(LLMUsageAttribution(user_id=ToolContext.get_user_id(), source="router")):
        response = await client.chat.completions.create(
            model=DEEPSEEK_WRITING_MODEL,
            messages=[
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
            # deepseek-flash is a reasoning model: chain-of-thought reasoning_tokens count
            # against completion tokens. A tight budget can be fully consumed by reasoning,
            # leaving an empty JSON answer and forcing a silent fallback to writer/quick.
            # Keep a generous budget so the short routing JSON always fits after reasoning.
            max_tokens=2048,
        )
    _meter_router_call(response)
    text = response.choices[0].message.content or ""
    return {
        "content": [
            {
                "type": "text",
                "text": text,
            }
        ],
        "usage": _extract_usage(response),
    }


def _meter_router_call(response: object) -> None:
    """Schedule the routing call's ledger write in the background (never raises).

    Routing sits in front of every writing turn; the write must not add a
    database round trip to the time before the first token.
    """
    from agent.tools.mcp_tools import ToolContext
    from models.llm_usage import LLM_USAGE_SOURCE_ROUTER
    from services.usage.llm_usage_service import (
        LLMUsageAttribution,
        schedule_llm_usage_record,
    )
    from utils.request_context import get_agent_run_id

    schedule_llm_usage_record(
        LLMUsageAttribution(
            user_id=ToolContext.get_user_id(),
            source=LLM_USAGE_SOURCE_ROUTER,
            project_id=ToolContext.get_project_id(),
            correlation_id=get_agent_run_id(),
        ),
        model=DEEPSEEK_WRITING_MODEL,
        usage=getattr(response, "usage", None),
    )


def _parse_router_response(response: dict) -> RouterDecision:
    """
    Parse and validate structured router decision from model response.

    Preferred format is strict JSON, with backward-compatible fallback to
    legacy two-line text format.
    """
    content_blocks = response.get("content", [])

    parse_errors: list[str] = []
    for block in content_blocks:
        if block.get("type") != "text":
            continue

        text = block.get("text", "").strip()
        payload = _extract_router_payload(text)
        normalized = _normalize_router_payload(payload)

        try:
            return RouterDecision.model_validate(normalized)
        except ValidationError as e:
            parse_errors.append(str(e))
            continue

    if parse_errors:
        raise ValueError(f"Invalid router response schema: {'; '.join(parse_errors)}")

    # No parseable text block
    return RouterDecision(
        agent_type="writer",
        workflow_type="quick",
        reason="empty_router_response",
        confidence=0.0,
    )


_ROUTER_KEYS: frozenset[str] = frozenset(
    {"agent_type", "agent", "target_agent", "workflow_type", "workflow", "workflow_plan"}
)


def _extract_router_payload(text: str) -> dict[str, object]:
    """
    Extract payload from router raw text.

    Parsing order:
    1) Strict JSON (fast path — no repair needed)
    2) Shared JSON-repair via parse_json_object (handles markdown fences, truncation, etc.)
       If the repaired result lacks routing keys, scan per-fragment for a better match.
    3) Legacy two-line text fallback (agent\\nworkflow)
    """
    if not text:
        return {}

    # 1) strict JSON
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 2) Shared JSON-repair path (delegates to json_repair library when available).
    #    If parse_json_object succeeds and the result has routing keys, use it.
    #    Otherwise scan per-fragment (raw_decode at each '{') to prefer a fragment
    #    that contains routing keys — this handles text with multiple JSON objects
    #    where json_repair returns the first/wrong one (or a non-dict like a list).
    repaired, _err, _meta = parse_json_object(text, tool_name="router")
    if repaired and any(key in repaired for key in _ROUTER_KEYS):
        return repaired

    # Per-fragment scan: prefer any fragment with routing keys, keep first as fallback.
    fallback_object: dict[str, object] | None = repaired if repaired else None
    for candidate in _iter_json_object_candidates(text):
        try:
            candidate_obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(candidate_obj, dict):
            continue
        if any(key in candidate_obj for key in _ROUTER_KEYS):
            return candidate_obj
        if fallback_object is None:
            fallback_object = candidate_obj

    if fallback_object is not None:
        return fallback_object

    # 3) Legacy two-line fallback: line1 agent, line2 workflow
    lowered = text.lower()
    lines = [line.strip() for line in lowered.split("\n") if line.strip()]
    payload: dict[str, object] = {}
    if lines:
        payload["agent_type"] = lines[0]
    if len(lines) > 1:
        payload["workflow_type"] = lines[1]
    return payload


def _iter_json_object_candidates(text: str) -> list[str]:
    """
    Extract JSON object candidates from free-form text.

    Uses JSONDecoder.raw_decode at every '{' start index to avoid greedy regex
    over-capturing when text contains multiple objects.
    """
    decoder = json.JSONDecoder()
    candidates: list[str] = []

    for match in re.finditer(r"\{", text):
        start = match.start()
        try:
            parsed_obj, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed_obj, dict):
            candidates.append(text[start:start + end])
    return candidates


def _normalize_router_payload(payload: dict[str, object]) -> dict[str, object]:
    """Normalize router payload into RouterDecision fields."""
    agent_raw = str(
        payload.get("agent_type")
        or payload.get("agent")
        or payload.get("target_agent")
        or "writer"
    ).strip().lower()

    agent_type: AgentType = "writer"
    if "planner" in agent_raw:
        agent_type = "planner"
    elif "hook_designer" in agent_raw:
        agent_type = "hook_designer"
    elif "quality_reviewer" in agent_raw:
        agent_type = "quality_reviewer"
    elif "writer" in agent_raw:
        agent_type = "writer"

    workflow_raw = str(
        payload.get("workflow_type")
        or payload.get("workflow")
        or payload.get("workflow_plan")
        or ""
    ).strip().lower()
    if not workflow_raw:
        workflow_raw = _infer_workflow_from_agent(agent_type)

    workflow_type: WorkflowType = "quick"
    if "full" in workflow_raw:
        workflow_type = "full"
    elif "standard" in workflow_raw:
        workflow_type = "standard"
    elif "hook_focus" in workflow_raw:
        workflow_type = "hook_focus"
    elif "review_only" in workflow_raw:
        workflow_type = "review_only"
    elif "quick" in workflow_raw:
        workflow_type = "quick"

    confidence_raw = payload.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reason = str(payload.get("reason", "")).strip()

    scope_raw = payload.get("scope")
    scope = str(scope_raw).strip() if isinstance(scope_raw, str) else ""
    scope = scope[:MAX_SCOPE_CHARS]

    return {
        "agent_type": agent_type,
        "workflow_type": workflow_type,
        "reason": reason,
        "confidence": confidence,
        "write_content": _coerce_optional_bool(payload.get("write_content")),
        # read_only 只认明确的 true：判断不出时按「可以写」处理，
        # 由 write_content 和各 agent 提示词去约束范围。
        "read_only": _coerce_optional_bool(payload.get("read_only")) is True,
        "scope": scope,
    }


_TRUE_STRINGS = frozenset({"true", "yes", "y", "1", "是", "对"})
_FALSE_STRINGS = frozenset({"false", "no", "n", "0", "否", "不"})


def _coerce_optional_bool(value: object) -> bool | None:
    """宽松解析布尔字段；无法识别（缺失/null/乱写）时返回 None。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    return None


def _infer_workflow_from_agent(agent_type: AgentType) -> WorkflowType:
    """Infer default workflow type based on initial agent."""
    workflow_map = {
        "planner": "standard",  # planner -> writer -> quality_reviewer
        "hook_designer": "hook_focus",  # hook_designer -> writer -> quality_reviewer
        "writer": "quick",  # writer -> quality_reviewer
        "quality_reviewer": "review_only",  # quality_reviewer only
    }
    return workflow_map.get(agent_type, "review_only")


def get_next_node(state: WritingState) -> AgentType:
    """
    Resolve the next agent from current state.

    Returns the next node based on current_agent in state.
    """
    agent = state.get("current_agent", "writer")

    if agent in ("planner", "hook_designer", "writer", "quality_reviewer"):
        return agent  # type: ignore[return-value]

    return "writer"




# =============================================================================
# 跨轮路由延续：「继续」直达上一个 agent / 回答提问时沿用上一轮路由
# =============================================================================
#
# 上一轮的路由结果由 service 存进 assistant 消息的 message_metadata.routing
# （initial_agent / workflow_type / read_only / write_content / scope / last_agent），
# session_loader 把它连同状态卡里的 lastAgent、是否以澄清卡收尾一起放进历史 dict：
#   history["routing"]               -> dict
#   history["last_agent"]            -> str（状态卡 lastAgent）
#   history["clarification_pending"] -> bool（workflow_stopped + clarification_needed）

_AGENT_TYPES: tuple[str, ...] = ("planner", "hook_designer", "writer", "quality_reviewer")

# 上一条 assistant 消息落库的 stop_reason：表示那一轮被上限或时限截停、没做完。
# - max_turns_exceeded（runner.MAX_TURNS_STOP_REASON）/ no_progress（repeat_read_guard）
# - model_call_budget_exhausted（runner.MODEL_CALL_BUDGET_STOP_REASON，请求级调用预算用完）
# - run_deadline_exceeded（service 在墙钟时限取消时随部分历史落库）
# 用户主动停止生成（cancelled）不算：那是用户自己的决定，「继续」照常走路由。
RESUMABLE_STOP_REASONS: frozenset[str] = frozenset(
    {
        "max_turns_exceeded",
        "no_progress",
        "model_call_budget_exhausted",
        "run_deadline_exceeded",
    }
)

# 「继续」类的简短跟进。前端「继续」按钮预填的文案改成了「继续」/「continue」，
# 旧版前端（以及历史里）预填的长句仍要认：Vercel 先上线、Railway 还没跟上的
# 那段时间里，旧按钮发出的「继续」不能被重新路由。
_CONTINUE_MESSAGES: frozenset[str] = frozenset(
    {
        "继续",
        "继续吧",
        "请继续",
        "继续写",
        "接着写",
        "接着",
        "接着来",
        "继续完成",
        "go on",
        "continue",
        "please continue",
        "请基于上一步结果继续完成剩余任务，优先最关键目标",
        "please continue from the previous result and finish the remaining work, "
        "prioritizing the most critical goal",
    }
)
_CONTINUE_TRAILING_PUNCT = " \t\r\n。.!！~～…,，"
_LAST_AGENT_RE = re.compile(r"last_agent:\s*([a-z_]+)")

# 回答澄清 / 确认上一轮提问时，回复不超过这个长度才直接沿用上一轮路由；
# 更长的回复多半带了新要求，交给 LLM 路由重新判断。
CLARIFICATION_REPLY_MAX_CHARS = 40

# 用户回复里出现这些明确的收窄说法时不沿用上一轮路由（交给 LLM 路由按原话判断）。
_NARROWING_MARKERS: tuple[str, ...] = (
    "先别写",
    "别写正文",
    "不要正文",
    "不写正文",
    "只要大纲",
    "不用写",
    "先不写",
    "don't write",
    "do not write",
    "no prose",
    "outline only",
    "only the outline",
)

# 只读 / 审查意图：出现就不沿用（澄清卡与问句收尾两种情况都适用）。沿用写作路由会让
# writer 带着 create/edit/delete 工具接手，用户说了「只审不改」也照样改稿；
# 交给 LLM 路由才能判成 quality_reviewer + review_only 并挂上只读护栏。
_READ_ONLY_INTENT_MARKERS: tuple[str, ...] = (
    "不改",
    "不要改",
    "别改",
    "不用改",
    "先别动",
    "不要动",
    "只看",
    "只帮我看",
    "看看",
    "审",
    "检查",
    "评估",
    "有没有问题",
    "挑毛病",
    "追更指数",
    "review",
    "check",
    "proofread",
    "evaluate",
    "critique",
    "feedback",
    "don't change",
    "do not change",
    "don't edit",
    "do not edit",
    "no changes",
    "read only",
    "read-only",
    "just look",
)

# 换成规划类任务：只在「问句收尾」时生效。上一轮多半是 writer 写完问下一步，
# 用户转去要大纲 / 人设 / 设定时应重新选 agent。澄清卡不适用——planner 问
# 「人设按哪版来？」时，回答里带「人设」是正常作答，应当沿用。
_PLANNING_INTENT_MARKERS: tuple[str, ...] = (
    "大纲",
    "细纲",
    "规划",
    "人设",
    "设定",
    "世界观",
    "outline",
    "planning",
    "character profile",
    "worldbuilding",
    "world-building",
)

# session_loader 追加到 assistant 正文后的合成段落（工具面包屑 / 状态卡摘要）：
# 判断「上一轮是否以提问收尾」时必须先剥掉，否则结尾永远是面包屑。
_SYNTHESIZED_SECTION_HEADERS: tuple[str, ...] = (
    "[此前的工具操作]",
    "[Previous tool actions]",
    "[workflow_stopped]",
    "[iteration_exhausted]",
)


def _is_bare_continue(message: str) -> bool:
    normalized = (message or "").strip().lower().rstrip(_CONTINUE_TRAILING_PUNCT).strip()
    return normalized in _CONTINUE_MESSAGES


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _reply_text(content: Any) -> str:
    """assistant 消息里模型自己说的话：去掉 session_loader 追加的合成段落。"""
    if isinstance(content, list):
        text = "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and not str(block.get("text") or "").lstrip().startswith(_SYNTHESIZED_SECTION_HEADERS)
        )
    else:
        text = _message_text(content)
    cut = len(text)
    for header in _SYNTHESIZED_SECTION_HEADERS:
        index = text.find(header)
        if index != -1:
            cut = min(cut, index)
    return text[:cut].strip()


def _last_assistant_message(messages: list[Any]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            return message
    return None


def _valid_agent(value: Any) -> str | None:
    agent = str(value or "").strip()
    return agent if agent in _AGENT_TYPES else None


def _previous_routing(previous: dict[str, Any]) -> dict[str, Any]:
    routing = previous.get("routing")
    return routing if isinstance(routing, dict) else {}


def _inherited_scope_fields(routing: dict[str, Any], *, keep_scope: bool = True) -> dict[str, Any]:
    """上一轮的用户范围字段（write_content / read_only / scope），按 RouterDecision 的口径规整。"""
    write_content = routing.get("write_content")
    scope_raw = routing.get("scope") if keep_scope else ""
    scope = scope_raw.strip()[:MAX_SCOPE_CHARS] if isinstance(scope_raw, str) else ""
    return {
        "write_content": write_content if isinstance(write_content, bool) else None,
        "read_only": routing.get("read_only") is True,
        "scope": scope,
    }


def resume_route_after_exhaustion(state: WritingState) -> dict[str, Any] | None:
    """上一轮被上限截停、用户只回「继续」时，直接交给上一轮的 agent 走 quick 工作流。

    之前「继续」会重新走一遍 LLM 路由，常被规划成 planner → writer 的多 agent 流程，
    每个 agent 又从头把文件读一遍——线上就是这样连着两次读到轮数上限的。

    判据：上一条 assistant 消息的 stop_reason 属于 RESUMABLE_STOP_REASONS，或它只有
    状态卡、合成文本里带着 ``[iteration_exhausted]`` + ``layer: tool_call``（旧数据）。

    上一轮的 agent 依次取：状态卡的 lastAgent（history["last_agent"]；旧数据从合成
    文本的 ``last_agent:`` 里取）→ 落库路由的 last_agent → 落库路由的 initial_agent →
    writer。上一轮的 read_only / scope / write_content 原样带过来：只读审查被截停后
    说「继续」，恢复的仍是只读的审稿人，而不是拿着写工具的 writer。
    返回 None 表示照常路由。
    """
    user_message = str(state.get("router_message") or state.get("user_message") or "")
    if not _is_bare_continue(user_message):
        return None
    previous = _last_assistant_message(list(state.get("messages") or []))
    if previous is None:
        return None

    text = _message_text(previous.get("content"))
    stop_reason = str(previous.get("stop_reason") or "")
    exhausted_card = "[iteration_exhausted]" in text and "layer: tool_call" in text
    if stop_reason not in RESUMABLE_STOP_REASONS and not exhausted_card:
        return None

    routing = _previous_routing(previous)
    match = _LAST_AGENT_RE.search(text)
    agent_type = (
        _valid_agent(previous.get("last_agent"))
        or _valid_agent(match.group(1) if match else None)
        or _valid_agent(routing.get("last_agent"))
        or _valid_agent(routing.get("initial_agent"))
        or "writer"
    )

    workflow_type = "review_only" if agent_type == "quality_reviewer" else "quick"
    scope_fields = _inherited_scope_fields(routing)
    log_with_context(
        logger,
        20,  # INFO
        "Resuming exhausted run with the previous agent (router skipped)",
        agent_type=agent_type,
        previous_stop_reason=stop_reason or None,
        read_only=scope_fields["read_only"],
        has_scope=bool(scope_fields["scope"]),
    )
    return {
        "current_agent": agent_type,
        "workflow_plan": workflow_type,
        "workflow_agents": [],
        "routing_metadata": {
            "agent_type": agent_type,
            "workflow_type": workflow_type,
            "reason": "resume_after_exhaustion",
            "confidence": 1.0,
            **scope_fields,
        },
    }


def _ended_with_question(previous: dict[str, Any]) -> bool:
    # 延迟导入：nodes 依赖 runner，router 会被轻量测试单独导入。
    from agent.graph.nodes import ends_with_question_to_user

    return ends_with_question_to_user(_reply_text(previous.get("content")))


def inherit_routing_after_clarification(state: WritingState) -> dict[str, Any] | None:
    """上一轮以提问收尾、用户简短作答时，沿用上一轮落库的路由，不再调用 LLM 路由。

    路由器只看用户本条原话：「可以」「B」「主角叫陈默」这类回答被单独分类时，
    常被判给别的 agent，或把 write_content 判成 false（随后以最高优先级下发
    「不要写正文」）。上一轮的路由已经落库，回答它的问题时直接沿用更可靠。

    触发条件（全部满足）：
    - 上一条 assistant 消息带着落库的 routing；
    - 它以结构化澄清卡收尾（clarification_pending），或模型自己的话以问句收尾；
    - 用户本条回复不超过 CLARIFICATION_REPLY_MAX_CHARS 字，且没有「先别写 / 不要正文 /
      只要大纲 / 不用写」这类明确的收窄说法；
    - 回复里没有只读 / 审查意图（只审不改 / 别改 / 看看 / 审查 / 检查 / 有没有问题 /
      review / don't change …）——这类回复要交给 LLM 路由判成 review_only 并挂只读护栏；
    - 问句收尾时，回复里也没有转去规划的说法（大纲 / 规划 / 人设 / 设定 / outline …）。

    沿用的内容：
    - 澄清卡（任务还没做，在等补充信息）：agent、workflow、write_content、read_only、
      scope 全部沿用。
    - 只以问句收尾（多半是做完后问「要不要接着写第二章？」）：上一轮若是只读或不要
      正文，这句问话很可能就在征求放开限制，沿用会把用户的「可以」变成禁令，所以
      交回 LLM 路由；否则沿用 agent、workflow、write_content、read_only，scope 清空——
      上一轮的范围已经交付，问的是下一步。
    返回 None 表示照常路由。
    """
    user_message = str(state.get("router_message") or state.get("user_message") or "").strip()
    if not user_message or len(user_message) > CLARIFICATION_REPLY_MAX_CHARS:
        return None
    lowered = user_message.lower()
    if any(marker in lowered for marker in _NARROWING_MARKERS + _READ_ONLY_INTENT_MARKERS):
        return None

    previous = _last_assistant_message(list(state.get("messages") or []))
    if previous is None:
        return None
    routing = _previous_routing(previous)
    if not routing:
        return None

    clarification = previous.get("clarification_pending") is True
    if not clarification:
        if not _ended_with_question(previous):
            return None
        if any(marker in lowered for marker in _PLANNING_INTENT_MARKERS):
            return None
        if routing.get("read_only") is True or routing.get("write_content") is False:
            return None

    initial_agent = _valid_agent(routing.get("initial_agent"))
    agent_type = _valid_agent(routing.get("last_agent")) or initial_agent or "writer"
    previous_workflow = str(routing.get("workflow_type") or "").strip()
    if agent_type == initial_agent and previous_workflow in WORKFLOW_AGENTS:
        workflow_type = previous_workflow
    else:
        workflow_type = _infer_workflow_from_agent(agent_type)  # type: ignore[arg-type]

    decision = RouterDecision(
        agent_type=agent_type,  # type: ignore[arg-type]
        workflow_type=workflow_type,  # type: ignore[arg-type]
        reason="inherit_after_clarification" if clarification else "inherit_after_question",
        confidence=1.0,
        **_inherited_scope_fields(routing, keep_scope=clarification),
    )
    workflow_agents = plan_workflow_agents(decision)
    log_with_context(
        logger,
        20,  # INFO
        "Short reply to the previous turn's question; reusing its routing (router skipped)",
        agent_type=agent_type,
        workflow_type=workflow_type,
        clarification_card=clarification,
        read_only=decision.read_only,
        write_content=decision.write_content,
    )
    return {
        "current_agent": agent_type,
        "workflow_plan": workflow_type,
        "workflow_agents": workflow_agents,
        "routing_metadata": decision.model_dump(),
    }


# ----------------------------------------------------------- 路由器看上一轮

# 交给 LLM 路由的上一轮助手结尾：只取模型自己说的话的最后这么多字（它的提问、
# 收尾的下一步建议都在结尾）；状态卡摘要（澄清卡的问题、截停信息）另取，单独封顶。
ROUTER_PREV_TAIL_MAX_CHARS = 600
ROUTER_PREV_CARD_MAX_CHARS = 400

# 上一轮路由里交给路由器的字段（顺序即输出顺序）。
_ROUTER_PREV_ROUTING_KEYS: tuple[str, ...] = (
    "initial_agent",
    "last_agent",
    "workflow_type",
    "write_content",
    "read_only",
    "scope",
)

_STATUS_CARD_HEADERS: tuple[str, ...] = ("[workflow_stopped]", "[iteration_exhausted]")
_BREADCRUMB_HEADERS: tuple[str, ...] = ("[此前的工具操作]", "[Previous tool actions]")


def _status_card_text(previous: dict[str, Any]) -> str:
    """上一轮状态卡的合成摘要。

    session_loader 在历史 dict 上放了 ``status_card_text``（正文非空时摘要不进回放，
    只能从这里拿到）；没有这个字段时，从正文里截出合成的状态卡段落。
    """
    text = previous.get("status_card_text")
    if isinstance(text, str):
        return text.strip()
    content = _message_text(previous.get("content"))
    starts = [content.find(header) for header in _STATUS_CARD_HEADERS if header in content]
    if not starts:
        return ""
    start = min(starts)
    end = len(content)
    for header in _BREADCRUMB_HEADERS:
        index = content.find(header, start)
        if index != -1:
            end = min(end, index)
    return content[start:end].strip()


def _previous_turn_tail(previous: dict[str, Any]) -> str:
    reply = _reply_text(previous.get("content"))
    if len(reply) > ROUTER_PREV_TAIL_MAX_CHARS:
        reply = "…" + reply[-ROUTER_PREV_TAIL_MAX_CHARS:]
    card = _status_card_text(previous)
    if len(card) > ROUTER_PREV_CARD_MAX_CHARS:
        card = card[:ROUTER_PREV_CARD_MAX_CHARS] + "…"
    return "\n".join(part for part in (reply, card) if part)


def _previous_routing_summary(previous: dict[str, Any]) -> str:
    routing = dict(_previous_routing(previous))
    # 状态卡上的 lastAgent 是这一轮最后说话的 agent（会话加载时取最后一张卡）。
    card_agent = _valid_agent(previous.get("last_agent"))
    if card_agent:
        routing["last_agent"] = card_agent
    summary: dict[str, Any] = {}
    for key in _ROUTER_PREV_ROUTING_KEYS:
        value = routing.get(key)
        if key in {"write_content", "read_only"}:
            if isinstance(value, bool):
                summary[key] = value
        elif key == "scope":
            if isinstance(value, str) and value.strip():
                summary[key] = value.strip()[:MAX_SCOPE_CHARS]
        elif isinstance(value, str) and value.strip():
            summary[key] = value.strip()
    return json.dumps(summary, ensure_ascii=False) if summary else ""


def build_router_user_message(state: WritingState) -> str:
    """LLM 路由的 user 消息：上一轮助手结尾 + 上一轮路由 + 用户本轮原话。

    路由器只看用户本条原话时，「可以」「B」「隐忍吧」这类回答会被当成孤立指令重新
    分类（换 agent、把 write_content 判成 false 后以最高优先级下发「不要写正文」）。
    确定性的沿用规则（inherit_routing_after_clarification）先处理最常见的短回答，
    没命中的情况由这里把上一轮交给路由器自己判断。

    没有上一轮助手回复（或它既没有正文也没有落库路由）时原样返回用户原话：第一轮
    的路由输入与改动前完全一致。动态内容只放在 user 消息里，系统提示保持稳定前缀。
    """
    user_message = str(state.get("router_message") or state.get("user_message") or "")
    previous = _last_assistant_message(list(state.get("messages") or []))
    if previous is None:
        return user_message
    tail = _previous_turn_tail(previous)
    routing = _previous_routing_summary(previous)
    if not tail and not routing:
        return user_message
    sections: list[str] = []
    if tail:
        sections.append(f"[上一轮助手结尾]\n{tail}")
    if routing:
        sections.append(f"[上一轮路由]\n{routing}")
    sections.append(f"[用户本轮原话]\n{user_message}")
    return "\n".join(sections)
