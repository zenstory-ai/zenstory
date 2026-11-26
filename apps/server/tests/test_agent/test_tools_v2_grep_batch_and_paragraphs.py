"""Tests for Agent Writing Tools v2:
1. Precise `grep` match mode on `query_files` (`response_mode="matches"`).
2. Cross-file deterministic `batch_replace_files` (dry_run, real multi-file commits with
   FileVersion + pre-write backup + undo anchors, 30-file cap, body_guards, stale isolation,
   cross-project isolation, pending-file block, ToolFailureBreaker reset, RepeatReadGuard,
   and StreamAdapter live edit events).
3. Paragraph-numbered + word-count read view (`show_paragraph_stats=True`) with raw
   `read_snapshots` preservation.
4. `edit_file` failure-only paragraph/line-number prefix strip retry.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
from agent.core.workflow_events import StreamEventType
from agent.openai_agents.events import mcp_text_result
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker
from agent.openai_agents.tools_adapter import build_agent_function_tools
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools import mcp_tools
from agent.tools.file_ops.edit import FileEditor
from agent.tools.registry import LOOP_TOOL_NAMES
from agent.tools.tool_context import AgentRequestContext
from models import File, FileVersion, Project, User
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import BEFORE_AI_EDIT_SUMMARY, FileVersionService
from utils.text_metrics import count_words

pytestmark = pytest.mark.unit


def _parse_mcp(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["content"][0]["text"])


def _make_user_project(db_session: Session, tag: str) -> tuple[User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"v2-{tag}-{suffix}@example.com",
        username=f"v2_{tag}_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(name=f"V2 {tag} {suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


def _add_file(
    db_session: Session,
    project_id: str,
    *,
    title: str,
    content: str = "",
    file_type: str = "draft",
    parent_id: str | None = None,
    order: int = 0,
    is_deleted: bool = False,
) -> File:
    f = File(
        project_id=project_id,
        title=title,
        content=content,
        file_type=file_type,
        parent_id=parent_id,
        order=order,
        is_deleted=is_deleted,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)
    return f


def _make_req_ctx(
    db_session: Session,
    user: User,
    project: Project,
    *,
    is_loop_agent: bool = True,
) -> AgentRequestContext:
    engine = db_session.get_bind()
    return AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id=f"sess-{uuid4().hex[:8]}",
        create_session_func=lambda: Session(engine),
        read_snapshots={},
        current_agent="loop" if is_loop_agent else None,
    )


@pytest.fixture(autouse=True)
def _silence_activation(monkeypatch):
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)


# ===========================================================================
# 1. Precise `grep` match mode on `query_files` (`response_mode="matches"`)
# ===========================================================================


@pytest.mark.asyncio
async def test_query_files_matches_single_char_punctuation_and_line_paragraph_numbers(db_session):
    user, project = _make_user_project(db_session, "grep-punct")
    _, other_project = _make_user_project(db_session, "grep-other")

    folder = _add_file(db_session, project.id, title="正文", file_type="folder", order=0)
    ch1 = _add_file(
        db_session,
        project.id,
        title="第1章 雨夜",
        parent_id=folder.id,
        order=1,
        content="　　雨还在下。\n\n　　你真的要去吗?他低声问。\n　　没人回答?只有风声。",
    )
    ch2 = _add_file(
        db_session,
        project.id,
        title="第2章 天明",
        parent_id=folder.id,
        order=2,
        content="　　晨光洒进屋里。\n　　一切都很安静。",
    )
    # Soft-deleted & other-project files must never appear.
    _add_file(
        db_session,
        project.id,
        title="第3章 已删除",
        parent_id=folder.id,
        order=3,
        content="这里有问号?",
        is_deleted=True,
    )
    _add_file(
        db_session,
        other_project.id,
        title="其他项目第1章",
        content="其他项目的问号?",
    )
    outline = _add_file(
        db_session,
        project.id,
        title="总大纲",
        file_type="outline",
        order=0,
        content="悬念：幕后黑手是谁?",
    )

    req_ctx = _make_req_ctx(db_session, user, project)
    res = _parse_mcp(
        await mcp_tools.query_files(
            {"query": "?", "response_mode": "matches"},
            request_context=req_ctx,
        )
    )
    assert res["status"] == "success"
    data = res["data"]
    assert data["query"] == "?"
    assert data["matched_files"] == 2
    assert data["total_matches"] == 3
    assert data["truncated"] is False
    assert [f["id"] for f in data["files"]] == [outline.id, ch1.id]

    ch1_entry = data["files"][1]
    assert ch1_entry["title"] == "第1章 雨夜"
    assert ch1_entry["file_type"] == "draft"
    assert ch1_entry["match_count"] == 2
    assert "content" not in ch1_entry
    assert ch1_entry["matches"][0]["paragraph"] == 2
    assert ch1_entry["matches"][0]["line"] == 3
    assert "你真的要去吗?他低声问。" in ch1_entry["matches"][0]["snippet"]
    assert ch1_entry["matches"][1]["paragraph"] == 3
    assert ch1_entry["matches"][1]["line"] == 4
    assert "没人回答?只有风声。" in ch1_entry["matches"][1]["snippet"]

    # Filter by file_type="draft" and parent_id excludes outline.
    res_draft = _parse_mcp(
        await mcp_tools.query_files(
            {"query": "?", "response_mode": "matches", "file_type": "draft", "parent_id": folder.id},
            request_context=req_ctx,
        )
    )
    assert [f["id"] for f in res_draft["data"]["files"]] == [ch1.id]
    assert res_draft["data"]["total_matches"] == 2

    # Filter by ids respects exact scope.
    res_ids = _parse_mcp(
        await mcp_tools.query_files(
            {"query": "?", "response_mode": "matches", "ids": [ch2.id]},
            request_context=req_ctx,
        )
    )
    assert res_ids["data"]["matched_files"] == 0
    assert res_ids["data"]["files"] == []


@pytest.mark.asyncio
async def test_query_files_matches_truncation_caps_and_literal_regex_safety(db_session):
    user, project = _make_user_project(db_session, "grep-caps")
    # Create 35 chapters, each with 3 hits of "(a+)+" (literal regex metachars, zero ReDoS).
    needle = "(a+)+"
    for idx in range(1, 36):
        _add_file(
            db_session,
            project.id,
            title=f"第{idx}章",
            order=idx,
            content=f"第一段 {needle} 结尾。\n第二段 {needle} 结尾。\n第三段 {needle} 结尾。",
        )

    req_ctx = _make_req_ctx(db_session, user, project)
    res = _parse_mcp(
        await mcp_tools.query_files(
            {"query": needle, "response_mode": "matches"},
            request_context=req_ctx,
        )
    )
    assert res["status"] == "success"
    data = res["data"]
    assert data["matched_files"] == 35
    assert data["total_matches"] == 105
    assert data["truncated"] is True
    assert len(data["files"]) <= mcp_tools.QUERY_MATCHES_MAX_FILES
    returned_matches = sum(len(f["matches"]) for f in data["files"])
    assert returned_matches == mcp_tools.QUERY_MATCHES_MAX_TOTAL

    # Missing/empty query in matches mode returns structured validation error.
    bad = _parse_mcp(
        await mcp_tools.query_files(
            {"response_mode": "matches", "query": "   "},
            request_context=req_ctx,
        )
    )
    assert bad["status"] == "error"
    assert bad["error_type"] == "invalid_tool_input"


# ===========================================================================
# 2. Cross-file deterministic `batch_replace_files`
# ===========================================================================


@pytest.mark.asyncio
async def test_batch_replace_files_dry_run_and_real_commit_with_backup_version_and_undo(db_session):
    user, project = _make_user_project(db_session, "batch-basic")
    f1 = _add_file(
        db_session,
        project.id,
        title="第1章 初遇",
        order=1,
        content="　　沈砚站在雨里，沈砚没有回头。",
    )
    f2 = _add_file(
        db_session,
        project.id,
        title="第2章 重逢",
        order=2,
        content="　　茶楼里，沈砚放下茶盏。",
    )
    f3 = _add_file(
        db_session,
        project.id,
        title="第3章 无关",
        order=3,
        content="　　城外下起了雪。",
    )

    req_ctx = _make_req_ctx(db_session, user, project)

    # 1) dry_run=True previews without modifying DB or creating FileVersion rows.
    dry = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "沈砚", "new_text": "陈砚", "dry_run": True},
            request_context=req_ctx,
        )
    )
    assert dry["status"] == "success"
    assert dry["mutation_applied"] is False
    dry_data = dry["data"]
    assert dry_data["dry_run"] is True
    assert dry_data["matched_files"] == 2
    assert dry_data["modified_files"] == 0
    assert dry_data["total_replacements"] == 3
    assert [item["id"] for item in dry_data["files"]] == [f1.id, f2.id]
    assert dry_data["files"][0]["replacements"] == 2
    assert dry_data["files"][0]["sample_snippets"]

    db_session.expire_all()
    assert "沈砚" in db_session.get(File, f1.id).content
    assert db_session.exec(select(FileVersion).where(FileVersion.file_id == f1.id)).all() == []

    # 2) dry_run=False commits via FileEditor._commit_ai_content with pre-write backup, AI version, & undo anchor.
    live = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "沈砚", "new_text": "陈砚"},
            request_context=req_ctx,
        )
    )
    assert live["status"] == "success"
    assert live["mutation_applied"] is True
    live_data = live["data"]
    assert live_data["modified_files"] == 2
    assert live_data["total_replacements"] == 3
    assert [item["id"] for item in live_data["files"]] == [f1.id, f2.id]

    db_session.expire_all()
    assert db_session.get(File, f1.id).content == "　　陈砚站在雨里，陈砚没有回头。"
    assert db_session.get(File, f2.id).content == "　　茶楼里，陈砚放下茶盏。"
    assert db_session.get(File, f3.id).content == "　　城外下起了雪。"

    for item in live_data["files"]:
        # P0-2: LLM-bound output strips UI-only undo anchors to save context and survive truncation.
        assert "undo" not in item

    ui_overlay = req_ctx.pop_tool_ui_payload(None, fallback_key=("batch_replace_files", project.id))
    assert isinstance(ui_overlay, dict)
    ui_files = ui_overlay["files"]
    assert [item["id"] for item in ui_files] == [f1.id, f2.id]

    for item, original in (
        (ui_files[0], "　　沈砚站在雨里，沈砚没有回头。"),
        (ui_files[1], "　　茶楼里，沈砚放下茶盏。"),
    ):
        undo = item.get("undo")
        assert isinstance(undo, dict)
        assert isinstance(undo["before_version_number"], int)
        assert isinstance(undo["expected_after_updated_at"], str)
        assert len(undo["expected_after_content_sha256"]) == 64

        versions = db_session.exec(
            select(FileVersion)
            .where(FileVersion.file_id == item["id"])
            .order_by(FileVersion.version_number.asc())
        ).all()
        assert len(versions) == 2
        assert versions[0].change_summary == BEFORE_AI_EDIT_SUMMARY
        assert "AI 批量替换" in (versions[1].change_summary or "")

        # Verify the undo anchor genuinely rolls back to the exact original manuscript.
        FileVersionService().rollback_to_version(
            db_session,
            file_id=item["id"],
            version_number=undo["before_version_number"],
            user_id=user.id,
        )
        db_session.expire_all()
        assert db_session.get(File, item["id"]).content == original


@pytest.mark.asyncio
async def test_batch_replace_files_30_file_cap_and_continuation(db_session):
    user, project = _make_user_project(db_session, "batch-cap")
    created_ids: list[str] = []
    for idx in range(1, 36):
        f = _add_file(
            db_session,
            project.id,
            title=f"第{idx}章",
            order=idx,
            content=f"　　第{idx}章里有旧名。",
        )
        created_ids.append(f.id)

    req_ctx = _make_req_ctx(db_session, user, project)
    first = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "旧名", "new_text": "新名"},
            request_context=req_ctx,
        )
    )
    assert first["status"] == "success"
    assert first["mutation_applied"] is True
    assert first["data"]["matched_files"] == 35
    assert first["data"]["modified_files"] == 30
    assert first["data"]["truncated"] is True
    assert first["data"]["remaining_count"] == 5
    assert [item["id"] for item in first["data"]["files"]] == created_ids[:30]
    assert [item["id"] for item in first["data"]["remaining_files"]] == created_ids[30:]
    assert "remaining_files" in first["note"]

    # Second call finishes the remaining 5 files cleanly.
    second = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "旧名", "new_text": "新名"},
            request_context=req_ctx,
        )
    )
    assert second["status"] == "success"
    assert second["data"]["modified_files"] == 5
    assert second["data"]["truncated"] is False
    assert second["data"]["remaining_files"] == []


@pytest.mark.asyncio
async def test_batch_replace_files_body_guards_stale_and_cross_project_isolation(db_session):
    user, project = _make_user_project(db_session, "batch-guards")
    other_user, other_project = _make_user_project(db_session, "batch-other")

    # File A: normal draft that will succeed.
    ok_file = _add_file(
        db_session,
        project.id,
        title="第1章 正常",
        order=1,
        content="　　这是正常段落。\n　　里面包含旧词。",
    )
    # File B: body_guards whole_body_replace trigger (entire body == old_text).
    whole_file = _add_file(
        db_session,
        project.id,
        title="第2章 仅旧词",
        order=2,
        content="旧词",
    )
    # File C: stale snapshot conflict.
    stale_file = _add_file(
        db_session,
        project.id,
        title="第3章 作者刚改过",
        order=3,
        content="　　作者刚保存的内容，含旧词。",
    )
    # Soft-deleted file & folder & other project file.
    deleted_file = _add_file(
        db_session,
        project.id,
        title="已删除",
        order=4,
        content="旧词",
        is_deleted=True,
    )
    other_file = _add_file(
        db_session,
        other_project.id,
        title="别的项目",
        order=1,
        content="　　别的项目里的旧词。",
    )

    req_ctx = _make_req_ctx(db_session, user, project, is_loop_agent=True)
    # Pretend model had read an older version of stale_file before author edited it.
    req_ctx.record_read_snapshot(stale_file.id, "　　更早之前的旧稿，含旧词。")
    req_ctx.promote_pending_reads()

    res = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "旧词", "new_text": "新词"},
            request_context=req_ctx,
        )
    )
    assert res["status"] == "partial"
    assert res["mutation_applied"] is True
    data = res["data"]
    assert [item["id"] for item in data["files"]] == [ok_file.id]
    skipped_by_id = {item["id"]: item["reason"] for item in data["skipped_files"]}
    assert skipped_by_id[whole_file.id] == "whole_body_replace"
    assert skipped_by_id[stale_file.id] == "stale_content"

    db_session.expire_all()
    assert db_session.get(File, ok_file.id).content == "　　这是正常段落。\n　　里面包含新词。"
    assert db_session.get(File, whole_file.id).content == "旧词"
    assert db_session.get(File, stale_file.id).content == "　　作者刚保存的内容，含旧词。"
    assert db_session.get(File, deleted_file.id).content == "旧词"
    assert db_session.get(File, other_file.id).content == "　　别的项目里的旧词。"

    # Explicit cross-project file_ids is rejected cleanly with file_not_found.
    cross = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "旧词", "new_text": "新词", "file_ids": [other_file.id]},
            request_context=req_ctx,
        )
    )
    assert cross["status"] == "error"
    assert cross["error_type"] == "file_not_found"
    db_session.expire_all()
    assert db_session.get(File, other_file.id).content == "　　别的项目里的旧词。"

    # Invalid input: old_text == new_text and empty old_text.
    same = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "相同", "new_text": "相同"},
            request_context=req_ctx,
        )
    )
    assert same["status"] == "error"
    assert same["error_type"] == "invalid_edit"


@pytest.mark.asyncio
async def test_batch_replace_adapter_guards_breaker_reset_read_guard_and_stream_events(db_session):
    assert LOOP_TOOL_NAMES[-1] == "batch_replace_files"

    user, project = _make_user_project(db_session, "batch-wiring")
    f1 = _add_file(db_session, project.id, title="第1章", order=1, content="　　旧名字出场。")
    f2 = _add_file(db_session, project.id, title="第2章", order=2, content="　　旧名字再次出场。")

    req_ctx = _make_req_ctx(db_session, user, project)
    breaker = ToolFailureBreaker()
    read_guard = RepeatReadGuard(request_context=req_ctx)

    tools = {t.name: t for t in build_agent_function_tools(failure_breaker=breaker, read_guard=read_guard)}
    assert "batch_replace_files" in tools

    class _RunCtx:
        context = req_ctx
        tool_call_id = "call-batch-1"

    # 1) Blocked during overdue pending <file> body just like edit_file.
    req_ctx.begin_model_reply()
    req_ctx.set_pending_empty_file("pending-ch", "第9章 待写")
    req_ctx.begin_model_reply()
    blocked_text = await tools["batch_replace_files"].on_invoke_tool(
        _RunCtx(), json.dumps({"old_text": "旧名字", "new_text": "新名字"})
    )
    blocked = json.loads(blocked_text)
    assert blocked["status"] == "error"
    assert blocked["error_type"] == "file_body_pending"
    req_ctx.clear_pending_empty_file("pending-ch")

    # 2) Pre-read f1 twice so RepeatReadGuard has read counts; simulate prior consecutive failures in breaker.
    read_args = json.dumps({"ids": [f1.id]})
    await tools["read_files"].on_invoke_tool(_RunCtx(), read_args)
    await tools["read_files"].on_invoke_tool(_RunCtx(), read_args)
    assert read_guard._read_counts[("query_files", f1.id, "full")] == 2
    breaker.consecutive_failures = 4

    # dry_run does NOT reset breaker and does NOT count as a write in RepeatReadGuard.
    dry_text = await tools["batch_replace_files"].on_invoke_tool(
        _RunCtx(), json.dumps({"old_text": "旧名字", "new_text": "新名字", "dry_run": True})
    )
    assert json.loads(dry_text)["mutation_applied"] is False
    assert breaker.consecutive_failures == 4
    assert read_guard.write_succeeded is False

    # Real batch replace resets breaker.consecutive_failures AND records writes + forgets reads in RepeatReadGuard.
    live_text = await tools["batch_replace_files"].on_invoke_tool(
        _RunCtx(), json.dumps({"old_text": "旧名字", "new_text": "新名字"})
    )
    live = json.loads(live_text)
    assert live["status"] == "success"
    assert live["mutation_applied"] is True
    assert breaker.consecutive_failures == 0
    assert read_guard.write_succeeded is True
    assert f1.id in read_guard.files_written
    assert f2.id in read_guard.files_written
    assert ("query_files", f1.id, "full") not in read_guard._read_counts

    # 3) StreamAdapter emits file_edit_start / file_edit_applied / file_edit_end for every modified file.
    adapter = StreamAdapter(StreamAdapterConfig(project_id=project.id, user_id=user.id, request_context=req_ctx))

    async def _workflow_events():
        yield WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "batch_replace_files",
                "tool_use_id": "call-batch-1",
                "result": mcp_text_result(live_text),
            },
        )

    emitted = [evt async for evt in adapter.process_workflow_events(_workflow_events())]
    tool_results = [e for e in emitted if e.type == "tool_result"]
    assert len(tool_results) == 1
    restored_files = tool_results[0].data["data"]["files"]
    assert [f["id"] for f in restored_files] == [f1.id, f2.id]
    assert all(isinstance(f.get("undo"), dict) and len(f["undo"]["expected_after_content_sha256"]) == 64 for f in restored_files)
    edit_starts = [e for e in emitted if e.type == "file_edit_start"]
    edit_applied = [e for e in emitted if e.type == "file_edit_applied"]
    edit_ends = [e for e in emitted if e.type == "file_edit_end"]
    assert [e.data["file_id"] for e in edit_starts] == [f1.id, f2.id]
    assert [e.data["file_id"] for e in edit_applied] == [f1.id, f2.id]
    assert [e.data["file_id"] for e in edit_ends] == [f1.id, f2.id]
    assert adapter._file_mutated is True

    # dry_run emits NO file_edit_* events.
    dry_adapter = StreamAdapter(StreamAdapterConfig(project_id=project.id, user_id=user.id))

    async def _dry_events():
        yield WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "batch_replace_files",
                "tool_use_id": "call-dry",
                "result": mcp_text_result(dry_text),
            },
        )

    dry_emitted = [evt async for evt in dry_adapter.process_workflow_events(_dry_events())]
    assert not any(e.type.startswith("file_edit_") for e in dry_emitted)
    assert dry_adapter._file_mutated is False


# ===========================================================================
# 3. Paragraph-numbered + word-count read view (`show_paragraph_stats=True`)
# ===========================================================================


@pytest.mark.asyncio
async def test_query_files_show_paragraph_stats_formats_paragraphs_and_keeps_raw_snapshots(db_session):
    user, project = _make_user_project(db_session, "pstats")
    raw_body = "　　第一段正文内容在这里。\n\n　　第二段较长一些，窗外雨声淅沥。"
    chapter = _add_file(db_session, project.id, title="第1章", content=raw_body)

    req_ctx = _make_req_ctx(db_session, user, project)
    res = _parse_mcp(
        await mcp_tools.query_files(
            {"id": chapter.id, "show_paragraph_stats": True},
            request_context=req_ctx,
        )
    )
    assert res["status"] == "success"
    [row] = res["data"]
    p1_words = count_words("　　第一段正文内容在这里。")
    p2_words = count_words("　　第二段较长一些，窗外雨声淅沥。")
    assert row["paragraph_count"] == 2
    assert row["paragraph_stats_annotated"] is True
    assert row["word_count"] == count_words(raw_body)
    assert row["content"] == (
        f"[P01 · {p1_words}字] 　　第一段正文内容在这里。\n\n"
        f"[P02 · {p2_words}字] 　　第二段较长一些，窗外雨声淅沥。"
    )

    # CRITICAL: read_snapshots must stay RAW (unannotated), so edit_file stale-check passes!
    req_ctx.promote_pending_reads()
    assert req_ctx.read_snapshot(chapter.id) == raw_body

    edit_res = _parse_mcp(
        await mcp_tools.edit_file(
            {
                "id": chapter.id,
                "edits": [{"op": "replace", "old": "第一段正文内容在这里。", "new": "第一段已经改好。"}],
            },
            request_context=req_ctx,
        )
    )
    assert edit_res["status"] == "success"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content.startswith("　　第一段已经改好。")


@pytest.mark.asyncio
async def test_query_files_show_paragraph_stats_on_range_read_uses_global_paragraph_numbers(db_session):
    user, project = _make_user_project(db_session, "pstats-range")
    body = "第1章 上\n" + ("甲" * 13000) + "\n\n第2章 下\n　　窗外天色渐明，雨势已经停了。"
    chapter = _add_file(db_session, project.id, title="合集", content=body)

    req_ctx = _make_req_ctx(db_session, user, project)
    res = _parse_mcp(
        await mcp_tools.query_files(
            {"id": chapter.id, "section": "第2章", "show_paragraph_stats": True},
            request_context=req_ctx,
        )
    )
    assert res["status"] == "success"
    [row] = res["data"]
    assert row["content_partial"] is True
    assert row["paragraph_count"] == 4
    assert row["paragraph_stats_annotated"] is True
    assert "[P03 · " in row["content"]
    assert "[P04 · " in row["content"]

    req_ctx.promote_pending_reads()
    assert req_ctx.read_snapshot(chapter.id) == body


# ===========================================================================
# 4. `edit_file` failure-only paragraph-marker prefix strip retry
# ===========================================================================


def test_edit_file_strips_paragraph_prefixes_on_failure_only_and_never_writes_markers(db_session):
    user, project = _make_user_project(db_session, "prefix-retry")
    # Include a long (>80 chars) paragraph to verify approximate match does NOT silently write markers!
    long_p = (
        "　　窗外的雨下了整整一夜，青石板路积满浑浊的雨水，廊下的铜铃被风吹得摇晃不止，"
        "茶盏里的残茶早已凉透，他盯着桌上那封尚未拆开的信，迟迟没有伸手去碰。"
    )
    short_p = "　　门忽然开了。"
    chapter = _add_file(
        db_session,
        project.id,
        title="第1章",
        content=f"{long_p}\n\n{short_p}",
    )

    editor = FileEditor(db_session, user.id)
    new_long_p = long_p.replace("迟迟没有伸手去碰", "终于缓缓伸出手去")
    result = editor.edit_file(
        id=chapter.id,
        edits=[
            {
                "op": "replace",
                "old": f"[P01 · 84字] {long_p}",
                "new": f"[P01 · 84字] {new_long_p}",
            }
        ],
    )
    assert result["mutation_applied"] is True
    assert result["edits_applied"] == 1
    assert result["details"][0]["paragraph_prefixes_stripped"] is True

    db_session.expire_all()
    saved = db_session.get(File, chapter.id).content
    assert "[P01" not in saved
    assert saved.startswith(new_long_p)

    # Also test line-number arrow prefix (`12→`) and delete/insert_after.
    result2 = editor.edit_file(
        id=chapter.id,
        edits=[
            {
                "op": "insert_after",
                "anchor": f"  3→{short_p}",
                "text": "\n  4→　　来人是陈砚。",
            }
        ],
    )
    assert result2["mutation_applied"] is True
    assert result2["details"][0]["paragraph_prefixes_stripped"] is True
    db_session.expire_all()
    saved2 = db_session.get(File, chapter.id).content
    assert "4→" not in saved2
    assert "来人是陈砚。" in saved2


def test_edit_file_first_attempt_untouched_for_literal_p_markers_and_partial_prefix_not_stripped(db_session):
    user, project = _make_user_project(db_session, "prefix-literal")
    literal_doc = _add_file(
        db_session,
        project.id,
        title="测试设定",
        file_type="lore",
        content="[P1] 原始设定条目\n[P2] 第二条设定",
    )

    editor = FileEditor(db_session, user.id)
    # 1) Literal [P1] in manuscript MUST match on attempt 1 without prefix stripping!
    res = editor.edit_file(
        id=literal_doc.id,
        edits=[{"op": "replace", "old": "[P1] 原始设定条目", "new": "[P1] 更新后的设定条目"}],
    )
    assert res["mutation_applied"] is True
    assert "paragraph_prefixes_stripped" not in res["details"][0]
    db_session.expire_all()
    assert db_session.get(File, literal_doc.id).content == "[P1] 更新后的设定条目\n[P2] 第二条设定"

    # 2) Partial prefixes (line 1 has prefix, line 2 does NOT) must NOT trigger prefix stripping.
    plain_doc = _add_file(
        db_session,
        project.id,
        title="普通正文",
        content="第一行正文\n第二行正文",
    )
    with pytest.raises(Exception) as exc_info:
        editor.edit_file(
            id=plain_doc.id,
            edits=[
                {
                    "op": "replace",
                    "old": "[P01 · 5字] 第一行正文\n第二行正文",
                    "new": "新第一行\n新第二行",
                    "match_mode": "exact",
                }
            ],
        )
    assert "not found" in str(exc_info.value).lower() or "找不到" in str(exc_info.value)


# ===========================================================================
# 5. Review regressions (P1-1..4 + P2-2, P2-4, P2-6)
# ===========================================================================


@pytest.mark.asyncio
async def test_batch_replace_every_paragraph_rename_allowed_under_body_guards(db_session):
    """P1-1: multi-paragraph draft where old_text appears in every paragraph must succeed under body_guards."""
    user, project = _make_user_project(db_session, "batch-every-para")
    draft = _add_file(
        db_session,
        project.id,
        title="第1章 每段都有主角",
        order=1,
        content="　　沈砚推开门。\n\n　　沈砚坐下喝茶。\n\n　　沈砚望向窗外。",
    )
    whole_only = _add_file(
        db_session,
        project.id,
        title="第2章 全文等于旧词",
        order=2,
        content="沈砚",
    )

    req_ctx = _make_req_ctx(db_session, user, project, is_loop_agent=True)
    res = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "沈砚", "new_text": "陈砚"},
            request_context=req_ctx,
        )
    )
    assert res["status"] == "partial"
    assert res["mutation_applied"] is True
    assert [item["id"] for item in res["data"]["files"]] == [draft.id]
    assert res["data"]["files"][0]["replacements"] == 3
    assert {item["id"]: item["reason"] for item in res["data"]["skipped_files"]} == {
        whole_only.id: "whole_body_replace"
    }

    db_session.expire_all()
    assert db_session.get(File, draft.id).content == "　　陈砚推开门。\n\n　　陈砚坐下喝茶。\n\n　　陈砚望向窗外。"
    assert db_session.get(File, whole_only.id).content == "沈砚"


def test_repeat_read_guard_forgets_stale_reads_on_batch_replace_error_and_matches_precedence():
    """P1-2 & P2-4: batch_replace_files error/mixed-skip stale_content clears read counts; matches mode takes precedence."""
    guard = RepeatReadGuard()

    # P1-2a: all-stale status="error" clears read count so recovery read is not blocked.
    key_a = ("query_files", "f-stale-1", "full")
    guard._read_counts[key_a] = 3
    all_stale_out = json.dumps(
        {
            "status": "error",
            "error_type": "stale_content",
            "mutation_applied": False,
            "data": {
                "modified_files": 0,
                "skipped_files": [{"id": "f-stale-1", "reason": "stale_content", "error": "stale"}],
            },
        },
        ensure_ascii=False,
    )
    guard.observe("batch_replace_files", guard.plan("batch_replace_files", "{}"), all_stale_out)
    assert key_a not in guard._read_counts
    assert guard.write_succeeded is False

    # P1-2b: mixed-skip status="error" where first skip is whole_body_replace and second is stale_content.
    key_b = ("query_files", "f-stale-2", "full")
    guard._read_counts[key_b] = 3
    mixed_err_out = json.dumps(
        {
            "status": "error",
            "error_type": "whole_body_replace",
            "mutation_applied": False,
            "data": {
                "modified_files": 0,
                "skipped_files": [
                    {"id": "f-whole", "reason": "whole_body_replace", "error": "whole"},
                    {"id": "f-stale-2", "reason": "stale_content", "error": "stale"},
                ],
            },
        },
        ensure_ascii=False,
    )
    guard.observe("batch_replace_files", guard.plan("batch_replace_files", "{}"), mixed_err_out)
    assert key_b not in guard._read_counts

    # P2-4: response_mode="matches" takes precedence over include_content=True and range args.
    plan_matches = guard.plan(
        "query_files",
        json.dumps({"id": "f-1", "response_mode": "matches", "query": "?", "include_content": True}),
    )
    assert plan_matches.read_key == ("query_files", "f-1", "matches:?")
    plan_matches_range = guard.plan(
        "query_files",
        json.dumps({"id": "f-1", "response_mode": "matches", "query": "?", "tail_chars": 100}),
    )
    assert plan_matches_range.read_key == ("query_files", "f-1", "matches:?")


def test_paragraph_index_at_offset_crlf_and_trailing_whitespace_alignment():
    """P1-3: CRLF and trailing-whitespace range boundaries never shift paragraph numbers by -1."""
    from agent.tools.file_ops.content_range import format_paragraph_stats, paragraph_index_at_offset

    # CRLF boundary: offset lands on `\r` at end of line 2 -> slice is "\r\n第三段"
    crlf_body = "第一段\r\n第二段\r\n第三段"
    crlf_offset = len(crlf_body) - 5  # index 8 -> "\r\n第三段"
    assert crlf_body[crlf_offset:] == "\r\n第三段"
    start_p = paragraph_index_at_offset(crlf_body, crlf_offset)
    assert start_p == 3
    annotated, _ = format_paragraph_stats(crlf_body[crlf_offset:], start_paragraph=start_p)
    assert "[P03 · 3字] 第三段" in annotated

    # Trailing spaces boundary: offset lands inside trailing spaces of line 2 -> slice is "  \n第三段"
    ws_body = "第一段\n第二段   \n第三段"
    ws_offset = 8  # "  \n第三段"
    assert ws_body[ws_offset:] == "  \n第三段"
    ws_start_p = paragraph_index_at_offset(ws_body, ws_offset)
    assert ws_start_p == 3
    ws_annotated, _ = format_paragraph_stats(ws_body[ws_offset:], start_paragraph=ws_start_p)
    assert "[P03 · 3字] 第三段" in ws_annotated

    # Mid-paragraph split: remaining text on line 2 is non-blank -> keeps P02 for remainder, P03 for line 3
    mid_body = "第一段\n第二段前半后半\n第三段"
    mid_offset = mid_body.index("后半")
    assert mid_body[mid_offset:] == "后半\n第三段"
    mid_start_p = paragraph_index_at_offset(mid_body, mid_offset)
    assert mid_start_p == 2
    mid_annotated, _ = format_paragraph_stats(mid_body[mid_offset:], start_paragraph=mid_start_p)
    assert "[P02 · 2字] 后半\n[P03 · 3字] 第三段" == mid_annotated


def test_edit_file_marker_only_old_does_not_corrupt_newlines_and_body_guards_sees_stripped_edit(db_session):
    """P1-4 & P2-2: marker-only old refuses to strip to '\\n'; stripped whole-body replace still trips body_guards."""
    user, project = _make_user_project(db_session, "prefix-edge")
    doc = _add_file(db_session, project.id, title="第1章", content="第一段\n第二段")
    editor = FileEditor(db_session, user.id)

    # P1-4: "[P01 · 3字]\n[P02 · 3字]" must NOT strip to "\n" and corrupt the paragraph break.
    with pytest.raises(Exception) as exc_info:
        editor.edit_file(
            id=doc.id,
            edits=[{"op": "replace", "old": "[P01 · 3字]\n[P02 · 3字]", "new": "BOOM"}],
        )
    assert "not found" in str(exc_info.value).lower() or "找不到" in str(exc_info.value)
    db_session.expire_all()
    assert db_session.get(File, doc.id).content == "第一段\n第二段"

    # P2-2: 1-paragraph draft replaced via prefixed old must still trigger whole_body_replace under body_guards=True.
    one_para = _add_file(db_session, project.id, title="第2章 单段", content="整章正文内容。")
    with pytest.raises(Exception) as guard_exc:
        editor.edit_file(
            id=one_para.id,
            edits=[
                {
                    "op": "replace",
                    "old": "[P01 · 6字] 整章正文内容。",
                    "new": "[P01 · 6字] 全新整章内容。",
                }
            ],
            body_guards=True,
        )
    assert getattr(guard_exc.value, "error_type", "") == "whole_body_replace"
    db_session.expire_all()
    assert db_session.get(File, one_para.id).content == "整章正文内容。"


@pytest.mark.asyncio
async def test_same_turn_query_then_batch_replace_then_edit_file_keeps_fresh_snapshot(db_session):
    """P2-6: query_files -> batch_replace_files -> edit_file in the same turn must not hit false StaleContentError."""
    user, project = _make_user_project(db_session, "same-turn-snap")
    ch = _add_file(
        db_session,
        project.id,
        title="第1章 连续修改",
        order=1,
        content="　　沈砚推开门。\n\n　　窗外雨声不停。",
    )
    req_ctx = _make_req_ctx(db_session, user, project, is_loop_agent=True)

    read_res = _parse_mcp(await mcp_tools.query_files({"id": ch.id}, request_context=req_ctx))
    assert read_res["status"] == "success"
    req_ctx.promote_pending_reads()

    batch_res = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "沈砚", "new_text": "陈砚"},
            request_context=req_ctx,
        )
    )
    assert batch_res["status"] == "success"
    assert batch_res["mutation_applied"] is True
    assert req_ctx.read_snapshot(ch.id) == "　　陈砚推开门。\n\n　　窗外雨声不停。"

    edit_res = _parse_mcp(
        await mcp_tools.edit_file(
            {
                "id": ch.id,
                "edits": [{"op": "replace", "old": "窗外雨声不停。", "new": "窗外雨势渐歇。"}],
            },
            request_context=req_ctx,
        )
    )
    assert edit_res["status"] == "success"
    assert edit_res["mutation_applied"] is True
    db_session.expire_all()
    assert db_session.get(File, ch.id).content == "　　陈砚推开门。\n\n　　窗外雨势渐歇。"


@pytest.mark.asyncio
async def test_batch_replace_files_unexpected_error_mid_batch_preserves_committed_files_and_undo(
    db_session, monkeypatch
):
    """P0-1: An unexpected DB error on file #2 after file #1 committed returns partial with file #1's undo intact."""
    from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
    from agent.core.workflow_events import StreamEventType
    from agent.stream_adapter import StreamAdapter, StreamAdapterConfig

    user, project = _make_user_project(db_session, "batch-p0-1")
    f1 = _add_file(db_session, project.id, title="第1章", order=1, content="旧词出现在第一章。")
    f2 = _add_file(db_session, project.id, title="第2章", order=2, content="旧词出现在第二章。")
    f3 = _add_file(db_session, project.id, title="第3章", order=3, content="旧词出现在第三章。")

    original_one = FileEditor._batch_replace_one_file

    def _flaky_one(self, *, file_id: str, **kwargs):
        if file_id == f2.id:
            raise RuntimeError("simulated database connection hiccup")
        return original_one(self, file_id=file_id, **kwargs)

    monkeypatch.setattr(FileEditor, "_batch_replace_one_file", _flaky_one)

    req_ctx = _make_req_ctx(db_session, user, project)
    raw_mcp = await mcp_tools.batch_replace_files(
        {"old_text": "旧词", "new_text": "新词"},
        request_context=req_ctx,
        tool_call_id="call-p0-1",
    )
    res = _parse_mcp(raw_mcp)

    assert res["status"] == "partial"
    assert res["mutation_applied"] is True
    data = res["data"]
    assert data["modified_files"] == 2
    assert [item["id"] for item in data["files"]] == [f1.id, f3.id]
    assert len(data["skipped_files"]) == 1
    assert data["skipped_files"][0]["id"] == f2.id
    assert data["skipped_files"][0]["reason"] == "unexpected_error"
    assert "hiccup" in data["skipped_files"][0]["error"]

    # File #1 and #3 committed; file #2 rolled back cleanly.
    db_session.expire_all()
    assert db_session.get(File, f1.id).content == "新词出现在第一章。"
    assert db_session.get(File, f2.id).content == "旧词出现在第二章。"
    assert db_session.get(File, f3.id).content == "新词出现在第三章。"

    # StreamAdapter restores undo anchors and emits file_edit_* events for both committed files.
    adapter = StreamAdapter(StreamAdapterConfig(project_id=project.id, user_id=user.id, request_context=req_ctx))

    async def _events():
        yield WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={"name": "batch_replace_files", "tool_use_id": "call-p0-1", "result": raw_mcp},
        )

    emitted = [evt async for evt in adapter.process_workflow_events(_events())]
    tool_evt = next(e for e in emitted if e.type == "tool_result")
    assert tool_evt.data["status"] == "success"
    restored = tool_evt.data["data"]["files"]
    assert [item["id"] for item in restored] == [f1.id, f3.id]
    for item in restored:
        assert isinstance(item.get("undo"), dict)
        assert isinstance(item["undo"]["before_version_number"], int)
        assert len(item["undo"]["expected_after_content_sha256"]) == 64
    assert [e.data["file_id"] for e in emitted if e.type == "file_edit_end"] == [f1.id, f3.id]
    assert adapter._file_mutated is True


