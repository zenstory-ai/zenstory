"""Intra-run tool-output trimmer (a RunConfig.call_model_input_filter).

Why a custom filter instead of agents.extensions.ToolOutputTrimmer:

The stock SDK trimmer only trims tool outputs that appear *before* the
Nth-from-last ``user`` message. In this project that is a guaranteed no-op:
``normalize_messages_for_openai_agents`` (runner.py) strips all tool blocks from
persisted history to plain text, so cross-request tool outputs are never
``function_call_output`` items; and intra-run tool outputs always appear *after*
the current turn's user message, so they are always in the stock trimmer's
"recent" (never-trimmed) zone. Verified empirically: the stock trimmer reclaims
0 chars for every ``recent_turns`` value here.

This filter trims by *intra-run recency* and by a runtime input budget. It
previews stale retrieval outputs, elides stale persisted write bodies, and — if
one recent full-file read is itself enormous — bounds that known tool payload as
well. Summaries keep tool/call pairing plus file IDs and result status. The
filter only changes copies sent to the next model call; the SDK's original input
and the parameters already used to execute tools are never mutated.

Control-flow tool outputs (``handoff_to_agent`` / ``request_clarification``) are
never trimmed: they are outside the allowlist, AND the runner reads their
payload from the live SDK run-item, not from this filtered model input — so the
filter cannot affect control flow even in principle.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from utils.logger import get_logger

if TYPE_CHECKING:
    from agents.run_config import CallModelData, ModelInputData

logger = get_logger(__name__)

# Read-only retrieval tools whose outputs are bulky and safe to preview once stale.
DEFAULT_TRIMMABLE_TOOLS: frozenset[str] = frozenset({"query_files", "hybrid_search"})
DEFAULT_WRITE_TOOLS: frozenset[str] = frozenset({"create_file", "edit_file"})
PARALLEL_TOOL = "parallel_execute"
PARALLEL_KNOWN_TASKS = frozenset({"write_chapter", *DEFAULT_WRITE_TOOLS, *DEFAULT_TRIMMABLE_TOOLS})
MAX_TRACE_VALUES = 40

_TRACE_KEYS = frozenset(
    {
        "id",
        "file_id",
        "fileId",
        "pending_file_id",
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
    """Preview stale intra-run tool outputs before each model call.

    Args:
        keep_recent: Number of most-recent trimmable tool outputs kept at full
            fidelity. Older trimmable outputs in the same run become candidates
            for previewing. Defaults to 2.
        max_output_chars: Only outputs longer than this are previewed. Defaults
            to 2000 (well above a one-line result, below a typical search dump).
        preview_chars: How many leading characters of the original output to keep
            in the preview. Defaults to 400.
        max_input_chars: Soft budget for the model input item list. Only known
            read/write tool payloads are compacted to approach it. User messages,
            control-flow tools, and unknown tools remain untouched.
        recent_preview_chars: Preview retained when an otherwise-recent known
            output must be compacted to approach ``max_input_chars``.
        trimmable_tools: Tool names whose outputs may be previewed. Defaults to
            ``{"query_files", "hybrid_search"}``. Control-flow tools are never
            included.
    """

    def __init__(
        self,
        *,
        keep_recent: int = 2,
        max_output_chars: int = 2000,
        preview_chars: int = 400,
        max_input_chars: int = 96_000,
        recent_preview_chars: int = 12_000,
        trimmable_tools: frozenset[str] | None = None,
    ) -> None:
        if keep_recent < 0:
            raise ValueError(f"keep_recent must be >= 0, got {keep_recent}")
        if max_output_chars < 1:
            raise ValueError(f"max_output_chars must be >= 1, got {max_output_chars}")
        if preview_chars < 0:
            raise ValueError(f"preview_chars must be >= 0, got {preview_chars}")
        if max_input_chars < 1:
            raise ValueError(f"max_input_chars must be >= 1, got {max_input_chars}")
        if recent_preview_chars < 0:
            raise ValueError(f"recent_preview_chars must be >= 0, got {recent_preview_chars}")
        self.keep_recent = keep_recent
        self.max_output_chars = max_output_chars
        self.preview_chars = preview_chars
        self.max_input_chars = max_input_chars
        self.recent_preview_chars = recent_preview_chars
        self.trimmable_tools = DEFAULT_TRIMMABLE_TOOLS if trimmable_tools is None else frozenset(trimmable_tools)

    def __call__(self, data: CallModelData[Any]) -> ModelInputData:
        from agents.run_config import ModelInputData

        model_data = data.model_data
        items = model_data.input
        if not items:
            return model_data

        chars_before = self._input_chars(items)
        call_id_to_info = self._build_call_id_to_info(items)

        # Indices of trimmable function_call_output items, in input order.
        trimmable_indices = [i for i, it in enumerate(items) if self._is_trimmable_output(it, call_id_to_info)]

        # Keep the most recent `keep_recent` trimmable outputs untouched.
        stale_cutoff = max(0, len(trimmable_indices) - self.keep_recent)
        stale_indices = set(trimmable_indices[:stale_cutoff])

        new_items: list[Any] = list(items)
        trimmed_count = 0
        chars_saved = 0
        for i, it in enumerate(items):
            if i in stale_indices:
                info = self._info_for_output(it, call_id_to_info)
                previewed, saved = self._summarize_output(
                    it,
                    tool_name=info.get("name", "") if info else "",
                    preview_chars=self.preview_chars,
                    reason="stale read output compacted",
                )
                if previewed is not None:
                    new_items[i] = previewed
                    trimmed_count += 1
                    chars_saved += saved

        latest_write_index = max(
            (info["index"] for info in call_id_to_info.values() if info["categories"] & {"write"}),
            default=-1,
        )
        for info in call_id_to_info.values():
            if "write" not in info["categories"] or info["index"] == latest_write_index:
                continue
            output_index = info.get("output_index")
            if not isinstance(output_index, int) or not self._write_output_succeeded(new_items[output_index]):
                continue
            call_index = info["index"]
            compacted_call, saved = self._summarize_arguments(new_items[call_index], info["name"])
            if compacted_call is not None:
                new_items[call_index] = compacted_call
                trimmed_count += 1
                chars_saved += saved

            compacted_output, saved = self._summarize_output(
                new_items[output_index],
                tool_name=info["name"],
                preview_chars=self.preview_chars,
                reason="persisted write receipt compacted",
            )
            if compacted_output is not None:
                new_items[output_index] = compacted_output
                trimmed_count += 1
                chars_saved += saved

        # Recency alone is insufficient: a latest full-file read (or inline
        # write) can itself be hundreds of thousands of characters. When known
        # tool history still exceeds the runtime budget, compact the largest
        # remaining known payloads. User messages, control-flow tools, and
        # unknown future tools are deliberately outside this pass.
        if self._input_chars(new_items) > self.max_input_chars:
            candidates: list[tuple[int, int, str, dict[str, Any]]] = []
            for info in call_id_to_info.values():
                if not info["categories"]:
                    continue
                call_index = info["index"]
                call_item = new_items[call_index]
                arguments = call_item.get("arguments", "") if isinstance(call_item, dict) else ""
                output_index = info.get("output_index")
                write_succeeded = isinstance(output_index, int) and self._write_output_succeeded(
                    new_items[output_index]
                )
                if "write" in info["categories"] and write_succeeded and len(str(arguments)) > self.max_output_chars:
                    candidates.append((len(str(arguments)), call_index, "arguments", info))
                if isinstance(output_index, int):
                    output = new_items[output_index].get("output", "")
                    # Failed/partial write output carries the recovery details
                    # the model needs. Read outputs remain safe to summarize.
                    output_is_safe = write_succeeded if "write" in info["categories"] else "read" in info["categories"]
                    if output_is_safe and len(str(output)) > self.max_output_chars:
                        candidates.append((len(str(output)), output_index, "output", info))

            for _size, item_index, kind, info in sorted(candidates, key=lambda candidate: candidate[0], reverse=True):
                if self._input_chars(new_items) <= self.max_input_chars:
                    break
                if kind == "arguments":
                    replacement, saved = self._summarize_arguments(new_items[item_index], info["name"], force=True)
                else:
                    replacement, saved = self._summarize_output(
                        new_items[item_index],
                        tool_name=info["name"],
                        preview_chars=self.recent_preview_chars,
                        reason="runtime input budget compaction",
                        force=True,
                    )
                if replacement is not None:
                    new_items[item_index] = replacement
                    trimmed_count += 1
                    chars_saved += saved

        chars_after = self._input_chars(new_items)

        self._record_metrics(trimmed_count, chars_saved, chars_before, chars_after)
        if trimmed_count:
            logger.debug(
                "IntraRunToolOutputTrimmer compacted %d payload(s), saved ~%d chars (%d -> %d)",
                trimmed_count,
                chars_saved,
                chars_before,
                chars_after,
            )

        return ModelInputData(input=new_items, instructions=model_data.instructions)

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
            # A mixed known/unknown parallel call must remain opaque. Summarizing
            # the entire envelope would erase arguments/results whose semantics
            # this filter does not understand.
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

    def _info_for_output(self, item: Any, call_id_to_info: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        return call_id_to_info.get(str(item.get("call_id") or item.get("id") or ""))

    def _is_trimmable_output(self, item: Any, call_id_to_info: dict[str, dict[str, Any]]) -> bool:
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            return False
        info = self._info_for_output(item, call_id_to_info)
        if not info or "read" not in info["categories"]:
            return False
        return "write" not in info["categories"] or self._write_output_succeeded(item)

    def _summarize_output(
        self,
        item: dict[str, Any],
        *,
        tool_name: str,
        preview_chars: int,
        reason: str,
        force: bool = False,
    ) -> tuple[dict[str, Any] | None, int]:
        output = item.get("output", "")
        output_str = output if isinstance(output, str) else str(output)
        original_len = len(output_str)
        if not force and original_len <= self.max_output_chars:
            return None, 0

        trace = self._collect_trace(self._parse_json(output_str))
        summary_payload: dict[str, Any] = {
            "_zenstory_context_summary": reason,
            "tool": tool_name,
            "original_chars": original_len,
        }
        if trace:
            summary_payload["trace"] = trace
        preview = output_str[:preview_chars]
        if preview:
            summary_payload["preview"] = preview
        summary = json.dumps(summary_payload, ensure_ascii=False)
        if len(summary) >= original_len:
            return None, 0

        previewed = dict(item)
        previewed["output"] = summary
        return previewed, original_len - len(summary)

    def _summarize_arguments(
        self, item: Any, tool_name: str, *, force: bool = False
    ) -> tuple[dict[str, Any] | None, int]:
        if not isinstance(item, dict):
            return None, 0
        arguments = item.get("arguments", "")
        arguments_str = arguments if isinstance(arguments, str) else str(arguments)
        if not force and len(arguments_str) <= self.max_output_chars:
            return None, 0

        parsed = self._parse_json(arguments_str)
        summary_payload: dict[str, Any] = {
            "_zenstory_context_summary": "persisted write arguments elided",
            "tool": tool_name,
            "original_chars": len(arguments_str),
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
            return None, 0
        compacted = dict(item)
        compacted["arguments"] = summary
        return compacted, len(arguments_str) - len(summary)

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
