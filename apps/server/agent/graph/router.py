"""
Router node for the writing workflow.

Uses DeepSeek's OpenAI-compatible Chat Completions API to classify user
intent and route to the appropriate writing agent.
"""

import ast
import json
import re
from contextlib import suppress
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, StrictBool, ValidationError, field_validator

from agent.core import llm_failover
from agent.core.stream_errors import StreamErrorInfo, classify_stream_exception
from agent.graph.completion_evidence import author_reply_tail
from agent.graph.state import WritingState
from agent.openai_agents.events import parse_json_object
from agent.prompts.subagents import ROUTER_PROMPT
from core.error_codes import ErrorCode
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# Valid agent types for routing
AgentType = Literal["planner", "hook_designer", "writer", "quality_reviewer"]

# Workflow type definitions
WorkflowType = Literal["quick", "standard", "full", "hook_focus", "review_only"]
ArtifactFileType = Literal["outline", "draft", "script", "character", "lore", "snippet", "document"]
TurnDisposition = Literal[
    "new_or_changed_request",
    "resume_interrupted_task",
    "answer_pending_clarification",
]

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


class RouterFailure(ValueError):
    """A failed decision grants no authority; never retain model text in errors."""

    def __init__(
        self,
        category: Literal["invalid_enum", "schema", "empty_output", "truncated", "provider_failure", "declined"],
        *,
        usage: dict[str, Any] | None = None,
        error_info: StreamErrorInfo | None = None,
        failed_fields: tuple[str, ...] = (),
        parse_path: str = "not_parsed",
    ) -> None:
        super().__init__(f"Router failed: {category}")
        self.category = category
        self.usage = usage
        if error_info is None and category == "declined":
            # The model answered in prose instead of routing (in practice a content
            # refusal). Resending the same request gets the same answer, so the card
            # offers no retry; nothing ran, so the round is still refunded.
            error_info = StreamErrorInfo(ErrorCode.AGENT_REQUEST_DECLINED, False, True)
        self.error_info = error_info or StreamErrorInfo(ErrorCode.AGENT_ROUTING_FAILED, True, True)
        self.failed_fields = failed_fields
        self.parse_path = parse_path


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
    requested_file_types: list[ArtifactFileType] = Field(default_factory=list, max_length=7)
    turn_disposition: TurnDisposition | None = None
    clarify_first: StrictBool | None = None
    notes_append_allowed: StrictBool = False

    @field_validator("requested_file_types")
    @classmethod
    def _unique_file_types(cls, value: list[ArtifactFileType]) -> list[ArtifactFileType]:
        if len(value) != len(set(value)):
            raise ValueError("requested_file_types must be unique")
        return value


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
    )

    if not user_message:
        raise RouterFailure("schema")

    response: dict[str, Any] | None = None
    try:
        # Route via DeepSeek Chat Completions + validated decision parser. (An SDK
        # output_type=RouterDecision path was evaluated and removed: DeepSeek
        # rejects response_format json_schema, so it could never succeed.)
        # 有上一轮助手回复时，把它的结尾和落库路由一起交给路由器（见
        # build_router_user_message）；第一轮仍只发用户原话，与改动前完全一致。
        response = await _route_with_chat_model(build_router_user_message(state))
        base_decision = _parse_router_response(response)
        decision, resolution_metadata = _resolve_turn_decision(state, base_decision)
        resumed_agents = resolution_metadata.pop("_workflow_agents", None)
        followup_resolved = resolution_metadata.pop("_followup_resolved", False) is True
        workflow_agents = (
            []
            if decision.clarify_first is True
            else resumed_agents
            if isinstance(resumed_agents, list)
            else plan_workflow_agents(decision)
        )

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
        )

        return {
            "current_agent": decision.agent_type,
            "workflow_plan": decision.workflow_type,
            "workflow_agents": workflow_agents,
            # Server-internal fact from a successful trusted-state resolution.
            # It is deliberately outside routing_metadata so it is not emitted or persisted.
            "followup_resolved": followup_resolved,
            "routing_metadata": {
                **decision.model_dump(),
                **resolution_metadata,
            },
            # 路由这次 LLM 调用的用量随决策一起上报，由 writing_graph 放进
            # ROUTER_DECIDED 事件，最终汇入 stream_adapter 的 usage 累加器。
            "routing_usage": response.get("usage"),
        }

    except Exception as error:
        from core.error_handler import APIException
        from services.usage.cost_budget import COST_LIMIT_CODE, UNAVAILABLE_CODE

        if isinstance(error, APIException) and error.error_code in {COST_LIMIT_CODE, UNAVAILABLE_CODE}:
            raise
        failure = error if isinstance(error, RouterFailure) else RouterFailure(
            "provider_failure", error_info=classify_stream_exception(error), parse_path="provider",
        )
        # The provider already completed; retain its actual usage even if its
        # decision was rejected. Ledger metering precedes parsing as before.
        if response is not None:
            failure.usage = response.get("usage")
        log_with_context(
            logger,
            40,  # ERROR
            "Router decision failed; stopping before agent execution",
            stage="routing",
            category=failure.category,
            has_usage=bool(failure.usage),
            failed_fields=failure.failed_fields,
            parse_path=failure.parse_path,
            finish_reason=(
                response.get("finish_reason")
                if response and isinstance(response.get("finish_reason"), str)
                and response.get("finish_reason") in {"stop", "length", "content_filter", "tool_calls"}
                else "other" if response and response.get("finish_reason") else "missing"
            ),
            exc_info=False,
        )
        raise failure from None


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


