"""Comprehensive regressions for PR 3:
- B1: Zombie Redis task board deleted (`update_project` memory-only)
- B2: `read_files` + `search_files` split & legacy `query_files` replay compatibility
- B3: Simplified 4-op `edit_file` + `replace(new="")` coupled move + runtime compat
- P1-4: Single JSON parse in `tools_adapter.py` & `edit_plan.arguments` threading
- P1-5: Overdue `<file>` non-conflicting tool cap (max 2, 3rd rejected) & `clear_all()`
- `batch_replace_files` full accounting (billing, round_outcome, chat_tool_cards, fingerprints, guards, session_loader)
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.core.chat_storage import archive_historical_tool_calls_json
from agent.core.round_outcome import RoundWrittenFiles
from agent.core.run_meter import AgentRunMeter
from agent.core.session_loader import SessionLoader
from agent.core.stream_billing import StreamBillingTracker
from agent.loop.compaction import archive_old_tool_results
from agent.loop.history import (
    ARCHIVABLE_READ_TOOLS,
    _loop_row_items,
    build_tool_archive_receipt,
    slim_client_tool_result,
)
from agent.openai_agents import tools_adapter
from agent.openai_agents.intra_run_trimmer import DEFAULT_TRIMMABLE_TOOLS, DEFAULT_WRITE_TOOLS
from agent.openai_agents.repeat_edit_guard import RepeatEditGuard
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker
from agent.service import _applied_file_mutations_for_fingerprints
from agent.tools import mcp_tools
from agent.tools.file_ops.edit import FileEditor
from agent.tools.file_ops.project import ProjectOperations
from agent.tools.registry import LOOP_TOOL_NAMES
from agent.tools.tool_context import AgentRequestContext, _PendingEmptyFileState
from agent.tools.tool_inputs import EditFileInput, validate_tool_input
from agent.tools.tool_schemas import EDIT_FILE_TOOL, UPDATE_PROJECT_TOOL
from models import ChatMessage, ChatSession, File, Project, User
from services.chat_tool_cards import record_edit_card_undone
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService


def _parse_mcp(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def workspace(db_session: Session, monkeypatch):
    import database

    suffix = uuid4().hex[:8]
    user = User(
        username=f"pr3-user-{suffix}",
        email=f"pr3-{suffix}@example.test",
        hashed_password="unused",
        email_verified=True,
        is_active=True,
    )
    project = Project(name="PR3 测试项目", owner_id=user.id, project_type="novel")
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


def _make_file(
    db_session: Session,
    project: Project,
    *,
    title: str,
    content: str,
    file_type: str = "draft",
) -> File:
    row = File(project_id=project.id, title=title, content=content, file_type=file_type)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _make_req_ctx(db_session: Session, user: User, project: Project, *, seen: dict[str, str] | None = None) -> AgentRequestContext:
    return AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id=f"sess-{uuid4().hex[:6]}",
        read_snapshots=dict(seen or {}),
    )


# ---------------------------------------------------------------------------
# B1: Zombie Redis task board retired & deleted
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "deleted_module",
    [
        "services.infra.task_board_service",
        "agent.core.task_board_completion",
        "agent.tools.task_progress",
    ],
)
def test_b1_deleted_task_board_modules_are_absent(deleted_module: str):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(deleted_module)


@pytest.mark.unit
def test_b1_project_ops_and_update_project_schema_have_no_task_board():
    assert not hasattr(ProjectOperations, "execute_update_plan")
    assert "tasks" not in UPDATE_PROJECT_TOOL["input_schema"]["properties"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_b1_update_project_updates_memory_and_ignores_extra_tasks(db_session: Session, workspace):
    user, project = workspace
    req_ctx = _make_req_ctx(db_session, user, project)
    try:
        payload = _parse_mcp(
            await mcp_tools.update_project(
                {
                    "summary": "暗潮涌动的海港悬疑故事",
                    "writing_style": "冷峻克制",
                    "current_phase": "正文第1章",
                    "tasks": [{"task": "旧任务", "status": "done"}],
                },
                request_context=req_ctx,
            )
        )
        assert payload["status"] == "success"
        assert "plan" not in payload["data"]
        assert set(payload["data"]["updated_fields"]) == {"summary", "writing_style", "current_phase"}
        db_session.expire_all()
        refreshed = db_session.get(Project, project.id)
        assert refreshed is not None
        assert refreshed.summary == "暗潮涌动的海港悬疑故事"
        assert refreshed.writing_style == "冷峻克制"
    finally:
        req_ctx.clear()


# ---------------------------------------------------------------------------
# B2: read_files + search_files split & legacy query_files replay compat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_b2_read_files_batch_alias_range_and_paragraph_stats(db_session: Session, workspace):
    user, project = workspace
    f1 = _make_file(db_session, project, title="第一章 雾港", content="第一段正文。\n\n第二段正文。")
    f2 = _make_file(db_session, project, title="主角卡", content="沈砚，二十八岁。", file_type="character")
    req_ctx = _make_req_ctx(db_session, user, project)
    try:
        # Batch read preserves order and reports missing_ids
        batch = _parse_mcp(
            await mcp_tools.read_files({"ids": [f2.id, "missing-id", f1.id]}, request_context=req_ctx)
        )
        assert batch["status"] == "success"
        assert [row["id"] for row in batch["data"]] == [f2.id, f1.id]
        assert batch["missing_ids"] == ["missing-id"]
        assert batch["data"][1]["content"] == "第一段正文。\n\n第二段正文。"
        assert batch["data"][1]["paragraph_count"] == 2

        # Single `id` alias works & show_paragraph_stats preserves raw content in read_snapshots
        req_ctx2 = _make_req_ctx(db_session, user, project)
        try:
            annotated = _parse_mcp(
                await mcp_tools.read_files(
                    {"id": f1.id, "show_paragraph_stats": True},
                    request_context=req_ctx2,
                )
            )
            assert annotated["status"] == "success"
            assert "[P01" in annotated["data"][0]["content"]
            req_ctx2.promote_pending_reads()
            assert req_ctx2.read_snapshot(f1.id) == "第一段正文。\n\n第二段正文。"
        finally:
            req_ctx2.clear()
    finally:
        req_ctx.clear()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_b2_search_files_matches_content_and_title_and_lists_summaries_without_full_body(
    db_session: Session, workspace
):
    user, project = workspace
    f1 = _make_file(db_session, project, title="第一章 雾港夜行", content="港口钟声响起，林深收起怀表。")
    f2 = _make_file(db_session, project, title="沈砚人物卡", content="外表温和，实则城府极深。", file_type="character")
    req_ctx = _make_req_ctx(db_session, user, project)
    try:
        # Content grep
        hit = _parse_mcp(await mcp_tools.search_files({"query": "怀表"}, request_context=req_ctx))
        assert hit["status"] == "success"
        assert hit["data"]["total_matches"] == 1
        assert hit["data"]["matched_files"] == 1
        assert hit["data"]["files"][0]["id"] == f1.id
        assert "content" not in hit["data"]["files"][0]

        # Title-only match still reports total_matches >= 1 so UI does not render no_search_matches
        title_hit = _parse_mcp(await mcp_tools.search_files({"query": "雾港夜行"}, request_context=req_ctx))
        assert title_hit["status"] == "success"
        assert title_hit["data"]["total_matches"] >= 1
        assert title_hit["data"]["matched_files"] == 1
        assert title_hit["data"]["files"][0]["id"] == f1.id

        # Filtered summary listing (file_type singular alias supported) never returns full content
        listing = _parse_mcp(await mcp_tools.search_files({"file_type": "character"}, request_context=req_ctx))
        assert listing["status"] == "success"
        assert [row["id"] for row in listing["data"]] == [f2.id]
        assert "content" not in listing["data"][0]
    finally:
        req_ctx.clear()


@pytest.mark.unit
def test_b2_runtime_subsystems_support_read_files_search_files_and_legacy_query_files():
    assert {"query_files", "read_files", "search_files"}.issubset(ARCHIVABLE_READ_TOOLS)
    assert {"query_files", "read_files", "search_files"}.issubset(DEFAULT_TRIMMABLE_TOOLS)
    assert "batch_replace_files" in DEFAULT_WRITE_TOOLS
    assert "read_files" in LOOP_TOOL_NAMES and "search_files" in LOOP_TOOL_NAMES
    assert "query_files" not in LOOP_TOOL_NAMES

    # Tool archive receipts & client slimming work for all three read/search tools
    for tool_name in ("read_files", "search_files", "query_files"):
        raw = json.dumps(
            {"status": "success", "data": [{"id": "f1", "title": "第一章", "content": "甲" * 6000}]},
            ensure_ascii=False,
        )
        receipt_json, file_ids = build_tool_archive_receipt(tool_name, raw)
        receipt = json.loads(receipt_json)
        assert receipt["archived"] is True and receipt["tool_name"] == tool_name
        if tool_name in ("read_files", "query_files"):
            assert file_ids == ["f1"]
        slimmed = slim_client_tool_result(tool_name, {"status": "success", "data": [{"id": "f1", "title": "第一章", "content": "甲" * 1000}]})
        assert "content" not in slimmed["data"][0]

    # Historical DB row replay handles legacy query_files + new read_files + search_files
    row = SimpleNamespace(
        id=99,
        content="已读完。",
        tool_calls=json.dumps(
            [
                {
                    "id": "tc-1",
                    "name": "query_files",
                    "arguments": {"id": "f1"},
                    "result": {"status": "success", "data": [{"id": "f1", "title": "旧章", "content": "旧文"}]},
                },
                {
                    "id": "tc-2",
                    "name": "read_files",
                    "arguments": {"ids": ["f2"]},
                    "result": {"status": "success", "data": [{"id": "f2", "title": "新章", "content": "新文"}]},
                },
                {
                    "id": "tc-3",
                    "name": "search_files",
                    "arguments": {"query": "灯塔"},
                    "result": {"status": "success", "data": {"query": "灯塔", "total_matches": 1, "matched_files": 1, "files": []}},
                },
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps(
            {
                "engine": "loop",
                "display_events": [
                    {"type": "tool_call", "data": {"id": "tc-1"}},
                    {"type": "tool_call", "data": {"id": "tc-2"}},
                    {"type": "tool_call", "data": {"id": "tc-3"}},
                    {"type": "content", "data": {"text": "已读完。"}},
                ],
            },
            ensure_ascii=False,
        ),
    )
    items = _loop_row_items(row, json.loads(row.message_metadata))
    called_names = [it["name"] for it in items if it.get("type") == "function_call"]
    assert called_names == ["query_files", "read_files", "search_files"]

    # Chat storage archiving compresses all three tools when oversized
    big_calls = json.dumps(
        [
            {"id": "a1", "name": "read_files", "result": {"status": "success", "data": [{"id": "f1", "title": "第一章", "content": "正" * 8000}]}},
            {"id": "a2", "name": "search_files", "result": {"status": "success", "data": {"query": "x", "files": [{"id": "f1", "title": "第一章", "matches": [{"snippet": "正" * 8000}]}]}}},
            {"id": "a3", "name": "query_files", "result": {"status": "success", "data": [{"id": "f2", "title": "第二章", "content": "文" * 8000}]}},
        ],
        ensure_ascii=False,
    )
    archived_json = archive_historical_tool_calls_json(big_calls)
    assert archived_json is not None and len(archived_json) < len(big_calls)
    archived_parsed = json.loads(archived_json)
    assert "content" not in archived_parsed[0]["result"]["data"][0]
    assert "matches" not in archived_parsed[1]["result"]["data"]["files"][0]
    assert "content" not in archived_parsed[2]["result"]["data"][0]

    # Stage 1 tool archive compaction tracks read_files alongside query_files
    from agent.loop.history import LoopHistory

    big_out = json.dumps({"status": "success", "data": [{"id": "ch-9", "title": "第九章", "content": "文" * 40000}]}, ensure_ascii=False)
    comp_items = [
        {"role": "user", "content": "读第九章"},
        {"type": "function_call", "call_id": "c1", "name": "read_files", "arguments": '{"ids":["ch-9"]}'},
        {"type": "function_call_output", "call_id": "c1", "output": big_out},
        {"role": "assistant", "content": "读好了"},
        {"role": "user", "content": "继续"},
        {"role": "assistant", "content": "好"},
        {"role": "user", "content": "再继续"},
        {"role": "assistant", "content": "好"},
        {"role": "user", "content": "最新"},
    ]
    history = LoopHistory(items=comp_items, last_message_id="m-last", last_prompt_tokens=310_000)
    outcome = archive_old_tool_results(history, keep_recent_turns=2, min_savings_tokens=100)
    assert outcome.admitted is True
    assert outcome.record is not None and outcome.record["archived_count"] == 1
    assert "ch-9" in outcome.archived_file_ids


# ---------------------------------------------------------------------------
# B3: Simplified 4-op edit_file + replace(new="") + runtime compat
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_b3_edit_file_schema_4_ops_and_coupled_replace_delete_semantics(db_session: Session, workspace):
    item_props = EDIT_FILE_TOOL["input_schema"]["properties"]["edits"]["items"]["properties"]
    assert item_props["op"]["enum"] == ["replace", "append", "insert_after", "insert_before"]
    for hidden in ("match_mode", "ignore_punct_whitespace", "replace_all"):
        assert hidden not in item_props

    # replace(new="") + insert_* / append is treated as coupled move -> continue_on_error defaults to False
    for pair_op in ("insert_after", "insert_before", "append"):
        parsed, err = validate_tool_input(
            EditFileInput,
            {
                "id": "f1",
                "edits": [
                    {"op": "replace", "old": "旧段。", "new": ""},
                    {"op": pair_op, "anchor": "锚点。", "text": "旧段。"},
                ],
            },
            tool_name="edit_file",
        )
        assert err is None and parsed is not None
        assert parsed.continue_on_error is False

    # Runtime execution: replace(new="") deletes cleanly, and legacy delete/prepend/replace_all still work
    user, project = workspace
    chapter = _make_file(db_session, project, title="第一章", content="开头。\n\n多余句。\n\n旧词与旧词。")
    editor = FileEditor(session=db_session, user_id=user.id)
    res = editor.edit_file(
        id=chapter.id,
        edits=[
            {"op": "replace", "old": "多余句。\n\n", "new": ""},
            {"op": "replace", "old": "旧词", "new": "新词", "replace_all": True},
            {"op": "prepend", "text": "序言。\n\n"},
        ],
    )
    assert res["edits_applied"] == 3
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == "序言。\n\n开头。\n\n新词与新词。"


# ---------------------------------------------------------------------------
# P1-4: Single JSON parse & edit_plan.arguments threading in tools_adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_p1_4_single_json_parse_and_edit_plan_arguments_threading(monkeypatch):
    req_ctx = AgentRequestContext(session=None, user_id="u1", project_id="p1", session_id="s1")
    req_ctx.mark_chapter_body_saved("ch1", "第一章", "draft")
    req_ctx.record_written_snapshot("ch1", "甲" * 1200, full=True)

    breaker = ToolFailureBreaker()
    read_guard = RepeatReadGuard(request_context=req_ctx)
    edit_guard = RepeatEditGuard(request_context=req_ctx)
    meter = AgentRunMeter()

    received_args: list[dict] = []

    async def fake_read(args):
        received_args.append(dict(args))
        rows = [{"id": fid, "title": f"标题-{fid}", "content": f"正文-{fid}"} for fid in args.get("ids", [])]
        return {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": rows}, ensure_ascii=False)}]}

    parse_calls = 0
    orig_parse = tools_adapter._parse_json_dict

    def spy_parse(raw: str):
        nonlocal parse_calls
        parse_calls += 1
        return orig_parse(raw)

    monkeypatch.setattr(tools_adapter, "_parse_json_dict", spy_parse)

    try:
        with patch.dict("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"read_files": fake_read}):
            tools = {
                t.name: t
                for t in tools_adapter.build_agent_function_tools(
                    failure_breaker=breaker,
                    read_guard=read_guard,
                    edit_guard=edit_guard,
                    run_meter=meter,
                )
            }
            # Mixed batch: ch1 (just written -> stripped by edit_guard) + outline-1 (allowed)
            out_raw = await tools["read_files"].on_invoke_tool(
                req_ctx, json.dumps({"ids": ["ch1", "outline-1"]})
            )
            out = json.loads(out_raw)

        # Single parse of raw_arguments + single parse of output (<=2 total)!
        assert parse_calls <= 2
        # edit_plan.arguments threaded cleanly: handler ONLY queried outline-1, never ch1!
        assert received_args == [{"ids": ["outline-1"]}]
        # Merged response keeps outline-1 content + ch1 metadata stub
        assert out["self_read_guarded_ids"] == ["ch1"]
        by_id = {row["id"]: row for row in out["data"]}
        assert by_id["outline-1"]["content"] == "正文-outline-1"
        assert by_id["ch1"]["content_omitted"] is True
        assert "content" not in by_id["ch1"]
        # read_guard only tracked outline-1 (not ch1) under canonical READ_TOOL_NAME key
        assert ("query_files", "outline-1", "full") in read_guard._read_counts
        assert ("query_files", "ch1", "full") not in read_guard._read_counts
    finally:
        req_ctx.clear()


# ---------------------------------------------------------------------------
# P1-5: Overdue <file> non-conflicting cap (max 2, 3rd rejected) & clear_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_p1_5_overdue_non_conflicting_tools_capped_at_two_and_clear_all_resets():
    req_ctx = AgentRequestContext(session=None, user_id="u1", project_id="p1", session_id="s1")
    executed: list[str] = []

    async def fake_ok(_args):
        executed.append("ok")
        return {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": []})}]}

    try:
        req_ctx.begin_model_reply()
        req_ctx.set_pending_empty_file("f-pending", "第一章 启程", version="v1", file_type="draft")
        req_ctx.begin_model_reply()  # Now overdue!

        with patch.dict(
            "agent.openai_agents.tools_adapter.TOOL_FUNCTIONS",
            {"read_files": fake_ok, "search_files": fake_ok, "update_project": fake_ok},
        ):
            tools = {t.name: t for t in tools_adapter.build_agent_function_tools()}
            r1 = json.loads(await tools["read_files"].on_invoke_tool(req_ctx, '{"ids": ["o1"]}'))
            r2 = json.loads(await tools["search_files"].on_invoke_tool(req_ctx, '{"query": "大纲"}'))
            r3 = json.loads(await tools["update_project"].on_invoke_tool(req_ctx, '{"current_phase": "第1章"}'))

        assert r1["status"] == "success" and "pending_file_reminder" in r1
        assert r2["status"] == "success" and "pending_file_reminder" in r2
        assert r3["status"] == "error" and r3["error_type"] == "file_body_pending"
        assert len(executed) == 2
    finally:
        req_ctx.clear()

    # _PendingEmptyFileState.clear_all() resets _overdue_non_conflicting_uses, _saved_body_types, _closed
    state = _PendingEmptyFileState()
    state.add("f1", "第一章", file_type="draft")
    assert state.consume_overdue_non_conflicting("f1", max_uses=2) is True
    assert state.consume_overdue_non_conflicting("f1", max_uses=2) is True
    assert state.consume_overdue_non_conflicting("f1", max_uses=2) is False
    state.mark_chapter_body_saved("f1", "第一章", "draft")
    state.close()
    state.clear_all()
    assert state.is_closed() is False
    assert state.saved_content_body_ids() == []
    state.add("f1", "第一章", file_type="draft")
    assert state.consume_overdue_non_conflicting("f1", max_uses=2) is True


# ---------------------------------------------------------------------------
# batch_replace_files accounting: billing, round_outcome, chat_tool_cards,
# context_fingerprints, repeat_edit_guard, session_loader
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_batch_replace_files_accounting_across_billing_outcome_fingerprints_and_edit_guard():
    def _sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    # 1) StreamBillingTracker: dry_run is NOT a write; real commit IS a write
    dry_tracker = StreamBillingTracker()
    dry_tracker.observe(
        _sse(
            "tool_result",
            {
                "tool_name": "batch_replace_files",
                "status": "success",
                "mutation_applied": False,
                "data": {"dry_run": True, "files_modified": 0, "files": []},
            },
        )
    )
    assert dry_tracker.file_written is False
    assert dry_tracker.produced_output is False

    live_tracker = StreamBillingTracker()
    live_payload = {
        "tool_name": "batch_replace_files",
        "status": "success",
        "mutation_applied": True,
        "data": {
            "dry_run": False,
            "files_modified": 2,
            "files": [
                {"id": "f1", "title": "第一章", "new_length": 500, "new_word_count": 480},
                {"id": "f2", "title": "第二章", "new_length": 600, "new_word_count": 580},
            ],
            "skipped_files": [{"id": "f3", "title": "第三章", "reason": "stale_content"}],
        },
    }
    live_tracker.observe(_sse("tool_result", live_payload))
    assert live_tracker.file_written is True
    assert live_tracker.produced_output is True

    # 2) RoundWrittenFiles tracks every modified file and flags partial when skipped_files exist
    written = RoundWrittenFiles()
    written.observe(_sse("tool_result", live_payload))
    assert written.delivery(final_word_counts={})["written_files"] == [
        {"id": "f1", "title": "第一章", "partial": True},
        {"id": "f2", "title": "第二章", "partial": True},
    ]

    # 3) service._applied_file_mutations_for_fingerprints evicts all modified file IDs, ignores dry_run
    dry_tool_calls = [
        {"name": "batch_replace_files", "status": "success", "arguments": {"dry_run": True}, "result": {"status": "success", "mutation_applied": False, "data": {"dry_run": True, "files": [{"id": "f1"}]}}}
    ]
    assert _applied_file_mutations_for_fingerprints(dry_tool_calls) == (set(), False)

    live_tool_calls = [
        {"name": "batch_replace_files", "status": "success", "arguments": {"old_text": "a", "new_text": "b"}, "result": {"status": "success", "mutation_applied": True, "data": live_payload["data"]}}
    ]
    assert _applied_file_mutations_for_fingerprints(live_tool_calls) == ({"f1", "f2"}, True)

    # 4) RepeatEditGuard tracks batch_replace_files commits & unlocks stale-skipped files
    edit_guard = RepeatEditGuard()
    edit_guard._files["f1"] = edit_guard._files.get("f1") or SimpleNamespace()  # ensure clean observe
    edit_guard._files.clear()
    plan = edit_guard.plan("batch_replace_files", '{"old_text":"a","new_text":"b"}')
    edit_guard.observe(
        "batch_replace_files",
        plan,
        json.dumps({"status": "success", "mutation_applied": True, "data": live_payload["data"]}, ensure_ascii=False),
    )
    assert edit_guard._files["f1"].last_length == 500
    assert edit_guard._files["f2"].last_word_count == 580
    assert "f3" in edit_guard._allow_self_read


@pytest.mark.unit
def test_batch_replace_files_chat_tool_cards_undo_and_session_loader(db_session: Session, workspace):
    from config.datetime_utils import normalize_datetime_to_utc
    from services.features.file_version_service import content_sha256

    user, project = workspace
    f1 = _make_file(db_session, project, title="第一章", content="新名字出场。")
    f2 = _make_file(db_session, project, title="第二章", content="新名字再现。")
    chat = ChatSession(user_id=user.id, project_id=project.id, title="批量改名测试")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    after_dt_1 = normalize_datetime_to_utc(f1.updated_at)
    after_dt_2 = normalize_datetime_to_utc(f2.updated_at)
    sha_1 = content_sha256(f1.content)
    sha_2 = content_sha256(f2.content)

    tool_calls = [
        {
            "id": "tc-batch-1",
            "name": "batch_replace_files",
            "arguments": {"old_text": "旧名字", "new_text": "新名字"},
            "status": "success",
            "result": {
                "status": "success",
                "mutation_applied": True,
                "data": {
                    "dry_run": False,
                    "files_modified": 2,
                    "files": [
                        {
                            "id": f1.id,
                            "title": f1.title,
                            "undo": {
                                "before_version_number": 1,
                                "expected_after_updated_at": after_dt_1.isoformat(),
                                "expected_after_content_sha256": sha_1,
                                "undone": False,
                            },
                        },
                        {
                            "id": f2.id,
                            "title": f2.title,
                            "undo": {
                                "before_version_number": 2,
                                "expected_after_updated_at": after_dt_2.isoformat(),
                                "expected_after_content_sha256": sha_2,
                                "undone": False,
                            },
                        },
                    ],
                },
            },
        }
    ]
    msg = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已批量替换。",
        tool_calls=json.dumps(tool_calls, ensure_ascii=False),
        message_metadata=json.dumps({"engine": "loop"}, ensure_ascii=False),
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    # 1) SessionLoader recognizes batch_replace_files in both written_file_ids and read_snapshots before undo
    loader = SessionLoader(project_id=project.id, user_id=user.id)
    written_ids = loader.session_written_file_ids(
        db_session,
        chat_session_id=chat.id,
        limit=10,
    )
    assert {f1.id, f2.id}.issubset(set(written_ids))
    snapshots_before = loader.session_read_snapshots(
        db_session,
        chat_session_id=chat.id,
    )
    assert snapshots_before[f1.id] == "新名字出场。"
    assert snapshots_before[f2.id] == "新名字再现。"

    # 2) chat_tool_cards marks ONLY f1's nested undo as undone=True
    updated = record_edit_card_undone(
        db_session,
        project_id=project.id,
        user_id=user.id,
        file_id=f1.id,
        message_id=msg.id,
        tool_use_id="tc-batch-1",
        before_version_number=1,
        expected_after_updated_at=after_dt_1,
    )
    assert updated is True
    db_session.commit()
    db_session.expire_all()
    saved_calls = json.loads(db_session.get(ChatMessage, msg.id).tool_calls)
    files_after = saved_calls[0]["result"]["data"]["files"]
    assert files_after[0]["undo"]["undone"] is True
    assert files_after[1]["undo"]["undone"] is False

    # 3) After f1 undo is recorded, f2 snapshot remains intact
    snapshots_after = loader.session_read_snapshots(
        db_session,
        chat_session_id=chat.id,
    )
    assert snapshots_after[f2.id] == "新名字再现。"
