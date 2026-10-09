"""edit_file 语义回归：报告成功但实际损坏 / 没生效 / 整批回滚说不清（F6、F7、F8）。

- F6 模糊匹配命中后，原文边缘的引号、句号不在替换范围里，替换出「“"有事？"…冷。。」，
  删除留下孤立的「“。」，结果还报成功。
- F7 replace 漏写 new 时被当成替换为空串（等于删除）；append 文本为空也记一次成功。
- F8 continue_on_error=false 时第 k 条失败整批回滚，但错误只说第 k 条，模型会只重发剩下几条。
- 错误结果带稳定的 error_type 和给作者看的 user_message，模型用的 error 原文不变。
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, select

from agent.core.events import EventType
from agent.core.stream_billing import StreamBillingTracker
from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
from agent.core.workflow_events import StreamEventType
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools.file_ops.edit import (
    EDIT_ERROR_ANCHOR_AMBIGUOUS,
    EDIT_ERROR_ANCHOR_NOT_FOUND,
    EDIT_ERROR_FILE_NOT_FOUND,
    EDIT_ERROR_INVALID_EDIT,
    FUZZY_EDIT_WARNING,
    EditBatchError,
    EditFileError,
    FileEditor,
)
from agent.tools.mcp_tools import edit_file
from models import File, Project, User
from models.file_version import FileVersion

_DIALOGUE = "他抬头看了一眼。\n“有事？”他声音很冷。\n她没有说话。"


@pytest.fixture
def editor() -> FileEditor:
    """只测纯文本编辑逻辑的 FileEditor（_apply_* 不触碰 session）。"""
    return FileEditor(session=None)


# ---------------------------------------------------------------- F6 边缘标点 --


def test_fuzzy_replace_with_straight_quotes_consumes_curly_edges(editor):
    """old/new 用直引号、半角问号：原文的弯引号和句号必须一并被替换，不能残留。"""
    applied: list[dict] = []
    result = editor._apply_replace(
        _DIALOGUE,
        {"op": "replace", "old": '"有事?"他声音很冷。', "new": "“有事？”他声音冷得像冰。"},
        0,
        applied,
        [],
    )
    assert result == "他抬头看了一眼。\n“有事？”他声音冷得像冰。\n她没有说话。"
    detail = applied[0]
    assert detail["match_mode"] == "fuzzy"
    assert detail["matched_original"] == "“有事？”他声音很冷。"
    assert detail["warning"] == FUZZY_EDIT_WARNING


def test_fuzzy_replace_old_without_edges_does_not_duplicate_new_edges(editor):
    """old 漏了引号和句号、new 带着：原文那一份要被 new 取代，而不是重复成「““」「。。」。"""
    applied: list[dict] = []
    result = editor._apply_replace(
        _DIALOGUE,
        {"op": "replace", "old": "有事他声音很冷", "new": "“有事？”他声音冷得像冰。"},
        0,
        applied,
        [],
    )
    assert result == "他抬头看了一眼。\n“有事？”他声音冷得像冰。\n她没有说话。"
    assert "。。" not in result and "““" not in result


def test_fuzzy_replace_does_not_swallow_previous_sentence_period(editor):
    """只吞同类标点：old 开头是引号，前一句的句号和段落换行都不能被并进替换范围。"""
    content = "他问。“你到底有什么事？”她答。"
    result = editor._apply_replace(
        content,
        {"op": "replace", "old": '"你到底有什么事?"', "new": "“你来做什么？”"},
        0,
        [],
        [],
    )
    assert result == "他问。“你来做什么？”她答。"


def test_fuzzy_delete_leaves_no_orphan_quote_or_period(editor):
    applied: list[dict] = []
    result = editor._apply_delete(
        _DIALOGUE,
        {"op": "delete", "old": '"有事?"他声音很冷。'},
        0,
        applied,
        [],
    )
    assert result == "他抬头看了一眼。\n\n她没有说话。"
    assert applied[0]["matched_original"] == "“有事？”他声音很冷。"
    assert applied[0]["warning"] == FUZZY_EDIT_WARNING


def test_fuzzy_insert_after_lands_after_closing_period(editor):
    """锚点以句号结尾：插入点要越过原文的句号，而不是插在「冷」和「。」之间。"""
    result = editor._apply_insert_after(
        _DIALOGUE,
        {"op": "insert_after", "anchor": '"有事?"他声音很冷。', "text": "他转身就走。"},
        0,
        [],
        [],
    )
    assert "“有事？”他声音很冷。他转身就走。\n" in result


def test_exact_replace_is_untouched_by_edge_extension(editor):
    """逐字命中时按模型给的原文精确替换，不做任何标点扩展。"""
    result = editor._apply_replace(
        _DIALOGUE, {"op": "replace", "old": "他声音很冷", "new": "他声音发紧"}, 0, [], []
    )
    assert result == "他抬头看了一眼。\n“有事？”他声音发紧。\n她没有说话。"


# ------------------------------------------------------------- F7 缺字段 --


def test_replace_with_text_instead_of_new_applies_text_with_warning(editor):
    warnings: list[str] = []
    result = editor._apply_replace(
        _DIALOGUE,
        {"op": "replace", "old": "他声音很冷。", "text": "他声音发紧。"},
        0,
        [],
        warnings,
    )
    assert "他声音发紧。" in result
    assert "他声音很冷" not in result
    assert any("text" in w for w in warnings)


def test_replace_without_new_errors_instead_of_deleting(editor):
    with pytest.raises(EditFileError) as exc:
        editor._apply_replace(_DIALOGUE, {"op": "replace", "old": "他声音很冷。"}, 0, [], [])
    assert exc.value.error_type == EDIT_ERROR_INVALID_EDIT
    assert "new" in str(exc.value)


def test_replace_with_explicit_empty_new_still_deletes(editor):
    """new="" 是模型明确要求替换为空，不能误判成漏写。"""
    result = editor._apply_replace(
        _DIALOGUE, {"op": "replace", "old": "他抬头看了一眼。\n", "new": ""}, 0, [], []
    )
    assert result == "“有事？”他声音很冷。\n她没有说话。"


# --------------------------------------------------------- DB 级：批量语义 --


@pytest.fixture
def test_user(db_session):
    from services.core.auth_service import hash_password

    user = User(
        email="edit_semantics@example.com",
        username="edit_semantics",
        hashed_password=hash_password("password123"),
        name="Edit Semantics",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_project(db_session, test_user):
    project = Project(name="Edit Semantics", description="", owner_id=test_user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def version_engine(db_session, monkeypatch):
    import database

    engine = db_session.get_bind()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    return engine


@pytest.fixture
def chapter(db_session, test_project) -> File:
    file = File(
        project_id=test_project.id,
        title="第一章",
        file_type="draft",
        content="他点了点头。她点了点头。窗外下着雨，屋里很安静。",
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)
    return file


def _stored_content(engine, file_id: str) -> str:
    with Session(engine) as check:
        return check.get(File, file_id).content


def _version_count(engine, file_id: str) -> int:
    with Session(engine) as check:
        return len(check.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all())


def test_empty_append_errors_and_writes_nothing(db_session, test_user, chapter, version_engine):
    editor = FileEditor(db_session, user_id=test_user.id)
    with pytest.raises(EditBatchError) as exc:
        editor.edit_file(chapter.id, [{"op": "append", "text": ""}])

    assert "text" in str(exc.value)
    assert exc.value.failed_edits[0]["error_type"] == EDIT_ERROR_INVALID_EDIT
    assert _stored_content(version_engine, chapter.id) == chapter.content


def test_append_with_new_key_writes_the_text(db_session, test_user, chapter, version_engine):
    """空正文修正轮里模型把正文放进了 new：照样写入并留告警，而不是空写一轮。"""
    editor = FileEditor(db_session, user_id=test_user.id)
    result = editor.edit_file(chapter.id, [{"op": "append", "new": "雨停了。"}])

    assert result["mutation_applied"] is True
    assert any("new" in w for w in result["warnings"])
    assert _stored_content(version_engine, chapter.id).endswith("雨停了。")


def test_failed_batch_rolls_back_whole_call_and_lists_every_failure(
    db_session, test_user, chapter, version_engine
):
    """3 条编辑里第 2、3 条失败：一处都不落库，错误写明整批未生效并列出全部失败项。"""
    versions_before = _version_count(version_engine, chapter.id)
    editor = FileEditor(db_session, user_id=test_user.id)
    with pytest.raises(EditBatchError) as exc:
        editor.edit_file(
            chapter.id,
            [
                {"op": "replace", "old": "窗外下着雨", "new": "窗外飘着雪"},
                {"op": "replace", "old": "这句话根本不存在于原文里", "new": "x"},
                {"op": "replace", "old": "点了点头。", "new": "摇了摇头。"},
            ],
        )

    error = exc.value
    message = str(error)
    assert message.startswith("本次调用的 3 处编辑全部未生效（已整体回滚）")
    assert "重新提交完整 edits 列表" in message
    assert "Edit 1" in message and "Edit 2" in message
    assert [item["index"] for item in error.failed_edits] == [1, 2]
    assert [item["error_type"] for item in error.failed_edits] == [
        EDIT_ERROR_ANCHOR_NOT_FOUND,
        EDIT_ERROR_ANCHOR_AMBIGUOUS,
    ]
    assert error.error_type == EDIT_ERROR_ANCHOR_NOT_FOUND
    fields = error.payload_fields()
    assert fields["edits_applied"] == 0
    assert fields["mutation_applied"] is False
    assert fields["edits_total"] == 3

    assert _stored_content(version_engine, chapter.id) == chapter.content
    assert _version_count(version_engine, chapter.id) == versions_before


def test_continue_on_error_keeps_successful_edits_and_tags_failures(
    db_session, test_user, chapter, version_engine
):
    editor = FileEditor(db_session, user_id=test_user.id)
    result = editor.edit_file(
        chapter.id,
        [
            {"op": "replace", "old": "窗外下着雨", "new": "窗外飘着雪"},
            {"op": "replace", "old": "这句话根本不存在于原文里", "new": "x"},
        ],
        continue_on_error=True,
    )
    assert result["partial_success"] is True
    assert result["failed_edits"][0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND
    assert "窗外飘着雪" in _stored_content(version_engine, chapter.id)


def test_missing_file_raises_file_not_found(db_session, test_user):
    editor = FileEditor(db_session, user_id=test_user.id)
    with pytest.raises(EditFileError) as exc:
        editor.edit_file("no-such-file", [{"op": "append", "text": "x"}])
    assert exc.value.error_type == EDIT_ERROR_FILE_NOT_FOUND
    assert exc.value.user_message == "这个文件已经不存在了，可能刚被删除。"


# ----------------------------------------------- 工具层：错误载荷与计费/守卫 --


def _parse(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


async def _call_edit_file(side_effect: Exception, edits: list[dict]) -> tuple[dict, dict]:
    mock_executor = MagicMock()
    mock_executor.edit_file.side_effect = side_effect
    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1"},
    ):
        result = await edit_file({"id": "f-1", "edits": edits})
    return result, _parse(result)


def _batch_error() -> EditBatchError:
    return EditBatchError(
        [
            {
                "index": 1,
                "op": "replace",
                "error": "Edit 1: 找不到要替换的原文片段。候选片段: []",
                "error_type": EDIT_ERROR_ANCHOR_NOT_FOUND,
            }
        ],
        edits_total=3,
    )


@pytest.mark.asyncio
async def test_rolled_back_batch_payload_has_stable_type_and_author_message():
    _, payload = await _call_edit_file(_batch_error(), [{"op": "replace"}] * 3)

    assert payload["status"] == "error"
    assert payload["error"].startswith("本次调用的 3 处编辑全部未生效（已整体回滚）")
    assert "找不到要替换的原文片段" in payload["error"], "模型重新定位所需的原始错误必须保留"
    assert payload["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND
    assert payload["user_message"] == "AI 没在原文里找到要改的那一段，正在重新定位。"
    assert payload["edits_applied"] == 0
    assert payload["mutation_applied"] is False
    assert payload["edits_total"] == 3
    assert payload["failed_edits"][0]["index"] == 1


@pytest.mark.asyncio
async def test_ambiguous_and_generic_errors_get_author_messages():
    ambiguous = EditBatchError(
        [{"index": 0, "op": "replace", "error": "Edit 0: 匹配到多个位置", "error_type": EDIT_ERROR_ANCHOR_AMBIGUOUS}],
        edits_total=1,
    )
    _, payload = await _call_edit_file(ambiguous, [{"op": "replace"}])
    assert payload["error_type"] == EDIT_ERROR_ANCHOR_AMBIGUOUS
    assert payload["user_message"] == "要改的这句话在文中出现了好几次，AI 正在确认是哪一处。"

    _, payload = await _call_edit_file(RuntimeError("db exploded"), [{"op": "append", "text": "x"}])
    assert payload["error"] == "db exploded"
    assert payload["error_type"] == "edit_failed"
    assert payload["user_message"] == "这一步没做成，AI 会换个方式继续。"
    # 未分类异常可能发生在提交之后，不能断言「一处都没生效」
    assert "mutation_applied" not in payload


@pytest.mark.asyncio
async def test_rolled_back_batch_is_not_counted_as_a_write_by_guard_or_billing():
    """整批回滚的结果：重复读取守卫不记写入，计费也不当作落库。"""
    result, _ = await _call_edit_file(_batch_error(), [{"op": "replace"}] * 3)
    output_text = result["content"][0]["text"]

    guard = RepeatReadGuard()
    raw_args = json.dumps({"id": "f-1", "edits": [{"op": "replace"}] * 3})
    guard.observe("edit_file", guard.plan("edit_file", raw_args), output_text)
    assert guard.write_succeeded is False
    assert guard.files_written == {}

    adapter = StreamAdapter(StreamAdapterConfig(project_id="p", user_id="u", process_file_markers=True))

    async def events():
        yield WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={"tool_use_id": "call-edit", "name": "edit_file", "result": result},
        )
        yield WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    sse_events = [event async for event in adapter.process_workflow_events(events())]
    tool_results = [e for e in sse_events if e.type == EventType.TOOL_RESULT]
    assert tool_results and tool_results[0].data["status"] == "error"

    tracker = StreamBillingTracker()
    for event in sse_events:
        tracker.observe(event.to_sse())
    assert tracker.write_succeeded is False


def test_insert_details_report_editor_word_count_next_to_char_length(editor):
    """修改卡片的「字 / 千字」按编辑器的字数口径（汉字 + 英文单词），不是字符长度。"""
    applied: list[dict] = []
    text = "雨停了，她推开门。\n\nHello world"
    editor._apply_insert_after(
        _DIALOGUE,
        {"op": "insert_after", "anchor": "她没有说话。", "text": text},
        0,
        applied,
        [],
    )
    detail = applied[0]
    # 7 个汉字 + 2 个英文单词；标点、换行、空格不算。
    assert detail["text_words"] == 9
    assert detail["text_len"] > detail["text_words"]
