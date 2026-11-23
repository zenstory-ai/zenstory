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
        history_items: Cross-turn history in SDK wire-format items (before this turn)
        run_items: Current turn's append-only SDK transcript (result.to_input_list())
        pending_steering: Steering messages drained during/after a run, awaiting the next run
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
    # 单循环 agent（agent/loop）：按会话重建（或 wire_prefix_cache 命中）的跨轮历史（SDK 输入项），排在本轮消息之前。
    history_items: list[dict[str, Any]]
    # 本轮最近一次正常结束的 SDK run 完整输入项（result.to_input_list()）：同轮续跑只在末尾追加，
    # 整轮干净结束后也直接交给 wire_prefix_cache 缓存。
    run_items: list[Any]
    # 运行中/边界消费到、尚未喂进下一次 SDK run 的作者插话文本。
    pending_steering: list[str]

    # Run state
    previous_output_truncations: int
    # 本次请求共享的 ToolFailureBreaker（工具重复失败熔断），每次 agent run 复用同一个。
    tool_failure_breaker: Any
    # 本次请求共享的 RepeatReadGuard（同一文件重复读取的拦截 + 本请求读写台账），跨 agent run 复用。
    repeat_read_guard: Any
    # 本次请求共享的 RepeatEditGuard（同一文件串行微调收敛护栏与刚写章节自读拦截），跨 agent run 复用。
    repeat_edit_guard: Any
    # 请求级模型调用计数与预算（agent.core.run_meter.AgentRunMeter，跨 agent run 共享）
    run_meter: Any
    # 请求级收尾状态（agent.core.round_outcome.RunOutcome，跨 agent run 共享）
    run_outcome: Any
    # 强类型请求上下文（agent.tools.tool_context.AgentRequestContext，贯穿单循环、SDK RunContextWrapper 与工具执行）
    request_context: Any
