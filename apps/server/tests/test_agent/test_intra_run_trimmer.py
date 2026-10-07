"""Tests for the intra-run tool-output trimmer (call_model_input_filter)."""

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
from agent.openai_agents.intra_run_trimmer import IntraRunToolOutputTrimmer

BIG = "X" * 5000  # well above the default 2000-char trim threshold
SMALL = "ok"


def _fc(call_id: str, name: str) -> dict[str, Any]:
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": "{}"}


def _fco(call_id: str, output: str) -> dict[str, Any]:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def _run(trimmer: IntraRunToolOutputTrimmer, items: list[dict[str, Any]]) -> list[Any]:
    md = ModelInputData(input=[dict(it) for it in items], instructions=None)
    data = CallModelData(model_data=md, agent=None, context=None)
    return trimmer(data).input


def _outputs(items: list[Any]) -> list[str]:
    return [it["output"] for it in items if it.get("type") == "function_call_output"]


def test_keeps_recent_trims_stale():
    """With 3 big retrieval outputs and keep_recent=2, only the oldest is previewed."""
    items = [
        {"role": "user", "content": "do it"},
        _fc("c1", "hybrid_search"),
        _fco("c1", BIG),
        _fc("c2", "hybrid_search"),
        _fco("c2", BIG),
        _fc("c3", "query_files"),
        _fco("c3", BIG),
    ]
    out = _run(IntraRunToolOutputTrimmer(keep_recent=2), items)
    outs = _outputs(out)
    assert "stale read output compacted" in outs[0]  # c1 previewed
    assert outs[1] == BIG  # c2 kept full
    assert outs[2] == BIG  # c3 kept full


def test_conservative_below_keep_recent():
    """With <= keep_recent trimmable outputs, nothing is trimmed."""
    items = [
        {"role": "user", "content": "do it"},
        _fc("c1", "hybrid_search"),
        _fco("c1", BIG),
        _fc("c2", "query_files"),
        _fco("c2", BIG),
    ]
    out = _run(IntraRunToolOutputTrimmer(keep_recent=2), items)
    assert _outputs(out) == [BIG, BIG]


def test_small_outputs_untouched():
    """Outputs below max_output_chars are never previewed, even if stale."""
    items = [
        _fc("c1", "hybrid_search"),
        _fco("c1", SMALL),
        _fc("c2", "hybrid_search"),
        _fco("c2", SMALL),
        _fc("c3", "hybrid_search"),
        _fco("c3", SMALL),
    ]
    out = _run(IntraRunToolOutputTrimmer(keep_recent=1), items)
    assert _outputs(out) == [SMALL, SMALL, SMALL]


def test_excludes_non_allowlisted_tools():
    """Control-flow / non-retrieval tool outputs are never trimmed."""
    items = [
        _fc("c1", "handoff_to_agent"),
        _fco("c1", BIG),
        _fc("c2", "request_clarification"),
        _fco("c2", BIG),
        _fc("c3", "create_file"),
        _fco("c3", BIG),
    ]
    out = _run(IntraRunToolOutputTrimmer(keep_recent=1), items)
    assert _outputs(out) == [BIG, BIG, BIG]


def test_only_stale_allowlisted_trimmed_mixed():
    """A mix: only the stale allowlisted output is previewed; others untouched."""
    items = [
        _fc("c1", "hybrid_search"),
        _fco("c1", BIG),  # stale allowlisted -> trimmed
        _fc("c2", "handoff_to_agent"),
        _fco("c2", BIG),  # excluded -> full
        _fc("c3", "query_files"),
        _fco("c3", BIG),  # recent allowlisted -> full
    ]
    out = _run(IntraRunToolOutputTrimmer(keep_recent=1), items)
    outs = _outputs(out)
    assert "stale read output compacted" in outs[0]
    assert outs[1] == BIG
    assert outs[2] == BIG


def test_empty_input_returns_unchanged():
    md = ModelInputData(input=[], instructions=None)
    data = CallModelData(model_data=md, agent=None, context=None)
    assert IntraRunToolOutputTrimmer()(data).input == []


def test_does_not_mutate_original_items():
    original = [
        _fc("c1", "hybrid_search"),
        _fco("c1", BIG),
        _fc("c2", "hybrid_search"),
        _fco("c2", BIG),
        _fc("c3", "hybrid_search"),
        _fco("c3", BIG),
    ]
    snapshot = copy.deepcopy(original)
    md = ModelInputData(input=original, instructions=None)
    IntraRunToolOutputTrimmer(keep_recent=2)(CallModelData(model_data=md, agent=None, context=None))
    assert original == snapshot  # filter must not mutate the caller's items


