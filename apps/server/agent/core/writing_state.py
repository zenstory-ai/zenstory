"""一轮写作请求的状态（service 建好后交给单循环 agent 与 openai-agents runner）。"""

from typing import Any, TypedDict


class WritingState(TypedDict, total=False):
    """
    State of one writing request.

    Attributes:
        user_message: The author's message with this turn's decorations (selected text, context, working set)
        router_message: The author's raw message, without UI decorations
        project_id: ID of the current project
        user_id: ID of the current user
        session_id: ID of the current chat session
        system_prompt: System prompt for the LLM
        messages: Conversation messages in workflow message format
    """

    # Input fields
    user_message: str
    router_message: str
    project_id: str
    user_id: str
    session_id: str | None
    # 作者界面语言（service 规范化后的小写值，默认 "zh"）；以 "en" 开头时作者可见文案用英文。
    language: str

    # Configuration
    system_prompt: str
    # 上一个正常结束的 agent run 的完整 SDK 输入（openai_agents/runner.py 写入）与本轮是否接着它跑。
    native_run_holder: dict[str, Any]
    continue_native_run: bool
    # 单循环 agent（agent/loop）：按会话重建的跨轮历史（SDK 输入项），排在本轮消息之前。
    history_items: list[dict[str, Any]]

    # Run state
    previous_output_truncations: int
    # 本次请求共享的 ToolFailureBreaker（工具重复失败熔断），每次 agent run 复用同一个。
    tool_failure_breaker: Any
    # 本次请求共享的 RepeatReadGuard（同一文件重复读取的拦截 + 本请求读写台账），跨 agent run 复用。
    repeat_read_guard: Any
    # 请求级模型调用计数与预算（agent.core.run_meter.AgentRunMeter，跨 agent run 共享）
    run_meter: Any

    # Conversation messages; the runner replaces it with a new list.
    messages: list[dict[str, Any]]
