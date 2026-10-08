"""
State definitions for the writing workflow.

Defines the WritingState TypedDict shared across workflow nodes.
"""

from typing import Annotated, Any, TypedDict


def merge_tool_calls(left: list, right: list) -> list:
    """Merge tool calls by appending new ones."""
    if not left:
        return right
    if not right:
        return left
    return left + right


def merge_messages(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge conversation messages by appending new entries."""
    if not left:
        return right
    if not right:
        return left
    return left + right


class ToolCall(TypedDict, total=False):
    """Represents a tool call in the workflow."""

    id: str
    name: str
    input: dict[str, Any]
    result: str | None


class AgentOutput(TypedDict, total=False):
    """Output from an agent node."""

    content: str
    thinking: str | None
    tool_calls: list[ToolCall]


class HandoffPacket(TypedDict, total=False):
    """Structured handoff payload for inter-agent collaboration."""

    target_agent: str
    reason: str
    context: str
    completed: list[str]
    todo: list[str]
    evidence: list[str]
    artifact_refs: list[str]
    overflow_backfill: list[dict[str, Any]]


class WritingState(TypedDict, total=False):
    """
    State for the writing workflow.

    This state is passed between nodes, accumulating information as the
    request is processed.

    Uses Annotated merge helpers:
    - messages: append conversation history
    - tool_calls: append tool call records

    Attributes:
        user_message: The original user message/request
        router_message: Raw user input for router intent classification (without UI decorations)
        project_id: ID of the current project
        user_id: ID of the current user
        session_id: ID of the current chat session
        system_prompt: System prompt for the LLM
        context_data: Assembled context from files, characters, etc.
        current_agent: Currently active agent (router/planner/writer/quality_reviewer)
        agent_output: Output from the current agent
        workflow_plan: Planned workflow path from router
        tool_calls: List of tool calls made during processing
        messages: Conversation history in workflow message format
    """

    # Input fields
    user_message: str
    router_message: str
    project_id: str
    user_id: str
    session_id: str | None
    generation_mode: str | None

    # Configuration
    system_prompt: str
    context_data: dict[str, Any]

    # Workflow state
    current_agent: str
    agent_output: AgentOutput | None

    # Workflow planning (from router)
    # Types: "quick" | "standard" | "full" | "hook_focus" | "review_only"
    workflow_plan: str | None  # Planned workflow path
    workflow_agents: list[str] | None  # Ordered list of agents to execute
    # 用户范围约束（只读 / 不要正文 / 交付范围），由 writing_graph 按路由结果
    # 生成，nodes.run_streaming_agent 追加到每个 agent 的系统提示末尾。
    scope_directive: str | None
    # 用户明确要求本轮不改文件：tools_adapter 对写文件工具的调用一律拒绝执行。
    read_only: bool
    # 本次请求共享的 ToolFailureBreaker（工具重复失败熔断），由 writing_graph 创建，
    # 每次 agent run 复用同一个，writer ↔ 审稿人往返不会让失败计数归零。
    tool_failure_breaker: Any
    # 本次请求共享的 RepeatReadGuard（同一文件重复读取的拦截 + 本请求读写台账），
    # 由 writing_graph 创建，跨 agent run 复用；交接时据此告诉下一个 agent 读过/改过什么。
    repeat_read_guard: Any
    # 请求级模型调用计数与预算（agent.core.run_meter.AgentRunMeter，跨 agent run 共享）
    run_meter: Any

    # Collaboration state
    next_agent: str | None  # Agent to hand off to (None = done)
    handoff_reason: str | None  # Why handing off to next agent
    handoff_packet: HandoffPacket | None  # Structured handoff payload
    iteration_count: int  # Number of agent iterations (to prevent infinite loops)

    # Accumulated state with merge strategies
    tool_calls: Annotated[list[ToolCall], merge_tool_calls]
    messages: Annotated[list[dict[str, Any]], merge_messages]