def test_budget_compacts_even_the_latest_oversized_read_without_dropping_pair():
    """A single latest full-file read must not bypass the runtime input budget."""
    items = [
        {"role": "user", "content": "请检查这个文件"},
        _fc("read-1", "query_files"),
        _fco(
            "read-1",
            json.dumps(
                {
                    "status": "success",
                    "data": {"id": "file-123", "content": "正文" * 80_000},
                },
                ensure_ascii=False,
            ),
        ),
    ]
    original = copy.deepcopy(items)

    out = _run(IntraRunToolOutputTrimmer(max_input_chars=24_000), items)

    assert len(json.dumps(out, ensure_ascii=False)) < 30_000
    assert out[0] == items[0]
    assert out[1]["call_id"] == out[2]["call_id"] == "read-1"
    assert "file-123" in out[2]["output"]
    assert "original_chars" in out[2]["output"]
    assert items == original


def test_stale_write_arguments_and_receipt_are_summarized_but_latest_write_is_kept():
    old_arguments = json.dumps(
        {
            "id": "file-old",
            "edits": [{"old_text": "甲" * 10_000, "new_text": "乙" * 10_000}],
        },
        ensure_ascii=False,
    )
    latest_arguments = json.dumps(
        {"id": "file-new", "edits": [{"old_text": "旧", "new_text": "新"}]},
        ensure_ascii=False,
    )
    old_receipt = json.dumps(
        {"status": "success", "data": {"id": "file-old", "content": "乙" * 12_000}},
        ensure_ascii=False,
    )
    items = [
        _fc("write-1", "edit_file") | {"arguments": old_arguments},
        _fco("write-1", old_receipt),
        _fc("write-2", "edit_file") | {"arguments": latest_arguments},
        _fco("write-2", json.dumps({"status": "success", "data": {"id": "file-new"}})),
    ]

    out = _run(IntraRunToolOutputTrimmer(max_input_chars=100_000), items)

    assert len(out[0]["arguments"]) < 1_000
    assert "file-old" in out[0]["arguments"]
    assert "persisted write arguments elided" in out[0]["arguments"]
    assert len(out[1]["output"]) < 2_000
    assert "file-old" in out[1]["output"]
    assert out[2]["arguments"] == latest_arguments
    assert out[3]["output"] == items[3]["output"]


def test_parallel_execute_nested_read_result_is_compacted_and_traceable():
    parallel_args = json.dumps(
        {
            "tasks": [
                {
                    "type": "query_files",
                    "description": "读取第一章",
                    "params": {"id": "chapter-1", "response_mode": "full"},
                },
                {
                    "type": "hybrid_search",
                    "description": "搜索伏笔",
                    "params": {"query": "旧钥匙"},
                },
            ]
        },
        ensure_ascii=False,
    )
    parallel_output = json.dumps(
        {
            "status": "success",
            "data": {
                "tasks": [
                    {
                        "id": "task-1",
                        "type": "query_files",
                        "status": "completed",
                        "result": {"id": "chapter-1", "content": "甲" * 50_000},
                    },
                    {"id": "task-2", "type": "hybrid_search", "status": "completed", "result": "乙" * 50_000},
                ]
            },
        },
        ensure_ascii=False,
    )
    items = [
        _fc("parallel-1", "parallel_execute") | {"arguments": parallel_args},
        _fco("parallel-1", parallel_output),
    ]

    out = _run(IntraRunToolOutputTrimmer(max_input_chars=20_000), items)

    assert out[0]["name"] == "parallel_execute"
    assert out[0]["arguments"] == parallel_args
    assert out[1]["call_id"] == "parallel-1"
    assert len(out[1]["output"]) < 15_000
    assert "chapter-1" in out[1]["output"]
    assert "completed" in out[1]["output"]


def test_unknown_tool_and_control_flow_are_never_compacted_even_over_budget():
    huge = "Z" * 40_000
    items = [
        _fc("unknown-1", "future_tool"),
        _fco("unknown-1", huge),
        _fc("handoff-1", "handoff_to_agent"),
        _fco("handoff-1", huge),
        {"role": "user", "content": huge},
    ]

    out = _run(IntraRunToolOutputTrimmer(max_input_chars=1_000), items)

    assert out == items


