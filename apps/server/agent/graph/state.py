"""
State definitions for the writing workflow.

Defines the WritingState TypedDict shared across workflow nodes.
"""

from typing import Any, TypedDict


class WritingState(TypedDict, total=False):
    """
    State for the writing workflow.

    This state is passed between nodes, accumulating information as the
    request is processed.

    Attributes:
        user_message: The original user message/request
        router_message: Raw user input for router intent classification (without UI decorations)
        project_id: ID of the current project
        user_id: ID of the current user
        session_id: ID of the current chat session
        system_prompt: System prompt for the LLM
        current_agent: Agent chosen by the router (planner/hook_designer/writer/quality_reviewer)
        messages: Conversation history in workflow message format
    """

    # Input fields
    user_message: str
    router_message: str
    project_id: str
    user_id: str
    session_id: str | None
    # 作者界面语言（service 规范化后的小写值，默认 "zh"）；以 "en" 开头时作者可见文案用英文。
    language: str
    generation_mode: str | None
    # Structured current-turn facts for semantic routing. Never inferred from
    # labels embedded in user text.
    has_selected_text: bool
    has_referenced_files: bool

    # Configuration
    system_prompt: str
    # 项目资料快照（agent/context/turn_snapshot.py）：放在系统提示之后、历史之前，跨轮不变。
    context_snapshot: str
    # 上一个正常结束的 agent run 的完整 SDK 输入（openai_agents/runner.py 写入）与本轮是否接着它跑。
    native_run_holder: dict[str, Any]
    continue_native_run: bool

    # Workflow state
    current_agent: str
    # 用户范围约束（只读 / 不要正文 / 交付范围），由 writing_graph 按路由结果
    # 生成，nodes.build_agent_directive 放进每个 agent 运行的系统指令（输入最后一条消息）。
    scope_directive: str | None
    length_requirements_directive: str | None
    # 用户明确要求本轮不改文件：tools_adapter 对写文件工具的调用一律拒绝执行。
    read_only: bool
    resume_from_output_truncation: bool
    previous_output_truncations: int
    # 作者的要求太笼统（「帮我优化一下」）：本轮先问清楚改哪里，写文件工具一律拒绝执行
    # （graph/author_scope.py；tools_adapter 用另一套拒绝说明）。
    clarify_first: bool
    # 作者在这部作品里的第一条消息（会话里还没有 AI 回复，见 service.FIRST_MESSAGE_ENTRIES）时
    # 为作品类型名，路由器据此按「先写再给方向」判断交付范围；其余为空字符串。
    first_message_project_label: str
    # 本次请求共享的 ToolFailureBreaker（工具重复失败熔断），由 writing_graph 创建，
    # 每次 agent run 复用同一个，writer ↔ 审稿人往返不会让失败计数归零。
    tool_failure_breaker: Any
    # 本次请求共享的 RepeatReadGuard（同一文件重复读取的拦截 + 本请求读写台账），
    # 由 writing_graph 创建，跨 agent run 复用；交接时据此告诉下一个 agent 读过/改过什么。
    repeat_read_guard: Any
    # 请求级模型调用计数与预算（agent.core.run_meter.AgentRunMeter，跨 agent run 共享）
    run_meter: Any

    # Conversation history; runner and writing_graph replace it with a new list.
    messages: list[dict[str, Any]]
