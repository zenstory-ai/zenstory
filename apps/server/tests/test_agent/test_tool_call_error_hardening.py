"""Comprehensive regression tests for production tool-call error hardening (P0-A, P0-B, P1, P2, P3)."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.core.events import EventType
from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
from agent.core.workflow_events import StreamEventType
from agent.openai_agents.tool_failure_breaker import (
    TRIP_REASON_RECOVERY,
    ToolFailureBreaker,
)
from agent.prompts.base import get_base_prompt, get_folder_reference
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools import mcp_tools
from agent.tools.file_ops.content_range import (
    INVALID_READ_RANGE_ERROR_TYPE,
    SHORT_FILE_FULL_RETURN_MAX_CHARS,
    ContentRangeError,
    apply_content_range,
)
from agent.tools.file_ops.edit import (
    EDIT_ERROR_ANCHOR_NOT_FOUND,
    EDIT_ERROR_FILE_NOT_FOUND,
    EDIT_ERROR_INVALID_PARENT_ID,
    EDIT_ERROR_SKILL_LIMIT_EXCEEDED,
    EDIT_ERROR_SKILL_NOT_FOUND,
    EDIT_ERROR_SKILL_RESOURCE_NOT_FOUND,
    EditBatchError,
    FileEditor,
    edit_error_user_message,
    format_malformed_uuid_hint,
)
from agent.tools.registry import LOOP_AGENT_TYPE
from agent.tools.tool_context import AgentRequestContext
from agent.tools.tool_schemas import CREATE_FILE_TOOL, LOAD_SKILL_TOOL, READ_FILES_TOOL, WRITE_CHAPTER_TOOL
from models import File, Project, PublicSkill, SkillResource, User, UserAddedSkill, UserSkill
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def owner_project(db_session: Session, monkeypatch):
    import database

    user = User(
        username="hardening_owner",
        email="hardening_owner@example.test",
        hashed_password="unused",
        email_verified=True,
        is_active=True,
    )
    project = Project(name="错误加固测试", owner_id=user.id, project_type="novel")
    db_session.add_all([user, project])
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(project)

    # Create standard novel root folders
    folders = [
        File(id=f"{project.id}-lore-folder", project_id=project.id, title="设定", file_type="folder", order=0),
        File(id=f"{project.id}-character-folder", project_id=project.id, title="角色", file_type="folder", order=1),
        File(id=f"{project.id}-material-folder", project_id=project.id, title="素材", file_type="folder", order=2),
        File(id=f"{project.id}-outline-folder", project_id=project.id, title="大纲", file_type="folder", order=3),
        File(id=f"{project.id}-draft-folder", project_id=project.id, title="正文", file_type="folder", order=4),
    ]
    db_session.add_all(folders)
    db_session.commit()

    engine = db_session.get_bind()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    return user, project


def _make_ctx(db_session: Session, user: User, project: Project, *, author_message: str | None = None) -> AgentRequestContext:
    engine = db_session.get_bind()
    return AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-hardening",
        current_agent=LOOP_AGENT_TYPE,
        author_message=author_message,
        create_session_func=lambda: Session(engine),
        read_snapshots={},
    )


def _add_file(
    session: Session,
    project: Project,
    *,
    title: str,
    content: str = "",
    file_type: str = "draft",
    parent_id: str | None = None,
) -> File:
    row = File(
        project_id=project.id,
        title=title,
        content=content,
        file_type=file_type,
        parent_id=parent_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ===========================================================================
# P0-A: read_files batch range + short-file out-of-bounds + error_type
# ===========================================================================


@pytest.mark.unit
def test_p0a_short_file_start_char_out_of_bounds_returns_full_and_long_file_errors():
    short_body = "　　雨停了，街灯亮起来。"
    row = apply_content_range({"id": "s1", "content": short_body}, {"start_char": 5400, "max_chars": 2000})
    assert row["content"] == short_body
    assert row["content_partial"] is False
    assert row["content_range"] == {"start": 0, "end": len(short_body)}
    assert "超出该文件长度" in row["note"]

    long_body = "甲" * (SHORT_FILE_FULL_RETURN_MAX_CHARS + 500)
    with pytest.raises(ContentRangeError, match="超出正文长度"):
        apply_content_range({"id": "l1", "content": long_body}, {"start_char": len(long_body) + 100})


@pytest.mark.asyncio
@pytest.mark.integration
async def test_p0a_read_files_batch_tail_and_start_char_and_reject_batch_section(db_session, owner_project):
    user, project = owner_project
    short_ch = _add_file(
        db_session,
        project,
        title="第1章 短章",
        content="第一段短内容。\n\n第二段结尾。",
        parent_id=f"{project.id}-draft-folder",
    )
    long_body = "开头片段。" + ("正文填充" * 4000) + "末尾收束。"
    long_ch = _add_file(
        db_session,
        project,
        title="第2章 长章",
        content=long_body,
        parent_id=f"{project.id}-draft-folder",
    )

    ctx = _make_ctx(db_session, user, project)
    try:
        # 1) Batch tail_chars works across multiple files independently
        tail_res = _payload(
            await mcp_tools.read_files({"ids": [short_ch.id, long_ch.id], "tail_chars": 10}, request_context=ctx)
        )
        assert tail_res["status"] == "success"
        by_id = {item["id"]: item for item in tail_res["data"]}
        assert by_id[short_ch.id]["content"] == short_ch.content[-10:]
        assert by_id[short_ch.id]["content_partial"] is True
        assert by_id[long_ch.id]["content"] == long_body[-10:]
        assert by_id[long_ch.id]["content_partial"] is True

        # 2) Batch start_char + max_chars: short file gets full text (seen), long file gets sliced (partial)
        range_res = _payload(
            await mcp_tools.read_files(
                {"ids": [short_ch.id, long_ch.id], "start_char": 0, "max_chars": 50},
                request_context=ctx,
            )
        )
        assert range_res["status"] == "success"
        by_id = {item["id"]: item for item in range_res["data"]}
        assert by_id[short_ch.id]["content"] == short_ch.content
        assert by_id[short_ch.id]["content_partial"] is False
        assert by_id[long_ch.id]["content"] == long_body[:50]
        assert by_id[long_ch.id]["content_partial"] is True
        assert by_id[long_ch.id]["content_range"] == {"start": 0, "end": 50}

        ctx.promote_pending_reads()
        assert ctx.has_seen_file(short_ch.id) is True
        assert ctx.has_seen_file(long_ch.id) is False
        assert ctx.seen_file_spans(long_ch.id) == [(0, 50), (len(long_body) - 10, len(long_body))]

        # 3) Batch section is rejected with structured invalid_read_range
        batch_sec = _payload(
            await mcp_tools.read_files(
                {"ids": [short_ch.id, long_ch.id], "section": "第1章"},
                request_context=ctx,
            )
        )
        assert batch_sec["status"] == "error"
        assert batch_sec["error_type"] == INVALID_READ_RANGE_ERROR_TYPE
        assert batch_sec["user_message"] == edit_error_user_message(INVALID_READ_RANGE_ERROR_TYPE)

        # 4) Caught ContentRangeError (out-of-bounds on long file) returns invalid_read_range
        oob = _payload(
            await mcp_tools.read_files(
                {"ids": [long_ch.id], "start_char": len(long_body) + 500},
                request_context=ctx,
            )
        )
        assert oob["status"] == "error"
        assert oob["error_type"] == INVALID_READ_RANGE_ERROR_TYPE
        assert oob["user_message"] == edit_error_user_message(INVALID_READ_RANGE_ERROR_TYPE)
    finally:
        ctx.clear()

    # Schema reflects batch tail_chars / start_char vs single-file section
    assert "批量" in READ_FILES_TOOL["description"]
    assert "单" in READ_FILES_TOOL["input_schema"]["properties"]["section"]["description"]
    assert "批量" in READ_FILES_TOOL["input_schema"]["properties"]["tail_chars"]["description"]


# ===========================================================================
# P0-B: write_chapter & create_file parent_id hardening + safe error allowlist
# ===========================================================================


@pytest.mark.unit
def test_p0b_prompts_and_schemas_omit_write_chapter_parent_id_and_clarify_project_id():
    folder_ids = {
        "draft": "proj-1-draft-folder",
        "outline": "proj-1-outline-folder",
        "character": "proj-1-character-folder",
    }
    ref = get_folder_reference(folder_ids)
    assert "不要把项目 ID 传作 parent_id" in ref
    assert "write_chapter 默认省略 parent_id" in ref

    prompt = get_base_prompt(
        project_id="proj-1",
        folder_ids=folder_ids,
        config={
            "role_definition": "role",
            "capabilities": "cap",
            "directory_structure": "{draft}",
            "content_structure": "struct",
            "file_types": "draft",
            "writing_guidelines": "guide",
        },
    )
    assert "write_chapter(chapter=3, title='第三章 雨夜', file_type='draft')" in prompt
    assert "parent_id='<正文目录>'" not in prompt
    assert prompt.endswith("## 当前项目 ID: proj-1")
    assert "默认省略" in WRITE_CHAPTER_TOOL["input_schema"]["properties"]["parent_id"]["description"]
    assert "最新一条消息" in CREATE_FILE_TOOL["input_schema"]["properties"]["source_text"]["description"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_p0b_logical_aliases_project_id_handling_and_invalid_parent_id(db_session, owner_project):
    user, project = owner_project
    ctx = _make_ctx(db_session, user, project, author_message="简短指令")
    try:
        # 1) Logical folder alias in create_file (character -> {project_id}-character-folder)
        char_res = _payload(
            await mcp_tools.create_file(
                {"title": "林舟", "file_type": "character", "parent_id": "character", "empty": True},
                request_context=ctx,
            )
        )
        assert char_res["status"] == "success"
        assert char_res["data"]["parent_id"] == f"{project.id}-character-folder"

        # 2) Logical folder alias in write_chapter (draft -> {project_id}-draft-folder)
        ch_res = _payload(
            await mcp_tools.write_chapter(
                {"chapter": 1, "title": "第一章 雨夜", "file_type": "draft", "parent_id": "draft", "empty": True},
                request_context=ctx,
            )
        )
        assert ch_res["status"] == "success"
        assert ch_res["data"]["parent_id"] == f"{project.id}-draft-folder"

        # 3) create_file(file_type="folder", parent_id=project_id) resolves cleanly to root (parent_id=None)
        folder_res = _payload(
            await mcp_tools.create_file(
                {"title": "第一卷", "file_type": "folder", "parent_id": project.id},
                request_context=ctx,
            )
        )
        assert folder_res["status"] == "success"
        assert folder_res["data"]["parent_id"] is None

        # 4) write_chapter(parent_id=project_id) in unambiguous single-folder project treats as omitted
        ch2_res = _payload(
            await mcp_tools.write_chapter(
                {"chapter": 2, "title": "第二章 渡口", "file_type": "draft", "parent_id": project.id, "empty": True},
                request_context=ctx,
            )
        )
        assert ch2_res["status"] == "success"
        assert ch2_res["data"]["parent_id"] == f"{project.id}-draft-folder"

        # 5) Invalid parent_id returns structured error_type="invalid_parent_id" + safe user_message
        bad_parent = _payload(
            await mcp_tools.create_file(
                {"title": "废稿", "file_type": "outline", "parent_id": f"{project.id}-bad-folder", "empty": True},
                request_context=ctx,
            )
        )
        assert bad_parent["status"] == "error"
        assert bad_parent["error_type"] == EDIT_ERROR_INVALID_PARENT_ID
        assert bad_parent["user_message"] == edit_error_user_message(EDIT_ERROR_INVALID_PARENT_ID)
        assert bad_parent["mutation_applied"] is False

        # 6) Ambiguous logical alias (two top-level folders titled "设定") refuses to guess -> invalid_parent_id
        _add_file(db_session, project, title="设定", file_type="folder", parent_id=None)
        ambig_alias = _payload(
            await mcp_tools.create_file(
                {"title": "世界观", "file_type": "lore", "parent_id": "lore", "empty": True},
                request_context=ctx,
            )
        )
        assert ambig_alias["status"] == "error"
        assert ambig_alias["error_type"] == EDIT_ERROR_INVALID_PARENT_ID

        # 7) write_chapter(parent_id=project_id) in multi-volume ambiguous project rejects cleanly
        vol2 = _add_file(db_session, project, title="第二卷", file_type="folder", parent_id=None)
        _add_file(db_session, project, title="第1章 卷二开篇", file_type="draft", parent_id=vol2.id)
        ambig_ch = _payload(
            await mcp_tools.write_chapter(
                {"chapter": 3, "title": "第三章 歧路", "file_type": "draft", "parent_id": project.id, "empty": True},
                request_context=ctx,
            )
        )
        assert ambig_ch["status"] == "error"
        assert ambig_ch["error_type"] == mcp_tools.WRITE_CHAPTER_AMBIGUOUS_ERROR_TYPE

        # 8) source_text not in current turn message includes earlier-turn guidance
        src_err = _payload(
            await mcp_tools.create_file(
                {
                    "title": "大纲",
                    "file_type": "outline",
                    "parent_id": "outline",
                    "source_text": "这是更早一轮消息里的设定内容，不在本轮最新消息中。",
                },
                request_context=ctx,
            )
        )
        assert src_err["status"] == "error"
        assert src_err["error_type"] == mcp_tools.SOURCE_TEXT_NOT_FOUND_ERROR_TYPE
        assert "更早轮次" in src_err["error"]
    finally:
        ctx.clear()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_p0b_stream_adapter_sanitizes_create_file_errors_via_safe_allowlist():
    adapter = StreamAdapter(StreamAdapterConfig(project_id="proj-safe", user_id="u-1"))
    raw_tool_result = json.dumps(
        {
            "status": "error",
            "error": "parent_id「bad-uuid」不能用：内部模型调试说明 secret-folder-id",
            "error_type": EDIT_ERROR_INVALID_PARENT_ID,
            "user_message": edit_error_user_message(EDIT_ERROR_INVALID_PARENT_ID),
            "mutation_applied": False,
            "warnings": ["raw internal warning"],
        },
        ensure_ascii=False,
    )

    async def events():
        yield WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "create_file",
                "tool_use_id": "call-cf-1",
                "result": {"content": [{"text": raw_tool_result}]},
            },
        )
        # Also verify untyped create_file error without user_message never leaks raw error text
        yield WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "create_file",
                "tool_use_id": "call-cf-2",
                "result": {
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "status": "error",
                                    "error": "RAW_INTERNAL_DB_FAILURE secret-id-999",
                                },
                                ensure_ascii=False,
                            )
                        }
                    ]
                },
            },
        )

    emitted = [ev async for ev in adapter.process_workflow_events(events())]
    result_events = [ev for ev in emitted if ev.type == EventType.TOOL_RESULT]
    assert len(result_events) == 2

    ev1 = result_events[0].data
    assert ev1["status"] == "error"
    assert ev1["error"] == edit_error_user_message(EDIT_ERROR_INVALID_PARENT_ID)
    assert "secret-folder-id" not in json.dumps(ev1, ensure_ascii=False)
    assert "raw internal warning" not in json.dumps(ev1, ensure_ascii=False)
    assert ev1["data"]["error_type"] == EDIT_ERROR_INVALID_PARENT_ID

    ev2 = result_events[1].data
    assert ev2["status"] == "error"
    assert ev2["error"] == edit_error_user_message(None)
    assert "RAW_INTERNAL_DB_FAILURE" not in json.dumps(ev2, ensure_ascii=False)
    assert "secret-id-999" not in json.dumps(ev2, ensure_ascii=False)


# ===========================================================================
# P1: edit_file empty text fallback, alias normalization before op inference, replace_all, old == new
# ===========================================================================


@pytest.mark.integration
def test_p1_edit_file_insert_empty_text_fallback_aliases_replace_all_and_noop(db_session, owner_project):
    user, project = owner_project
    original = "　　旧称呼走了进来。\n\n　　旧称呼又说了一句话。\n\n　　窗外雨声渐歇。"
    chapter = _add_file(db_session, project, title="第1章 润色", content=original)
    editor = FileEditor(db_session, user.id)

    # 1) insert_after with text="" falls back cleanly to new="<content>"
    res1 = editor.edit_file(
        chapter.id,
        [{"op": "insert_after", "anchor": "窗外雨声渐歇。", "text": "", "new": "\n\n　　屋檐滴水。"}],
    )
    assert res1["edits_applied"] == 1
    assert res1["failed_edits"] == []
    db_session.expire_all()
    assert "屋檐滴水。" in db_session.get(File, chapter.id).content

    # 2) Omitted op with old_text + new_text infers op=replace cleanly
    res2 = editor.edit_file(
        chapter.id,
        [{"old_text": "屋檐滴水。", "new_text": "屋檐滴水不断。"}],
    )
    assert res2["edits_applied"] == 1
    assert res2["failed_edits"] == []
    db_session.expire_all()
    assert "屋檐滴水不断。" in db_session.get(File, chapter.id).content

    # 3) op="replace_all" normalizes to op="replace", replace_all=True
    res3 = editor.edit_file(
        chapter.id,
        [{"op": "replace_all", "old": "旧称呼", "new": "林舟"}],
    )
    assert res3["edits_applied"] == 1
    db_session.expire_all()
    updated = db_session.get(File, chapter.id).content
    assert "旧称呼" not in updated
    assert updated.count("林舟") == 2

    # 4) old == new (both exact and punctuation-drifted) -> unchanged_edits, NOT failed_edits / partial
    res4 = editor.edit_file(
        chapter.id,
        [
            {"op": "replace", "old": "林舟走了进来。", "new": "林舟走了进来。"},
            {"op": "replace", "old": "屋檐，滴水不断", "new": "屋檐，滴水不断"},
            {"op": "replace", "old": "窗外雨声渐歇。", "new": "窗外雨势转小。"},
        ],
    )
    assert res4["edits_applied"] == 1
    assert res4["failed_edits"] == []
    assert res4["partial_success"] is False
    assert res4["unchanged_edits"] == [
        {"index": 0, "op": "replace", "reason": "already_matches"},
        {"index": 1, "op": "replace", "reason": "identical_old_new"},
    ]


# ===========================================================================
# P2: ToolFailureBreaker per-reply_seq dedup + UUID length hint + edit_file hints
# ===========================================================================


@pytest.mark.unit
def test_p2_tool_failure_breaker_collapses_parallel_file_not_found_within_same_reply_seq():
    breaker = ToolFailureBreaker()
    err = json.dumps({"status": "error", "error": "文件不存在或已删除", "error_type": "file_not_found"})
    ok = json.dumps({"status": "success", "mutation_applied": True, "data": {"id": "valid-1"}})

    # Step 12: 1 success + 2 parallel file_not_found in same reply_seq=12 -> counts as +1 (not +2!)
    breaker.observe("edit_file", json.dumps({"id": "valid-1"}), ok, reply_seq=12)
    breaker.observe("edit_file", json.dumps({"id": "bad-37-a"}), err, reply_seq=12)
    breaker.observe("edit_file", json.dumps({"id": "bad-37-b"}), err, reply_seq=12)
    assert not breaker.is_open

    # Step 13: 1st file_not_found in reply_seq=13 -> count=2 (hints, does NOT trip sibling valid calls!)
    out = breaker.observe("edit_file", json.dumps({"id": "bad-40-c"}), err, reply_seq=13)
    assert not breaker.is_open
    assert "retry_hint" in json.loads(out)

    # Another parallel file_not_found in same reply_seq=13 still does NOT trip
    breaker.observe("edit_file", json.dumps({"id": "bad-40-d"}), err, reply_seq=13)
    assert not breaker.is_open

    # Step 14: 3rd distinct reply_seq with file_not_found -> trips TRIP_REASON_RECOVERY
    breaker.observe("edit_file", json.dumps({"id": "bad-35-e"}), err, reply_seq=14)
    assert breaker.is_open
    assert breaker.trip is not None
    assert breaker.trip.reason == TRIP_REASON_RECOVERY


@pytest.mark.asyncio
@pytest.mark.integration
async def test_p2_malformed_uuid_length_hint_without_guessing(db_session, owner_project):
    user, project = owner_project
    bad_37 = f"{uuid4()}a"  # 37 chars
    valid_missing = str(uuid4())  # 36 chars valid UUID

    assert "长度 37" in format_malformed_uuid_hint([bad_37])
    assert format_malformed_uuid_hint([valid_missing]) == ""

    ctx = _make_ctx(db_session, user, project)
    try:
        # edit_file malformed UUID hint
        edit_res = _payload(
            await mcp_tools.edit_file(
                {"id": bad_37, "edits": [{"op": "append", "text": "x"}]},
                request_context=ctx,
            )
        )
        assert edit_res["status"] == "error"
        assert edit_res["error_type"] == EDIT_ERROR_FILE_NOT_FOUND
        assert "长度 37" in edit_res["error"]
        assert "36 位 UUID" in edit_res["error"]

        # read_files malformed UUID hint
        read_res = _payload(
            await mcp_tools.read_files({"ids": [bad_37]}, request_context=ctx)
        )
        assert read_res["status"] == "error"
        assert read_res["error_type"] == "files_not_found"
        assert "长度 37" in read_res["error"]
    finally:
        ctx.clear()


@pytest.mark.integration
def test_p2_edit_file_same_turn_merge_hint_and_paragraph_break_excerpt(db_session, owner_project):
    user, project = owner_project
    body = "　　第一段结束在这里。\n\n　　第二段从这里开始。"
    chapter = _add_file(db_session, project, title="第3章 段落", content=body)
    ctx = _make_ctx(db_session, user, project)
    try:
        # 1) MISSING_PARAGRAPH_BREAK preserves keep_line_breaks=True and includes compact two-paragraph excerpt
        with pytest.raises(EditBatchError) as pb_exc:
            FileEditor(db_session, user.id, request_context=ctx).edit_file(
                chapter.id,
                [{"op": "replace", "old": "第一段结束在这里。第二段从这里开始。", "new": "合并了。"}],
            )
        pb_err = pb_exc.value.failed_edits[0]["error"]
        assert pb_exc.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND
        assert "跨过了段落分隔" in pb_err
        assert "\\n" in pb_err
        db_session.expire_all()
        assert db_session.get(File, chapter.id).content == body

        # 2) Repeated same-turn edit to the same file causing anchor_not_found includes merge hint
        ctx.record_written_snapshot(chapter.id, body, full=True)
        with pytest.raises(EditBatchError) as repeat_exc:
            FileEditor(db_session, user.id, request_context=ctx).edit_file(
                chapter.id,
                [{"op": "replace", "old": "已经被前一次编辑改掉的旧句。", "new": "新句。"}],
            )
        repeat_err = repeat_exc.value.failed_edits[0]["error"]
        assert "合并进一次 edit_file 的 edits 数组" in repeat_err
    finally:
        ctx.clear()


# ===========================================================================
# P3: load_skill / read_skill_resource error_types, cross-skill hint & awaiting_body hint
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_p3_skill_error_types_cross_skill_resource_hint_and_awaiting_body_hint(db_session, owner_project):
    user, project = owner_project
    custom_skill = UserSkill(user_id=user.id, name="大纲", description="d", instructions="自定义方法")
    public_skill = PublicSkill(name="创建大纲", description="官方", instructions="官方方法", status="approved")
    db_session.add_all([custom_skill, public_skill])
    db_session.commit()
    db_session.add(UserAddedSkill(user_id=user.id, public_skill_id=public_skill.id, is_active=True))
    db_session.add(
        SkillResource(
            public_skill_id=public_skill.id,
            path="references/细纲模板.md",
            content="模板内容",
            size=12,
        )
    )
    # Extra skills to hit limit
    for idx in range(3):
        db_session.add(UserSkill(user_id=user.id, name=f"额外技能{idx}", description="d", instructions="内容"))
    db_session.commit()

    ctx = _make_ctx(db_session, user, project)
    try:
        # 1) skill_not_found
        nf = _payload(await mcp_tools.load_skill({"name": "不存在的技能"}, request_context=ctx))
        assert nf["status"] == "error"
        assert nf["error_type"] == EDIT_ERROR_SKILL_NOT_FOUND
        assert nf["user_message"] == edit_error_user_message(EDIT_ERROR_SKILL_NOT_FOUND)

        # 2) skill_resource_not_found with exact cross-skill path hint
        res_nf = _payload(
            await mcp_tools.read_skill_resource(
                {"name": "大纲", "path": "references/细纲模板.md"},
                request_context=ctx,
            )
        )
        assert res_nf["status"] == "error"
        assert res_nf["error_type"] == EDIT_ERROR_SKILL_RESOURCE_NOT_FOUND
        assert res_nf["user_message"] == edit_error_user_message(EDIT_ERROR_SKILL_RESOURCE_NOT_FOUND)
        assert "创建大纲" in res_nf["error"]

        # 3) skill_limit_exceeded
        for name in ("大纲", "创建大纲", "额外技能0"):
            ok = _payload(await mcp_tools.load_skill({"name": name}, request_context=ctx))
            assert ok["status"] == "success"
        limit_err = _payload(await mcp_tools.load_skill({"name": "额外技能1"}, request_context=ctx))
        assert limit_err["status"] == "error"
        assert limit_err["error_type"] == EDIT_ERROR_SKILL_LIMIT_EXCEEDED
        assert limit_err["user_message"] == edit_error_user_message(EDIT_ERROR_SKILL_LIMIT_EXCEEDED)

        # 4) awaiting_body hint explicitly warns against edit_file on newly created empty file
        created = _payload(
            await mcp_tools.create_file(
                {"title": "新设定", "file_type": "lore", "parent_id": "lore"},
                request_context=ctx,
            )
        )
        assert created["status"] == "success"
        assert created["data"]["awaiting_body"] is True
        assert "不要对新建空文件调用 edit_file" in created["data"]["next_step"]
    finally:
        ctx.clear()

    # 5) LOAD_SKILL_TOOL clarifies no-catalog rule
    assert "没有该目录说明用户未启用任何技能" in LOAD_SKILL_TOOL["description"]


# ===========================================================================
# Review PR #520 Follow-up Regressions (P1/P2)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_review_p1_files_not_found_uuid_hint_never_leaks_to_client_sse(db_session, owner_project):
    """read_files & query_files files_not_found carry safe user_message so StreamAdapter never leaks model hints."""
    user, project = owner_project
    bad_37 = f"{uuid4()}f"
    ctx = _make_ctx(db_session, user, project)
    try:
        read_raw = await mcp_tools.read_files({"ids": [bad_37]}, request_context=ctx)
        query_raw = await mcp_tools.query_files({"ids": [bad_37]}, request_context=ctx)
    finally:
        ctx.clear()

    read_payload = _payload(read_raw)
    query_payload = _payload(query_raw)
    assert read_payload["error_type"] == "files_not_found"
    assert read_payload["user_message"] == edit_error_user_message(EDIT_ERROR_FILE_NOT_FOUND)
    assert "<project_files>" in read_payload["error"]
    assert query_payload["error_type"] == "files_not_found"
    assert query_payload["user_message"] == edit_error_user_message(EDIT_ERROR_FILE_NOT_FOUND)
    assert "<project_files>" in query_payload["error"]

    adapter = StreamAdapter(StreamAdapterConfig(project_id=project.id, user_id=user.id))

    async def events():
        yield WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={"name": "read_files", "tool_use_id": "call-rf-nf", "result": read_raw},
        )
        yield WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={"name": "query_files", "tool_use_id": "call-qf-nf", "result": query_raw},
        )

    emitted = [ev async for ev in adapter.process_workflow_events(events())]
    result_events = [ev for ev in emitted if ev.type == EventType.TOOL_RESULT]
    assert len(result_events) == 2
    for ev in result_events:
        assert ev.data["status"] == "error"
        assert ev.data["error"] == edit_error_user_message(EDIT_ERROR_FILE_NOT_FOUND)
        serialized = json.dumps(ev.data, ensure_ascii=False)
        assert "<project_files>" not in serialized
        assert "search_files" not in serialized
        assert bad_37 not in serialized


@pytest.mark.integration
def test_review_p2_explicit_null_canonical_keys_fall_back_to_aliases_preserving_empty_delete(db_session, owner_project):
    """Explicit null canonical keys fall back to aliases, while new="" deletion stays intact."""
    user, project = owner_project
    original = "　　第一句原文。\n\n　　要删除的句子。\n\n　　结尾句。"
    chapter = _add_file(db_session, project, title="第4章 空键别名", content=original)
    editor = FileEditor(db_session, user.id)

    res = editor.edit_file(
        chapter.id,
        [
            {"old": None, "old_text": "第一句原文。", "new": None, "new_text": "第一句改写。"},
            {"op": "replace", "old": "要删除的句子。", "new": "", "new_text": "不应误用此别名"},
            {"op": "insert_after", "anchor": "结尾句。", "text": None, "content": "\n\n　　追加一句。"},
        ],
    )
    assert res["edits_applied"] == 3
    assert res["failed_edits"] == []
    db_session.expire_all()
    updated = db_session.get(File, chapter.id).content
    assert "第一句改写。" in updated
    assert "要删除的句子。" not in updated
    assert "不应误用此别名" not in updated
    assert "追加一句。" in updated


@pytest.mark.integration
def test_review_p2_full_body_noop_with_body_guards_never_trips_whole_body_replace(db_session, owner_project):
    """Full-body old == new no-op under body_guards=True skips whole_body_replace guard."""
    user, project = owner_project
    body = "　　第一段足够长的正文内容，用于触发正文保护检查。\n\n　　第二段同样足够长的正文内容，用于验证多段正文。"
    chapter = _add_file(db_session, project, title="第5章 整篇无改动", content=body, file_type="draft")
    editor = FileEditor(db_session, user.id)

    # 1) Standalone full-body old == new -> unchanged_edits, no whole_body_replace error
    res_only = editor.edit_file(
        chapter.id,
        [{"op": "replace", "old": body, "new": body}],
        body_guards=True,
    )
    assert res_only["edits_applied"] == 0
    assert res_only["mutation_applied"] is False
    assert res_only["failed_edits"] == []
    assert res_only["unchanged_edits"] == [{"index": 0, "op": "replace", "reason": "already_matches"}]

    # 2) Full-body old == new alongside a small real edit -> real edit succeeds, no whole_body_replace
    res_mixed = editor.edit_file(
        chapter.id,
        [
            {"op": "replace", "old": body, "new": body},
            {"op": "replace", "old": "验证多段正文。", "new": "验证多段正文无误。"},
        ],
        body_guards=True,
    )
    assert res_mixed["edits_applied"] == 1
    assert res_mixed["mutation_applied"] is True
    assert res_mixed["failed_edits"] == []
    db_session.expire_all()
    assert "验证多段正文无误。" in db_session.get(File, chapter.id).content