def test_mixed_unknown_parallel_call_is_left_opaque():
    arguments = json.dumps(
        {
            "tasks": [
                {"type": "query_files", "description": "known", "params": {"id": "file-1"}},
                {
                    "type": "future_control_tool",
                    "description": "unknown",
                    "params": {"opaque": "A" * 20_000},
                },
            ]
        }
    )
    output = json.dumps(
        {
            "status": "success",
            "data": {
                "tasks": [
                    {"type": "query_files", "result": BIG},
                    {"type": "future_control_tool", "result": "B" * 20_000},
                ]
            },
        }
    )
    items = [
        _fc("parallel-mixed", "parallel_execute") | {"arguments": arguments},
        _fco("parallel-mixed", output),
    ]

    assert _run(IntraRunToolOutputTrimmer(max_input_chars=1_000), items) == items


def test_failed_write_keeps_arguments_and_error_details():
    failed_arguments = json.dumps({"id": "file-failed", "edits": [{"new_text": "X" * 10_000}]})
    failed_output = json.dumps(
        {
            "status": "success",
            "data": {
                "status": "error",
                "error": "target text not found: " + "diagnostic " * 500,
                "id": "file-failed",
            },
        }
    )
    items = [
        _fc("failed", "edit_file") | {"arguments": failed_arguments},
        _fco("failed", failed_output),
        _fc("success", "edit_file") | {"arguments": json.dumps({"id": "file-ok", "edits": []})},
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
                {
                    "type": "edit_file",
                    "description": "write",
                    "params": {"id": "file-write", "edits": [{"new_text": "X" * 10_000}]},
                },
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
        _fc("mixed-failed", "parallel_execute") | {"arguments": arguments},
        _fco("mixed-failed", output),
        _fc("later-read", "query_files"),
        _fco("later-read", BIG),
    ]

    out = _run(IntraRunToolOutputTrimmer(keep_recent=1, max_input_chars=1_000), items)

    assert out[0]["arguments"] == arguments
    assert out[1]["output"] == output


def test_write_without_success_output_keeps_execution_arguments():
    pending_arguments = json.dumps({"id": "file-pending", "content": "X" * 10_000})
    items = [
        _fc("pending", "create_file") | {"arguments": pending_arguments},
        _fc("later", "create_file") | {"arguments": json.dumps({"title": "later"})},
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
    items = [
        _fc("parallel-20", "parallel_execute") | {"arguments": arguments},
        _fco("parallel-20", output),
    ]

    out = _run(IntraRunToolOutputTrimmer(max_input_chars=5_000), items)

    assert all(file_id in out[1]["output"] for file_id in ids)


def test_metrics_measure_model_input_reduction():
    reset_metrics_collector()
    items = [
        _fc("read-1", "query_files"),
        _fco("read-1", json.dumps({"id": "file-1", "content": "X" * 80_000})),
    ]

    _run(IntraRunToolOutputTrimmer(max_input_chars=10_000), items)

    histograms = get_metrics_collector().get_all_metrics()["histograms"]
    before = histograms[MODEL_INPUT_FILTER_CHARS_BEFORE]["summary"]
    after = histograms[MODEL_INPUT_FILTER_CHARS_AFTER]["summary"]
    assert before["count"] == after["count"] == 1
    assert before["max"] > 80_000
    assert after["max"] < before["max"] / 4


def test_metrics_recorded_on_trim():
    reset_metrics_collector()
    items = [
        _fc("c1", "hybrid_search"),
        _fco("c1", BIG),
        _fc("c2", "hybrid_search"),
        _fco("c2", BIG),
        _fc("c3", "hybrid_search"),
        _fco("c3", BIG),
    ]
    _run(IntraRunToolOutputTrimmer(keep_recent=2), items)
    counters = get_metrics_collector().get_all_metrics()["counters"]
    assert counters[TOOL_OUTPUT_TRIMMED_TOTAL]["value"] == 1
    assert counters[TOOL_OUTPUT_TRIMMED_CHARS]["value"] > 0


def test_no_metrics_when_nothing_trimmed():
    reset_metrics_collector()
    items = [_fc("c1", "hybrid_search"), _fco("c1", BIG)]
    _run(IntraRunToolOutputTrimmer(keep_recent=2), items)
    counters = get_metrics_collector().get_all_metrics()["counters"]
    assert TOOL_OUTPUT_TRIMMED_TOTAL not in counters


@pytest.mark.parametrize(
    "kwargs",
    [
        {"keep_recent": -1},
        {"max_output_chars": 0},
        {"preview_chars": -1},
        {"max_input_chars": 0},
        {"recent_preview_chars": -1},
    ],
)
def test_invalid_config_rejected(kwargs):
    with pytest.raises(ValueError):
        IntraRunToolOutputTrimmer(**kwargs)