async def _route_with_chat_model(user_message: str) -> dict[str, Any]:
    """Route using the configured chat model (primary, or backup on failover)."""
    from agent.tools.mcp_tools import ToolContext
    from services.usage.cost_budget import budget_attribution
    from services.usage.llm_usage_service import LLMUsageAttribution

    with budget_attribution(LLMUsageAttribution(user_id=ToolContext.get_user_id(), source="router")):
        response, provider = await llm_failover.acreate_chat_completion(
            call_site="router",
            session_id=ToolContext.get_session_id(),
            # Every attempt is written to the ledger in the background: routing sits in front of
            # every writing turn and must not add a database round trip before the first token.
            usage_attribution=_router_usage_attribution(),
            # 路由没有副作用：一边拒收这次请求（生产 10-25 MiniMax 连续 422）时让另一边试，
            # 不让作者这一轮在动笔前就停下。
            fail_over_on_rejection=True,
            messages=[
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
            # Both models reason before answering: chain-of-thought reasoning_tokens count
            # against completion tokens. A tight budget can be fully consumed by reasoning,
            # leaving an empty JSON answer (historically this silently fell back to writer/quick).
            # 2048 was hit by 4 of 686 production routing calls in three days (ledger
            # output_tokens == 2048); a truncated answer is a routing failure, so leave
            # reasoning room. Only tokens actually generated are billed.
            max_tokens=4096,
        )
    choice = response.choices[0] if response.choices else None
    text = choice.message.content or "" if choice is not None else ""
    return {
        "content": [
            {
                "type": "text",
                "text": text,
            }
        ],
        "usage": _extract_usage(response),
        "finish_reason": getattr(choice, "finish_reason", None),
    }


def _router_usage_attribution() -> Any:
    """Who the routing call is billed to, with the run id for the ledger."""
    from agent.tools.mcp_tools import ToolContext
    from models.llm_usage import LLM_USAGE_SOURCE_ROUTER
    from services.usage.llm_usage_service import LLMUsageAttribution
    from utils.request_context import get_agent_run_id

    return LLMUsageAttribution(
        user_id=ToolContext.get_user_id(),
        source=LLM_USAGE_SOURCE_ROUTER,
        project_id=ToolContext.get_project_id(),
        correlation_id=get_agent_run_id(),
    )


def _parse_router_response(response: dict) -> RouterDecision:
    """Validate known routes without replacing existing compatibility with new required fields."""
    if response.get("finish_reason") == "length":
        raise RouterFailure("truncated", failed_fields=("finish_reason",))
    blocks = response.get("content", [])
    if not isinstance(blocks, list):
        raise RouterFailure("schema", failed_fields=("content",))

    failure: RouterFailure | None = None
    for block in blocks:
        if not isinstance(block, dict) or (block.get("type") == "text" and not isinstance(block.get("text", ""), str)):
            failure = RouterFailure("schema", failed_fields=("content",))
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text", "").strip()
        if not text:
            continue
        diagnostics = {"parse_path": "unparseable"}
        try:
            payload = _extract_router_payload(text, diagnostics=diagnostics)
            agent = payload.get("agent_type") or payload.get("agent") or payload.get("target_agent")
            if not isinstance(agent, str) or not agent.strip():
                raise RouterFailure("schema", failed_fields=("agent_type",))
            workflow_keys = ("workflow_type", "workflow", "workflow_plan")
            if any(key in payload for key in workflow_keys):
                workflow = next(payload[key] for key in workflow_keys if key in payload)
                if not isinstance(workflow, str) or not workflow.strip():
                    raise RouterFailure("schema", failed_fields=("workflow_type",))
            # Preserve absent optional fields and known boolean equivalents from
            # the existing contract, but never turn an explicit unknown into a default.
            for field in ("read_only", "write_content"):
                if field in payload and _coerce_optional_bool(payload[field]) is None:
                    raise RouterFailure("schema", failed_fields=(field,))
            if "clarify_first" in payload and not isinstance(payload["clarify_first"], bool):
                raise RouterFailure("schema", failed_fields=("clarify_first",))
            if "scope" in payload and not isinstance(payload["scope"], str):
                raise RouterFailure("schema", failed_fields=("scope",))
            normalized = _normalize_router_payload(payload)
            try:
                return RouterDecision.model_validate(normalized)
            except ValidationError as error:
                errors = error.errors(include_input=False, include_context=False, include_url=False)
                fields = tuple(sorted({
                    item["loc"][0] for item in errors if item["loc"]
                    and item["loc"][0] in RouterDecision.model_fields
                }))
                invalid_enum = any(
                    item["type"] == "literal_error" and item["loc"] in {("agent_type",), ("workflow_type",)}
                    for item in errors
                )
                raise RouterFailure("invalid_enum" if invalid_enum else "schema", failed_fields=fields) from None
        except RouterFailure as error:
            error.parse_path = diagnostics["parse_path"]
            failure = error
            continue
    raise failure or RouterFailure("empty_output", failed_fields=("content",))


_ROUTER_KEYS: frozenset[str] = frozenset(
    {"agent_type", "agent", "target_agent", "workflow_type", "workflow", "workflow_plan"}
)


def _extract_router_payload(text: str, *, diagnostics: dict[str, str] | None = None) -> dict[str, object]:
    """Keep strict/complete-object, closed syntax-repair and exact two-line compatibility."""
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics["parse_path"] = "strict_json"
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    diagnostics["parse_path"] = "complete_object"
    fallback_object = None
    for candidate in _iter_json_object_candidates(text):
        parsed = json.loads(candidate)
        if any(key in parsed for key in _ROUTER_KEYS):
            return parsed
        if fallback_object is None:
            fallback_object = parsed

    # Reuse the existing repair utility for a complete object (for example
    # single quotes/trailing commas), not to complete an unfinished authority field.
    raw = text.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[1:-1]).strip()
    if raw.startswith("{") and raw.endswith("}"):
        diagnostics["parse_path"] = "json_repair"
        # Syntax completeness only; never evaluate this AST or complete a quote/value.
        try:
            complete_object = isinstance(ast.parse(raw, mode="eval").body, ast.Dict)
        except SyntaxError:
            complete_object = False
        if not complete_object:
            raise RouterFailure("schema", failed_fields=("content",))
        repaired, _error, _metadata = parse_json_object(raw, tool_name="router")
        if repaired and any(key in repaired for key in _ROUTER_KEYS):
            return repaired
    if fallback_object is not None:
        diagnostics["parse_path"] = "complete_object"
        return fallback_object

    diagnostics["parse_path"] = "two_line"
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    if len(lines) == 2 and all(_ENUM_TOKEN_RE.fullmatch(line) for line in lines):
        return {"agent_type": lines[0], "workflow_type": lines[1]}
    if "{" not in text:
        # No object at all: the model replied in prose. Live replays of the
        # production failures were content refusals; a two-line refusal used to be
        # read as two invalid enum values.
        diagnostics["parse_path"] = "prose"
        raise RouterFailure("declined", failed_fields=("content",))
    diagnostics["parse_path"] = "unparseable"
    raise RouterFailure("schema", failed_fields=("content",))


