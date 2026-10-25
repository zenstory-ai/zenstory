"""一次 /agent/stream 请求的模型调用计数与耗时（请求级预算 + 运行摘要日志）。

writing_graph 为整条工作流建一个（或沿用 service 传入的），放进
``state["run_meter"]``；runner 在每次模型调用前（SDK 的 call_model_input_filter）
计数，在 tool_use_behavior 里检查是否已用尽，用尽即结束当前 SDK run，不再发起
下一次模型调用。service 在请求结束时把计数写进运行摘要。
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

from config.agent_runtime import AGENT_RUN_MAX_MODEL_CALLS, AGENT_RUN_WRAP_UP_AFTER_S

from .server_drain import is_draining

TimingStage = Literal[
    "prepare_context",
    "router",
    "model",
    "tool",
    "file_save",
    "agent_planner",
    "agent_hook_designer",
    "agent_writer",
    "agent_quality_reviewer",
]
TimingOutcome = Literal["completed", "error", "interrupted"]
# 工具业务失败按 error_type 计数：只收程序定义的标识符，种类有上限，其余归 other。
_ERROR_TYPE_PATTERN = re.compile(r"[a-z0-9_]{1,48}")
_MAX_ERROR_TYPES_PER_TOOL = 8
_OTHER_ERROR_TYPE = "other"


@dataclass
class _ToolFailureStats:
    count: int = 0
    batch_count: int = 0
    error_types: dict[str, int] = field(default_factory=dict)


_ROLE_STAGES: dict[str, TimingStage] = {
    "planner": "agent_planner",
    "hook_designer": "agent_hook_designer",
    "writer": "agent_writer",
    "quality_reviewer": "agent_quality_reviewer",
}


@dataclass
class AgentRunMeter:
    max_model_calls: int = AGENT_RUN_MAX_MODEL_CALLS
    model_calls: int = 0
    agent_runs: int = 0
    llm_duration_ms: float = 0.0
    # 预算用尽且确实因此截停了一次 SDK run（区别于最后一次调用恰好自然结束）。
    budget_stopped: bool = False
    # 最近一次模型调用实际用的模型（主备切换后可能是备用供应商的模型）。
    last_served_model: str | None = None
    # 请求开始的时刻（monotonic）：到 wrap_up_after_s 就提醒收尾、不再开新的 agent run。
    started_at: float = field(default_factory=time.monotonic)
    wrap_up_after_s: float = AGENT_RUN_WRAP_UP_AFTER_S

    # Diagnostic origin does not alter the existing wrap-up budget clock.
    timing_origin: float | None = None
    first_tool_started_ms: float | None = None
    first_body_persisted_ms: float | None = None
    _stage_timings: dict[TimingStage, dict[str, float | int]] = field(default_factory=dict)
    _tool_failures: dict[str, _ToolFailureStats] = field(default_factory=dict)

    @property
    def exhausted(self) -> bool:
        return self.model_calls >= self.max_model_calls

    def record_model_call(self) -> None:
        self.model_calls += 1

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def past_wrap_up(self) -> bool:
        """请求时间快用完了：离墙钟时限不到 3 分钟，或进程正在为发布交接收尾。"""
        return is_draining() or self.elapsed_s() >= self.wrap_up_after_s

    def record_served_model(self, model: str) -> None:
        self.last_served_model = model

    def record_agent_run(
        self,
        duration_ms: float,
        *,
        agent_type: str | None = None,
        outcome: TimingOutcome = "completed",
    ) -> None:
        self.agent_runs += 1
        self.llm_duration_ms += max(0.0, float(duration_ms))
        if stage := _ROLE_STAGES.get(agent_type or ""):
            self.record_stage(stage, duration_ms, outcome=outcome)

    def record_stage(
        self,
        stage: TimingStage,
        duration_ms: float,
        *,
        outcome: TimingOutcome = "completed",
    ) -> None:
        """Fixed stages and numeric facts only; no text or exception details."""
        stats = self._stage_timings.setdefault(
            stage,
            {
                "count": 0,
                "total_ms": 0.0,
                "max_ms": 0.0,
                "error_count": 0,
                "interrupted_count": 0,
            },
        )
        duration_ms = max(0.0, duration_ms)
        stats["count"] += 1
        stats["total_ms"] += duration_ms
        stats["max_ms"] = max(stats["max_ms"], duration_ms)
        stats["error_count"] += outcome == "error"
        stats["interrupted_count"] += outcome == "interrupted"

    @contextmanager
    def measure_stage(self, stage: TimingStage) -> Iterator[None]:
        started = time.monotonic()
        outcome: TimingOutcome = "completed"
        try:
            yield
        except Exception:
            outcome = "error"
            raise
        except BaseException:
            outcome = "interrupted"
            raise
        finally:
            self.record_stage(stage, (time.monotonic() - started) * 1000, outcome=outcome)

    def record_tool_failure(self, tool_name: str, error_type: object, *, batch: bool) -> None:
        """An executed tool returned status=error (business failure; raised errors stay in tool.error_count).

        Counts only: the tool name (registry key), a bounded error_type id, and whether the call was a
        multi-edit batch, which fails as a whole by default. No arguments, file text or messages.
        """
        stats = self._tool_failures.setdefault(tool_name, _ToolFailureStats())
        stats.count += 1
        stats.batch_count += batch
        key = error_type if isinstance(error_type, str) and _ERROR_TYPE_PATTERN.fullmatch(error_type) else _OTHER_ERROR_TYPE
        if key not in stats.error_types and len(stats.error_types) >= _MAX_ERROR_TYPES_PER_TOOL:
            key = _OTHER_ERROR_TYPE
        stats.error_types[key] = stats.error_types.get(key, 0) + 1

    def _offset_ms(self) -> float:
        origin = self.started_at if self.timing_origin is None else self.timing_origin
        return round(max(0.0, time.monotonic() - origin) * 1000, 1)

    def mark_first_tool_started(self) -> None:
        if self.first_tool_started_ms is None:
            self.first_tool_started_ms = self._offset_ms()

    def mark_first_body_persisted(self) -> None:
        if self.first_body_persisted_ms is None:
            self.first_body_persisted_ms = self._offset_ms()

    def timing_report(self) -> dict[str, object]:
        """Snapshot for the existing billing log. Nested stages overlap: do not sum them."""
        return {
            "first_tool_started_ms": self.first_tool_started_ms,
            "first_body_persisted_ms": self.first_body_persisted_ms,
            "stage_timings": {
                stage: {key: round(value, 1) if isinstance(value, float) else value for key, value in stats.items()}
                for stage, stats in self._stage_timings.items()
            },
            "tool_failures": {
                tool: {"count": stats.count, "batch_count": stats.batch_count, "error_types": dict(stats.error_types)}
                for tool, stats in self._tool_failures.items()
            },
        }
