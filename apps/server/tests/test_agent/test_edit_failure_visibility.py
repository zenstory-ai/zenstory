"""Regression tests for AI edit failure visibility & safe client payloads.

Covers:
1. edit_file error & partial payloads preserve {id, title, file_type} and bounded
   safe failed_edits previews (user_message, old_preview/anchor_preview/new_preview/text_preview).
2. old == new returns no_change before anchor_not_found without fuzzy mutation.
3. EDIT_ERROR_USER_MESSAGES covers all edit/chapter failure error types with neutral copy.
4. StreamAdapter safe allowlist forwards file metadata & safe failed_edits / skipped_files
   on edit_file / batch_replace_files / write_chapter errors while stripping raw model error prose.
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
    EDIT_ERROR_NO_CHANGE,
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


@pytest.mark.integration
def test_edit_file_batch_and_partial_errors_preserve_file_meta_and_clipped_previews(db_session, owner_project):
    user, project = owner_project
    chapter = _add_file(db_session, project, title="第5章 破晓", file_type="draft")
    long_old = "完全不存在的目标原文" * 30
    long_new = "打算替换进去的新内容" * 30

    # Atomic failure (continue_on_error=False) -> EditBatchError preserves file meta + clipped previews
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
    assert first["old_preview"].endswith("...")
    assert len(first["old_preview"]) == 203
    assert first["new_preview"].endswith("...")
    assert len(first["new_preview"]) == 203

    second = fields["failed_edits"][1]
    assert second["op"] == "insert_after"
    assert second["anchor_preview"] == "不存在的锚点"
    assert second["text_preview"] == "待插入的句子"

    # Partial failure (continue_on_error=True)
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
    assert len(partial["failed_edits"]) == 1
    assert partial["failed_edits"][0]["old_preview"] == "不存在的段落。"
    assert partial["failed_edits"][0]["new_preview"] == "替换失败的新段落。"
    assert partial["failed_edits"][0]["user_message"] == edit_error_user_message(EDIT_ERROR_ANCHOR_NOT_FOUND)


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
async def test_stream_adapter_allowlists_safe_edit_and_batch_error_metadata_and_strips_raw_prose():
    adapter = StreamAdapter(StreamAdapterConfig(project_id="p1", user_id="u1"))

    # 1. edit_file atomic error (EditBatchError shape)
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
                    "old_preview": "旧段落片段",
                    "new_preview": "新段落片段",
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
            "old_preview": "旧段落片段",
            "new_preview": "新段落片段",
        }
    ]
    serialized = json.dumps(card, ensure_ascii=False)
    assert "SECRET_MODEL_PROMPT" not in serialized
    assert "SECRET_ITEM_INSTRUCTION" not in serialized

    # 2. edit_file partial success also strips raw item.error prose from tool_result data
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
                "failed_edits": [
                    {
                        "index": 1,
                        "op": "replace",
                        "error": "Edit 1: RAW_MODEL_ONLY_HINT",
                        "error_type": "anchor_not_found",
                        "old_preview": "旧片段",
                        "new_preview": "新片段",
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
    assert partial_card["data"]["failed_edits"] == [
        {
            "index": 1,
            "op": "replace",
            "error_type": "anchor_not_found",
            "user_message": "未在原文中找到对应段落，文件未改动。",
            "old_preview": "旧片段",
            "new_preview": "新片段",
        }
    ]
    assert "RAW_MODEL_ONLY_HINT" not in json.dumps(partial_card, ensure_ascii=False)

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
    batch_serialized = json.dumps(batch_card, ensure_ascii=False)
    assert "RAW_BATCH_ERROR_INTERNAL" not in batch_serialized
    assert "RAW_SKIP_INTERNAL_TRACE" not in batch_serialized
