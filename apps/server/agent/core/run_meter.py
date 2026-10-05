"""一次 /agent/stream 请求的模型调用计数与耗时（请求级预算 + 运行摘要日志）。

writing_graph 为整条工作流建一个（或沿用 service 传入的），放进
``state["run_meter"]``；runner 在每次模型调用前（SDK 的 call_model_input_filter）
计数，在 tool_use_behavior 里检查是否已用尽，用尽即结束当前 SDK run，不再发起
下一次模型调用。service 在请求结束时把计数写进运行摘要。
"""

from __future__ import annotations

from dataclasses import dataclass

from config.agent_runtime import AGENT_RUN_MAX_MODEL_CALLS


@dataclass
class AgentRunMeter:
    max_model_calls: int = AGENT_RUN_MAX_MODEL_CALLS
    model_calls: int = 0
    agent_runs: int = 0
    llm_duration_ms: float = 0.0
    # 预算用尽且确实因此截停了一次 SDK run（区别于最后一次调用恰好自然结束）。
    budget_stopped: bool = False

    @property
    def exhausted(self) -> bool:
        return self.model_calls >= self.max_model_calls

    def record_model_call(self) -> None:
        self.model_calls += 1

    def record_agent_run(self, duration_ms: float) -> None:
        self.agent_runs += 1
        self.llm_duration_ms += max(0.0, float(duration_ms))
