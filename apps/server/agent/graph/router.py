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
        response = await _route_with_deepseek_chat(user_message)
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
    client = get_deepseek_client()
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
    await _meter_router_call(response)
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


async def _meter_router_call(response: object) -> None:
    """Write the routing call to the usage ledger (never raises)."""
    from agent.tools.mcp_tools import ToolContext
    from models.llm_usage import LLM_USAGE_SOURCE_ROUTER
    from services.usage.llm_usage_service import (
        LLMUsageAttribution,
        record_llm_usage_async,
    )
    from utils.request_context import get_agent_run_id

    await record_llm_usage_async(
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
