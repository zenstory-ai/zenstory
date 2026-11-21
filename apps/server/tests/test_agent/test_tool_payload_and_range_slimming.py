"""Regression tests for Batch A tool-payload & range-read slimming (Ranks 1a, 2, 3, 4)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop.history import _tool_output_text
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.tools_adapter import build_agent_function_tools
from agent.stream_adapter import create_stream_adapter
from agent.tools import mcp_tools
from agent.tools.file_ops.content_range import SHORT_FILE_FULL_RETURN_MAX_CHARS, apply_content_range
from agent.tools.file_ops.serialization import QUERY_FILES_OMITTED_FIELDS, WRITE_FILE_OMITTED_FIELDS
from models import File, Project, User
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService


@pytest.fixture
def owner_project(db_session, monkeypatch):
    user = User(username="opt-a-owner", email="opt-a@example.test", hashed_password="x", email_verified=True)
    project = Project(name="Batch A 测试项目", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    return user, project


def _make_file(
    session: Session,
    project: Project,
    *,
    title: str = "第1章 测试",
    content: str = "",
    file_type: str = "draft",
    file_metadata: str | None = None,
) -> File:
    row = File(
        project_id=project.id,
        title=title,
        content=content,
        file_type=file_type,
        file_metadata=file_metadata,
    )
    session.add(row)
    session.flush()
    FileVersionService().create_version(
        session, row.id, content, change_type="create", change_source="ai", commit=False
    )
    session.commit()
    session.refresh(row)
    return row


def _long_collection() -> str:
    sections = []
    for n in range(1, 13):
        body = (
            f"第{n}章 雨夜{n}\n"
            f"　　林晚在第{n}章收起雨伞，靠在廊柱边上，听见远处钟声敲了三下。\n\n"
            f"　　陈默翻开旧账本，第{n}页写着一行极细的小字，墨迹早已被潮气晕开。\n\n"
            + ("　　长街尽头的灯笼在风里微微晃动，青石板泛着冷光。\n\n" * 40)
        )
        sections.append(body)
    text = "".join(sections)
    assert len(text) > SHORT_FILE_FULL_RETURN_MAX_CHARS
    return text


# ---------------------------------------------------------------------------
# Rank 1a: Range-read seen spans allow fuzzy/approx inside span, exact outside,
# and keep StaleContentError 100% strict.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_range_read_allows_fuzzy_and_approx_inside_seen_span_but_blocks_unseen_region(
    db_session, owner_project
):
    user, project = owner_project
    content = _long_collection()
    chapter = _make_file(db_session, project, title="合集", content=content)
    engine = db_session.get_bind()

    mcp_tools.ToolContext.set_context(
        None,
        user.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
        read_snapshots={},
    )
    try:
        raw_read = await mcp_tools.query_files({"id": chapter.id, "section": "第8章"})
        read_payload = json.loads(raw_read["content"][0]["text"])
        assert read_payload["data"][0]["content_partial"] is True
        mcp_tools.ToolContext.promote_pending_reads()

        # Partial range read is still NOT a full read, but tracks the seen character span.
        assert mcp_tools.ToolContext.has_seen_file(chapter.id) is False
        spans = mcp_tools.ToolContext.seen_file_spans(chapter.id)
        assert len(spans) == 1 and spans[0][1] > spans[0][0]

        # 1) Fuzzy punctuation mismatch INSIDE seen section 第8章 succeeds.
        fuzzy_inside = json.loads(
            (
                await mcp_tools.edit_file(
                    {
                        "id": chapter.id,
                        "edits": [
                            {
                                "op": "replace",
                                "old": "林晚在第8章收起雨伞, 靠在廊柱边上, 听见远处钟声敲了三下.",
                                "new": "林晚在第8章合上油纸伞，靠在廊柱边上听钟声。",
                            }
                        ],
                    }
                )
            )["content"][0]["text"]
        )
        assert fuzzy_inside["status"] == "success", fuzzy_inside
        assert fuzzy_inside["data"]["details"][0]["match_mode"] == "fuzzy"
        assert "林晚在第8章收起雨伞" in fuzzy_inside["data"]["details"][0]["matched_original"]

        # 2) Subsequent approximate edit in the SAME seen section succeeds after span shift!
        approx_inside = json.loads(
            (
                await mcp_tools.edit_file(
                    {
                        "id": chapter.id,
                        "edits": [
                            {
                                "op": "replace",
                                "old": "陈默翻开旧账本，第8页写着一行极细的小字，墨迹早就被潮气晕开了。",
                                "new": "陈默合上旧账本，把第8页折了一角。",
                            }
                        ],
                    }
                )
            )["content"][0]["text"]
        )
        assert approx_inside["status"] == "success", approx_inside
        assert approx_inside["data"]["details"][0]["match_mode"] == "approximate"

        # 3) Fuzzy punctuation mismatch OUTSIDE seen section (in unseen 第2章) is REJECTED!
        fuzzy_outside = json.loads(
            (
                await mcp_tools.edit_file(
                    {
                        "id": chapter.id,
                        "edits": [
                            {
                                "op": "replace",
                                "old": "林晚在第2章收起雨伞, 靠在廊柱边上, 听见远处钟声敲了三下.",
                                "new": "不应改到未读章节。",
                            }
                        ],
                    }
                )
            )["content"][0]["text"]
        )
        assert fuzzy_outside["status"] == "error"
        assert fuzzy_outside["error_type"] == "anchor_not_found"
        assert "范围" in fuzzy_outside["error"]

        # 4) Exact verbatim edit outside seen span still works (existing behavior).
        exact_outside = json.loads(
            (
                await mcp_tools.edit_file(
                    {
                        "id": chapter.id,
                        "edits": [
                            {
                                "op": "replace",
                                "old": "第2章 雨夜2",
                                "new": "第2章 晴夜2",
                            }
                        ],
                    }
                )
            )["content"][0]["text"]
        )
        assert exact_outside["status"] == "success"

        # 5) begin_agent_run clears range spans.
        mcp_tools.ToolContext.begin_agent_run()
        assert mcp_tools.ToolContext.seen_file_spans(chapter.id) == []
    finally:
        mcp_tools.ToolContext.clear_context()


@pytest.mark.asyncio
async def test_range_read_still_enforces_stale_content_error_on_author_edit_anywhere(db_session, owner_project):
    user, project = owner_project
    content = _long_collection()
    chapter = _make_file(db_session, project, title="合集", content=content)
    engine = db_session.get_bind()

    mcp_tools.ToolContext.set_context(
        None,
        user.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
        read_snapshots={},
    )
    try:
        await mcp_tools.query_files({"id": chapter.id, "section": "第8章"})
        mcp_tools.ToolContext.promote_pending_reads()

        # Author edits an unseen section (第1章) after the range read.
        with Session(engine) as other:
            row = other.get(File, chapter.id)
            row.content = row.content.replace("第1章 雨夜1", "第1章 作者改写")
            other.add(row)
            other.commit()

        edit = json.loads(
            (
                await mcp_tools.edit_file(
                    {
                        "id": chapter.id,
                        "edits": [
                            {
                                "op": "replace",
                                "old": "林晚在第8章收起雨伞, 靠在廊柱边上, 听见远处钟声敲了三下.",
                                "new": "林晚收了伞。",
                            }
                        ],
                    }
                )
            )["content"][0]["text"]
        )
        assert edit["status"] == "error"
        assert edit["error_type"] == "stale_content"
    finally:
        mcp_tools.ToolContext.clear_context()


# ---------------------------------------------------------------------------
# Rank 2: edit_file LLM slimming + side-channel UI parity + update_project dedupe + compact JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_file_slims_llm_output_and_stream_adapter_restores_ui_card_and_undo(
    db_session, owner_project
):
    user, project = owner_project
    chapter = _make_file(
        db_session,
        project,
        title="第一章 归来",
        content="　　雨下了一整夜。\n\n　　温梨把伞收了，靠在摊子边上。",
    )
    engine = db_session.get_bind()

    mcp_tools.ToolContext.set_context(
        None,
        user.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
        read_snapshots={chapter.id: chapter.content},
    )
    try:
        tools = {t.name: t for t in build_agent_function_tools()}
        sdk_ctx = SimpleNamespace(tool_call_id="call-sdk-edit-1")
        args = json.dumps(
            {
                "id": chapter.id,
                "edits": [
                    {"op": "replace", "old": "雨下了一整夜。", "new": "雨停在黎明前。"},
                    {"op": "append", "text": "\n\n　　天亮了。"},
                ],
            },
            ensure_ascii=False,
        )
        llm_text = await tools["edit_file"].on_invoke_tool(sdk_ctx, args)

        # Compact JSON: no ": " or ", " formatting bloat.
        assert '": "' not in llm_text and '", "' not in llm_text

        llm_payload = json.loads(llm_text)
        assert llm_payload["status"] == "success"
        data = llm_payload["data"]
        assert "undo" not in data
        for empty_key in ("failed_edits", "unchanged_edits", "warnings", "partial_success", "all_failed"):
            assert empty_key not in data
        assert data["edits_applied"] == 2
        assert data["new_word_count"] > 0

        # Exact replace & append omit verbatim preview echoes.
        replace_detail, append_detail = data["details"]
        assert replace_detail["op"] == "replace" and replace_detail["match_mode"] == "exact"
        assert "old_preview" not in replace_detail and "new_preview" not in replace_detail
        assert append_detail["op"] == "append" and append_detail["text_words"] > 0
        assert "text_preview" not in append_detail and "new_preview" not in append_detail

        # StreamAdapter restores full UI undo & details previews via ToolContext side-channel!
        adapter = create_stream_adapter(project_id=project.id, user_id=user.id)

        async def events():
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "tool_use_id": "call-sdk-edit-1",
                    "name": "edit_file",
                    "result": {"content": [{"type": "text", "text": llm_text}]},
                },
            )

        emitted = [ev async for ev in adapter.process_workflow_events(events())]
        tool_result_ev = next(ev for ev in emitted if ev.type == "tool_result")
        ui_data = tool_result_ev.data["data"]
        assert isinstance(ui_data["undo"]["before_version_number"], int)
        assert ui_data["undo"]["expected_after_updated_at"]
        assert len(ui_data["undo"]["expected_after_content_sha256"]) == 64
        assert ui_data["details"][0]["old_preview"] == "雨下了一整夜。"
        assert ui_data["details"][0]["new_preview"] == "雨停在黎明前。"
        assert "天亮了。" in ui_data["details"][1]["text_preview"]
        assert "天亮了。" in ui_data["details"][1]["new_preview"]

        applied_events = [ev for ev in emitted if ev.type == "file_edit_applied"]
        assert len(applied_events) == 2
        assert applied_events[0].data["old_preview"] == "雨下了一整夜。"
        assert applied_events[0].data["new_preview"] == "雨停在黎明前。"
        assert "天亮了。" in applied_events[1].data["new_preview"]

        # History replay from persisted UI-enriched card re-slims undo & exact previews + compact JSON.
        replayed_text = _tool_output_text(
            {"name": "edit_file", "status": "success", "result": ui_data, "error": None}
        )
        assert '": "' not in replayed_text
        replayed = json.loads(replayed_text)
        assert "undo" not in replayed["data"]
        assert "old_preview" not in replayed["data"]["details"][0]
        assert "new_preview" not in replayed["data"]["details"][0]
        assert "text_preview" not in replayed["data"]["details"][1]
    finally:
        mcp_tools.ToolContext.clear_context()

    # clear_context empties side-channel cleanly.
    assert mcp_tools.ToolContext.pop_tool_ui_payload("call-sdk-edit-1") is None


@pytest.mark.asyncio
async def test_edit_file_keeps_non_exact_diagnostics_and_partial_failures(db_session, owner_project):
    user, project = owner_project
    chapter = _make_file(
        db_session,
        project,
        title="第一章",
        content="　　温梨把伞收了，靠在摊子边上，看那些旧书一本一本被雨打湿。",
    )
    engine = db_session.get_bind()

    mcp_tools.ToolContext.set_context(
        None,
        user.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
        read_snapshots={chapter.id: chapter.content},
    )
    try:
        raw = await mcp_tools.edit_file(
            {
                "id": chapter.id,
                "continue_on_error": True,
                "edits": [
                    {"op": "replace", "old": "温梨把伞收了, 靠在摊子边上.", "new": "温梨合上了伞。"},
                    {"op": "replace", "old": "根本不存在的句子", "new": "x"},
                ],
            }
        )
        payload = json.loads(raw["content"][0]["text"])
        assert payload["status"] == "partial"
        data = payload["data"]
        assert data["partial_success"] is True
        assert "all_failed" not in data
        assert len(data["failed_edits"]) == 1
        assert data["warnings"]
        assert data["details"][0]["match_mode"] == "fuzzy"
        assert data["details"][0]["matched_original"]
        assert data["details"][0]["warning"]
        assert "undo" not in data
    finally:
        mcp_tools.ToolContext.clear_context()


@pytest.mark.asyncio
async def test_update_project_omits_duplicate_top_level_current_status(db_session, owner_project):
    user, project = owner_project
    engine = db_session.get_bind()
    mcp_tools.ToolContext.set_context(
        None, user.id, project.id, None, create_session_func=lambda: Session(engine)
    )
    try:
        raw = await mcp_tools.update_project(
            {"summary": "一段较长的项目摘要" * 20, "current_phase": "已写至第3章"}
        )
        text = raw["content"][0]["text"]
        assert '": "' not in text
        payload = json.loads(text)
        assert payload["status"] == "success"
        assert "current_status" not in payload["data"]
        assert payload["data"]["project_status"]["current_status"]["current_phase"] == "已写至第3章"
        assert payload["data"]["updated_fields"] == ["summary", "current_phase"]
    finally:
        mcp_tools.ToolContext.clear_context()


# ---------------------------------------------------------------------------
# Rank 3: query_files / create_file / write_chapter serialization + id+query bug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_files_slims_dead_columns_metadata_preview_and_honors_id_plus_query(
    db_session, owner_project
):
    user, project = owner_project
    f_match = _make_file(
        db_session,
        project,
        title="第一章 玉佩",
        content=("前情铺垫。" * 80) + "温梨从袖口摸出半块青玉佩，放回木匣。" + ("后文收尾。" * 80),
        file_metadata=json.dumps({"word_count": 999, "word_count_rev": 2}),
    )
    f_title_only = _make_file(
        db_session,
        project,
        title="玉佩设定集",
        content="这是一份只写了材质来历的设定文件，正文里没有那个词。" * 10,
        file_type="lore",
        file_metadata=json.dumps({"word_count": 100, "word_count_rev": 1, "importance": "high"}),
    )
    f_null_meta = _make_file(
        db_session,
        project,
        title="空元数据章节",
        content="简单正文。",
        file_metadata=None,
    )
    engine = db_session.get_bind()
    mcp_tools.ToolContext.set_context(
        None,
        user.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
        read_snapshots={},
    )
    try:
        # 1) List summary omits 5 dead DB cols & redundant/empty file_metadata.
        raw_list = await mcp_tools.query_files({})
        list_text = raw_list["content"][0]["text"]
        assert '": "' not in list_text
        rows_by_id = {row["id"]: row for row in json.loads(list_text)["data"]}
        for row in rows_by_id.values():
            for col in QUERY_FILES_OMITTED_FIELDS:
                assert col not in row
        assert "file_metadata" not in rows_by_id[f_match.id]
        assert "file_metadata" not in rows_by_id[f_null_meta.id]
        assert rows_by_id[f_title_only.id]["file_metadata"] == '{"importance":"high"}'

        # 2) Search summary omits content_preview when match_count > 0, keeps it on title-only match_count == 0.
        search_rows = {
            row["id"]: row
            for row in json.loads((await mcp_tools.query_files({"query": "玉佩"}))["content"][0]["text"])["data"]
        }
        assert search_rows[f_match.id]["match_count"] == 1
        assert "青玉佩" in search_rows[f_match.id]["matches"][0]
        assert "content_preview" not in search_rows[f_match.id]
        assert search_rows[f_title_only.id]["match_count"] == 0
        assert "content_preview" in search_rows[f_title_only.id]

        # 3) id + query honors query (returns compact match snippet, NOT full body!).
        id_query_hit = json.loads(
            (await mcp_tools.query_files({"id": f_match.id, "query": "青玉佩"}))["content"][0]["text"]
        )["data"]
        assert len(id_query_hit) == 1
        assert "content" not in id_query_hit[0]
        assert "content_preview" not in id_query_hit[0]
        assert id_query_hit[0]["match_count"] == 1
        assert "青玉佩" in id_query_hit[0]["matches"][0]

        id_query_miss = json.loads(
            (await mcp_tools.query_files({"id": f_match.id, "query": "绝不存在的词"}))["content"][0]["text"]
        )["data"]
        assert id_query_miss == []

        # Explicit response_mode="full" with id + query still returns full content.
        id_query_full = json.loads(
            (await mcp_tools.query_files({"id": f_match.id, "query": "青玉佩", "response_mode": "full"}))[
                "content"
            ][0]["text"]
        )["data"]
        assert len(id_query_full) == 1
        assert id_query_full[0]["content"] == f_match.content

        # 4) create_file / write_chapter drop 4 dead DB cols but KEEP updated_at for StreamAdapter.
        created = json.loads(
            (await mcp_tools.create_file({"title": "大纲", "file_type": "outline", "content": "提纲"}))[
                "content"
            ][0]["text"]
        )["data"]
        for col in WRITE_FILE_OMITTED_FIELDS:
            assert col not in created
        assert isinstance(created.get("updated_at"), str) and created["updated_at"]
        assert "file_metadata" not in created
    finally:
        mcp_tools.ToolContext.clear_context()


def test_repeat_read_guard_treats_id_plus_query_as_distinct_searches():
    guard = RepeatReadGuard()
    plan_a = guard.plan("query_files", json.dumps({"id": "f1", "query": "玉佩"}))
    plan_b = guard.plan("query_files", json.dumps({"id": "f1", "query": "长剑"}))
    assert plan_a.read_key == ("query_files", "f1", "search:玉佩")
    assert plan_b.read_key == ("query_files", "f1", "search:长剑")
    assert plan_a.read_key != plan_b.read_key


# ---------------------------------------------------------------------------
# Rank 4: Short-file start_char full-return (<= 12k vs > 12k) + full read snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_short_file_start_char_returns_full_and_records_full_snapshot(db_session, owner_project):
    user, project = owner_project
    short_content = "第一章 短稿\n" + ("温梨收起雨伞，靠在摊子边上。" * 300)
    assert len(short_content) <= SHORT_FILE_FULL_RETURN_MAX_CHARS
    short_file = _make_file(db_session, project, title="短稿", content=short_content)

    long_content = "长稿开头\n" + ("甲" * 18_000)
    assert len(long_content) > SHORT_FILE_FULL_RETURN_MAX_CHARS
    long_file = _make_file(db_session, project, title="长稿", content=long_content)

    engine = db_session.get_bind()
    mcp_tools.ToolContext.set_context(
        None,
        user.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
        read_snapshots={},
    )
    try:
        # Short file with start_char + tiny max_chars returns the entire file in 1 step!
        short_resp = json.loads(
            (await mcp_tools.query_files({"id": short_file.id, "start_char": 1000, "max_chars": 1500}))[
                "content"
            ][0]["text"]
        )
        [short_row] = short_resp["data"]
        assert short_row["content"] == short_content
        assert short_row["content_partial"] is False
        assert short_row["content_range"] == {"start": 0, "end": len(short_content)}
        assert "note" not in short_row

        mcp_tools.ToolContext.promote_pending_reads()
        assert mcp_tools.ToolContext.has_seen_file(short_file.id) is True

        # Long file (> 12k) with start_char=0, max_chars=2000 still slices.
        long_slice = json.loads(
            (await mcp_tools.query_files({"id": long_file.id, "start_char": 0, "max_chars": 2000}))[
                "content"
            ][0]["text"]
        )["data"][0]
        assert long_slice["content_partial"] is True
        assert long_slice["content_range"] == {"start": 0, "end": 2000}

        # Long file (> 12k) where remaining (total - start <= 12k) returns entire remaining tail in 1 step.
        long_tail = apply_content_range(
            {"id": long_file.id, "content": long_content},
            {"start_char": 10_000, "max_chars": 2000},
        )
        assert long_tail["content_range"] == {"start": 10_000, "end": len(long_content)}
        assert long_tail["content_partial"] is True
    finally:
        mcp_tools.ToolContext.clear_context()
