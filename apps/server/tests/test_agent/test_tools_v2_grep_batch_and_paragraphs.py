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

    for item, original in (
        (live_data["files"][0], "　　沈砚站在雨里，沈砚没有回头。"),
        (live_data["files"][1], "　　茶楼里，沈砚放下茶盏。"),
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
    read_args = json.dumps({"id": f1.id})
    await tools["query_files"].on_invoke_tool(_RunCtx(), read_args)
    await tools["query_files"].on_invoke_tool(_RunCtx(), read_args)
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
