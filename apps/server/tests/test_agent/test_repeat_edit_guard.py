"""Unit tests for RepeatEditGuard (serial micro-edit convergence + post-write self-read guard)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.core.request_context import AgentRequestContext
from agent.openai_agents import repeat_edit_guard as guard_mod
from agent.openai_agents.repeat_edit_guard import (
    BULK_EDIT_MIN_ITEMS,
    EDIT_REPEAT_LIMIT_ERROR_TYPE,
    EXISTING_HARD_BLOCK_AT,
    EXISTING_HINT_AT,
    EXISTING_MICRO_BLOCK_AT,
    POST_WRITE_BLOCK_AT,
    POST_WRITE_HINT_AT,
    RepeatEditGuard,
)
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker
from agent.openai_agents.tools_adapter import build_agent_function_tools
from agent.tools.tool_context import _ReadSnapshots


def _edit_args(file_id: str, edits: list[dict], *, continue_on_error: bool = False) -> str:
    payload: dict = {"id": file_id, "edits": edits}
    if continue_on_error:
        payload["continue_on_error"] = True
    return json.dumps(payload, ensure_ascii=False)


def _edit_ok(
    file_id: str,
    *,
    title: str = "第1章 雨夜",
    file_type: str = "draft",
    new_length: int = 2200,
    new_word_count: int = 2150,
    edits_applied: int = 1,
    mutation_applied: bool = True,
) -> str:
    return json.dumps(
        {
            "status": "success",
            "mutation_applied": mutation_applied,
            "data": {
                "id": file_id,
                "title": title,
                "file_type": file_type,
                "edits_applied": edits_applied,
                "new_length": new_length,
                "new_word_count": new_word_count,
            },
        },
        ensure_ascii=False,
    )


def _edit_err(error_type: str, *, error: str = "not found", status: str = "error") -> str:
    return json.dumps(
        {
            "status": status,
            "error_type": error_type,
            "error": error,
            "mutation_applied": False,
            "edits_applied": 0,
        },
        ensure_ascii=False,
    )


def _run_edit(guard: RepeatEditGuard, raw_args: str, output: str) -> dict:
    plan = guard.plan("edit_file", raw_args)
    if plan.blocked_output is not None:
        return json.loads(plan.blocked_output)
    return json.loads(guard.observe("edit_file", plan, output))


# ------------------------------------------------------------------ positive cases


@pytest.mark.unit
def test_post_write_chapter_second_hint_and_third_block(monkeypatch):
    """Newly written chapter: 1st normal, 2nd hint, 3rd blocked with word_count + INFO log."""
    req_ctx = AgentRequestContext(
        session=None, user_id="u", project_id="p", session_id="s", read_snapshots=_ReadSnapshots()
    )
    logged = MagicMock(wraps=guard_mod.log_with_context)
    monkeypatch.setattr(guard_mod, "log_with_context", logged)
    try:
        req_ctx.mark_chapter_body_saved("ch1", "第1章 启程", "draft")
        req_ctx.record_written_snapshot("ch1", "甲" * 2300, full=True)
        guard = RepeatEditGuard(request_context=req_ctx)

        args1 = _edit_args("ch1", [{"op": "replace", "old": "甲" * 30, "new": "乙" * 20}])
        out1 = _run_edit(guard, args1, _edit_ok("ch1", title="第1章 启程", new_length=2290, new_word_count=2240))
        assert out1["status"] == "success"
        assert "edit_hint" not in out1

        args2 = _edit_args("ch1", [{"op": "replace", "old": "甲" * 25, "new": "丙" * 15}])
        out2 = _run_edit(guard, args2, _edit_ok("ch1", title="第1章 启程", new_length=2280, new_word_count=2210))
        assert out2["status"] == "success"
        assert "edit_hint" in out2
        assert str(POST_WRITE_HINT_AT) in out2["edit_hint"]
        assert guard.hinted_edits == 1

        # 3rd clean edit is soft-skipped synchronously with word_count (non-error status)
        args3 = _edit_args("ch1", [{"op": "replace", "old": "甲" * 20, "new": "丁" * 10}])
        plan3 = guard.plan("edit_file", args3)
        assert plan3.blocked_output is not None
        out3 = json.loads(plan3.blocked_output)
        assert out3["status"] == "skipped"
        assert out3["guard_skipped"] is True
        assert out3["reason"] == "post_write_limit"
        assert out3["error_type"] == EDIT_REPEAT_LIMIT_ERROR_TYPE
        assert "error" not in out3
        assert out3["mutation_applied"] is False
        assert out3["edits_applied"] == 0
        assert out3["word_count"] == 2210
        assert "2210" in out3["message"]
        assert guard.blocked_edits == 1
        assert POST_WRITE_BLOCK_AT == 3

        trigger_logs = [
            call for call in logged.call_args_list if len(call.args) > 2 and call.args[2] == "RepeatEditGuard triggered"
        ]
        assert len(trigger_logs) == 1
        assert trigger_logs[0].kwargs["file_id"] == "ch1"
        assert trigger_logs[0].kwargs["written_this_turn"] is True
        assert trigger_logs[0].kwargs["clean_calls"] == 2
        assert trigger_logs[0].kwargs["reason"] == "post_write_limit"
    finally:
        req_ctx.clear()


@pytest.mark.unit
def test_existing_file_fourth_hint_and_fifth_micro_shaving_block():
    """Existing file: 1st-3rd normal, 4th hint (+ 60% rewrite note), 5th micro-shaving blocked."""
    req_ctx = AgentRequestContext(
        session=None, user_id="u", project_id="p", session_id="s", read_snapshots=_ReadSnapshots()
    )
    try:
        # 1000-char existing file (not in saved_bodies_this_request)
        req_ctx.record_written_snapshot("ex1", "文" * 1000, full=True)
        guard = RepeatEditGuard(request_context=req_ctx)

        length = 1000
        wc = 980
        for step in range(1, EXISTING_HINT_AT + 1):
            # Replace 200 chars with 180 chars: cumulative replaced = 800 (>= 60% of 1000),
            # net delta = 20 <= 80 -> micro call!
            args = _edit_args("ex1", [{"op": "replace", "old": "文" * 200, "new": "新" * 180}])
            length -= 20
            wc -= 20
            out = _run_edit(guard, args, _edit_ok("ex1", title="第2章 旧稿", new_length=length, new_word_count=wc))
            assert out["status"] == "success"
            if step < EXISTING_HINT_AT:
                assert "edit_hint" not in out
            else:
                assert "edit_hint" in out
                assert "write_chapter" in out["edit_hint"]

        # 5th call: consecutive_micro_calls == 4 >= 2 -> soft-skipped!
        args5 = _edit_args("ex1", [{"op": "replace", "old": "新" * 30, "new": "改" * 20}])
        plan5 = guard.plan("edit_file", args5)
        assert plan5.blocked_output is not None
        out5 = json.loads(plan5.blocked_output)
        assert out5["status"] == "skipped"
        assert out5["guard_skipped"] is True
        assert out5["reason"] == "existing_micro_loop"
        assert out5["error_type"] == EDIT_REPEAT_LIMIT_ERROR_TYPE
        assert "error" not in out5
        assert out5["word_count"] == wc
        assert EXISTING_MICRO_BLOCK_AT == 5
        assert guard.blocked_edits == 1
    finally:
        req_ctx.clear()


@pytest.mark.unit
def test_existing_file_substantial_edits_allowed_up_to_seven_and_safety_cap_at_eight():
    """Substantial non-micro edits (|new-old| > 80) succeed up to 7 calls; 8th hits safety cap."""
    guard = RepeatEditGuard()
    length = 3000
    for step in range(1, EXISTING_HARD_BLOCK_AT):
        # Net delta = 150 > 80 -> NOT micro, so consecutive_micro_calls stays 0
        args = _edit_args("ex2", [{"op": "replace", "old": "旧" * 50, "new": "新" * 200}])
        length += 150
        out = _run_edit(guard, args, _edit_ok("ex2", new_length=length, new_word_count=length - 50))
        assert out["status"] == "success"
        if step >= EXISTING_HINT_AT:
            assert "edit_hint" in out
        else:
            assert "edit_hint" not in out

    assert guard.blocked_edits == 0
    # 8th non-bulk call hits safety cap
    args8 = _edit_args("ex2", [{"op": "replace", "old": "旧" * 50, "new": "新" * 200}])
    plan8 = guard.plan("edit_file", args8)
    assert plan8.blocked_output is not None
    out8 = json.loads(plan8.blocked_output)
    assert out8["error_type"] == EDIT_REPEAT_LIMIT_ERROR_TYPE
    assert out8["word_count"] == length - 50
    assert guard.blocked_edits == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_edit_repeat_limit_excluded_from_tool_failure_breaker_and_adapter():
    """Blocked edit_repeat_limit responses never trip ToolFailureBreaker and skip tool execution."""
    req_ctx = AgentRequestContext(
        session=None, user_id="u", project_id="p", session_id="s", read_snapshots=_ReadSnapshots()
    )
    try:
        req_ctx.mark_chapter_body_saved("ch1", "第1章", "draft")
        breaker = ToolFailureBreaker()
        read_guard = RepeatReadGuard(request_context=req_ctx)
        edit_guard = RepeatEditGuard(request_context=req_ctx)
        executed = 0

        async def fake_edit(args, **_kwargs):
            nonlocal executed
            executed += 1
            return {"content": [{"type": "text", "text": _edit_ok("ch1", new_length=2100 - executed * 10, new_word_count=2050 - executed * 10)}]}

        with patch.dict("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"edit_file": fake_edit}):
            tools = {
                t.name: t
                for t in build_agent_function_tools(
                    failure_breaker=breaker,
                    read_guard=read_guard,
                    edit_guard=edit_guard,
                )
            }
            raw = _edit_args("ch1", [{"op": "replace", "old": "旧句", "new": "新句"}])
            # 1st and 2nd execute cleanly
            r1 = json.loads(await tools["edit_file"].on_invoke_tool(req_ctx, raw))
            r2 = json.loads(await tools["edit_file"].on_invoke_tool(req_ctx, raw))
            assert r1["status"] == "success" and "edit_hint" not in r1
            assert r2["status"] == "success" and "edit_hint" in r2
            assert executed == 2

            # Repeated soft-skipped calls (even 12x with identical args) NEVER trip ToolFailureBreaker!
            for _ in range(12):
                rb = json.loads(await tools["edit_file"].on_invoke_tool(req_ctx, raw))
                assert rb["status"] == "skipped"
                assert rb["guard_skipped"] is True
                assert rb["error_type"] == EDIT_REPEAT_LIMIT_ERROR_TYPE
                assert rb["mutation_applied"] is False
                assert rb["word_count"] == 2030

            assert executed == 2, "blocked edits must never invoke underlying tool handler"
            assert breaker.is_open is False
            assert breaker.total_failures == 0
    finally:
        req_ctx.clear()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_self_read_guard_after_write_chapter_and_anchor_recovery():
    """query_files(id=ch1) and start_char=0 on a chapter written this turn are guarded; anchor_not_found unlocks 1 recovery read."""
    req_ctx = AgentRequestContext(
        session=None, user_id="u", project_id="p", session_id="s", read_snapshots=_ReadSnapshots()
    )
    try:
        req_ctx.mark_chapter_body_saved("ch1", "第1章 初遇", "draft")
        req_ctx.record_written_snapshot("ch1", "雨落长安街。" * 400, full=True)
        edit_guard = RepeatEditGuard(request_context=req_ctx)
        read_guard = RepeatReadGuard(request_context=req_ctx)
        breaker = ToolFailureBreaker()
        query_calls: list[dict] = []

        async def fake_query(args, **_kwargs):
            query_calls.append(dict(args))
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"status": "success", "data": [{"id": args.get("id", "ch1"), "title": "第1章 初遇", "content": "全文"}]},
                            ensure_ascii=False,
                        ),
                    }
                ]
            }

        async def fake_edit(args, **_kwargs):
            return {"content": [{"type": "text", "text": _edit_err("anchor_not_found", error="找不到锚点")}]}

        with patch.dict(
            "agent.openai_agents.tools_adapter.TOOL_FUNCTIONS",
            {"query_files": fake_query, "edit_file": fake_edit},
        ):
            tools = {
                t.name: t
                for t in build_agent_function_tools(
                    failure_breaker=breaker,
                    read_guard=read_guard,
                    edit_guard=edit_guard,
                )
            }
            # 1) Full self-read guarded (never hits DB/tool)
            res_full = json.loads(await tools["query_files"].on_invoke_tool(req_ctx, json.dumps({"id": "ch1"})))
            assert res_full["status"] == "success"
            assert res_full["self_read_guarded"] is True
            assert res_full["data"][0]["content_omitted"] is True
            assert res_full["data"][0]["word_count"] == 2000
            assert "read_hint" in res_full
            assert len(query_calls) == 0

            # 2) start_char=0 self-read also guarded
            res_zero = json.loads(
                await tools["query_files"].on_invoke_tool(
                    req_ctx, json.dumps({"id": "ch1", "start_char": 0, "max_chars": 6000})
                )
            )
            assert res_zero["self_read_guarded"] is True
            assert len(query_calls) == 0
            assert edit_guard.guarded_self_reads == 2

            # 3) Existing file, keyword search, or multi-file ids are NOT guarded
            await tools["query_files"].on_invoke_tool(req_ctx, json.dumps({"id": "outline-1"}))
            await tools["query_files"].on_invoke_tool(req_ctx, json.dumps({"id": "ch1", "query": "长安"}))
            await tools["query_files"].on_invoke_tool(req_ctx, json.dumps({"ids": ["ch1", "outline-1"]}))
            assert len(query_calls) == 3

            # 4) After anchor_not_found on ch1, ONE recovery read is allowed, then guarded again
            err_out = json.loads(
                await tools["edit_file"].on_invoke_tool(
                    req_ctx, _edit_args("ch1", [{"op": "replace", "old": "不存在", "new": "新"}])
                )
            )
            assert err_out["error_type"] == "anchor_not_found"

            rec_read = json.loads(await tools["query_files"].on_invoke_tool(req_ctx, json.dumps({"id": "ch1"})))
            assert rec_read.get("self_read_guarded") is not True
            assert len(query_calls) == 4

            # Subsequent redundant self-read is guarded again
            after_rec = json.loads(await tools["query_files"].on_invoke_tool(req_ctx, json.dumps({"id": "ch1"})))
            assert after_rec["self_read_guarded"] is True
            assert len(query_calls) == 4
    finally:
        req_ctx.clear()


# ------------------------------------------------------------------ negative / exemption cases


@pytest.mark.unit
def test_bulk_outline_edits_exempt_from_micro_caps():
    """Bulk batches (len(edits) >= 6) on existing files are never counted, hinted, or blocked."""
    guard = RepeatEditGuard()
    bulk_edits = [{"op": "replace", "old": f"旧{i}", "new": f"新{i}"} for i in range(BULK_EDIT_MIN_ITEMS)]
    for step in range(12):
        args = _edit_args("outline-1", bulk_edits, continue_on_error=True)
        out = _run_edit(
            guard,
            args,
            _edit_ok("outline-1", title="分章大纲", file_type="outline", new_length=15000 + step, new_word_count=14500),
        )
        assert out["status"] == "success"
        assert "edit_hint" not in out
    assert guard.blocked_edits == 0
    assert guard.hinted_edits == 0


@pytest.mark.unit
def test_bulk_batch_breaks_micro_streak_on_existing_file():
    """A bulk batch on an existing file resets consecutive_micro_calls and executes even after 4 micro calls."""
    guard = RepeatEditGuard()
    for step in range(4):
        args = _edit_args("ch2", [{"op": "replace", "old": "旧句", "new": "新句"}])
        out = _run_edit(guard, args, _edit_ok("ch2", new_length=3000 - step * 5, new_word_count=2900))
        assert out["status"] == "success"

    # Now 4 micro calls happened (next non-bulk would be blocked), BUT a bulk batch (>=6) is allowed and resets streak!
    bulk = [{"op": "replace", "old": f"段落{i}", "new": f"改段{i}"} for i in range(6)]
    out_bulk = _run_edit(guard, _edit_args("ch2", bulk), _edit_ok("ch2", new_length=3020, new_word_count=2920))
    assert out_bulk["status"] == "success"
    assert guard.blocked_edits == 0

    # Follow-up single non-bulk edit now sees consecutive_micro_calls == 0 so it is allowed (clean_non_bulk_calls=5)
    out_next = _run_edit(
        guard,
        _edit_args("ch2", [{"op": "replace", "old": "一处", "new": "另一处"}]),
        _edit_ok("ch2", new_length=3021, new_word_count=2921),
    )
    assert out_next["status"] == "success"


@pytest.mark.unit
def test_append_continuation_always_exempt_on_new_and_existing_files():
    """op=append calls (e.g. _BODY_CUT_CONTEXT continuation) are never counted or blocked."""
    req_ctx = AgentRequestContext(
        session=None, user_id="u", project_id="p", session_id="s", read_snapshots=_ReadSnapshots()
    )
    try:
        req_ctx.mark_chapter_body_saved("ch1", "第1章", "draft")
        guard = RepeatEditGuard(request_context=req_ctx)

        # Even 6 consecutive appends on a newly written chapter are allowed without hints/blocks
        for step in range(6):
            args = _edit_args("ch1", [{"op": "append", "text": "续写段落。" * 5}])
            out = _run_edit(guard, args, _edit_ok("ch1", new_length=1500 + step * 25, new_word_count=1400 + step * 20))
            assert out["status"] == "success"
            assert "edit_hint" not in out

        # And normal post-write edit budget is still intact (1st edit still has no hint)
        out_first_replace = _run_edit(
            guard,
            _edit_args("ch1", [{"op": "replace", "old": "错字", "new": "正字"}]),
            _edit_ok("ch1", new_length=1700, new_word_count=1600),
        )
        assert out_first_replace["status"] == "success"
        assert "edit_hint" not in out_first_replace
        assert guard.blocked_edits == 0
    finally:
        req_ctx.clear()


@pytest.mark.unit
def test_failed_and_partial_edits_not_counted():
    """anchor_not_found, stale_content, partial, and no-op edits never count toward RepeatEditGuard caps."""
    req_ctx = AgentRequestContext(
        session=None, user_id="u", project_id="p", session_id="s", read_snapshots=_ReadSnapshots()
    )
    try:
        req_ctx.mark_chapter_body_saved("ch1", "第1章", "draft")
        guard = RepeatEditGuard(request_context=req_ctx)
        raw = _edit_args("ch1", [{"op": "replace", "old": "旧", "new": "新"}])

        # 5 errors/partials/no-ops do NOT exhaust the post-write budget
        for err in (
            _edit_err("anchor_not_found"),
            _edit_err("stale_content"),
            _edit_err("anchor_ambiguous"),
            json.dumps({"status": "partial", "mutation_applied": True, "data": {"id": "ch1", "new_word_count": 2000}}),
            _edit_ok("ch1", mutation_applied=False, edits_applied=0),
        ):
            out = _run_edit(guard, raw, err)
            assert out.get("error_type") != EDIT_REPEAT_LIMIT_ERROR_TYPE

        # 1st real clean edit still succeeds with no hint!
        out1 = _run_edit(guard, raw, _edit_ok("ch1", new_length=2000, new_word_count=1950))
        assert out1["status"] == "success"
        assert "edit_hint" not in out1
        assert guard.blocked_edits == 0
    finally:
        req_ctx.clear()


@pytest.mark.unit
def test_multi_file_edits_across_ten_files_tracked_independently():
    """Editing 10 different existing files 3 times each never interferes or triggers hints/blocks."""
    guard = RepeatEditGuard()
    for round_idx in range(3):
        for file_idx in range(10):
            fid = f"file-{file_idx}"
            args = _edit_args(fid, [{"op": "replace", "old": "旧名", "new": "新名"}])
            out = _run_edit(
                guard,
                args,
                _edit_ok(fid, title=f"文件{file_idx}", new_length=1200 + round_idx, new_word_count=1150),
            )
            assert out["status"] == "success"
            assert "edit_hint" not in out

    assert guard.blocked_edits == 0
    assert guard.hinted_edits == 0


@pytest.mark.unit
def test_post_write_allows_one_bulk_self_check_revision_before_post_write_limit():
    """Newly written chapter: a bulk revision (>=6 edits) is allowed even after 2 small edits;
    a 2nd bulk revision or any further small edit is then soft-skipped cleanly."""
    req_ctx = AgentRequestContext(
        session=None, user_id="u", project_id="p", session_id="s", read_snapshots=_ReadSnapshots()
    )
    try:
        req_ctx.mark_chapter_body_saved("ch1", "第1章 雨夜", "draft")
        guard = RepeatEditGuard(request_context=req_ctx)

        small = _edit_args("ch1", [{"op": "replace", "old": "旧句", "new": "新句"}])
        out1 = _run_edit(guard, small, _edit_ok("ch1", new_length=2200, new_word_count=2150))
        out2 = _run_edit(guard, small, _edit_ok("ch1", new_length=2190, new_word_count=2140))
        assert out1["status"] == "success" and out2["status"] == "success"
        assert "edit_hint" in out2

        # 3rd call is a bulk self-check batch (>= BULK_EDIT_MIN_ITEMS): MUST be allowed!
        bulk = _edit_args(
            "ch1",
            [{"op": "replace", "old": f"旧段{i}", "new": f"新段{i}"} for i in range(BULK_EDIT_MIN_ITEMS + 3)],
            continue_on_error=True,
        )
        out_bulk = _run_edit(guard, bulk, _edit_ok("ch1", new_length=2260, new_word_count=2210, edits_applied=9))
        assert out_bulk["status"] == "success"
        assert guard.blocked_edits == 0

        # 2nd bulk revision on the same newly written chapter hits post_write_limit (bounded!).
        out_bulk2 = _run_edit(guard, bulk, _edit_ok("ch1", new_length=2270, new_word_count=2220, edits_applied=9))
        assert out_bulk2["status"] == "skipped"
        assert out_bulk2["guard_skipped"] is True
        assert out_bulk2["reason"] == "post_write_limit"
        assert out_bulk2["mutation_applied"] is False
        assert out_bulk2["word_count"] == 2210
        assert guard.blocked_edits == 1
    finally:
        req_ctx.clear()


@pytest.mark.unit
def test_post_write_bulk_first_preserves_small_edit_budget_and_stays_bounded():
    """Doing the 1 allowed bulk revision first still leaves the normal 2 small edits before soft-skip."""
    req_ctx = AgentRequestContext(
        session=None, user_id="u", project_id="p", session_id="s", read_snapshots=_ReadSnapshots()
    )
    try:
        req_ctx.mark_chapter_body_saved("ch1", "第1章 雨夜", "draft")
        guard = RepeatEditGuard(request_context=req_ctx)

        bulk = _edit_args(
            "ch1",
            [{"op": "replace", "old": f"旧{i}", "new": f"新{i}"} for i in range(BULK_EDIT_MIN_ITEMS)],
        )
        small = _edit_args("ch1", [{"op": "replace", "old": "微调前", "new": "微调后"}])

        out_bulk = _run_edit(guard, bulk, _edit_ok("ch1", new_length=2300, new_word_count=2250, edits_applied=6))
        assert out_bulk["status"] == "success"
        assert "edit_hint" not in out_bulk

        out_s1 = _run_edit(guard, small, _edit_ok("ch1", new_length=2301, new_word_count=2251))
        out_s2 = _run_edit(guard, small, _edit_ok("ch1", new_length=2302, new_word_count=2252))
        assert out_s1["status"] == "success" and "edit_hint" not in out_s1
        assert out_s2["status"] == "success" and "edit_hint" in out_s2

        out_s3 = _run_edit(guard, small, _edit_ok("ch1", new_length=2303, new_word_count=2253))
        assert out_s3["status"] == "skipped"
        assert out_s3["guard_skipped"] is True
        assert out_s3["error_type"] == EDIT_REPEAT_LIMIT_ERROR_TYPE
        assert guard.blocked_edits == 1
    finally:
        req_ctx.clear()


@pytest.mark.unit
def test_soft_skip_status_does_not_pollute_error_card_telemetry_or_reset_breaker_streaks():
    """Soft-skip maps to non-error status in round_outcome/telemetry, does not count as a write,
    and neither increments nor resets ToolFailureBreaker failure streaks."""
    from agent.core.round_outcome import tool_result_status

    req_ctx = AgentRequestContext(
        session=None, user_id="u", project_id="p", session_id="s", read_snapshots=_ReadSnapshots()
    )
    try:
        req_ctx.mark_chapter_body_saved("ch1", "第1章", "draft")
        guard = RepeatEditGuard(request_context=req_ctx)
        small = _edit_args("ch1", [{"op": "replace", "old": "旧", "new": "新"}])
        _run_edit(guard, small, _edit_ok("ch1", new_length=2000, new_word_count=1950))
        _run_edit(guard, small, _edit_ok("ch1", new_length=1995, new_word_count=1945))
        skipped_text = guard.plan("edit_file", small).blocked_output
        assert skipped_text is not None
        skipped = json.loads(skipped_text)

        # StreamAdapter / ChatMessage.tool_calls status is non-error ("success") with no leaked raw error text.
        card_status, card_data = tool_result_status("edit_file", skipped)
        assert card_status == "success"
        assert card_data["guard_skipped"] is True
        assert card_data["mutation_applied"] is False
        assert "error" not in skipped

        # ToolFailureBreaker treats soft-skip as neutral: does NOT clear an active file_not_found family!
        breaker = ToolFailureBreaker()
        for bad_id in ("bad-1", "bad-2"):
            breaker.observe(
                "edit_file",
                _edit_args(bad_id, [{"op": "replace", "old": "x", "new": "y"}]),
                _edit_err("file_not_found"),
            )
        assert breaker.total_failures == 2 and not breaker.is_open

        assert breaker.observe("edit_file", small, skipped_text) == skipped_text
        assert breaker.total_failures == 2 and not breaker.is_open

        # 3rd file_not_found STILL trips recovery family immediately (soft-skip did not clear it).
        breaker.observe(
            "edit_file",
            _edit_args("bad-3", [{"op": "replace", "old": "x", "new": "y"}]),
            _edit_err("file_not_found"),
        )
        assert breaker.is_open and breaker.trip.reason == "repeated_recoverable_tool_failure"
    finally:
        req_ctx.clear()