_ENUM_TOKEN_RE = re.compile(r"[a-z_]+")


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
        or ""
    ).strip().lower()

    # Values are enum tokens, not sentences containing an enum token.
    agent_type = agent_raw

    workflow_raw = str(
        payload.get("workflow_type")
        or payload.get("workflow")
        or payload.get("workflow_plan")
        or ""
    ).strip().lower()
    if not workflow_raw:
        workflow_raw = _infer_workflow_from_agent(agent_type)

    workflow_type = workflow_raw

    confidence_raw = payload.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reason = str(payload.get("reason", "")).strip()

    scope_raw = payload.get("scope")
    scope = str(scope_raw).strip() if isinstance(scope_raw, str) else ""
    turn_disposition = payload.get("turn_disposition")
    # Existing single-turn output historically clipped scope. Keep that
    # compatibility, but never clip a follow-up restriction: clipping would
    # silently grant more authority than the router actually requested.
    if turn_disposition not in {"resume_interrupted_task", "answer_pending_clarification"}:
        scope = scope[:MAX_SCOPE_CHARS]

    return {
        "agent_type": agent_type,
        "workflow_type": workflow_type,
        "reason": reason,
        "confidence": confidence,
        "write_content": _coerce_optional_bool(payload.get("write_content")),
        # 保留既有可选字段和明确等价布尔值；外部显式未知值已在 parser 拒绝。
        "read_only": _coerce_optional_bool(payload.get("read_only")) is True,
        "scope": scope,
        "requested_file_types": payload.get("requested_file_types", []),
        "turn_disposition": turn_disposition,
        "clarify_first": payload.get("clarify_first"),
        "notes_append_allowed": payload.get("notes_append_allowed", False),
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
    agent = state.get("current_agent")

    if agent in ("planner", "hook_designer", "writer", "quality_reviewer"):
        return agent  # type: ignore[return-value]

    raise RouterFailure("invalid_enum" if agent is not None else "schema")




# =============================================================================
# Cross-turn routing: semantic disposition + trusted structured state
# =============================================================================

_AGENT_TYPES: tuple[str, ...] = ("planner", "hook_designer", "writer", "quality_reviewer")

RESUMABLE_STOP_REASONS: frozenset[str] = frozenset(
    {"max_turns_exceeded", "no_progress", "model_call_budget_exhausted",
     "run_deadline_exceeded", "output_truncated"}
)
MAX_SCOPE_CONSTRAINTS = 8
MAX_SCOPE_CONSTRAINTS_JSON_CHARS = 2048


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


def has_pending_followup_state(state: WritingState) -> bool:
    """Return whether router-off mode still needs a semantic decision."""
    previous = _last_assistant_message(list(state.get("messages") or []))
    if previous is None:
        return False
    stop_reason = previous.get("stop_reason")
    return (
        isinstance(stop_reason, str) and stop_reason in RESUMABLE_STOP_REASONS
    ) or previous.get("clarification_pending") is True


def _validate_scope_constraints(constraints: list[Any], *, field: str) -> list[str]:
    if any(
        not isinstance(item, str)
        or not item.strip()
        or len(item.strip()) > MAX_SCOPE_CHARS
        for item in constraints
    ):
        raise RouterFailure("schema", failed_fields=(field,))
    normalized: list[str] = []
    for item in constraints:
        stripped = item.strip()
        if stripped not in normalized:
            normalized.append(stripped)
    if len(normalized) > MAX_SCOPE_CONSTRAINTS:
        raise RouterFailure("schema", failed_fields=(field,))
    try:
        encoded = json.dumps(normalized, ensure_ascii=False)
    except (TypeError, ValueError):
        raise RouterFailure("schema", failed_fields=(field,)) from None
    if len(encoded) > MAX_SCOPE_CONSTRAINTS_JSON_CHARS:
        raise RouterFailure("schema", failed_fields=(field,))
    return normalized


def _inherited_scope_fields(routing: dict[str, Any]) -> dict[str, Any]:
    """Validate persisted authority fields before a follow-up inherits them."""
    if "initial_agent" in routing and _valid_agent(routing.get("initial_agent")) is None:
        raise RouterFailure("schema", failed_fields=("previous_routing.initial_agent",))
    workflow = routing.get("workflow_type")
    if "workflow_type" in routing and (
        not isinstance(workflow, str) or workflow.strip() not in WORKFLOW_AGENTS
    ):
        raise RouterFailure("schema", failed_fields=("previous_routing.workflow_type",))
    for field in ("last_agent", "owner_agent"):
        if field in routing and routing[field] is not None and _valid_agent(routing[field]) is None:
            raise RouterFailure("schema", failed_fields=(f"previous_routing.{field}",))
    write_content = routing.get("write_content")
    if write_content is not None and not isinstance(write_content, bool):
        raise RouterFailure("schema", failed_fields=("previous_routing.write_content",))
    read_only = routing.get("read_only", False)
    if not isinstance(read_only, bool):
        raise RouterFailure("schema", failed_fields=("previous_routing.read_only",))
    scope = routing.get("scope", "")
    if not isinstance(scope, str):
        raise RouterFailure("schema", failed_fields=("previous_routing.scope",))
    scope = scope.strip()
    if len(scope) > MAX_SCOPE_CHARS:
        raise RouterFailure("schema", failed_fields=("previous_routing.scope",))
    requested = routing.get("requested_file_types", [])
    allowed = get_args(ArtifactFileType)
    if (
        not isinstance(requested, list)
        or len(requested) > len(allowed)
        or any(not isinstance(item, str) or item not in allowed for item in requested)
        or len(requested) != len(set(requested))
    ):
        raise RouterFailure("schema", failed_fields=("previous_routing.requested_file_types",))
    constraints = routing.get("scope_constraints", [])
    if not isinstance(constraints, list):
        raise RouterFailure("schema", failed_fields=("previous_routing.scope_constraints",))
    constraints = _validate_scope_constraints(
        constraints, field="previous_routing.scope_constraints"
    )
    return {
        "write_content": write_content,
        "read_only": read_only,
        "scope": scope,
        "requested_file_types": list(requested),
        "scope_constraints": constraints,
    }


def _accumulate_scope_constraints(
    old_scope: str, existing: list[str], current_scope: str
) -> list[str]:
    if len(current_scope) > MAX_SCOPE_CHARS:
        raise RouterFailure("schema", failed_fields=("scope",))
    accumulated: list[str] = []
    for item in (old_scope, *existing, current_scope):
        normalized = item.strip()
        if normalized and normalized not in accumulated:
            accumulated.append(normalized)
    return _validate_scope_constraints(accumulated, field="scope_constraints")


def _remaining_planned_agents(routing: dict[str, Any], agent_type: str) -> list[str]:
    initial = _valid_agent(routing.get("initial_agent"))
    workflow = str(routing.get("workflow_type") or "").strip()
    if initial is None or workflow not in WORKFLOW_AGENTS or agent_type == "quality_reviewer":
        return []
    fields = _inherited_scope_fields(routing)
    decision = RouterDecision(
        agent_type=initial,  # type: ignore[arg-type]
        workflow_type=workflow,  # type: ignore[arg-type]
        write_content=fields["write_content"],
        read_only=fields["read_only"],
        scope=fields["scope"],
        requested_file_types=fields["requested_file_types"],
    )
    planned = plan_workflow_agents(decision)
    # Match the graph's initial-stage deduplication. A resumed middle stage is
    # persisted as this round's initial agent, not a new copy of the same stage.
    while planned and planned[0] == initial:
        planned.pop(0)
    sequence = [initial, *planned]
    if agent_type not in sequence:
        return []
    return sequence[sequence.index(agent_type) + 1:]


def _is_review_task(routing: dict[str, Any]) -> bool:
    return (
        _valid_agent(routing.get("initial_agent")) == "quality_reviewer"
        or str(routing.get("workflow_type") or "").strip() == "review_only"
    )


def task_owner_agent(routing: dict[str, Any]) -> str | None:
    """Resolve the task owner from persisted routing, never from prose."""
    owner = _valid_agent(routing.get("owner_agent"))
    last = _valid_agent(routing.get("last_agent"))
    initial = _valid_agent(routing.get("initial_agent"))
    if last == "quality_reviewer" and not _is_review_task(routing):
        return owner if owner and owner != "quality_reviewer" else initial
    return last or owner or initial


def _trusted_resume_agent(previous: dict[str, Any], routing: dict[str, Any]) -> str | None:
    return (
        _valid_agent(previous.get("last_agent"))
        or _valid_agent(routing.get("last_agent"))
        or _valid_agent(routing.get("initial_agent"))
    )


def _compose_followup_write_content(old: bool | None, current: bool | None) -> bool | None:
    if old is False or current is False:
        return False
    if old is True:
        return True
    return None


def _resolve_turn_decision(
    state: WritingState, decision: RouterDecision
) -> tuple[RouterDecision, dict[str, Any]]:
    """Apply semantic disposition to trusted previous state without text heuristics."""
    disposition = decision.turn_disposition
    if disposition not in {"resume_interrupted_task", "answer_pending_clarification"}:
        constraints = _validate_scope_constraints(
            [decision.scope] if decision.scope else [], field="scope_constraints"
        )
        return decision, {"scope_constraints": constraints}

    previous = _last_assistant_message(list(state.get("messages") or []))
    if previous is None:
        log_with_context(
            logger, 20, "Router follow-up disposition has no matching state",
            turn_disposition=disposition, followup_resolution="state_mismatch",
        )
        constraints = _validate_scope_constraints(
            [decision.scope] if decision.scope else [], field="scope_constraints"
        )
        return decision, {"scope_constraints": constraints}

    routing = _previous_routing(previous)
    stop_reason = previous.get("stop_reason")
    if disposition == "resume_interrupted_task":
        matches_state = isinstance(stop_reason, str) and stop_reason in RESUMABLE_STOP_REASONS
        agent_type = _trusted_resume_agent(previous, routing) if matches_state else None
    else:
        matches_state = previous.get("clarification_pending") is True
        agent_type = task_owner_agent(routing) if matches_state else None

    if not matches_state or not routing:
        log_with_context(
            logger, 20, "Router follow-up disposition has no matching state",
            turn_disposition=disposition, followup_resolution="state_mismatch",
        )
        constraints = _validate_scope_constraints(
            [decision.scope] if decision.scope else [], field="scope_constraints"
        )
        return decision, {"scope_constraints": constraints}

    fields = _inherited_scope_fields(routing)
    if agent_type is None:
        raise RouterFailure("schema", failed_fields=("previous_routing.last_agent",))
    constraints = _accumulate_scope_constraints(
        fields["scope"], fields["scope_constraints"], decision.scope
    )
    read_only = fields["read_only"] or decision.read_only
    write_content = _compose_followup_write_content(
        fields["write_content"], decision.write_content
    )
    initial = _valid_agent(routing.get("initial_agent"))
    previous_workflow = str(routing.get("workflow_type") or "").strip()
    if disposition == "resume_interrupted_task":
        remaining = _remaining_planned_agents(routing, agent_type)
        workflow_type = (
            previous_workflow
            if remaining and previous_workflow in WORKFLOW_AGENTS
            else _infer_workflow_from_agent(agent_type)  # type: ignore[arg-type]
        )
    else:
        workflow_type = (
            previous_workflow
            if agent_type == initial and previous_workflow in WORKFLOW_AGENTS
            else _infer_workflow_from_agent(agent_type)  # type: ignore[arg-type]
        )
        remaining = []

    resolved = RouterDecision(
        agent_type=agent_type,  # type: ignore[arg-type]
        workflow_type=workflow_type,
        reason=decision.reason,
        confidence=decision.confidence,
        write_content=write_content,
        read_only=read_only,
        scope=fields["scope"],
        requested_file_types=fields["requested_file_types"],
        turn_disposition=disposition,
        clarify_first=decision.clarify_first,
        # Notes permission belongs to this request, never the interrupted task.
        notes_append_allowed=decision.notes_append_allowed,
    )
    metadata: dict[str, Any] = {
        "scope_constraints": constraints,
        "_followup_resolved": True,
    }
    if disposition == "resume_interrupted_task" and stop_reason == "output_truncated":
        metadata["previous_stop_reason"] = stop_reason
    if disposition == "resume_interrupted_task":
        metadata["_workflow_agents"] = remaining
    return resolved, metadata

# ----------------------------------------------------------- Router context

ROUTER_PREV_TAIL_MAX_CHARS = 600
_ROUTER_PREV_ROUTING_KEYS: tuple[str, ...] = (
    "initial_agent", "last_agent", "owner_agent", "workflow_type",
    "write_content", "read_only", "scope", "scope_constraints",
    "requested_file_types",
)
_SYNTHESIZED_SECTION_HEADERS: tuple[str, ...] = (
    "[此前的工具操作]",
    "[Previous tool actions]",
    "[workflow_stopped]",
    "[iteration_exhausted]",
)


def _previous_reply_tail(previous: dict[str, Any]) -> str:
    content = previous.get("content")
    if isinstance(content, list):
        text = "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    elif isinstance(content, str):
        text = content
    else:
        return ""
    cut = len(text)
    for header in _SYNTHESIZED_SECTION_HEADERS:
        index = text.find(header)
        if index != -1:
            cut = min(cut, index)
    text = text[:cut].strip()
    text = author_reply_tail(text, max_chars=max(len(text) + 1, ROUTER_PREV_TAIL_MAX_CHARS))
    if len(text) > ROUTER_PREV_TAIL_MAX_CHARS:
        return "…" + text[-ROUTER_PREV_TAIL_MAX_CHARS:]
    return text


def _previous_routing_summary(previous: dict[str, Any]) -> str:
    routing = dict(_previous_routing(previous))
    card_agent = _valid_agent(previous.get("last_agent"))
    if card_agent:
        routing["last_agent"] = card_agent
    owner = task_owner_agent(routing)
    if owner:
        routing["owner_agent"] = owner
    summary: dict[str, Any] = {}
    for key in _ROUTER_PREV_ROUTING_KEYS:
        value = routing.get(key)
        if key in {"write_content", "read_only"}:
            if isinstance(value, bool):
                summary[key] = value
        elif key == "scope":
            if isinstance(value, str) and value.strip() and len(value.strip()) <= MAX_SCOPE_CHARS:
                summary[key] = value.strip()
        elif key == "scope_constraints":
            if isinstance(value, list):
                with suppress(RouterFailure):
                    summary[key] = _validate_scope_constraints(
                        value, field="previous_routing.scope_constraints"
                    )
        elif key == "requested_file_types":
            allowed = get_args(ArtifactFileType)
            if (
                isinstance(value, list)
                and len(value) <= len(allowed)
                and all(isinstance(item, str) and item in allowed for item in value)
                and len(value) == len(set(value))
            ):
                summary[key] = value
        elif isinstance(value, str) and value.strip():
            summary[key] = value.strip()
    return json.dumps(summary, ensure_ascii=False) if summary else ""


def _previous_followup_state_summary(previous: dict[str, Any]) -> str:
    summary: dict[str, Any] = {}
    stop_reason = previous.get("stop_reason")
    if isinstance(stop_reason, str) and stop_reason in RESUMABLE_STOP_REASONS:
        summary["stop_reason"] = stop_reason
    if previous.get("clarification_pending") is True:
        summary["clarification_pending"] = True
        pending = previous.get("pending_clarification")
        if isinstance(pending, dict):
            safe_pending: dict[str, Any] = {}
            for key in ("question", "context"):
                value = pending.get(key)
                if isinstance(value, str) and value.strip():
                    safe_pending[key] = value.strip()[:400]
            details = pending.get("details")
            if isinstance(details, list):
                safe_pending["details"] = [
                    item.strip()[:160]
                    for item in details[:8]
                    if isinstance(item, str) and item.strip()
                ]
            if safe_pending:
                summary["pending_clarification"] = safe_pending
    return json.dumps(summary, ensure_ascii=False) if summary else ""


def build_router_user_message(state: WritingState) -> str:
    """Build bounded semantic context from structured state and current facts."""
    user_message = str(state.get("router_message") or state.get("user_message") or "")
    previous = _last_assistant_message(list(state.get("messages") or []))
    if previous is None:
        first_message_label = str(state.get("first_message_project_label") or "")
        if first_message_label:
            sections = [f"[新作品的第一条消息]\n作品类型：{first_message_label}"]
            if state.get("has_selected_text") is True or state.get("has_referenced_files") is True:
                facts = {
                    "has_selected_text": state.get("has_selected_text") is True,
                    "has_referenced_files": state.get("has_referenced_files") is True,
                }
                sections.append(f"[本轮上下文事实]\n{json.dumps(facts, ensure_ascii=False)}")
            sections.append(f"[用户本轮原话]\n{user_message}")
            return "\n".join(sections)
        if state.get("has_selected_text") is not True and state.get("has_referenced_files") is not True:
            return user_message
        facts = {
            "has_selected_text": state.get("has_selected_text") is True,
            "has_referenced_files": state.get("has_referenced_files") is True,
        }
        return (
            f"[本轮上下文事实]\n{json.dumps(facts, ensure_ascii=False)}\n"
            f"[用户本轮原话]\n{user_message}"
        )

    sections: list[str] = []
    tail = _previous_reply_tail(previous)
    followup = _previous_followup_state_summary(previous)
    routing = _previous_routing_summary(previous)
    if tail:
        sections.append(f"[上一轮助手结尾]\n{tail}")
    if followup:
        sections.append(f"[上一轮结构化状态]\n{followup}")
    if routing:
        sections.append(f"[上一轮路由]\n{routing}")
    if not sections and state.get("has_selected_text") is not True and state.get("has_referenced_files") is not True:
        return user_message
    facts = {
        "has_selected_text": state.get("has_selected_text") is True,
        "has_referenced_files": state.get("has_referenced_files") is True,
    }
    sections.append(f"[本轮上下文事实]\n{json.dumps(facts, ensure_ascii=False)}")
    sections.append(f"[用户本轮原话]\n{user_message}")
    return "\n".join(sections)
