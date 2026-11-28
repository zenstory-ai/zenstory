"""Regression tests for AI edit failure visibility & safe client payloads.

Covers:
1. edit_file error & partial payloads preserve {id, title, file_type} and user_message
   without attaching failed-edit snippet preview fields.
2. old == new returns no_change before anchor_not_found without fuzzy mutation.
3. EDIT_ERROR_USER_MESSAGES covers all edit/chapter failure error types with neutral copy.
4. StreamAdapter safe allowlist forwards file metadata & safe failed_edits / skipped_files
   on edit_file / batch_replace_files / write_chapter errors while stripping raw model error
   prose and preview keys, and emits no spurious file_edit_* events on pre-edit errors.
5. mcp_tools.edit_file omits unauthorized file metadata on ForbiddenError.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session

from agent.core.events import EventType
from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
from agent.core.workflow_events import StreamEventType
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools import mcp_tools
from agent.tools.file_ops.edit import (
    EDIT_ERROR_ANCHOR_NOT_FOUND,
    EDIT_ERROR_CHAPTER_REPLACE_PENDING,
    EDIT_ERROR_CHAPTER_TARGET_AMBIGUOUS,
    EDIT_ERROR_DUPLICATED_PARAGRAPH,
    EDIT_ERROR_FILE_BUSY,
    EDIT_ERROR_INVALID_EDIT,
    EDIT_ERROR_NO_CHANGE,
    EDIT_ERROR_PERMISSION_DENIED,
    EDIT_ERROR_PRE_WRITE_HISTORY_UNAVAILABLE,
    EDIT_ERROR_STALE_CONTENT,
    EDIT_ERROR_USER_MESSAGES,
    EDIT_ERROR_WHOLE_BODY_REPLACE,
    EditBatchError,
    EditFileError,
    FileEditor,
    PreWriteHistoryUnavailableError,
    StaleContentError,
    edit_error_user_message,
)
from agent.tools.tool_context import AgentRequestContext
from models import File, Project, User
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService

_PREVIEW_KEYS = ("old_preview", "anchor_preview", "new_preview", "text_preview")


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def owner_project(db_session, monkeypatch):
    import database

    user = User(
        username="edit-fail-vis-owner",
        email="edit-fail-vis@example.test",
        hashed_password="unused",
        email_verified=True,
        is_active=True,
    )
    project = Project(name="编辑失败可见性", owner_id=user.id, project_type="novel")
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


def _add_file(
    session: Session,
    project: Project,
    *,
    title: str = "第1章 雨夜",
    content: str = "窗外下着大雨。\n\n屋里只有一盏孤灯。",
    file_type: str = "draft",
) -> File:
    row = File(project_id=project.id, title=title, content=content, file_type=file_type)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.mark.unit
def test_edit_error_user_messages_cover_all_new_types_with_neutral_copy():
    expected_types = [
        EDIT_ERROR_INVALID_EDIT,
        EDIT_ERROR_PERMISSION_DENIED,
        EDIT_ERROR_NO_CHANGE,
        EDIT_ERROR_FILE_BUSY,
        EDIT_ERROR_PRE_WRITE_HISTORY_UNAVAILABLE,
        EDIT_ERROR_WHOLE_BODY_REPLACE,
        EDIT_ERROR_DUPLICATED_PARAGRAPH,
        EDIT_ERROR_CHAPTER_REPLACE_PENDING,
        EDIT_ERROR_CHAPTER_TARGET_AMBIGUOUS,
    ]
    for error_type in expected_types:
        assert error_type in EDIT_ERROR_USER_MESSAGES
        msg = edit_error_user_message(error_type)
        assert msg
        assert "正在重新定位" not in msg
        assert "正在确认" not in msg
        assert "换个方式继续" not in msg
    assert edit_error_user_message(EDIT_ERROR_PERMISSION_DENIED) == "无权修改该文件，文件未改动。"
    assert edit_error_user_message(EDIT_ERROR_INVALID_EDIT) == "修改参数不完整，此次修改未执行，文件未改动。"


@pytest.mark.integration
def test_edit_file_batch_and_partial_errors_preserve_file_meta_without_failed_previews(db_session, owner_project):
    user, project = owner_project
    chapter = _add_file(db_session, project, title="第5章 破晓", file_type="draft")
    long_old = "完全不存在的目标原文" * 30
    long_new = "打算替换进去的新内容" * 30

    # Atomic failure (continue_on_error=False) -> EditBatchError preserves file meta & user_message, no preview keys
    with pytest.raises(EditBatchError) as exc:
        FileEditor(db_session, user.id).edit_file(
            chapter.id,
            [
                {"op": "replace", "old": long_old, "new": long_new},
                {"op": "insert_after", "anchor": "不存在的锚点", "text": "待插入的句子"},
            ],
            continue_on_error=False,
        )

    fields = exc.value.payload_fields()
    assert fields["id"] == chapter.id
    assert fields["title"] == "第5章 破晓"
    assert fields["file_type"] == "draft"
    assert fields["edits_total"] == 2
    assert len(fields["failed_edits"]) == 2

    first = fields["failed_edits"][0]
    assert first["op"] == "replace"
    assert first["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND
    assert first["user_message"] == edit_error_user_message(EDIT_ERROR_ANCHOR_NOT_FOUND)
    for key in _PREVIEW_KEYS:
        assert key not in first

    second = fields["failed_edits"][1]
    assert second["op"] == "insert_after"
    assert second["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND
    assert second["user_message"] == edit_error_user_message(EDIT_ERROR_ANCHOR_NOT_FOUND)
    for key in _PREVIEW_KEYS:
        assert key not in second

    # Partial failure (continue_on_error=True): applied details keep previews, failed_edits omit previews
    partial = FileEditor(db_session, user.id).edit_file(
        chapter.id,
        [
            {"op": "replace", "old": "窗外下着大雨。", "new": "窗外飘着细雨。"},
            {"op": "replace", "old": "不存在的段落。", "new": "替换失败的新段落。"},
        ],
        continue_on_error=True,
    )
    assert partial["id"] == chapter.id
    assert partial["title"] == "第5章 破晓"
    assert partial["file_type"] == "draft"
    assert partial["partial_success"] is True
    assert partial["details"][0]["old_preview"] == "窗外下着大雨。"
    assert partial["details"][0]["new_preview"] == "窗外飘着细雨。"
    assert len(partial["failed_edits"]) == 1
    assert partial["failed_edits"][0]["user_message"] == edit_error_user_message(EDIT_ERROR_ANCHOR_NOT_FOUND)
    for key in _PREVIEW_KEYS:
        assert key not in partial["failed_edits"][0]


@pytest.mark.integration
def test_edit_file_identical_old_and_new_returns_no_change(db_session, owner_project):
    user, project = owner_project
    chapter = _add_file(db_session, project, title="第6章 无变化", file_type="draft")

    with pytest.raises(EditBatchError) as exc:
        FileEditor(db_session, user.id).edit_file(
            chapter.id,
            [{"op": "replace", "old": "不存在且相同的文本", "new": "不存在且相同的文本"}],
            continue_on_error=False,
        )

    fields = exc.value.payload_fields()
    assert fields["error_type"] == EDIT_ERROR_NO_CHANGE
    assert fields["user_message"] == edit_error_user_message(EDIT_ERROR_NO_CHANGE)
    assert fields["failed_edits"][0]["error_type"] == EDIT_ERROR_NO_CHANGE
    for key in _PREVIEW_KEYS:
        assert key not in fields["failed_edits"][0]


@pytest.mark.integration
def test_stale_content_and_body_guard_errors_preserve_file_meta(db_session, owner_project):
    user, project = owner_project
    chapter = _add_file(
        db_session,
        project,
        title="第7章 夜行",
        content="第一段足够长的内容用于测试整章替换保护。\n\n第二段足够长的内容用于测试整章替换保护。",
    )

    with pytest.raises(StaleContentError) as stale_exc:
        FileEditor(db_session, user.id).edit_file(
            chapter.id,
            [{"op": "append", "text": "补充"}],
            read_snapshot_fn=lambda: "旧版本内容",
        )
    stale_fields = stale_exc.value.payload_fields()
    assert stale_fields["id"] == chapter.id
    assert stale_fields["title"] == "第7章 夜行"
    assert stale_fields["file_type"] == "draft"
    assert stale_fields["error_type"] == EDIT_ERROR_STALE_CONTENT

    with pytest.raises(EditFileError) as guard_exc:
        FileEditor(db_session, user.id).edit_file(
            chapter.id,
            [{"op": "replace", "old": chapter.content, "new": "完全重写后的新正文。"}],
            body_guards=True,
        )
    guard_fields = guard_exc.value.payload_fields()
    assert guard_fields["id"] == chapter.id
    assert guard_fields["title"] == "第7章 夜行"
    assert guard_fields["file_type"] == "draft"
    assert guard_fields["error_type"] == EDIT_ERROR_WHOLE_BODY_REPLACE


@pytest.mark.asyncio
@pytest.mark.integration
async def test_mcp_edit_file_preserves_file_meta_on_pre_write_history_error(db_session, owner_project, monkeypatch):
    user, project = owner_project
    chapter = _add_file(db_session, project, title="第8章 回响", file_type="draft")

    def boom(*_args, **_kwargs):
        raise PreWriteHistoryUnavailableError(
            chapter.id, action="edit_file", title=chapter.title, file_type=chapter.file_type
        )

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="s-hist-err",
        read_snapshots={chapter.id: chapter.content},
    )
    monkeypatch.setattr(FileEditor, "_commit_ai_content", boom)
    try:
        resp = _payload(
            await mcp_tools.edit_file(
                {"id": chapter.id, "edits": [{"op": "replace", "old": "窗外下着大雨。", "new": "窗外停雨了。"}]},
                request_context=req_ctx,
            )
        )
    finally:
        req_ctx.clear()

    assert resp["status"] == "error"
    assert resp["error_type"] == EDIT_ERROR_PRE_WRITE_HISTORY_UNAVAILABLE
    assert resp["id"] == chapter.id
    assert resp["title"] == "第8章 回响"
    assert resp["file_type"] == "draft"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_batch_replace_all_skipped_includes_file_type_and_user_message(db_session, owner_project):
    user, project = owner_project
    chapter = _add_file(db_session, project, title="第2章 旧巷", content="林舟站在雨里。", file_type="draft")
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="s-batch-skip",
        read_snapshots={chapter.id: "陈旧快照内容"},
    )
    try:
        resp = _payload(
            await mcp_tools.batch_replace_files(
                {"old_text": "林舟", "new_text": "沈舟", "file_ids": [chapter.id]},
                request_context=req_ctx,
            )
        )
    finally:
        req_ctx.clear()

    assert resp["status"] == "error"
    skipped = resp["data"]["skipped_files"]
    assert len(skipped) == 1
    assert skipped[0]["id"] == chapter.id
    assert skipped[0]["title"] == "第2章 旧巷"
    assert skipped[0]["file_type"] == "draft"
    assert skipped[0]["reason"] == EDIT_ERROR_STALE_CONTENT
    assert skipped[0]["user_message"] == edit_error_user_message(EDIT_ERROR_STALE_CONTENT)


@pytest.mark.asyncio
async def test_stream_adapter_allowlists_safe_edit_and_batch_error_metadata_and_strips_raw_prose_and_previews():
    adapter = StreamAdapter(StreamAdapterConfig(project_id="p1", user_id="u1"))

    # 1. edit_file atomic error (EditBatchError shape): strips raw error & preview keys
    edit_error_json = json.dumps(
        {
            "status": "error",
            "error": "本次调用的 2 处编辑全部未生效，请先用 read_files(ids=['f-1']) 重读。SECRET_MODEL_PROMPT",
            "error_type": "anchor_not_found",
            "user_message": "未在原文中找到对应段落，文件未改动。",
            "id": "f-1",
            "title": "第3章 迷雾",
            "file_type": "draft",
            "edits_total": 2,
            "edits_applied": 0,
            "failed_edits": [
                {
                    "index": 0,
                    "op": "replace",
                    "error": "Edit 0: 请用 read_files(ids=['f-1']) SECRET_ITEM_INSTRUCTION",
                    "error_type": "anchor_not_found",
                    "user_message": "未在原文中找到对应段落，文件未改动。",
                    "batch_anchor_invalidated": True,
                    "old_preview": "旧段落片段",
                    "anchor_preview": "锚点片段",
                    "new_preview": "新段落片段",
                    "text_preview": "文本片段",
                }
            ],
        },
        ensure_ascii=False,
    )
    events = [
        sse
        async for sse in adapter._process_workflow_event(
            WorkflowStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={"name": "edit_file", "result": {"content": [{"text": edit_error_json}]}},
            )
        )
    ]
    card = events[0].data
    assert card["status"] == "error"
    assert card["error"] == "未在原文中找到对应段落，文件未改动。"
    assert card["data"]["id"] == "f-1"
    assert card["data"]["title"] == "第3章 迷雾"
    assert card["data"]["file_type"] == "draft"
    assert card["data"]["edits_total"] == 2
    assert card["data"]["edits_applied"] == 0
    assert card["data"]["failed_edits"] == [
        {
            "index": 0,
            "op": "replace",
            "error_type": "anchor_not_found",
            "user_message": "未在原文中找到对应段落，文件未改动。",
            "batch_anchor_invalidated": True,
        }
    ]
    serialized_all = json.dumps([e.data for e in events], ensure_ascii=False)
    assert "SECRET_MODEL_PROMPT" not in serialized_all
    assert "SECRET_ITEM_INSTRUCTION" not in serialized_all
    for key in _PREVIEW_KEYS:
        assert key not in card["data"]["failed_edits"][0]

    # 2. edit_file partial success strips raw item.error, raw failure warnings, and failed_edits previews;
    #    failed file_edit_applied event keeps old_preview=None and new_preview=None.
    partial_json = json.dumps(
        {
            "status": "partial",
            "mutation_applied": True,
            "data": {
                "id": "f-1",
                "title": "第3章 迷雾",
                "file_type": "draft",
                "edits_applied": 1,
                "partial_success": True,
                "details": [{"op": "replace", "old_preview": "A", "new_preview": "B"}],
                "warnings": [
                    "Edit 1: failed and skipped (RAW_WARNING_LEAK_TRACE)",
                    "Edit 2: empty edit ignored",
                ],
                "failed_edits": [
                    {
                        "index": 1,
                        "op": "replace",
                        "error": "Edit 1: RAW_MODEL_ONLY_HINT",
                        "error_type": "anchor_not_found",
                        "old_preview": "超长旧片段",
                        "new_preview": "超长新片段",
                    }
                ],
            },
        },
        ensure_ascii=False,
    )
    partial_events = [
        sse
        async for sse in adapter._process_workflow_event(
            WorkflowStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={"name": "edit_file", "result": {"content": [{"text": partial_json}]}},
            )
        )
    ]
    partial_card = next(e.data for e in partial_events if e.type == EventType.TOOL_RESULT)
    assert partial_card["status"] == "success"
    assert partial_card["data"]["warnings"] == ["Edit 2: empty edit ignored"]
    assert partial_card["data"]["details"] == [{"op": "replace", "old_preview": "A", "new_preview": "B"}]
    assert partial_card["data"]["failed_edits"] == [
        {
            "index": 1,
            "op": "replace",
            "error_type": "anchor_not_found",
            "user_message": "未在原文中找到对应段落，文件未改动。",
        }
    ]
    failed_applied_events = [
        e.data for e in partial_events if e.type == EventType.FILE_EDIT_APPLIED and e.data.get("success") is False
    ]
    assert len(failed_applied_events) == 1
    assert failed_applied_events[0]["old_preview"] is None
    assert failed_applied_events[0]["new_preview"] is None
    assert failed_applied_events[0]["error"] == "未在原文中找到对应段落，文件未改动。"
    partial_all_serialized = json.dumps([e.data for e in partial_events], ensure_ascii=False)
    assert "RAW_MODEL_ONLY_HINT" not in partial_all_serialized
    assert "RAW_WARNING_LEAK_TRACE" not in partial_all_serialized
    assert "超长旧片段" not in partial_all_serialized
    assert "超长新片段" not in partial_all_serialized

    # 3. batch_replace_files all-skipped error forwards safe skipped_files without raw error prose
    batch_error_json = json.dumps(
        {
            "status": "error",
            "error": "RAW_BATCH_ERROR_INTERNAL",
            "error_type": "stale_content",
            "user_message": "该文件在 AI 读取后又被修改过，未在旧内容上改动。",
            "data": {
                "skipped_files": [
                    {
                        "id": "f-2",
                        "title": "第4章 长夜",
                        "file_type": "draft",
                        "reason": "stale_content",
                        "user_message": "该文件在 AI 读取后又被修改过，未在旧内容上改动。",
                        "error": "RAW_SKIP_INTERNAL_TRACE",
                    }
                ]
            },
        },
        ensure_ascii=False,
    )
    batch_events = [
        sse
        async for sse in adapter._process_workflow_event(
            WorkflowStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={"name": "batch_replace_files", "result": {"content": [{"text": batch_error_json}]}},
            )
        )
    ]
    batch_card = batch_events[0].data
    assert batch_card["status"] == "error"
    assert batch_card["data"]["skipped_files"] == [
        {
            "id": "f-2",
            "title": "第4章 长夜",
            "file_type": "draft",
            "reason": "stale_content",
            "user_message": "该文件在 AI 读取后又被修改过，未在旧内容上改动。",
        }
    ]
    batch_serialized = json.dumps([e.data for e in batch_events], ensure_ascii=False)
    assert "RAW_BATCH_ERROR_INTERNAL" not in batch_serialized
    assert "RAW_SKIP_INTERNAL_TRACE" not in batch_serialized

    # 4. Safe file error tool missing user_message still replaces raw error prose with neutral message
    write_chapter_raw_err = json.dumps(
        {
            "status": "error",
            "error": "RAW_DB_TRACEBACK_SECRET",
            "error_type": "invalid_chapter",
        },
        ensure_ascii=False,
    )
    wc_events = [
        sse
        async for sse in adapter._process_workflow_event(
            WorkflowStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={"name": "write_chapter", "result": {"content": [{"text": write_chapter_raw_err}]}},
            )
        )
    ]
    assert wc_events[0].data["error"] == "此次修改未能生效，文件未改动。"
    assert "RAW_DB_TRACEBACK_SECRET" not in json.dumps([e.data for e in wc_events], ensure_ascii=False)

    # 5. Pre-edit atomic edit_file error with id (no applied_edits, no failed_edits) emits NO spurious file_edit_* events
    pre_edit_err = json.dumps(
        {
            "status": "error",
            "error": "RAW_STALE_MODEL_DIFF",
            "error_type": "stale_content",
            "user_message": "该文件在 AI 读取后又被修改过，未在旧内容上改动。",
            "data": {"id": "f-3", "title": "第6章 黎明", "file_type": "draft", "details": [], "failed_edits": []},
        },
        ensure_ascii=False,
    )
    stale_events = [
        sse
        async for sse in adapter._process_workflow_event(
            WorkflowStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={"name": "edit_file", "result": {"content": [{"text": pre_edit_err}]}},
            )
        )
    ]
    assert [e.type for e in stale_events] == [EventType.TOOL_RESULT]
    assert "RAW_STALE_MODEL_DIFF" not in json.dumps([e.data for e in stale_events], ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_mcp_edit_file_omits_file_id_on_forbidden_error(db_session, owner_project, monkeypatch):
    from agent.tools.permissions import ForbiddenError

    user, project = owner_project
    chapter = _add_file(db_session, project, title="他人章节", file_type="draft")

    def raise_forbidden(*_args, **_kwargs):
        raise ForbiddenError("无权访问目标文件")

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="s-forbid",
    )
    monkeypatch.setattr(FileEditor, "edit_file", raise_forbidden)
    try:
        resp = _payload(
            await mcp_tools.edit_file(
                {"id": chapter.id, "edits": [{"op": "append", "text": "x"}]},
                request_context=req_ctx,
            )
        )
    finally:
        req_ctx.clear()

    assert resp["status"] == "error"
    assert resp["error_type"] == "permission_denied"
    assert resp["user_message"] == "无权修改该文件，文件未改动。"
    assert "id" not in resp
    assert "title" not in resp


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_adapter_strips_file_metadata_on_permission_denied_defense_in_depth():
    parsed = {
        "status": "error",
        "error": "RAW_FORBIDDEN_INTERNAL_DETAIL",
        "error_type": "permission_denied",
        "id": "secret-file-99",
        "title": "他人私密章节",
        "file_type": "draft",
        "data": {
            "id": "secret-file-99",
            "title": "他人私密章节",
            "file_type": "draft",
            "edits_applied": 0,
        },
    }
    safe = StreamAdapter._safe_tool_error_payload(parsed, parsed["data"])
    assert "id" not in safe
    assert "title" not in safe
    assert "file_type" not in safe
    assert safe["edits_applied"] == 0

    adapter = StreamAdapter(StreamAdapterConfig(project_id="p1", user_id="u1"))
    events = [
        sse
        async for sse in adapter._process_workflow_event(
            WorkflowStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "name": "edit_file",
                    "result": {"content": [{"text": json.dumps(parsed, ensure_ascii=False)}]},
                },
            )
        )
    ]
    assert [e.type for e in events] == [EventType.TOOL_RESULT]
    card = events[0].data
    assert card["status"] == "error"
    assert card["error"] == "无权修改该文件，文件未改动。"
    assert card["data"]["error_type"] == "permission_denied"
    assert "id" not in card["data"]
    assert "title" not in card["data"]
    assert "file_type" not in card["data"]
    serialized = json.dumps(card, ensure_ascii=False)
    assert "secret-file-99" not in serialized
    assert "他人私密章节" not in serialized
    assert "RAW_FORBIDDEN_INTERNAL_DETAIL" not in serialized
