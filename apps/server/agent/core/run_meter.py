"""一次 /agent/stream 请求的模型调用计数与耗时（请求级预算 + 运行摘要日志）。

service 为一轮请求建一个（单循环 agent 没拿到时自建），放进
``state["run_meter"]``；runner 在每次模型调用前（SDK 的 call_model_input_filter）
计数，在 tool_use_behavior 里检查是否已用尽，用尽即结束当前 SDK run，不再发起
下一次模型调用。service 在请求结束时把计数写进运行摘要。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from config.agent_runtime import AGENT_RUN_MAX_MODEL_CALLS, AGENT_RUN_WRAP_UP_AFTER_S

from .server_drain import is_draining

TimingStage = Literal[
    "prepare_context",
    "model",
    "tool",
    "file_save",
    "agent_loop",
    "compaction",
]
TimingOutcome = Literal["completed", "error", "interrupted"]
# 工具业务失败按 error_type 计数：只收程序定义的标识符，种类有上限，其余归 other。
_ERROR_TYPE_PATTERN = re.compile(r"[a-z0-9_]{1,48}")
_MAX_ERROR_TYPES_PER_TOOL = 8
_OTHER_ERROR_TYPE = "other"


@dataclass
class _ToolFailureStats:
    count: int = 0
    rolled_back_batches: int = 0
    error_types: dict[str, int] = field(default_factory=dict)


_ROLE_STAGES: dict[str, TimingStage] = {
    "loop": "agent_loop",
}
# 进入墙钟收尾提醒（LOOP_TIME_LANDING_PROMPT）后允许模型最多再发起几次调用来收尾：
# 第 1 次提醒收尾；若模型仍忽略提醒并调用工具，tools_adapter 立即以 wall_clock_wrap_up 挡回，
# 第 2 次看到挡回后写阶段总结；若连续 3 次仍只调工具则由 tool_use_behavior 同步结束 run，避免拖到 780s 硬超时。
MAX_WALL_CLOCK_LANDING_CALLS = 3


@dataclass
class AgentRunMeter:
    max_model_calls: int = AGENT_RUN_MAX_MODEL_CALLS
    model_calls: int = 0
    agent_runs: int = 0
    llm_duration_ms: float = 0.0
    # 预算用尽且确实因此截停了一次 SDK run（区别于最后一次调用恰好自然结束）。
    budget_stopped: bool = False
    # 墙钟收尾提醒已注入到模型调用的次数（首次注入后 tools_adapter 同步拦截模型继续发起的新工具调用）。
    wall_clock_landing_calls: int = 0
    # 最近一次模型调用实际用的模型（主备切换后可能是备用供应商的模型）。
    last_served_model: str | None = None
    # 可选实时回调：模型开始服务时立即通知调用方（如实时更新 run_report["model"]，防止断线时丢失）。
    on_served_model: Callable[[str], None] | None = field(default=None, repr=False)
    # 最近一次模型调用的 provider:model:key_slot 标识，用于校验跨轮 wire-prefix 缓存归属。
    last_served_provider_key: str | None = None
    # 请求开始的时刻（monotonic）：到 wrap_up_after_s 就提醒收尾、不再开新的 agent run。
    started_at: float = field(default_factory=time.monotonic)
    wrap_up_after_s: float = AGENT_RUN_WRAP_UP_AFTER_S

    # Diagnostic origin does not alter the existing wrap-up budget clock.
    timing_origin: float | None = None
    first_tool_started_ms: float | None = None
    chapter_first_prose_ms: float | None = None
    first_body_persisted_ms: float | None = None
    # Monotonic arming timestamp for pending file writes (write_chapter / create_file awaiting <file> body).
    _pending_file_write_armed_at: float | None = field(default=None, repr=False)
    _pending_file_write_file_id: str | None = field(default=None, repr=False)
    leaked_tool_call_count: int = 0
    # 最近一次模型调用的输入 token（含命中缓存的部分）：单循环 agent 据此决定下一轮前是否压缩历史。
    last_prompt_tokens: int = 0
    # 本轮所有模型调用的输入 token 峰值（防末尾调用触发 IntraRunToolOutputTrimmer 后掩盖真实未裁剪前缀大小）。
    max_prompt_tokens: int = 0
    _stage_timings: dict[TimingStage, dict[str, float | int]] = field(default_factory=dict)
    _tool_failures: dict[str, _ToolFailureStats] = field(default_factory=dict)

    @property
    def exhausted(self) -> bool:
        return self.model_calls >= self.max_model_calls

    def record_model_call(self) -> None:
        self.model_calls += 1

    def record_wall_clock_landing_call(self) -> None:
        """记录一次已注入墙钟收尾提醒的模型调用。"""
        self.wall_clock_landing_calls += 1

    @property
    def wall_clock_landing_injected(self) -> bool:
        """是否已向模型注入过墙钟收尾提醒（此后不允许再执行新工具调用）。"""
        return self.wall_clock_landing_calls > 0

    @property
    def wall_clock_landing_exhausted(self) -> bool:
        """模型在收到墙钟收尾提醒后仍连续调用工具达到上限，应同步结束 run。"""
        return self.wall_clock_landing_calls >= MAX_WALL_CLOCK_LANDING_CALLS

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def record_prompt_tokens(self, prompt_tokens: int) -> None:
        """Record measured input tokens for a model call, keeping both latest and round peak."""
        if isinstance(prompt_tokens, int) and not isinstance(prompt_tokens, bool) and prompt_tokens > 0:
            self.last_prompt_tokens = prompt_tokens
            if prompt_tokens > self.max_prompt_tokens:
                self.max_prompt_tokens = prompt_tokens

    @property
    def effective_prompt_tokens(self) -> int:
        """Higher of latest and peak measured prompt tokens across the round."""
        return max(self.last_prompt_tokens, self.max_prompt_tokens)

    @property
    def server_draining(self) -> bool:
        """Whether the process is currently draining for a planned deploy/shutdown."""
        return is_draining()

    @property
    def is_draining_stop(self) -> bool:
        """Whether wrap-up was triggered by a planned deploy drain before the wall-clock cap."""
        return is_draining() and self.elapsed_s() < self.wrap_up_after_s

    @property
    def past_wrap_up(self) -> bool:
        """请求时间快用完了：到达收尾时刻（默认离墙钟时限 4 分钟），或进程正在为发布交接收尾。"""
        return is_draining() or self.elapsed_s() >= self.wrap_up_after_s

    def record_served_model(self, model: str) -> None:
        self.last_served_model = model
        if self.on_served_model is not None and model:
            self.on_served_model(model)

    def record_served_provider(self, provider: Any) -> None:
        from agent.loop.wire_prefix_cache import provider_cache_key

        key = provider_cache_key(provider)
        if key is not None:
            self.last_served_provider_key = key

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

    def record_tool_failure(self, tool_name: str, error_type: object, *, rolled_back_batch: bool) -> None:
        """An executed tool returned status=error or partial (business failure; raised errors stay in tool.error_count).

        Counts only: the tool name (registry key), a bounded error_type id, and whether a multi-edit
        batch was rolled back as a whole (the default without continue_on_error). No arguments,
        file text or messages.
        """
        stats = self._tool_failures.setdefault(tool_name, _ToolFailureStats())
        stats.count += 1
        stats.rolled_back_batches += rolled_back_batch
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

    def mark_pending_file_write_armed(self, file_id: str | None = None) -> None:
        """Record when a pending file write (write_chapter / create_file awaiting <file>) arms."""
        if self.chapter_first_prose_ms is None:
            self._pending_file_write_armed_at = time.monotonic()
            self._pending_file_write_file_id = file_id

    def mark_first_file_prose_chunk(self, file_id: str | None = None) -> None:
        """Compute write_chapter/create_file -> first streamed <file> prose chunk latency once per round."""
        if self.chapter_first_prose_ms is not None or self._pending_file_write_armed_at is None:
            return
        if (
            file_id is not None
            and self._pending_file_write_file_id is not None
            and file_id != self._pending_file_write_file_id
        ):
            return
        self.chapter_first_prose_ms = round(max(0.0, time.monotonic() - self._pending_file_write_armed_at) * 1000, 1)

    def record_leaked_tool_call(self, count: int = 1) -> None:
        """Increment count of leaked tool-call protocol aborts/detections in this round."""
        if count > 0:
            self.leaked_tool_call_count += int(count)

    def mark_first_body_persisted(self) -> None:
        if self.first_body_persisted_ms is None:
            self.first_body_persisted_ms = self._offset_ms()

    def timing_report(self) -> dict[str, object]:
        """Snapshot for the existing billing log. Nested stages overlap: do not sum them."""
        return {
            "first_tool_started_ms": self.first_tool_started_ms,
            "chapter_first_prose_ms": self.chapter_first_prose_ms,
            "first_body_persisted_ms": self.first_body_persisted_ms,
            "leaked_tool_call_count": self.leaked_tool_call_count,
            "stage_timings": {
                stage: {key: round(value, 1) if isinstance(value, float) else value for key, value in stats.items()}
                for stage, stats in self._stage_timings.items()
            },
            "tool_failures": {
                tool: {
                    "count": stats.count,
                    "rolled_back_batches": stats.rolled_back_batches,
                    "error_types": dict(stats.error_types),
                }
                for tool, stats in self._tool_failures.items()
            },
        }