@pytest.mark.asyncio
async def test_batch_replace_files_200_overflow_and_oversized_payload_preserves_all_undo_anchors(
    db_session, monkeypatch
):
    """P0-2: 200+ overflow files + long old_text/new_text + tight TOOL_RESULT_MAX_CHARS never drops undo anchors."""
    from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
    from agent.core.workflow_events import StreamEventType
    from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
    from agent.tools.file_ops.edit import BATCH_REPLACE_MAX_REMAINING_FILES

    user, project = _make_user_project(db_session, "batch-p0-2")
    long_old = "旧长词" * 400  # 1,200 chars
    long_new = "新长词" * 400  # 1,200 chars

    created_ids: list[str] = []
    for idx in range(1, 236):  # 30 modified + 205 overflow
        f = File(
            project_id=project.id,
            title=f"第{idx}章 超长章节标题测试",
            file_type="draft",
            order=idx,
            content=f"开头{long_old}结尾。",
        )
        db_session.add(f)
        created_ids.append(f.id)
    db_session.commit()

    # 1) Verify _BOUND_PROTECTED_KEYS protects undo tokens even under min_keep=0 compaction.
    assert "expected_after_updated_at" in mcp_tools._BOUND_PROTECTED_KEYS
    assert "expected_after_content_sha256" in mcp_tools._BOUND_PROTECTED_KEYS
    sample_sha = "a" * 64
    sample_ts = "2026-11-26T12:00:00.123456+00:00"
    bounded, was_cut = mcp_tools.bound_payload_strings(
        {
            "old_text": "x" * 2000,
            "files": [
                {
                    "id": "f1",
                    "undo": {
                        "before_version_number": 1,
                        "expected_after_updated_at": sample_ts,
                        "expected_after_content_sha256": sample_sha,
                    },
                }
            ],
        },
        max_chars=500,
        min_keep=0,
    )
    assert was_cut is True
    assert bounded["files"][0]["undo"]["expected_after_updated_at"] == sample_ts
    assert bounded["files"][0]["undo"]["expected_after_content_sha256"] == sample_sha

    # 2) Run batch_replace_files under an artificially tight TOOL_RESULT_MAX_CHARS so LLM output hits truncation.
    monkeypatch.setattr(mcp_tools, "TOOL_RESULT_MAX_CHARS", 1024)
    req_ctx = _make_req_ctx(db_session, user, project)
    raw_mcp = await mcp_tools.batch_replace_files(
        {"old_text": long_old, "new_text": long_new},
        request_context=req_ctx,
        tool_call_id="call-p0-2",
    )
    llm_payload = _parse_mcp(raw_mcp)
    assert llm_payload["status"] == "success"
    assert llm_payload["mutation_applied"] is True
    assert len(raw_mcp["content"][0]["text"]) <= 1024

    # 3) StreamAdapter restores all 30 modified files, their intact undo anchors, capped remaining_files, & exact count.
    adapter = StreamAdapter(StreamAdapterConfig(project_id=project.id, user_id=user.id, request_context=req_ctx))

    async def _events():
        yield WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={"name": "batch_replace_files", "tool_use_id": "call-p0-2", "result": raw_mcp},
        )

    emitted = [evt async for evt in adapter.process_workflow_events(_events())]
    tool_evt = next(e for e in emitted if e.type == "tool_result")
    ui_data = tool_evt.data["data"]
    assert ui_data["matched_files"] == 235
    assert ui_data["modified_files"] == 30
    assert ui_data["remaining_count"] == 205
    assert len(ui_data["remaining_files"]) == BATCH_REPLACE_MAX_REMAINING_FILES
    assert ui_data["truncated"] is True
    assert len(ui_data["files"]) == 30
    for item in ui_data["files"]:
        undo = item.get("undo")
        assert isinstance(undo, dict)
        assert isinstance(undo["before_version_number"], int)
        assert isinstance(undo["expected_after_updated_at"], str) and undo["expected_after_updated_at"]
        assert isinstance(undo["expected_after_content_sha256"], str) and len(undo["expected_after_content_sha256"]) == 64


