"""Intra-run context safety valve (a RunConfig.call_model_input_filter).

为什么这里**默认什么都不做**
---------------------------

DeepSeek（deepseek-flash，1M token 上下文）有自动前缀缓存：与上一次请求逐字相同的
前缀按 cache-hit 计价，只有 cache-miss 的 1/50。只要每次模型调用的输入是「上一次
输入 + 新增条目」（append-only），重发整段历史几乎不花钱。

历史实现按「本轮内的新旧」裁剪：只保留最近 2 个读取结果的全文，更早的一律折叠成
400 字预览，并折叠所有旧的写入回执，另有 96k 字符预算。它在每次模型调用时都重写
历史中段，导致：

1. 前缀缓存失效。改写点之后全部按 cache-miss 计费。事故数据（10-03 起）：
   cache-hit 输入 3.9 元、cache-miss 输入 34.9 元、输出 43.2 元——重发历史很便宜，
   改写历史（cache miss）和重读才贵。
2. 重读死循环。模型读了 A、B，下一步 A、B 已被折叠成预览，于是「还缺 C、D」；
   读 C、D 后 A、B 又不见了……直到 100 轮上限。约 1/3 的 agent 轮次把同一文件读了
   4 次以上，这些轮次占 agent 花费的 69%。

因此本过滤器只是**上下文安全阀**，不是省钱工具：

- 模型输入（items + instructions）不超过 ``max_input_chars``（默认 600k 字符，远低于
  1M token）时原样返回，不做任何改写。
- 只有超预算时才折叠：按输入顺序**从最旧开始**，折叠已知读取结果、以及已成功落库的
  写入参数/回执，直到降到目标线以下。折叠量按 ``max_input_chars - target`` 的整数倍
  量化（滞回）：输入继续增长时，折叠边界保持不动，直到再跨过一个量化台阶才前移，
  所以两次越界之间的调用仍然共享稳定前缀；最旧优先 + 量化使结果对同一输入确定、
  随输入增长单调。
- 永不触碰：控制流工具（handoff_to_agent / request_clarification）、用户消息、未知
  工具、失败/部分失败的写入（它们携带模型自我纠错所需的细节）。控制流工具的载荷
  由 runner 从 SDK 的 run-item 读取，与本过滤器的输出无关。
- 折叠摘要保留 call/output 配对、文件 id/标题等 trace，并用中文 ``note`` 告诉模型
  这是「被折叠」而非「没读过」，指引它最多按 id 全文重读一次，而不是整篇重读。

为什么不用 agents.extensions.ToolOutputTrimmer：SDK 自带的裁剪器只处理倒数第 N 个
``user`` 消息之前的工具输出。本项目 ``normalize_messages_for_openai_agents`` 会把跨请求
的工具块转成纯文本，而本轮的工具输出总在本轮 user 消息之后，所以它在这里恒为 no-op。

过滤器只修改发给下一次模型调用的副本；SDK 原始输入与已用于执行工具的参数从不被修改。
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

from utils.logger import get_logger

if TYPE_CHECKING:
    from agents.run_config import CallModelData, ModelInputData

logger = get_logger(__name__)

# 只读检索工具：结果体积大，且原文仍可按 id 重新读取，超预算时可安全折叠。
DEFAULT_TRIMMABLE_TOOLS: frozenset[str] = frozenset({"query_files", "hybrid_search"})
DEFAULT_WRITE_TOOLS: frozenset[str] = frozenset({"create_file", "edit_file"})
PARALLEL_TOOL = "parallel_execute"
PARALLEL_KNOWN_TASKS = frozenset({"write_chapter", *DEFAULT_WRITE_TOOLS, *DEFAULT_TRIMMABLE_TOOLS})
MAX_TRACE_VALUES = 40

# 安全阀阈值：600k 字符远低于 deepseek-flash 的 1M token 上下文。
DEFAULT_MAX_INPUT_CHARS = 600_000
# 超预算后折叠到预算的 70% 左右；30% 的余量就是滞回台阶。
DEFAULT_TARGET_RATIO = 0.7
# 小于该长度的载荷不值得折叠（摘要本身也有几百字符）。
DEFAULT_MIN_COMPACT_CHARS = 2_000
DEFAULT_PREVIEW_CHARS = 400

COMPACTION_REASON = "context_budget_compaction"
READ_COMPACTION_NOTE = (
    "该结果已在本轮更早时完整返回，因上下文接近上限被折叠。不要整篇重读；"
    '只在确实需要原文细节时，用 query_files(id=…, response_mode="full") 读取该文件一次。'
)
WRITE_RECEIPT_COMPACTION_NOTE = (
    "该写入已成功落库，回执因上下文接近上限被折叠。不要重复写入；"
    '需要核对当前正文时，用 query_files(id=…, response_mode="full") 读取该文件一次。'
)
WRITE_ARGUMENTS_COMPACTION_NOTE = "该写入参数已执行成功并落库，因上下文接近上限被折叠；不要重复执行。"

_TRACE_KEYS = frozenset(
    {
        "id",
        "file_id",
        "fileId",
        "pending_file_id",
        "entity_id",
        "entity_type",
        "line_start",
        "artifact_ref",
        "artifact_refs",
        "overflow_ref",
        "title",
        "file_type",
        "status",
        "type",
        "task_type",
        "tool_name",
        "completed",
        "failed",
        "total_tasks",
        "edits_applied",
        "reused_existing",
        "original_content_length",
    }
)


class IntraRunToolOutputTrimmer:
    """Append-only context safety valve applied before each model call.

    Args:
        max_input_chars: 模型输入（items JSON + instructions）的安全阀上限。不超过时
            输入原样返回。默认 600_000。
        target_ratio: 超预算时折叠到 ``max_input_chars * target_ratio`` 附近；
            ``max_input_chars - target`` 同时是折叠量的量化台阶（滞回）。默认 0.7。
        min_compact_chars: 只折叠长度超过该值的载荷。默认 2000。
        preview_chars: 折叠摘要里保留的原文开头字符数。默认 400。
        trimmable_tools: 可折叠结果的只读工具名，默认 ``{"query_files", "hybrid_search"}``。
            控制流工具永远不在其中。
    """

    def __init__(
        self,
        *,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        target_ratio: float = DEFAULT_TARGET_RATIO,
        min_compact_chars: int = DEFAULT_MIN_COMPACT_CHARS,
        preview_chars: int = DEFAULT_PREVIEW_CHARS,
        trimmable_tools: frozenset[str] | None = None,
    ) -> None:
        if max_input_chars < 1:
            raise ValueError(f"max_input_chars must be >= 1, got {max_input_chars}")
        if not 0 < target_ratio < 1:
            raise ValueError(f"target_ratio must be in (0, 1), got {target_ratio}")
        if min_compact_chars < 1:
            raise ValueError(f"min_compact_chars must be >= 1, got {min_compact_chars}")
        if preview_chars < 0:
            raise ValueError(f"preview_chars must be >= 0, got {preview_chars}")
        self.max_input_chars = max_input_chars
        self.target_ratio = target_ratio
        self.min_compact_chars = min_compact_chars
        self.preview_chars = preview_chars
        self.trimmable_tools = DEFAULT_TRIMMABLE_TOOLS if trimmable_tools is None else frozenset(trimmable_tools)

    @property
    def target_chars(self) -> int:
        return int(self.max_input_chars * self.target_ratio)

    def __call__(self, data: CallModelData[Any]) -> ModelInputData:
        from agents.run_config import ModelInputData

        model_data = data.model_data
        items = model_data.input
        if not items:
            return model_data

        instructions = model_data.instructions
        instructions_chars = len(instructions) if isinstance(instructions, str) else 0
        chars_before = self._input_chars(items) + instructions_chars

        if chars_before <= self.max_input_chars:
            # 常态路径：不改写任何历史，保住 DeepSeek 前缀缓存，也不让模型「忘掉」读过的文件。
            self._record_metrics(0, 0, chars_before, chars_before)
            return model_data

        # 需要折叠掉的量按台阶量化：台阶 = max - target。输入在同一台阶内增长时，
        # required 不变 → 最旧优先的折叠边界不变 → 前缀稳定；结果总落在 (target, max] 内。
        step = max(1, self.max_input_chars - self.target_chars)
        required = math.ceil((chars_before - self.max_input_chars) / step) * step

        new_items: list[Any] = list(items)
        trimmed_count = 0
        chars_saved = 0
        call_id_to_info = self._build_call_id_to_info(items)
        for item_index, kind, info in self._candidates_oldest_first(items, call_id_to_info):
            if chars_saved >= required:
                break
            original = new_items[item_index]
            if kind == "arguments":
                replacement = self._summarize_arguments(original, info["name"])
            else:
                replacement = self._summarize_output(original, tool_name=info["name"], kind=kind)
            if replacement is None:
                continue
            saved = self._item_chars(original) - self._item_chars(replacement)
            if saved <= 0:
                continue
            new_items[item_index] = replacement
            trimmed_count += 1
            chars_saved += saved

        chars_after = chars_before - chars_saved
        self._record_metrics(trimmed_count, chars_saved, chars_before, chars_after)
        if trimmed_count:
            logger.info(
                "IntraRunToolOutputTrimmer over budget: compacted %d payload(s), saved ~%d chars (%d -> %d, max %d)",
                trimmed_count,
                chars_saved,
                chars_before,
                chars_after,
                self.max_input_chars,
            )
        return ModelInputData(input=new_items, instructions=instructions)

    # ------------------------------------------------------------------ candidates

    def _candidates_oldest_first(
        self, items: list[Any], call_id_to_info: dict[str, dict[str, Any]]
    ) -> list[tuple[int, str, dict[str, Any]]]:
        """可折叠的 (item_index, kind, info)，按输入位置从旧到新排序。"""
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for info in call_id_to_info.values():
            categories = info["categories"]
            if not categories:
                continue  # 控制流 / 未知工具 / 混入未知任务的 parallel_execute
            output_index = info.get("output_index")
            if not isinstance(output_index, int):
                continue  # 没有结果的调用：参数可能仍是执行依据，保持原样
            output_item = items[output_index]
            if "write" in categories:
                # 失败/部分失败的写入携带恢复细节；只有确认成功的写入才可折叠。
                if not self._write_output_succeeded(output_item):
                    continue
                candidates.append((info["index"], "arguments", info))
                candidates.append((output_index, "write_output", info))
            else:
                candidates.append((output_index, "read_output", info))
        candidates.sort(key=lambda candidate: candidate[0])
        return candidates

    def _build_call_id_to_info(self, items: list[Any]) -> dict[str, dict[str, Any]]:
        mapping: dict[str, dict[str, Any]] = {}
        for index, it in enumerate(items):
            if isinstance(it, dict) and it.get("type") == "function_call":
                call_id = it.get("call_id") or it.get("id")
                name = it.get("name")
                if call_id and name:
                    normalized_name = str(name)
                    mapping[str(call_id)] = {
                        "name": normalized_name,
                        "index": index,
                        "categories": self._tool_categories(normalized_name, it.get("arguments")),
                    }
        for index, it in enumerate(items):
            if not isinstance(it, dict) or it.get("type") != "function_call_output":
                continue
            info = mapping.get(str(it.get("call_id") or it.get("id") or ""))
            if info is not None:
                info["output_index"] = index
        return mapping

    def _tool_categories(self, name: str, arguments: Any) -> frozenset[str]:
        if name in self.trimmable_tools:
            return frozenset({"read"})
        if name in DEFAULT_WRITE_TOOLS:
            return frozenset({"write"})
        if name != PARALLEL_TOOL:
            return frozenset()

        parsed = self._parse_json(arguments)
        tasks = parsed.get("tasks", []) if isinstance(parsed, dict) else []
        if not isinstance(tasks, list) or any(
            not isinstance(task, dict) or task.get("type") not in PARALLEL_KNOWN_TASKS for task in tasks
        ):
            # 混入未知任务的并行调用整体保持不透明：折叠整个信封会抹掉本过滤器
            # 不理解其语义的参数/结果。
            return frozenset()
        categories: set[str] = set()
        for task in tasks:
            task_type = task.get("type")
            if task_type in self.trimmable_tools:
                categories.add("read")
            elif task_type in {"write_chapter", *DEFAULT_WRITE_TOOLS}:
                categories.add("write")
        return frozenset(categories)

    def _write_output_succeeded(self, item: Any) -> bool:
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            return False
        parsed = self._parse_json(item.get("output"))
        if not isinstance(parsed, dict):
            return False

        statuses: list[str] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                status = node.get("status")
                if isinstance(status, str):
                    statuses.append(status.lower())
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)

        visit(parsed)
        if not statuses:
            return False
        failed_statuses = {"error", "failed", "failure", "partial", "cancelled", "canceled"}
        return not any(status in failed_statuses for status in statuses)

    # ------------------------------------------------------------------ summaries

    def _summarize_output(self, item: Any, *, tool_name: str, kind: str) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        output = item.get("output", "")
        output_str = output if isinstance(output, str) else str(output)
        original_len = len(output_str)
        if original_len <= self.min_compact_chars:
            return None

        summary_payload: dict[str, Any] = {
            "_zenstory_context_summary": COMPACTION_REASON,
            "tool": tool_name,
            "original_chars": original_len,
            "note": WRITE_RECEIPT_COMPACTION_NOTE if kind == "write_output" else READ_COMPACTION_NOTE,
        }
        trace = self._collect_trace(self._parse_json(output_str))
        if trace:
            summary_payload["trace"] = trace
        preview = output_str[: self.preview_chars]
        if preview:
            summary_payload["preview"] = preview
        summary = json.dumps(summary_payload, ensure_ascii=False)
        if len(summary) >= original_len:
            return None

        compacted = dict(item)
        compacted["output"] = summary
        return compacted

    def _summarize_arguments(self, item: Any, tool_name: str) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        arguments = item.get("arguments", "")
        arguments_str = arguments if isinstance(arguments, str) else str(arguments)
        if len(arguments_str) <= self.min_compact_chars:
            return None

        parsed = self._parse_json(arguments_str)
        summary_payload: dict[str, Any] = {
            "_zenstory_context_summary": COMPACTION_REASON,
            "tool": tool_name,
            "original_chars": len(arguments_str),
            "note": WRITE_ARGUMENTS_COMPACTION_NOTE,
        }
        trace = self._collect_trace(parsed)
        if trace:
            summary_payload["trace"] = trace
        if tool_name == PARALLEL_TOOL and isinstance(parsed, dict):
            tasks = parsed.get("tasks")
            if isinstance(tasks, list):
                summary_payload["tasks"] = [
                    self._summarize_parallel_task(task) for task in tasks if isinstance(task, dict)
                ]
        elif isinstance(parsed, dict):
            if isinstance(parsed.get("content"), str):
                summary_payload["content_chars"] = len(parsed["content"])
            if isinstance(parsed.get("edits"), list):
                summary_payload["edit_count"] = len(parsed["edits"])

        summary = json.dumps(summary_payload, ensure_ascii=False)
        if len(summary) >= len(arguments_str):
            return None
        compacted = dict(item)
        compacted["arguments"] = summary
        return compacted

    def _summarize_parallel_task(self, task: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {"type": task.get("type")}
        description = task.get("description")
        if isinstance(description, str) and description:
            summary["description"] = description[:160]
        params = task.get("params")
        if isinstance(params, dict):
            trace = self._collect_trace(params)
            if trace:
                summary["trace"] = trace
            if isinstance(params.get("content"), str):
                summary["content_chars"] = len(params["content"])
            if isinstance(params.get("edits"), list):
                summary["edit_count"] = len(params["edits"])
        return summary

    def _collect_trace(self, value: Any) -> dict[str, list[Any]]:
        found: dict[str, list[Any]] = {}

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                for key, child in node.items():
                    if key in _TRACE_KEYS and isinstance(child, (str, int, float, bool)):
                        bucket = found.setdefault(key, [])
                        if child not in bucket and len(bucket) < MAX_TRACE_VALUES:
                            bucket.append(child)
                    elif key in _TRACE_KEYS and isinstance(child, list):
                        for entry in self._bounded_entries(child):
                            if isinstance(entry, (str, int, float, bool)):
                                bucket = found.setdefault(key, [])
                                if entry not in bucket and len(bucket) < MAX_TRACE_VALUES:
                                    bucket.append(entry)
                    visit(child)
            elif isinstance(node, list):
                for child in self._bounded_entries(node):
                    visit(child)

        visit(value)
        return found

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _bounded_entries(values: list[Any]) -> list[Any]:
        if len(values) <= MAX_TRACE_VALUES:
            return values
        half = MAX_TRACE_VALUES // 2
        return [*values[:half], *values[-half:]]

    @staticmethod
    def _parse_json(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _item_chars(item: Any) -> int:
        try:
            return len(json.dumps(item, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return len(str(item))

    @staticmethod
    def _input_chars(items: list[Any]) -> int:
        try:
            return len(json.dumps(items, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return sum(len(str(item)) for item in items)

    def _record_metrics(self, trimmed_count: int, chars_saved: int, chars_before: int, chars_after: int) -> None:
        try:
            from agent.core.metrics import (
                MODEL_INPUT_FILTER_CHARS_AFTER,
                MODEL_INPUT_FILTER_CHARS_BEFORE,
                TOOL_OUTPUT_TRIMMED_CHARS,
                TOOL_OUTPUT_TRIMMED_TOTAL,
                get_metrics_collector,
            )

            mc = get_metrics_collector()
            mc.observe_histogram(MODEL_INPUT_FILTER_CHARS_BEFORE, chars_before)
            mc.observe_histogram(MODEL_INPUT_FILTER_CHARS_AFTER, chars_after)
            if trimmed_count:
                mc.increment_counter(TOOL_OUTPUT_TRIMMED_TOTAL, trimmed_count)
                mc.increment_counter(TOOL_OUTPUT_TRIMMED_CHARS, chars_saved)
        except Exception:  # metrics are best-effort; never break a model call
            logger.debug("Failed to record tool-output-trim metrics", exc_info=True)
