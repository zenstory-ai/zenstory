"""Regression tests for the 2026-11-27 production audit fixes.

Covers all 5 confirmed production anomalies:
1. [P1] Parallel ``file_body_pending`` calls in one LLM reply turn must count at most once
   toward ``ToolFailureBreaker`` so they never trip ``too_many_tool_failures`` before
   ``runner._stop_run_after_tools`` hands off to ``FILE_BODY_RESUME``.
2. [P1] ``find_repetition_start`` / ``StreamProcessor.trim_repeated_tail`` must catch
   pure punctuation/separator repetition loops (e.g. ``\\n\\n　　——`` repeated 20+ times or
   long inline dash floods) while keeping short legitimate dividers and pure whitespace safe.
3. [P2] ``edit_file`` with ``continue_on_error=True`` and ``all_failed=True`` must expose a
   non-empty top-level ``error`` (so ``ToolFailureBreaker`` preserves ``last_error`` and error
   fingerprints), AND CRLF (``\\r\\n``) files must accept ``\\n``-based whitespace/blank-line
   edits in ``edit_file`` and ``batch_replace_files`` while preserving CRLF line endings.
4. [P2] Client-disconnected rounds must flush live ``tool_calls_count``, ``stop_reason``,
   ``first_tool_started_ms``, and ``tool_failures_count`` into ``agent_round_metric`` even
   when ``api/agent.py`` ``finish()`` runs before ``AgentService.process_stream`` ``finally``.
5. [P2] Soft-landing prompts (wall-clock / max_turns / budget) must inject factual written-file
   state (explicitly stating when 0 files were modified), continuation ``<file>`` runs must
   not accumulate protocol newlines into ``chat_message.content``, and multi-file runs hitting
   the collaboration cap must emit a factual closing summary instead of blank ``content=""``.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from agents import RunContextWrapper
from agents.run_config import ModelInputData
from sqlmodel import Session, select

from agent.core.author_facing_text import AgentTextShaper
from agent.core.events import EventType
from agent.core.run_meter import AgentRunMeter
from agent.core.stream_errors import REPETITION_LOOP_STOP_REASON
from agent.core.stream_processor import find_repetition_start
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.runner import (
    LOOP_SOFT_LANDING_PROMPT,
    LOOP_TIME_LANDING_PROMPT,
    _ModelCallCountingFilter,
)
from agent.openai_agents.tool_failure_breaker import (
    MAX_TOOL_FAILURES_PER_REQUEST,
    TRIP_REASON_TOTAL,
    ToolFailureBreaker,
)
from agent.openai_agents.tools_adapter import build_agent_function_tools
from agent.stream_adapter import create_stream_adapter
from agent.tools import mcp_tools
from agent.tools.file_ops.edit import EDIT_ERROR_ANCHOR_NOT_FOUND, EditFileError, FileEditor
from agent.tools.tool_context import AgentRequestContext
from models import AgentRoundMetric, ChatMessage, File, Project, User
from services.core.auth_service import hash_password
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _make_user_and_project(db_session: Session, monkeypatch, *, tag: str) -> tuple[User, Project]:
    import database

    suffix = f"{tag}-{uuid4().hex[:8]}"
    user = User(
        username=f"u_{suffix}",
        email=f"{suffix}@example.test",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    project = Project(name=f"测试作品-{suffix}", owner_id=user.id, project_type="novel")
    db_session.add_all([user, project])
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(project)
    engine = db_session.get_bind()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    return user, project


# ===========================================================================
# Fix 1: Parallel file_body_pending in a single reply must not trip breaker
# ===========================================================================


@pytest.mark.unit
def test_parallel_file_body_pending_in_same_reply_counts_once():
    """11 parallel file_body_pending rejections in the same reply_seq count as 1, not 11."""
    breaker = ToolFailureBreaker()
    rejection = json.dumps(
        {
            "status": "error",
            "error": "「第1章 唢呐走错门」已经建好但还没写正文，delete_file 未执行。",
            "error_type": "file_body_pending",
        },
        ensure_ascii=False,
    )

    for idx in range(11):
        out = breaker.observe(
            "delete_file",
            json.dumps({"id": f"old-chapter-{idx}"}),
            rejection,
            reply_seq=2,
        )
        assert out == rejection

    assert breaker.is_open is False
    assert breaker.total_failures == 1
    assert breaker.consecutive_failures == 1

    # Distinct later reply turns still count once each so the request cap remains bounded.
    for reply_seq in range(3, 3 + MAX_TOOL_FAILURES_PER_REQUEST - 1):
        breaker.observe("delete_file", json.dumps({"id": f"other-{reply_seq}"}), rejection, reply_seq=reply_seq)
    assert breaker.is_open is True
    assert breaker.trip is not None
    assert breaker.trip.reason == TRIP_REASON_TOTAL


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tools_adapter_parallel_delete_file_while_body_overdue_keeps_breaker_closed():
    """Reproduces prod run c705f8901f1c: 11 parallel delete_file calls while a chapter awaits <file>."""
    req_ctx = AgentRequestContext(session=None, user_id="u1", project_id="p1", session_id="s1")
    req_ctx.begin_model_reply()  # reply 1: write_chapter creates pending empty file
    req_ctx.set_pending_empty_file("ch-new", "第1章 唢呐走错门", version="v1", file_type="draft")
    req_ctx.begin_model_reply()  # reply 2: model emits 11 parallel delete_file calls

    breaker = ToolFailureBreaker()
    meter = AgentRunMeter()
    tools = {t.name: t for t in build_agent_function_tools(failure_breaker=breaker, run_meter=meter)}
    delete_tool = tools["delete_file"]
    ctx = RunContextWrapper(req_ctx)

    outputs = []
    for idx in range(11):
        raw = await delete_tool.on_invoke_tool(ctx, json.dumps({"id": f"old-{idx}"}))
        outputs.append(json.loads(raw))

    assert breaker.is_open is False
    assert breaker.total_failures == 1
    assert breaker.consecutive_failures == 1
    assert all(item["error_type"] == "file_body_pending" for item in outputs)
    assert all(item["error_type"] != "tool_failure_circuit_open" for item in outputs)


# ===========================================================================
# Fix 2: Repetition loop detector catches pure punctuation/separator loops
# ===========================================================================


@pytest.mark.unit
def test_repetition_detector_catches_dash_and_punctuation_line_loops_without_false_positives():
    """Reproduces prod run e60b9f63803e: repeated '\\n\\n　　——' must trigger and trim cleanly."""
    prose = "　　灵光散尽，归墟珠表面浮现出一道淡青色纹路，缓缓沉入丹田深处。"
    loop_body = prose + "\n\n　　——" * 25
    cut = find_repetition_start(loop_body)
    assert cut == len(prose)
    assert loop_body[:cut] == prose

    # Mid-stream partial trailing line (e.g. ending halfway through "　　—") still detected cleanly.
    partial_tail = prose + "\n\n　　——" * 25 + "\n\n　　—"
    assert find_repetition_start(partial_tail) == len(prose)

    # Pure symbol-only content trims to 0 (nothing bogus kept).
    assert find_repetition_start("——\n\n" * 25) == 0
    assert find_repetition_start("---\n" * 25) == 0

    # Inline single-line dash flood without newlines also caught once past 2*window.
    inline_flood = prose + "——" * 900
    inline_cut = find_repetition_start(inline_flood)
    assert inline_cut is not None
    assert inline_cut <= len(prose) + 4

    # Short legitimate dividers, Markdown table rules, and pure whitespace remain safe (None).
    assert find_repetition_start(prose + "\n\n　　——\n\n　　下一场戏开始。") is None
    assert find_repetition_start("——\n\n" * 5) is None
    assert find_repetition_start("---\n" * 5) is None
    assert find_repetition_start("| 字段 | 说明 |\n| --- | --- |\n| 境界 | 大乘 |\n") is None
    assert find_repetition_start("\n\n" * 1000) is None
    assert find_repetition_start("   \n" * 500) is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_adapter_trims_trailing_dash_repetition_before_saving():
    prose = "　　青光一闪，阵眼轰然合拢。"
    req_ctx = AgentRequestContext(project_id="p", user_id="u")
    adapter = create_stream_adapter(
        project_id="p", user_id="u", process_file_markers=True, request_context=req_ctx
    )
    adapter._save_file_content = AsyncMock(return_value=True)
    req_ctx.set_pending_empty_file("ch268", "第268章")
    adapter.set_pending_file_write("ch268", "draft", "第268章")

    async def events():
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"<file>\n{prose}" + "\n\n　　——" * 40})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": REPETITION_LOOP_STOP_REASON})
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data={
                "code": "ERR_AGENT_REPETITION_LOOP",
                "retryable": False,
                "refundable": False,
                "reason": REPETITION_LOOP_STOP_REASON,
            },
        )

    out = [e async for e in adapter.process_workflow_events(events())]

    adapter._save_file_content.assert_awaited_once()
    assert adapter._save_file_content.await_args.args[:2] == ("ch268", prose)
    assert any(e.type == EventType.ERROR and e.data["reason"] == REPETITION_LOOP_STOP_REASON for e in out)


# ===========================================================================
# Fix 3: edit_file all_failed top-level error + CRLF blank-line matching
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_edit_file_continue_on_error_all_failed_sets_top_level_error_and_breaker_sees_it(
    db_session: Session, monkeypatch
):
    """Reproduces prod run aa5936b0a0df: continue_on_error=True all_failed had empty last_error."""
    user, project = _make_user_and_project(db_session, monkeypatch, tag="allfail")
    chapter = File(project_id=project.id, title="第1章", file_type="draft", content="　　第一段。\n\n　　第二段。")
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="s-allfail",
        read_snapshots={chapter.id: chapter.content},
    )
    try:
        res_text = (
            await mcp_tools.edit_file(
                {
                    "id": chapter.id,
                    "continue_on_error": True,
                    "edits": [
                        {"op": "replace", "old": "不存在的片段甲", "new": "新甲"},
                        {"op": "replace", "old": "不存在的片段乙", "new": "新乙"},
                    ],
                },
                request_context=req_ctx,
            )
        )["content"][0]["text"]
    finally:
        req_ctx.clear()

    parsed = json.loads(res_text)
    assert parsed["status"] == "error"
    assert parsed["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND
    assert isinstance(parsed.get("error"), str) and parsed["error"].strip()
    assert "找不到" in parsed["error"]

    breaker = ToolFailureBreaker()
    args = json.dumps({"id": chapter.id, "continue_on_error": True, "edits": []})
    for _ in range(3):
        breaker.observe("edit_file", args, res_text)
    assert breaker.is_open is True
    assert breaker.trip is not None
    assert breaker.trip.last_error != ""
    assert "找不到" in breaker.trip.last_error


@pytest.mark.unit
def test_edit_file_crlf_whitespace_and_newline_replace_preserves_crlf():
    """Reproduces prod sess 0bcf97b3 / f8679bc4: editing blank lines in Windows CRLF files."""
    editor = FileEditor(session=None)
    crlf_content = "　　第一段。\r\n\r\n\r\n\r\n　　第二段。\r\n\r\n\r\n\r\n　　第三段。"

    # 1) Pure blank-line replace_all with \n on CRLF file (default auto mode)
    applied: list[dict] = []
    out = editor._apply_replace(
        crlf_content,
        {"op": "replace", "old": "\n\n\n\n", "new": "\n\n", "replace_all": True},
        0,
        applied,
        [],
    )
    assert out == "　　第一段。\r\n\r\n　　第二段。\r\n\r\n　　第三段。"
    assert applied[0]["count"] == 2
    assert "\r\n" in out and "\r\r\n" not in out

    # 2) Same edit under exact_normalized (unread file) also succeeds cleanly
    applied_norm: list[dict] = []
    out_norm = editor._apply_replace(
        crlf_content,
        {
            "op": "replace",
            "old": "\n\n\n\n",
            "new": "\n\n",
            "replace_all": True,
            "match_mode": "exact_normalized",
        },
        0,
        applied_norm,
        [],
    )
    assert out_norm == "　　第一段。\r\n\r\n　　第二段。\r\n\r\n　　第三段。"

    # 3) Explicit match_mode="exact" stays strictly byte-literal and still fails with CRLF hint
    with pytest.raises(EditFileError) as exc_exact:
        editor._apply_replace(
            crlf_content,
            {"op": "replace", "old": "\n\n\n\n", "new": "\n\n", "match_mode": "exact"},
            0,
            [],
            [],
        )
    assert exc_exact.value.error_type == EDIT_ERROR_ANCHOR_NOT_FOUND
    assert "CRLF" in str(exc_exact.value)

    # 4) Non-matching blank-line count in CRLF file surfaces CRLF hint in error message
    with pytest.raises(EditFileError) as exc_miss:
        editor._apply_replace(
            "　　第一段。\r\n\r\n　　第二段。",
            {"op": "replace", "old": "\n\n\n\n", "new": "\n\n", "replace_all": True},
            0,
            [],
            [],
        )
    assert exc_miss.value.error_type == EDIT_ERROR_ANCHOR_NOT_FOUND
    assert "CRLF" in str(exc_miss.value)

    # 5) Multi-line delete on CRLF file with \n works and preserves CRLF
    applied_del: list[dict] = []
    out_del = editor._apply_delete(
        "　　第一段。\r\n\r\n　　多余段。\r\n\r\n　　第三段。",
        {"op": "delete", "old": "\n\n　　多余段。"},
        0,
        applied_del,
        [],
    )
    assert out_del == "　　第一段。\r\n\r\n　　第三段。"


@pytest.mark.integration
def test_batch_replace_files_handles_crlf_newline_cleanup(db_session: Session, monkeypatch):
    user, project = _make_user_and_project(db_session, monkeypatch, tag="batchcrlf")
    ch1 = File(project_id=project.id, title="第1章", file_type="draft", content="甲。\r\n\r\n\r\n\r\n乙。")
    ch2 = File(project_id=project.id, title="第2章", file_type="draft", content="丙。\n\n\n\n丁。")
    db_session.add_all([ch1, ch2])
    db_session.commit()
    db_session.refresh(ch1)
    db_session.refresh(ch2)

    editor = FileEditor(db_session, user.id)
    preview = editor.batch_replace_files(
        project_id=project.id, old_text="\n\n\n\n", new_text="\n\n", dry_run=True
    )
    assert preview["matched_files"] == 2
    assert preview["total_replacements"] == 2

    applied = editor.batch_replace_files(
        project_id=project.id, old_text="\n\n\n\n", new_text="\n\n", dry_run=False
    )
    assert applied["modified_files"] == 2
    db_session.expire_all()
    assert db_session.get(File, ch1.id).content == "甲。\r\n\r\n乙。"
    assert db_session.get(File, ch2.id).content == "丙。\n\n丁。"


# ===========================================================================
# Fix 4: Client-disconnected round preserves tool & stop_reason telemetry
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_client_disconnected_round_metric_preserves_tools_and_stop_reason(
    db_session: Session, monkeypatch
):
    """Reproduces prod f4e09dd4 / fe698c56: disconnect after tool calls lost tool_calls_count,
    first_tool_started_ms, tool_failures_count, and stop_reason in agent_round_metric."""
    from agent.service import AgentService
    from api.agent import AgentRequest, stream_request

    writer, project = _make_user_and_project(db_session, monkeypatch, tag="disctel")
    monkeypatch.setattr("agent.service.primary_model_name", lambda: "MiniMax-M3.1-Flash")
    monkeypatch.setattr(
        "agent.service.MessageManager.build_system_prompt",
        lambda self, **_kwargs: "系统提示",
    )

    tools_emitted = asyncio.Event()

    async def hanging_tool_loop(writing_state, **_kwargs):
        run_meter = writing_state["run_meter"]
        run_meter.record_served_model("deepseek-flash")
        run_meter.mark_first_tool_started()
        for idx in range(3):
            call_id = f"call-{idx}"
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={"id": call_id, "name": "read_files", "status": "complete", "input": {"ids": [f"f-{idx}"]}},
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "tool_use_id": call_id,
                    "name": "read_files",
                    "result": {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": []})}]},
                },
            )
        run_meter.record_tool_failure("edit_file", "anchor_not_found", rolled_back_batch=False)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "已查看三章..."})
        tools_emitted.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("agent.service.run_loop_agent_streaming", hanging_tool_loop)

    fake_request = SimpleNamespace(
        headers={"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        url=SimpleNamespace(path="/api/v1/agent/stream"),
        client=SimpleNamespace(host="127.0.0.1"),
    )
    body = AgentRequest(project_id=project.id, message="检查前三章")

    with patch("api.agent.get_agent_service", return_value=AgentService()):
        response = await stream_request(
            request=fake_request,  # type: ignore[arg-type]
            body=body,
            session=db_session,
            current_user=writer,
            accept_language="zh",
            client_stop_contract="1",
            _rate_limit=1,
        )
        run_id = response.headers["X-Agent-Run-ID"]
        iterator = response.body_iterator
        async for _chunk in iterator:
            if tools_emitted.is_set():
                break
        await iterator.aclose()
        await asyncio.sleep(0.05)

    db_session.expire_all()
    row = db_session.exec(select(AgentRoundMetric).where(AgentRoundMetric.agent_run_id == run_id)).one()
    assert row.client_disconnected is True
    assert row.primary_model == "deepseek-flash"
    assert row.stop_reason == "client_disconnected"
    assert row.tool_calls_count == 3
    assert row.first_tool_started_ms is not None
    assert row.tool_failures_count == 1


# ===========================================================================
# Fix 5: Wall-clock write facts + multi-file newline/blank-content hygiene
# ===========================================================================


@pytest.mark.unit
@pytest.mark.parametrize("trigger", ["time", "turns"])
def test_landing_prompt_injects_factual_written_files_or_zero_writes(trigger: str):
    """Reproduces prod sess 0bcf97b3: read-only wall-clock landing must tell model 0 files were modified."""
    req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    read_guard = RepeatReadGuard(request_context=req_ctx)

    def _run_filter(meter: AgentRunMeter) -> str:
        flt = _ModelCallCountingFilter(
            lambda data: data.model_data,
            meter,
            max_turns=60,
            read_guard=read_guard,
            request_context=req_ctx,
        )
        sent = flt(SimpleNamespace(model_data=ModelInputData(input=[], instructions="sys")))
        return sent.input[-1]["content"]

    def _meter() -> AgentRunMeter:
        return (
            AgentRunMeter(wrap_up_after_s=0)
            if trigger == "time"
            else AgentRunMeter(max_model_calls=10, model_calls=8)
        )

    # Case 1: 0 writes (only reads) -> explicitly state no files were modified.
    text_zero = _run_filter(_meter())
    assert (LOOP_TIME_LANDING_PROMPT if trigger == "time" else LOOP_SOFT_LANDING_PROMPT.format(remaining=1)) in text_zero
    assert "尚未修改或保存任何项目文件" in text_zero
    assert "严禁宣称" in text_zero

    # Case 2: 1 file actually written via edit_file + 1 body saved -> list exact written files only.
    read_guard._record_write("f-23", "第023章")
    req_ctx.mark_chapter_body_saved("f-24", "第024章", "draft")
    text_written = _run_filter(_meter())
    assert "《第023章》(id=f-23)" in text_written
    assert "《第024章》(id=f-24)" in text_written
    assert "尚未修改或保存任何项目文件" not in text_written
    assert "清单外" in text_written

    # Case 3: Pending unwritten empty file must NOT be claimed as already saved.
    req_ctx.set_pending_empty_file("f-pending", "第025章", version="v1", file_type="draft")
    read_guard._record_write("f-pending", "第025章")
    text_pending = _run_filter(_meter())
    assert "仍在等待 <file> 正文写入" in text_pending
    assert "《第025章》(id=f-pending)" in text_pending


@pytest.mark.unit
def test_landing_write_facts_edge_cases_pending_only_deleted_unidentified_and_english():
    """Verify non-contradictory pending-only, delete-only, unidentified-write, overflow, and EN facts."""
    from agent.openai_agents.runner import _landing_write_facts

    # 1) Pending-only (0 saved bodies, 1 file awaiting <file>): must NOT tell model "only inspected/no writes".
    req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    guard = RepeatReadGuard(request_context=req_ctx)
    req_ctx.set_pending_empty_file("f-new", "第1章", version="v1", file_type="draft")
    guard._record_write("f-new", "第1章")
    zh_pending = _landing_write_facts(guard, req_ctx, english=False)
    assert "仍在等待 <file> 正文写入" in zh_pending
    assert "请先按上方提醒写完" in zh_pending
    assert "仅完成检查/阅读" not in zh_pending
    en_pending = _landing_write_facts(guard, req_ctx, english=True)
    assert "still awaiting <file> body content" in en_pending
    assert "Finish writing" in en_pending

    # 2) Delete-only round: deleted files listed as deleted, NOT as saved.
    req_del = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    guard_del = RepeatReadGuard(request_context=req_del)
    guard_del._record_write("f-old", "废稿", deleted=True)
    zh_del = _landing_write_facts(guard_del, req_del, english=False)
    assert "仅删除了以下文件" in zh_del and "《废稿》(id=f-old)" in zh_del
    en_del = _landing_write_facts(guard_del, req_del, english=True)
    assert "files deleted in this round" in en_del and '"废稿" (id=f-old)' in en_del

    # 3) Unidentified write_succeeded (e.g. truncated tool output without id)
    guard_unid = RepeatReadGuard()
    guard_unid.write_succeeded = True
    assert "工具结果明确确认" in _landing_write_facts(guard_unid, None, english=False)
    assert "without full file details" in _landing_write_facts(guard_unid, None, english=True)

    # 4) Zero writes in English + >20 written files overflow truncation
    req_zero = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    assert "No project files have been modified" in _landing_write_facts(RepeatReadGuard(), req_zero, english=True)

    guard_many = RepeatReadGuard()
    for idx in range(23):
        guard_many._record_write(f"f-{idx}", f"第{idx}章" if idx else "")
    zh_many = _landing_write_facts(guard_many, None, english=False)
    assert "等共 23 份文件" in zh_many and "id=f-0" in zh_many
    en_many = _landing_write_facts(guard_many, None, english=True)
    assert "(and 3 more)" in en_many


@pytest.mark.unit
def test_tool_failure_breaker_extracts_nested_failed_edits_error_fallback():
    """Defense-in-depth: even if top-level 'error' is omitted, breaker extracts data.failed_edits[0].error."""
    breaker = ToolFailureBreaker()
    legacy_payload = json.dumps(
        {
            "status": "error",
            "error_type": "anchor_not_found",
            "data": {"failed_edits": [{"index": 0, "error": "Edit 0: 找不到要替换的原文片段。"}]},
        },
        ensure_ascii=False,
    )
    args = json.dumps({"id": "f-1", "edits": [{"op": "replace", "old": "x", "new": "y"}]})
    for _ in range(3):
        breaker.observe("edit_file", args, legacy_payload)
    assert breaker.is_open is True
    assert breaker.trip is not None
    assert "找不到要替换的原文片段" in breaker.trip.last_error

    # Empty failed_edits still produces empty fallback cleanly without raising.
    breaker_empty = ToolFailureBreaker()
    empty_payload = json.dumps({"status": "error", "data": {"failed_edits": []}})
    for _ in range(3):
        breaker_empty.observe("edit_file", args, empty_payload)
    assert breaker_empty.is_open is True
    assert breaker_empty.trip.last_error == ""


@pytest.mark.unit
def test_crlf_insert_and_single_line_multiline_replace_preserve_crlf_cleanly():
    """Fixes P1-2 (paragraph_layout dropping \\r) and P2-1 (single-line old + multi-line new on CRLF)."""
    editor = FileEditor(session=None)
    crlf = "第一段。\r\n\r\n第二段。\r\n\r\n第三段。"

    # 1) insert_after with LF multi-line anchor & LF paragraph text on CRLF file -> zero bare LF!
    applied_ins: list[dict] = []
    out_ins = editor._apply_insert(
        crlf,
        {"op": "insert_after", "anchor": "第一段。\n\n第二段。", "text": "\n\n新增段落。"},
        0,
        applied_ins,
        [],
        after=True,
    )
    assert out_ins == "第一段。\r\n\r\n第二段。\r\n\r\n新增段落。\r\n\r\n第三段。"
    assert "\n" not in out_ins.replace("\r\n", "")

    # 2) Single-line old + multi-line LF new on pure CRLF file -> adapts new_text to CRLF!
    applied_rep: list[dict] = []
    out_rep = editor._apply_replace(
        crlf,
        {"op": "replace", "old": "第二段。", "new": "第二段甲。\n\n第二段乙。"},
        0,
        applied_rep,
        [],
    )
    assert out_rep == "第一段。\r\n\r\n第二段甲。\r\n\r\n第二段乙。\r\n\r\n第三段。"
    assert "\n" not in out_rep.replace("\r\n", "")

    # 3) Single-line anchor + multi-line LF text in insert_after on pure CRLF file -> zero bare LF!
    applied_ins2: list[dict] = []
    out_ins2 = editor._apply_insert(
        crlf,
        {"op": "insert_after", "anchor": "第一段。", "text": "\n\n插段甲。\n\n插段乙。"},
        0,
        applied_ins2,
        [],
        after=True,
    )
    assert "\n" not in out_ins2.replace("\r\n", "")


@pytest.mark.unit
def test_text_shaper_separates_angle_bracket_prose_while_skipping_file_tag():
    """Fixes P2-6: '<第一幕>' gets separated from prior prose while '<file>' / '<' does not."""
    shaper_prose = AgentTextShaper(previous_text="准备好了。", drop_english_narration=True)
    out = shaper_prose.feed("<第一幕> 场景开始") + shaper_prose.end_segment(before_tool_call=False)
    assert out == "\n\n<第一幕> 场景开始"

    shaper_stream = AgentTextShaper(previous_text="<file>\n正文\n</file>", drop_english_narration=False)
    out2 = shaper_stream.feed("<") + shaper_stream.feed("file>\n下章\n</file>")
    assert out2 == "<file>\n下章\n</file>"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_collaboration_cap_excludes_deleted_unsaved_and_pending_files_and_supports_en():
    """Fixes P1-1 + tests run_support.collaboration_cap_text across ZH/EN, quota-left, and >10 titles."""
    from agent.loop import run_support
    from agent.loop.engine import run_loop_agent_streaming

    # 1) Direct unit tests for collaboration_cap_text branches (EN, ZH, no-messages-left, empty, >10 titles).
    with patch("agent.loop.run_support._ai_messages_left_today", return_value=False):
        req_u = AgentRequestContext(session=None, user_id="u1", project_id="p1", session_id="s1")
        assert 'Reply "continue"' not in await run_support.collaboration_cap_text(
            True, 12, ["Ch 1"], request_context=req_u
        )
        assert 'Reply "continue"' not in await run_support.collaboration_cap_text(
            True, 12, [], request_context=req_u
        )
        assert "回复「继续」" not in await run_support.collaboration_cap_text(
            False, 12, ["第1章"], request_context=req_u
        )
        assert "回复「继续」" not in await run_support.collaboration_cap_text(
            False, 12, [], request_context=req_u
        )
    with patch("agent.loop.run_support._ai_messages_left_today", return_value=True):
        many = [f"第{i}章" for i in range(1, 13)]
        zh_many = await run_support.collaboration_cap_text(False, 12, many, request_context=req_u)
        assert "等 12 份文件" in zh_many and "回复「继续」" in zh_many
        en_many = await run_support.collaboration_cap_text(True, 12, many, request_context=req_u)
        assert "and 2 more" in en_many and 'Reply "continue"' in en_many
        en_empty = await run_support.collaboration_cap_text(True, 12, [], request_context=req_u)
        assert "Reached the per-round run limit (12)" in en_empty
        zh_empty = await run_support.collaboration_cap_text(False, 12, [], request_context=req_u)
        assert "本轮已达到单轮连续运行上限（12 次）" in zh_empty

    # 2) Engine integration: deleted, unsaved replacement, and pending empty files must NOT appear in "已保存".
    req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    payload = {"content": [{"type": "text", "text": json.dumps({"status": "success"})}]}

    async def fake_runner(*, state, agent_type, system_prompt, **_kwargs):
        ctx = state["request_context"]
        guard = state["repeat_read_guard"]
        ctx.mark_chapter_body_saved("ch-saved", "第1章 已存", "draft")
        guard._record_write("ch-edited", "第2章 局部改")
        guard._record_write("ch-deleted", "废稿A", deleted=True)
        # Arm an unsaved chapter replacement for ch-unsaved
        ok, _ = ctx.try_reserve_pending_empty_file("t1", "第3章 未存替换")
        assert ok
        ctx.bind_pending_empty_file("t1", "ch-unsaved", "第3章 未存替换", replacement={"id": "ch-unsaved", "updated_at": "v1"})
        guard._record_write("ch-unsaved", "第3章 未存替换")
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": agent_type})
        yield StreamEvent(type=StreamEventType.TOOL_USE, data={"id": "c1", "name": "create_file", "status": "complete", "input": {}})
        yield StreamEvent(type=StreamEventType.TOOL_RESULT, data={"tool_use_id": "c1", "name": "create_file", "result": payload})
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n第1章正文。\n</file>\n"})
        state["run_items"] = [{"role": "user", "content": "x"}]
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    state = {"user_message": "整理章节", "messages": [], "system_prompt": "系统", "request_context": req_ctx}
    with (
        patch("agent.loop.engine.run_openai_agents_streaming_agent", fake_runner),
        patch("agent.loop.run_support._ai_messages_left_today", return_value=True),
    ):
        events = [e async for e in run_loop_agent_streaming(state, max_runs=1)]

    closing_texts = [
        str(e.data.get("text") or "")
        for e in events
        if e.type == StreamEventType.TEXT and "连续运行上限" in str(e.data.get("text") or "")
    ]
    assert len(closing_texts) == 1
    assert "《第1章 已存》" in closing_texts[0]
    assert "《第2章 局部改》" in closing_texts[0]
    assert "废稿A" not in closing_texts[0]
    assert "第3章 未存替换" not in closing_texts[0]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_service_space_padded_blank_lines_after_file_and_live_stop_reason_on_error(
    db_session: Session, monkeypatch
):
    """Fixes P2-5 (space-padded blank line after file_content_end) & P2-3 (live stop_reason on error)."""
    from agent.service import AgentService

    user, project = _make_user_and_project(db_session, monkeypatch, tag="spacepad")
    monkeypatch.setattr("agent.service.primary_model_name", lambda: "deepseek-flash")
    monkeypatch.setattr(
        "agent.service.MessageManager.build_system_prompt",
        lambda self, **_kwargs: "系统提示",
    )
    monkeypatch.setattr("agent.service.create_session", lambda: Session(db_session.get_bind()))
    monkeypatch.setattr("database.is_postgres", True)

    f = File(project_id=project.id, title="第1章", content="", file_type="draft")
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)

    async def padded_stream(_state, **_kwargs):
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "前文"})
        yield StreamEvent(type=StreamEventType.TOOL_USE, data={"id": "w1", "name": "write_chapter", "input": {}, "status": "complete"})
        yield StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": "w1",
                "name": "write_chapter",
                "result": {
                    "status": "success",
                    "mutation_applied": True,
                    "data": {
                        "id": f.id,
                        "title": f.title,
                        "file_type": "draft",
                        "action": "created",
                        "awaiting_body": True,
                        "content": "",
                        "updated_at": f.updated_at.isoformat(),
                    },
                },
            },
        )
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n正文\n</file>   "})
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "\n\n总结"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    monkeypatch.setattr("agent.service.run_loop_agent_streaming", padded_stream)
    run_report: dict = {}
    frames = [
        frame
        async for frame in AgentService().process_stream(
            project_id=project.id,
            user_id=user.id,
            message="写第一章",
            session=db_session,
            run_report=run_report,
        )
    ]
    done = [json.loads(fr.split("data:", 1)[1]) for fr in frames if "event: done" in fr]
    db_session.expire_all()
    saved = db_session.get(ChatMessage, done[0]["assistant_message_id"])
    assert saved is not None
    assert saved.content == "前文\n\n总结"
    assert run_report["stop_reason"] == "end_turn"

    # Verify stop_reason is populated live on error BEFORE process_stream exits finally.
    async def error_stream(_state, **_kwargs):
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": REPETITION_LOOP_STOP_REASON})
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data={"error": "重复循环", "code": "ERR_AGENT_REPETITION_LOOP", "reason": REPETITION_LOOP_STOP_REASON},
        )

    monkeypatch.setattr("agent.service.run_loop_agent_streaming", error_stream)
    err_report: dict = {}
    stream = AgentService().process_stream(
        project_id=project.id,
        user_id=user.id,
        message="触发复读",
        session=db_session,
        run_report=err_report,
    )
    async for frame in stream:
        if "event: error" in frame:
            break
    await stream.aclose()
    if AgentService.background_cleanup_tasks:
        await asyncio.gather(*list(AgentService.background_cleanup_tasks), return_exceptions=True)
    assert err_report.get("stop_reason") == REPETITION_LOOP_STOP_REASON


@pytest.mark.unit
def test_text_shaper_does_not_prepend_blank_lines_before_continuation_file_block():
    """AgentTextShaper starting a continuation run with <file> must not emit leading '\\n\\n'."""
    shaper = AgentTextShaper(previous_text="<file>\n第1章正文。\n</file>", drop_english_narration=True)
    emitted = shaper.feed("<file>\n第2章正文。\n</file>")
    emitted += shaper.end_segment(before_tool_call=False)
    assert emitted == "<file>\n第2章正文。\n</file>"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_multi_file_writes_do_not_accumulate_blank_newlines_in_saved_content(
    db_session: Session, monkeypatch
):
    """Reproduces prod sess 928826bf / fa2cbcf9: repeated pre/post-<file> newlines must not produce 22 '\\n'."""
    from agent.service import AgentService

    user, project = _make_user_and_project(db_session, monkeypatch, tag="multifilews")
    monkeypatch.setattr("agent.service.primary_model_name", lambda: "deepseek-flash")
    monkeypatch.setattr(
        "agent.service.MessageManager.build_system_prompt",
        lambda self, **_kwargs: "系统提示",
    )

    files = []
    for idx in range(1, 6):
        f = File(project_id=project.id, title=f"第{idx}章", content="", file_type="draft")
        db_session.add(f)
        files.append(f)
    db_session.commit()
    for f in files:
        db_session.refresh(f)

    async def multi_file_stream(_state, **_kwargs):
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "开始连续写作。"})
        for idx, f in enumerate(files, start=1):
            call_id = f"w-{idx}"
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={"id": call_id, "name": "write_chapter", "input": {"chapter": idx}, "status": "complete"},
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "tool_use_id": call_id,
                    "name": "write_chapter",
                    "result": {
                        "status": "success",
                        "mutation_applied": True,
                        "data": {
                            "id": f.id,
                            "title": f.title,
                            "file_type": "draft",
                            "action": "created",
                            "awaiting_body": True,
                            "content": "",
                            "updated_at": f.updated_at.isoformat(),
                        },
                    },
                },
            )
            # Leading and trailing blank newlines around every <file> block across 5 files.
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"\n\n<file>\n第{idx}章正文。\n</file>\n\n"})
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "五章全部写好了。"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    monkeypatch.setattr("agent.service.run_loop_agent_streaming", multi_file_stream)
    monkeypatch.setattr("agent.service.create_session", lambda: Session(db_session.get_bind()))
    monkeypatch.setattr("database.is_postgres", True)

    service = AgentService()
    frames = [
        frame
        async for frame in service.process_stream(
            project_id=project.id,
            user_id=user.id,
            message="写五章",
            session=db_session,
        )
    ]
    done = [json.loads(f.split("data:", 1)[1]) for f in frames if "event: done" in f]
    assert len(done) == 1
    db_session.expire_all()
    saved = db_session.get(ChatMessage, done[0]["assistant_message_id"])
    assert saved is not None
    assert "\n\n\n" not in saved.content
    assert saved.content == "开始连续写作。\n\n五章全部写好了。"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_collaboration_cap_emits_factual_closing_text_so_reply_is_not_blank():
    """When multi-file writes hit max_runs (12) ending on </file>, emit a concise factual summary."""
    from agent.loop.engine import run_loop_agent_streaming

    req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    payload = {"content": [{"type": "text", "text": json.dumps({"status": "success"})}]}

    async def fake_runner(*, state, agent_type, system_prompt, **_kwargs):
        ctx = state["request_context"]
        ctx.mark_chapter_body_saved("ch1", "第1章 雨夜", "draft")
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": agent_type})
        yield StreamEvent(type=StreamEventType.TOOL_USE, data={"id": "c1", "name": "create_file", "status": "complete", "input": {}})
        yield StreamEvent(type=StreamEventType.TOOL_RESULT, data={"tool_use_id": "c1", "name": "create_file", "result": payload})
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n第1章正文。\n</file>\n"})
        state["run_items"] = [{"role": "user", "content": "x"}]
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    state = {
        "user_message": "连写多章",
        "messages": [],
        "system_prompt": "系统",
        "request_context": req_ctx,
    }
    with (
        patch("agent.loop.engine.run_openai_agents_streaming_agent", fake_runner),
        patch("agent.loop.run_support._ai_messages_left_today", return_value=True),
    ):
        events = [e async for e in run_loop_agent_streaming(state, max_runs=1)]

    exhausted = [e for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert len(exhausted) == 1
    assert exhausted[0].data["layer"] == "collaboration"
    # A closing TEXT event after </file> must be emitted so chat_message.content is not empty.
    texts = [str(e.data.get("text") or "") for e in events if e.type == StreamEventType.TEXT]
    assert any("《第1章 雨夜》" in t and "连续运行上限" in t for t in texts)