@pytest.mark.asyncio
async def test_read_files_show_paragraph_stats_preserves_cross_turn_session_read_snapshots(db_session):
    """P1-3: read_files(show_paragraph_stats=True) carries over session_read_snapshots for both <=4k and >4k files."""
    from agent.core.chat_storage import slim_query_files_result_for_persistence
    from agent.core.session_loader import SessionLoader
    from agent.loop.history import LOOP_ENGINE_METADATA_VALUE
    from models import ChatMessage, ChatSession

    user, project = _make_user_project(db_session, "pstats-cross-turn")
    chat = ChatSession(user_id=user.id, project_id=project.id, title="段落统计跨轮")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    short_body = "　　第一段短正文。\n\n　　第二段短正文。"
    long_body = "\n\n".join(f"　　第{i}段长正文内容用于触发落库瘦身阈值校验。" for i in range(1, 220))
    assert len(long_body) > 4000

    short_file = _add_file(db_session, project.id, title="第1章 短章", order=1, content=short_body)
    long_file = _add_file(db_session, project.id, title="第2章 长章", order=2, content=long_body)

    req_ctx = _make_req_ctx(db_session, user, project)
    short_res = _parse_mcp(
        await mcp_tools.read_files({"ids": [short_file.id], "show_paragraph_stats": True}, request_context=req_ctx)
    )
    long_res = _parse_mcp(
        await mcp_tools.read_files({"ids": [long_file.id], "show_paragraph_stats": True}, request_context=req_ctx)
    )
    assert short_res["data"][0]["paragraph_stats_annotated"] is True
    assert long_res["data"][0]["paragraph_stats_annotated"] is True
    assert isinstance(short_res["data"][0].get("raw_content_sha256"), str)
    assert isinstance(long_res["data"][0].get("raw_content_sha256"), str)

    # Simulate StreamAdapter persistence slimming: short stays un-slimmed, long gets slimmed to content_sha256.
    persisted_short = slim_query_files_result_for_persistence(short_res["data"])
    persisted_long = slim_query_files_result_for_persistence(long_res["data"])
    assert "content" in persisted_short[0]
    assert "content" not in persisted_long[0]
    assert persisted_long[0]["content_sha256"] == long_res["data"][0]["raw_content_sha256"]

    msg = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="已读取两章段落统计。",
        tool_calls=json.dumps(
            [
                {"id": "tc-short", "name": "read_files", "status": "success", "result": persisted_short},
                {"id": "tc-long", "name": "read_files", "status": "success", "result": persisted_long},
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE}, ensure_ascii=False),
    )
    db_session.add(msg)
    db_session.commit()

    loader = SessionLoader(project_id=project.id, user_id=user.id)
    # Cold DB replay: un-slimmed annotated read carries over via raw_content_sha256, while
    # persist-slimmed long_file (content stripped from DB) MUST NOT carry over without warm cache.
    cold_snapshots = loader.session_read_snapshots(db_session, chat_session_id=chat.id)
    assert cold_snapshots.get(short_file.id) == short_body
    assert long_file.id not in cold_snapshots

    # Warm wire_prefix_cache hit (allow_slimmed_read_sha256=True): both short and slimmed long_file carry over.
    warm_snapshots = loader.session_read_snapshots(
        db_session, chat_session_id=chat.id, allow_slimmed_read_sha256=True
    )
    assert warm_snapshots.get(short_file.id) == short_body
    assert warm_snapshots.get(long_file.id) == long_body

    # If the author edits short_file before next turn, carryover MUST reject stale snapshot.
    short_file.content = short_body + "\n\n　　作者手动新增一段。"
    db_session.add(short_file)
    db_session.commit()
    snapshots_after_edit = loader.session_read_snapshots(
        db_session, chat_session_id=chat.id, allow_slimmed_read_sha256=True
    )
    assert short_file.id not in snapshots_after_edit
    assert snapshots_after_edit.get(long_file.id) == long_body
