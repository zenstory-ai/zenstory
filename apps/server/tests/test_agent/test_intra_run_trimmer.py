"""Tests for the intra-run context safety valve (call_model_input_filter).

The filter must be append-only below budget (DeepSeek prefix cache + no forced
re-reads) and only compact oldest-first once the model input exceeds the valve.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from agents.run_config import CallModelData, ModelInputData

from agent.core.metrics import (
    MODEL_INPUT_FILTER_CHARS_AFTER,
    MODEL_INPUT_FILTER_CHARS_BEFORE,
    TOOL_OUTPUT_TRIMMED_CHARS,
    TOOL_OUTPUT_TRIMMED_TOTAL,
    get_metrics_collector,
    reset_metrics_collector,
)
from agent.openai_agents.intra_run_trimmer import (
    COMPACTION_REASON,
    DEFAULT_MAX_INPUT_CHARS,
    IntraRunToolOutputTrimmer,
)

BIG = "X" * 5000


def _fc(call_id: str, name: str, arguments: str = "{}") -> dict[str, Any]:
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}


def _fco(call_id: str, output: str) -> dict[str, Any]:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def _read_output(file_id: str, body: str) -> str:
    return json.dumps(
        {"status": "success", "data": [{"id": file_id, "title": f"标题-{file_id}", "content": body}]},
        ensure_ascii=False,
    )


def _apply(
    trimmer: IntraRunToolOutputTrimmer, items: list[dict[str, Any]], instructions: str | None = None
) -> ModelInputData:
    md = ModelInputData(input=[dict(it) for it in items], instructions=instructions)
    return trimmer(CallModelData(model_data=md, agent=None, context=None))


def _run(trimmer: IntraRunToolOutputTrimmer, items: list[dict[str, Any]]) -> list[Any]:
    return _apply(trimmer, items).input


def _outputs(items: list[Any]) -> list[str]:
    return [it["output"] for it in items if it.get("type") == "function_call_output"]


def _chars(items: list[Any]) -> int:
    return len(json.dumps(items, ensure_ascii=False))


def test_default_constructor_is_a_600k_safety_valve():
    trimmer = IntraRunToolOutputTrimmer()
    assert trimmer.max_input_chars == DEFAULT_MAX_INPUT_CHARS == 600_000
    assert trimmer.target_chars == 420_000


def test_regression_four_full_reads_and_a_search_pass_through_unchanged():
    """事故回归：4 次 query_files 全文读取（共 ~31k 字）+ 1 次 hybrid_search 不得被改写。

    旧实现只保留最近 2 个读取结果，把更早的 A、B 折叠成预览，模型随即重读 A、B，
    形成「读 A,B → 缺 C,D → 读 C,D → 缺 A,B」循环。
    """
    bodies = {"file-a": "甲" * 5_000, "file-b": "乙" * 8_000, "file-c": "丙" * 8_000, "file-d": "丁" * 10_000}
    assert sum(len(body) for body in bodies.values()) == 31_000
    items: list[dict[str, Any]] = [{"role": "user", "content": "根据 A、B、C、D 四个设定写第五章"}]
    for index, (file_id, body) in enumerate(bodies.items()):
        call_id = f"read-{index}"
        items.append(_fc(call_id, "query_files", json.dumps({"id": file_id, "response_mode": "full"})))
        items.append(_fco(call_id, _read_output(file_id, body)))
    items.append(_fc("search-1", "hybrid_search", json.dumps({"query": "旧钥匙"}, ensure_ascii=False)))
    items.append(_fco("search-1", json.dumps({"status": "success", "data": {"results": ["片段" * 2_000]}})))

    md = ModelInputData(input=copy.deepcopy(items), instructions="系统提示" * 2_000)
    result = IntraRunToolOutputTrimmer()(CallModelData(model_data=md, agent=None, context=None))

    assert result is md  # 原样返回同一对象：没有任何改写
    assert result.input == items


def test_under_budget_never_compacts_stale_reads_or_write_receipts():
    old_write_args = json.dumps({"id": "file-old", "edits": [{"old": "甲" * 6_000, "new": "乙" * 6_000}]})
    items = [
        _fc("c1", "hybrid_search"),
        _fco("c1", BIG),
        _fc("c2", "hybrid_search"),
        _fco("c2", BIG),
        _fc("c3", "query_files"),
        _fco("c3", BIG),
        _fc("w1", "edit_file", old_write_args),
        _fco("w1", json.dumps({"status": "success", "data": {"id": "file-old", "content": "乙" * 6_000}})),
        _fc("w2", "edit_file", json.dumps({"id": "file-new", "edits": []})),
        _fco("w2", json.dumps({"status": "success", "data": {"id": "file-new"}})),
    ]
    assert _run(IntraRunToolOutputTrimmer(max_input_chars=_chars(items) + 1), items) == items


def test_instructions_count_toward_the_budget():
    items = [_fc("r1", "query_files"), _fco("r1", _read_output("file-1", "正文" * 3_000))]
    budget = _chars(items) + 100
    trimmer = IntraRunToolOutputTrimmer(max_input_chars=budget)

    assert _apply(trimmer, items, instructions="短").input == items
    compacted = _apply(trimmer, items, instructions="长" * 1_000).input
    assert COMPACTION_REASON in compacted[1]["output"]


def test_over_budget_compacts_oldest_first_and_keeps_newest_full():
    items = [{"role": "user", "content": "写下一章"}]
    for index in range(6):
        items.append(_fc(f"r{index}", "query_files", json.dumps({"id": f"file-{index}"})))
        items.append(_fco(f"r{index}", _read_output(f"file-{index}", "字" * 10_000)))
    total = _chars(items)
    trimmer = IntraRunToolOutputTrimmer(max_input_chars=total - 1)

    out = _run(trimmer, items)
    outputs = _outputs(out)

    compacted_flags = [COMPACTION_REASON in output for output in outputs]
    # Oldest-first: the compacted ones form a prefix, the newest stays full.
    assert compacted_flags[0] is True
    assert compacted_flags == sorted(compacted_flags, reverse=True)
    assert compacted_flags[-1] is False
    assert _chars(out) <= trimmer.max_input_chars
    # The pairing and the user message survive.
    assert out[0] == items[0]
    assert [it.get("call_id") for it in out[1:]] == [it.get("call_id") for it in items[1:]]


def test_compaction_summary_tells_model_not_to_reread_and_keeps_trace():
    items = [
        _fc("r1", "query_files", json.dumps({"id": "file-123"})),
        _fco("r1", _read_output("file-123", "正文" * 20_000)),
        _fc("r2", "query_files"),
        _fco("r2", BIG),
    ]
    out = _run(IntraRunToolOutputTrimmer(max_input_chars=_chars(items) - 1), items)

    summary = json.loads(out[1]["output"])
    assert summary["_zenstory_context_summary"] == COMPACTION_REASON
    assert summary["trace"]["id"] == ["file-123"]
    assert summary["trace"]["title"] == ["标题-file-123"]
    assert "不要整篇重读" in summary["note"]
    assert 'response_mode="full"' in summary["note"]
    assert summary["original_chars"] == len(items[1]["output"])


def test_hybrid_search_trace_keeps_entity_and_line_references():
    search_output = json.dumps(
        {
            "status": "success",
            "data": {
                "results": [
                    {"entity_id": "file-9", "entity_type": "lore", "line_start": 42, "snippet": "伏笔" * 3_000}
                ]
            },
        },
        ensure_ascii=False,
    )
    items = [_fc("s1", "hybrid_search"), _fco("s1", search_output), _fc("r2", "query_files"), _fco("r2", BIG)]
    out = _run(IntraRunToolOutputTrimmer(max_input_chars=_chars(items) - 1), items)

    trace = json.loads(out[1]["output"])["trace"]
    assert trace["entity_id"] == ["file-9"]
    assert trace["entity_type"] == ["lore"]
    assert trace["line_start"] == [42]


def test_compaction_boundary_is_stable_while_input_grows_within_a_step():
    """滞回：越界后继续追加条目，只要没跨过下一个量化台阶，已发送的前缀不变。"""
    base = []
    for index in range(10):
        base.append(_fc(f"r{index}", "query_files", json.dumps({"id": f"file-{index}"})))
        base.append(_fco(f"r{index}", _read_output(f"file-{index}", "字" * 10_000)))
    trimmer = IntraRunToolOutputTrimmer(max_input_chars=_chars(base) - 1_000)

    first = _run(trimmer, base)
    grown = [*base, _fc("tail", "update_project"), _fco("tail", "ok")]
    second = _run(trimmer, grown)

    assert second[: len(first)] == first  # append-only between threshold crossings
    assert _run(trimmer, base) == first  # deterministic for identical input


def test_compaction_is_monotone_as_the_run_grows():
    items: list[dict[str, Any]] = []
    trimmer = IntraRunToolOutputTrimmer(max_input_chars=60_000)
    previously_compacted: set[str] = set()
    for index in range(20):
        items.append(_fc(f"r{index}", "query_files", json.dumps({"id": f"file-{index}"})))
        items.append(_fco(f"r{index}", _read_output(f"file-{index}", "字" * 8_000)))
        out = _run(trimmer, items)
        assert _chars(out) <= trimmer.max_input_chars
        compacted = {it["call_id"] for it in out if it["type"] == "function_call_output" and COMPACTION_REASON in it["output"]}
        assert previously_compacted <= compacted
        previously_compacted = compacted
    assert previously_compacted  # the valve actually engaged


def test_stale_successful_write_arguments_and_receipt_compacted_only_over_budget():
    old_arguments = json.dumps(
        {"id": "file-old", "edits": [{"old_text": "甲" * 10_000, "new_text": "乙" * 10_000}]},
        ensure_ascii=False,
    )
    old_receipt = json.dumps(
        {"status": "success", "data": {"id": "file-old", "content": "乙" * 12_000}}, ensure_ascii=False
    )
    latest_arguments = json.dumps({"id": "file-new", "edits": [{"old_text": "旧", "new_text": "新"}]})
    items = [
        _fc("write-1", "edit_file", old_arguments),
        _fco("write-1", old_receipt),
        _fc("write-2", "edit_file", latest_arguments),
        _fco("write-2", json.dumps({"status": "success", "data": {"id": "file-new"}})),
    ]

    assert _run(IntraRunToolOutputTrimmer(max_input_chars=_chars(items) + 1), items) == items

    out = _run(IntraRunToolOutputTrimmer(max_input_chars=8_000), items)
    assert len(out[0]["arguments"]) < 1_000
    assert "file-old" in out[0]["arguments"]
    assert "不要重复执行" in out[0]["arguments"]
    assert len(out[1]["output"]) < 2_000
    assert "file-old" in out[1]["output"]
    assert out[2]["arguments"] == latest_arguments
    assert out[3]["output"] == items[3]["output"]


def test_parallel_execute_read_result_is_compacted_and_traceable_over_budget():
    parallel_args = json.dumps(
        {
            "tasks": [
                {"type": "query_files", "description": "读取第一章", "params": {"id": "chapter-1", "response_mode": "full"}},
                {"type": "hybrid_search", "description": "搜索伏笔", "params": {"query": "旧钥匙"}},
            ]
        },
        ensure_ascii=False,
    )
    parallel_output = json.dumps(
        {
            "status": "success",
            "data": {
                "tasks": [
                    {"id": "task-1", "type": "query_files", "status": "completed", "result": {"id": "chapter-1", "content": "甲" * 50_000}},
                    {"id": "task-2", "type": "hybrid_search", "status": "completed", "result": "乙" * 50_000},
                ]
            },
        },
        ensure_ascii=False,
    )
    items = [_fc("parallel-1", "parallel_execute", parallel_args), _fco("parallel-1", parallel_output)]

    out = _run(IntraRunToolOutputTrimmer(max_input_chars=20_000), items)

    assert out[0]["arguments"] == parallel_args
    assert out[1]["call_id"] == "parallel-1"
    assert len(out[1]["output"]) < 15_000
    assert "chapter-1" in out[1]["output"]
    assert "completed" in out[1]["output"]


def test_unknown_tool_control_flow_and_user_messages_never_compacted():
    huge = "Z" * 40_000
    items = [
        _fc("unknown-1", "future_tool"),
        _fco("unknown-1", huge),
        _fc("handoff-1", "handoff_to_agent"),
        _fco("handoff-1", huge),
        _fc("clarify-1", "request_clarification"),
        _fco("clarify-1", huge),
        {"role": "user", "content": huge},
    ]
    assert _run(IntraRunToolOutputTrimmer(max_input_chars=1_000), items) == items


def test_mixed_unknown_parallel_call_is_left_opaque():
    arguments = json.dumps(
        {
            "tasks": [
                {"type": "query_files", "description": "known", "params": {"id": "file-1"}},
                {"type": "future_control_tool", "description": "unknown", "params": {"opaque": "A" * 20_000}},
            ]
        }
    )
    output = json.dumps(
        {
            "status": "success",
            "data": {"tasks": [{"type": "query_files", "result": BIG}, {"type": "future_control_tool", "result": "B" * 20_000}]},
        }
    )
    items = [_fc("parallel-mixed", "parallel_execute", arguments), _fco("parallel-mixed", output)]
    assert _run(IntraRunToolOutputTrimmer(max_input_chars=1_000), items) == items


def test_failed_write_keeps_arguments_and_error_details():
    failed_arguments = json.dumps({"id": "file-failed", "edits": [{"new_text": "X" * 10_000}]})
    failed_output = json.dumps(
        {
            "status": "success",
            "data": {"status": "error", "error": "target text not found: " + "diagnostic " * 500, "id": "file-failed"},
        }
    )
    items = [
        _fc("failed", "edit_file", failed_arguments),
        _fco("failed", failed_output),
        _fc("success", "edit_file", json.dumps({"id": "file-ok", "edits": []})),
        _fco("success", json.dumps({"status": "success", "data": {"status": "success", "id": "file-ok"}})),
    ]
    out = _run(IntraRunToolOutputTrimmer(max_input_chars=1_000), items)
    assert out[0]["arguments"] == failed_arguments
    assert out[1]["output"] == failed_output


def test_mixed_parallel_read_and_failed_write_keeps_full_recovery_context():
    arguments = json.dumps(
        {
            "tasks": [
                {"type": "query_files", "description": "read", "params": {"id": "file-read"}},
                {"type": "edit_file", "description": "write", "params": {"id": "file-write", "edits": [{"new_text": "X" * 10_000}]}},
            ]
        }
    )
    output = json.dumps(
        {
            "status": "success",
            "data": {
                "status": "partial",
                "tasks": [
                    {"type": "query_files", "status": "completed", "result": BIG},
                    {"type": "edit_file", "status": "failed", "error": "conflict " * 1_000},
                ],
            },
        }
    )
    items = [
        _fc("mixed-failed", "parallel_execute", arguments),
        _fco("mixed-failed", output),
        _fc("later-read", "query_files"),
        _fco("later-read", BIG),
    ]
    out = _run(IntraRunToolOutputTrimmer(max_input_chars=1_000), items)
    assert out[0]["arguments"] == arguments
    assert out[1]["output"] == output


def test_write_without_output_keeps_execution_arguments():
    pending_arguments = json.dumps({"id": "file-pending", "content": "X" * 10_000})
    items = [
        _fc("pending", "create_file", pending_arguments),
        _fc("later", "create_file", json.dumps({"title": "later"})),
        _fco("later", json.dumps({"status": "success", "data": {"status": "success", "id": "file-later"}})),
    ]
    out = _run(IntraRunToolOutputTrimmer(max_input_chars=1_000), items)
    assert out[0]["arguments"] == pending_arguments


def test_parallel_summary_keeps_head_and_tail_file_references():
    ids = [f"file-{index:02d}" for index in range(20)]
    arguments = json.dumps(
        {"tasks": [{"type": "query_files", "description": file_id, "params": {"id": file_id}} for file_id in ids]}
    )
    output = json.dumps(
        {
            "status": "success",
            "data": {
                "tasks": [
                    {"type": "query_files", "status": "completed", "result": {"id": file_id, "content": BIG}}
                    for file_id in ids
                ]
            },
        }
    )
    items = [_fc("parallel-20", "parallel_execute", arguments), _fco("parallel-20", output)]
    out = _run(IntraRunToolOutputTrimmer(max_input_chars=5_000), items)
    assert all(file_id in out[1]["output"] for file_id in ids)


def test_empty_input_returns_unchanged():
    md = ModelInputData(input=[], instructions=None)
    assert IntraRunToolOutputTrimmer()(CallModelData(model_data=md, agent=None, context=None)).input == []


def test_does_not_mutate_original_items_when_compacting():
    original = [
        _fc("c1", "hybrid_search"),
        _fco("c1", BIG),
        _fc("c2", "hybrid_search"),
        _fco("c2", BIG),
    ]
    snapshot = copy.deepcopy(original)
    md = ModelInputData(input=original, instructions=None)
    out = IntraRunToolOutputTrimmer(max_input_chars=1_000)(CallModelData(model_data=md, agent=None, context=None))
    assert COMPACTION_REASON in out.input[1]["output"]
    assert original == snapshot


def test_metrics_measure_model_input_reduction_over_budget():
    reset_metrics_collector()
    items = [_fc("read-1", "query_files"), _fco("read-1", json.dumps({"id": "file-1", "content": "X" * 80_000}))]

    _run(IntraRunToolOutputTrimmer(max_input_chars=10_000), items)

    metrics = get_metrics_collector().get_all_metrics()
    before = metrics["histograms"][MODEL_INPUT_FILTER_CHARS_BEFORE]["summary"]
    after = metrics["histograms"][MODEL_INPUT_FILTER_CHARS_AFTER]["summary"]
    assert before["count"] == after["count"] == 1
    assert before["max"] > 80_000
    assert after["max"] < before["max"] / 4
    assert metrics["counters"][TOOL_OUTPUT_TRIMMED_TOTAL]["value"] == 1
    assert metrics["counters"][TOOL_OUTPUT_TRIMMED_CHARS]["value"] > 0


def test_no_trim_metrics_under_budget():
    reset_metrics_collector()
    _run(IntraRunToolOutputTrimmer(), [_fc("c1", "hybrid_search"), _fco("c1", BIG)])
    metrics = get_metrics_collector().get_all_metrics()
    assert TOOL_OUTPUT_TRIMMED_TOTAL not in metrics["counters"]
    before = metrics["histograms"][MODEL_INPUT_FILTER_CHARS_BEFORE]["summary"]
    after = metrics["histograms"][MODEL_INPUT_FILTER_CHARS_AFTER]["summary"]
    assert before["max"] == after["max"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_input_chars": 0},
        {"target_ratio": 0},
        {"target_ratio": 1},
        {"min_compact_chars": 0},
        {"preview_chars": -1},
    ],
)
def test_invalid_config_rejected(kwargs):
    with pytest.raises(ValueError):
        IntraRunToolOutputTrimmer(**kwargs)
