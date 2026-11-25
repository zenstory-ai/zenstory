"""Regression tests for Tool-Error Governance Part 1: tool contracts, matchers & bug fixes.

Covers all 5 items from audit #39:
1. update_project: auto-normalize multiple in_progress, combined outline & character/lore
   artifact_refs handling, compatible material ref types & actual file_type diagnostics.
2. edit_file: default continue_on_error=True for len(edits) > 1, already_matches unchanged_edits
   on stale-memory approx collision (preserving quote-normalization no_change), deterministic
   whitespace/newline/quote-normalized unique match for exact_only=True, keep_line_breaks
   \n vs \n\n tolerance, _apply_delete approximate fallback, missing_id recent file ID hints.
3. create_file(source_text=...): search author_steering + 98% verbatim snap for >=500-char pastes.
4. read_skill_resource during ui_action="natural_polish": resolve official PublicSkill('去AI味')
   without requiring UserAddedSkill.
5. query_files(section=...): extended heading regex (卷N, Markdown #/**, 一、, bracket-optional
   【...】) + short-file full-text fallback with concise note.
"""

from __future__ import annotations

import importlib
import json
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from agent.core.request_context import AgentRequestContext
from agent.loop.ui_actions import OFFICIAL_NATURAL_POLISH_SKILL_NAME, UI_ACTION_NATURAL_POLISH
from agent.tools import mcp_tools
from agent.tools.file_ops.content_range import (
    SHORT_FILE_FULL_RETURN_MAX_CHARS,
    ContentRangeError,
    apply_content_range,
)
from agent.tools.file_ops.edit import (
    EDIT_ERROR_ANCHOR_NOT_FOUND,
    EDIT_ERROR_INVALID_EDIT,
    EDIT_ERROR_NO_CHANGE,
    EditBatchError,
    EditFileError,
    FileEditor,
)
from agent.tools.file_ops.project import ProjectOperations
from agent.tools.tool_inputs import EditFileInput, validate_tool_input
from agent.tools.tool_schemas import QUERY_FILES_TOOL
from models import File, Project, PublicSkill, SkillResource, User
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def owner_project(db_session, monkeypatch):
    import database

    user = User(
        username="gov-contracts-owner",
        email="gov-contracts@example.test",
        hashed_password="unused",
        email_verified=True,
        is_active=True,
    )
    project = Project(name="工具契约治理", owner_id=user.id, project_type="novel")
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
    content: str = "正文内容。",
    file_type: str = "draft",
    file_id: str | None = None,
) -> File:
    kwargs = {"project_id": project.id, "title": title, "content": content, "file_type": file_type}
    if file_id is not None:
        kwargs["id"] = file_id
    row = File(**kwargs)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _mock_task_board(monkeypatch, previous_tasks=None):
    module = importlib.import_module("services.infra.task_board_service")
    service = MagicMock()
    service.get_tasks.return_value = previous_tasks or []
    service.save_tasks.return_value = True
    monkeypatch.setattr(module, "task_board_service", service)
    return service


def _ops_with_rows(rows: list[File]) -> ProjectOperations:
    session = MagicMock()
    session.exec.return_value.all.return_value = rows
    return ProjectOperations(session=session, user_id="u")


# ===========================================================================
# Item 1: update_project false rejections
# ===========================================================================


@pytest.mark.unit
def test_update_project_auto_normalizes_three_in_progress_tasks_with_warning(monkeypatch):
    board = _mock_task_board(monkeypatch, previous_tasks=[])
    ops = ProjectOperations(session=MagicMock(), user_id="u")
    tasks = [
        {"task": "世界观设定", "status": "in_progress"},
        {"task": "人物小传", "status": "in_progress"},
        {"task": "分卷大纲", "status": "in_progress"},
    ]

    result = ops.execute_update_plan(session_id="s-multi", tasks=tasks)

    assert result["status"] == "success"
    assert "3 个任务为 in_progress" in result["warning"]
    assert "世界观设定" in result["warning"]
    assert [t["status"] for t in result["tasks"]] == ["in_progress", "pending", "pending"]
    assert result["task_summary"] == {"total": 3, "pending": 2, "in_progress": 1, "done": 0}
    board.save_tasks.assert_called_once()


@pytest.mark.unit
def test_update_project_still_blocks_done_to_in_progress_rollback_after_normalization(monkeypatch):
    board = _mock_task_board(
        monkeypatch,
        previous_tasks=[{"task": "总纲", "status": "done", "phase_id": "p1"}],
    )
    ops = ProjectOperations(session=MagicMock(), user_id="u")
    tasks = [
        {"task": "总纲", "status": "in_progress", "phase_id": "p1"},
        {"task": "第1章", "status": "in_progress", "phase_id": "p2"},
    ]

    result = ops.execute_update_plan(session_id="s-rollback", tasks=tasks)

    assert result["status"] == "ignored"
    assert result["reason"] == "validation_rejected"
    assert "done -> in_progress rollback is not allowed" in result["error"]
    board.save_tasks.assert_not_called()


@pytest.mark.unit
def test_update_project_combined_outline_ref_satisfies_done_without_leading_range_title(monkeypatch):
    board = _mock_task_board(monkeypatch)
    outline = File(id="out-1", project_id="p", title="分卷分章大纲", file_type="outline", content="第一卷大纲正文")
    ops = _ops_with_rows([outline])

    # Both explicit unit_spec AND legacy range wording in task name should succeed with valid outline ref.
    tasks = [
        {
            "task": "完成第1-8章大纲",
            "status": "done",
            "artifact_file_type": "outline",
            "artifact_refs": ["out-1"],
            "unit_spec": {"unit": "章", "numbers": [1, 2, 3, 4, 5]},
        }
    ]
    result = ops.execute_update_plan(session_id="s-outline", tasks=tasks, user_id="u", project_id="p")

    assert result["status"] == "success"
    assert result["tasks"][0]["status"] == "done"
    assert result["tasks"][0]["completed_artifact_refs"] == ["out-1"]
    board.save_tasks.assert_called_once()


@pytest.mark.unit
def test_update_project_ignores_unit_spec_on_character_and_lore_with_valid_refs(monkeypatch):
    board = _mock_task_board(monkeypatch)
    char = File(id="c-1", project_id="p", title="沈砚人物卡", file_type="character", content="沈砚：28岁。")
    lore = File(id="l-1", project_id="p", title="灵气境界设定", file_type="lore", content="九境体系。")
    ops = _ops_with_rows([char, lore])

    tasks = [
        {
            "task": "完善第1-5章主要人物卡",
            "status": "done",
            "artifact_file_type": "character",
            "artifact_refs": ["c-1"],
            "unit_spec": {"unit": "章", "numbers": [1, 2, 3]},
        },
        {
            "task": "整理第1卷世界观设定",
            "status": "done",
            "artifact_file_type": "lore",
            "artifact_refs": ["l-1"],
            "unit_spec": {"unit": "卷", "numbers": [1]},
        },
    ]
    result = ops.execute_update_plan(session_id="s-mat", tasks=tasks, user_id="u", project_id="p")

    assert result["status"] == "success"
    assert [t["status"] for t in result["tasks"]] == ["done", "done"]
    board.save_tasks.assert_called_once()


@pytest.mark.unit
def test_update_project_compatible_material_ref_types_and_actual_file_type_detail(monkeypatch):
    board = _mock_task_board(monkeypatch)
    doc = File(id="doc-1", project_id="p", title="分卷细纲", file_type="document", content="完整细纲。")
    char_as_doc = File(id="c-doc", project_id="p", title="主角卡", file_type="document", content="主角设定。")
    ops = _ops_with_rows([doc, char_as_doc])

    # Compatible metadata types (outline <-> document <-> lore) succeed with explicit artifact_refs.
    ok = ops.execute_update_plan(
        session_id="s-compat",
        tasks=[
            {
                "task": "完成总纲",
                "status": "done",
                "artifact_file_type": "outline",
                "artifact_refs": ["doc-1"],
            }
        ],
        user_id="u",
        project_id="p",
    )
    assert ok["status"] == "success"
    assert ok["tasks"][0]["completed_artifact_refs"] == ["doc-1"]

    # Incompatible mismatch (character vs document, or missing/deleted ref) reports actual file_type!
    bad = ops.execute_update_plan(
        session_id="s-mismatch",
        tasks=[
            {
                "task": "交付人物卡",
                "status": "done",
                "artifact_file_type": "character",
                "artifact_refs": ["c-doc", "missing-id"],
            }
        ],
        user_id="u",
        project_id="p",
    )
    assert bad["status"] == "ignored"
    assert bad["reason"] == "unverified_done"
    missing_detail = bad["unverified_done"][0]["missing"]
    assert "c-doc" in missing_detail and "document" in missing_detail and "character" in missing_detail
    assert "missing-id" in missing_detail

    # Body types (draft/script) NEVER cross-accept outline/document refs!
    body_mismatch = ops.execute_update_plan(
        session_id="s-body-mismatch",
        tasks=[
            {
                "task": "写第1章",
                "status": "done",
                "artifact_file_type": "draft",
                "artifact_refs": ["doc-1"],
                "unit_spec": {"unit": "章", "numbers": [1]},
            }
        ],
        user_id="u",
        project_id="p",
    )
    assert body_mismatch["status"] == "ignored"
    assert body_mismatch["reason"] == "unverified_done"
    assert "document" in body_mismatch["unverified_done"][0]["missing"]
    assert board.save_tasks.call_count == 1


# ===========================================================================
# Item 2: edit_file contract & matcher upgrades
# ===========================================================================


@pytest.mark.unit
def test_edit_file_input_defaults_continue_on_error_true_for_multi_edit_batches():
    multi_omitted, err = validate_tool_input(
        EditFileInput,
        {"id": "f-1", "edits": [{"op": "append", "text": "a"}, {"op": "append", "text": "b"}]},
        tool_name="edit_file",
    )
    assert err is None and multi_omitted is not None
    assert multi_omitted.continue_on_error is True

    multi_explicit_false, err = validate_tool_input(
        EditFileInput,
        {
            "id": "f-1",
            "edits": [{"op": "append", "text": "a"}, {"op": "append", "text": "b"}],
            "continue_on_error": False,
        },
        tool_name="edit_file",
    )
    assert err is None and multi_explicit_false is not None
    assert multi_explicit_false.continue_on_error is False

    multi_str_false, err = validate_tool_input(
        EditFileInput,
        {
            "id": "f-1",
            "edits": [{"op": "append", "text": "a"}, {"op": "append", "text": "b"}],
            "continue_on_error": "false",
        },
        tool_name="edit_file",
    )
    assert err is None and multi_str_false is not None
    assert multi_str_false.continue_on_error is False

    single_omitted, err = validate_tool_input(
        EditFileInput,
        {"id": "f-1", "edits": [{"op": "append", "text": "a"}]},
        tool_name="edit_file",
    )
    assert err is None and single_omitted is not None
    assert single_omitted.continue_on_error is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_mcp_edit_file_multi_edit_default_avoids_whole_batch_rollback(db_session, owner_project):
    user, project = owner_project
    chapter = _add_file(db_session, project, content="窗外下着雨。\n\n屋里很安静。")
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="s-coe",
        read_snapshots={chapter.id: chapter.content},
    )
    try:
        resp = _payload(
            await mcp_tools.edit_file(
                {
                    "id": chapter.id,
                    "edits": [
                        {"op": "replace", "old": "窗外下着雨。", "new": "窗外飘着雪。"},
                        {"op": "replace", "old": "完全不存在的一整句台词。", "new": "x"},
                    ],
                },
                request_context=req_ctx,
            )
        )
    finally:
        req_ctx.clear()

    assert resp["status"] == "partial"
    assert resp["mutation_applied"] is True
    assert resp["data"]["edits_applied"] == 1
    assert len(resp["data"]["failed_edits"]) == 1
    db_session.expire_all()
    assert "窗外飘着雪。" in db_session.get(File, chapter.id).content


@pytest.mark.integration
def test_stale_memory_approx_collision_records_unchanged_not_no_change_error(db_session, owner_project):
    """When the file already contains new_text and approx-matching old hits new_text, record already_matches."""
    user, project = owner_project
    original = "　　林舟轻轻推开了那扇沉重的铁门，屋里一片漆黑。\n\n　　桌上的油灯已经熄灭。"
    chapter = _add_file(db_session, project, content=original)

    result = FileEditor(db_session, user.id).edit_file(
        chapter.id,
        [
            {
                "op": "replace",
                "old": "林舟轻轻推开了那扇厚重的铁门，屋里一片漆黑。",
                "new": "林舟轻轻推开了那扇沉重的铁门，屋里一片漆黑。",
            },
            {
                "op": "replace",
                "old": "桌上的油灯已经熄灭。",
                "new": "桌上的油灯还留着一点余温。",
            },
        ],
    )

    assert result["failed_edits"] == []
    assert result["edits_applied"] == 1
    assert result["unchanged_edits"] == [{"index": 0, "op": "replace", "reason": "already_matches"}]
    db_session.expire_all()
    assert "余温" in db_session.get(File, chapter.id).content


@pytest.mark.integration
def test_quote_normalization_reversion_still_raises_no_change_error(db_session, owner_project):
    user, project = owner_project
    original = "　　“回来了？”母亲问。"
    chapter = _add_file(db_session, project, content=original)

    with pytest.raises(EditBatchError) as exc:
        FileEditor(db_session, user.id).edit_file(
            chapter.id,
            [{"op": "replace", "old": "“回来了？”母亲问。", "new": '"回来了？"母亲问。'}],
            normalize_quotes=True,
        )
    assert exc.value.failed_edits[0]["error_type"] == EDIT_ERROR_NO_CHANGE


@pytest.mark.integration
def test_exact_only_unread_file_allows_deterministic_whitespace_newline_quote_match_only(db_session, owner_project):
    user, project = owner_project
    original = "　　“有事？”他声音很冷。\n\n　　她没有说话，只是抬头看着窗外。"
    chapter = _add_file(db_session, project, content=original)
    editor = FileEditor(db_session, user.id)

    # 1) Blank-line (\n vs \n\n) + indent + straight-quote difference on unread file -> unique deterministic match!
    ok = editor.edit_file(
        chapter.id,
        [
            {
                "op": "replace",
                "old": '"有事？"他声音很冷。\n她没有说话，只是抬头看着窗外。',
                "new": "“有事？”他声音低沉。\n\n她没有说话，只是抬头看着窗外。",
            }
        ],
        exact_only=True,
    )
    assert ok["edits_applied"] == 1
    assert ok["details"][0]["match_mode"] == "normalized_exact"
    db_session.expire_all()
    updated = db_session.get(File, chapter.id).content
    assert updated == "　　“有事？”他声音低沉。\n\n　　她没有说话，只是抬头看着窗外。"

    # 2) Punctuation difference (ASCII '?' vs '？') on unread file -> strictly rejected!
    with pytest.raises(EditBatchError) as exc_punct:
        editor.edit_file(
            chapter.id,
            [{"op": "replace", "old": '"有事?"他声音低沉。', "new": "“没事。”"}],
            exact_only=True,
        )
    assert exc_punct.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND

    # 3) Word error drift on unread file -> strictly rejected!
    with pytest.raises(EditBatchError) as exc_drift:
        editor.edit_file(
            chapter.id,
            [{"op": "replace", "old": "她没有说话，只是低头看着窗外。", "new": "她沉默了。"}],
            exact_only=True,
        )
    assert exc_drift.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND

    # 4) Paragraph-merging single-line old on unread file -> strictly rejected!
    with pytest.raises(EditBatchError) as exc_merge:
        editor.edit_file(
            chapter.id,
            [{"op": "replace", "old": "他声音低沉。她没有说话", "new": "他停住了。"}],
            exact_only=True,
        )
    assert exc_merge.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND


@pytest.mark.unit
def test_keep_line_breaks_allows_single_vs_double_newline_even_for_short_anchors():
    editor = FileEditor(session=None)
    # Short (< 6 normalized chars) multi-line replace with \n vs \n\n succeeds:
    short_content = "甲。\n\n乙。\n\n丙。"
    applied: list[dict] = []
    out = editor._apply_replace(
        short_content,
        {"op": "replace", "old": "甲。\n乙。", "new": "甲改。\n\n乙改。"},
        0,
        applied,
        [],
    )
    assert out == "甲改。\n\n乙改。\n\n丙。"

    # Still blocks merging distinct paragraphs onto one line:
    with pytest.raises(EditFileError) as exc:
        editor._apply_replace(
            "　　第一段话已经说完了。\n\n　　第二段话刚刚才开始。",
            {"op": "replace", "old": "第一段话已经说完了。第二段话刚刚才开始。", "new": "合并。"},
            0,
            [],
            [],
        )
    assert exc.value.error_type == EDIT_ERROR_ANCHOR_NOT_FOUND
    assert "换行" in str(exc.value)


@pytest.mark.unit
def test_apply_delete_uses_approximate_match_fallback_and_respects_paragraph_guard():
    editor = FileEditor(session=None)
    content = (
        "　　风从窗缝里钻进来，吹得纸页沙沙地响个不停。\n\n"
        "　　林舟翻到腰酸，直起身靠着书架站了一会儿，然后慢慢走到窗边。\n\n"
        "　　楼下的街灯亮了起来。"
    )
    applied: list[dict] = []
    # Slight word drift in delete target -> approximate fallback succeeds!
    out = editor._apply_delete(
        content,
        {"op": "delete", "old": "林舟翻得腰酸，直起身靠着书架站了一阵子，然后慢慢走到窗边。"},
        0,
        applied,
        [],
    )
    assert applied[0]["match_mode"] == "approximate"
    assert "林舟" not in out
    assert "楼下的街灯亮了起来。" in out

    # Cross-paragraph drift in delete -> still blocked by keep_line_breaks!
    with pytest.raises(EditFileError) as exc:
        editor._apply_delete(
            content,
            {"op": "delete", "old": "吹得纸页沙沙地响个不停。林舟翻得腰酸，直起身靠着书架"},
            0,
            [],
            [],
        )
    assert exc.value.error_type == EDIT_ERROR_ANCHOR_NOT_FOUND
    assert "段落分隔" in str(exc.value) or "换行" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_edit_file_missing_id_includes_recent_and_focus_file_id_hints():
    req_ctx = AgentRequestContext(
        session=MagicMock(),
        user_id="u-1",
        project_id="p-1",
        session_id="s-1",
        read_snapshots={"focus-file-id": "焦点正文", "other-id": "其他正文"},
    )
    req_ctx.record_read_snapshot("recent-read-id", "最近读过")
    req_ctx.promote_pending_reads()

    resp = _payload(
        await mcp_tools.edit_file(
            {"edits": [{"op": "replace", "old": "a", "new": "b"}]},
            request_context=req_ctx,
        )
    )
    assert resp["status"] == "error"
    assert resp["error_type"] == EDIT_ERROR_INVALID_EDIT
    assert resp["recent_file_ids"][:2] == ["recent-read-id", "focus-file-id"]
    assert "recent-read-id" in resp["error"]
    assert "focus-file-id" in resp["error"]


# ===========================================================================
# Item 3: create_file(source_text=...) steering & 98% verbatim snap
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_source_text_matches_mid_run_author_steering():
    steering_body = "卷一设定：北境寒霜城常年积雪。\n主角林寒：二十四岁，铁匠铺学徒。"
    req_ctx = AgentRequestContext(
        session=MagicMock(),
        user_id="u-1",
        project_id="p-1",
        session_id="s-1",
        author_message="先帮我想个开头方向。",
        author_steering=[f"把这段设定原样存成资料：\n{steering_body}"],
    )
    executor = MagicMock()
    executor.create_file.side_effect = lambda **kw: {
        "id": "lore-steer",
        "title": kw["title"],
        "file_type": kw["file_type"],
        "content": kw["content"],
        "mutation_applied": True,
    }
    with patch.object(req_ctx, "executor", return_value=executor):
        resp = _payload(
            await mcp_tools.create_file(
                {"title": "北境设定", "file_type": "lore", "source_text": steering_body},
                request_context=req_ctx,
            )
        )

    assert resp["status"] == "success"
    assert executor.create_file.call_args.kwargs["content"] == steering_body


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_source_text_long_paste_98_percent_snap_saves_exact_author_bytes():
    lines = [f"第{i:02d}条设定：青州港码头每日卯时开闸，商船须持铜牌入港核验。" for i in range(1, 61)]
    author_paste = "\n".join(lines)
    assert len(author_paste) >= 500

    # Simulate 1 LLM JSON-copy corruption char (U+FFFD) on 1 middle line (59/60 = 98.33% exact lines).
    corrupted_lines = list(lines)
    corrupted_lines[25] = corrupted_lines[25].replace("铜牌", "铜\ufffd")
    llm_copy = "\n".join(corrupted_lines)

    req_ctx = AgentRequestContext(
        session=MagicMock(),
        user_id="u-1",
        project_id="p-1",
        session_id="s-1",
        author_message=f"请把下面长资料原样录入：\n\n{author_paste}\n\n存好告诉我。",
    )
    executor = MagicMock()
    executor.create_file.side_effect = lambda **kw: {
        "id": "lore-snap",
        "title": kw["title"],
        "file_type": kw["file_type"],
        "content": kw["content"],
        "mutation_applied": True,
    }
    with patch.object(req_ctx, "executor", return_value=executor):
        ok = _payload(
            await mcp_tools.create_file(
                {"title": "青州港设定", "file_type": "lore", "source_text": llm_copy},
                request_context=req_ctx,
            )
        )
    assert ok["status"] == "success"
    # Saved bytes MUST be 100% original author bytes (zero U+FFFD)!
    saved = executor.create_file.call_args.kwargs["content"]
    assert saved == author_paste
    assert "\ufffd" not in saved

    # Below 98% line match (3/60 lines altered = 95%) must be rejected!
    too_noisy = list(lines)
    for idx in (10, 20, 30):
        too_noisy[idx] = too_noisy[idx].replace("铜牌", "铁牌")
    with patch.object(req_ctx, "executor", return_value=executor):
        bad_ratio = _payload(
            await mcp_tools.create_file(
                {"title": "青州港设定2", "file_type": "lore", "source_text": "\n".join(too_noisy)},
                request_context=req_ctx,
            )
        )
    assert bad_ratio["status"] == "error"
    assert bad_ratio["error_type"] == "source_text_not_in_author_message"

    # Skipped middle line must still be rejected!
    skipped_lines = lines[:25] + lines[26:]
    with patch.object(req_ctx, "executor", return_value=executor):
        bad_skip = _payload(
            await mcp_tools.create_file(
                {"title": "青州港设定3", "file_type": "lore", "source_text": "\n".join(skipped_lines)},
                request_context=req_ctx,
            )
        )
    assert bad_skip["status"] == "error"
    assert bad_skip["error_type"] == "source_text_not_in_author_message"


# ===========================================================================
# Item 4: read_skill_resource during ui_action="natural_polish"
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_read_skill_resource_resolves_official_deai_skill_during_natural_polish(
    db_session: Session, owner_project
):
    user, project = owner_project
    official = PublicSkill(
        name=OFFICIAL_NATURAL_POLISH_SKILL_NAME,
        description="官方去AI味",
        instructions="详见 references/表达.md",
        source="official",
        status="approved",
    )
    db_session.add(official)
    db_session.commit()
    db_session.refresh(official)
    db_session.add(
        SkillResource(
            public_skill_id=official.id,
            path="references/表达.md",
            content="治理电报体与套词。",
            size=9,
        )
    )
    db_session.commit()

    # Active ui_action="natural_polish": user has ZERO UserAddedSkill rows yet succeeds!
    polish_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="s-polish",
        ui_action=UI_ACTION_NATURAL_POLISH,
    )
    res_zh = _payload(
        await mcp_tools.read_skill_resource(
            {"name": "去AI味", "path": "references/表达.md"},
            request_context=polish_ctx,
        )
    )
    assert res_zh["status"] == "success"
    assert res_zh["data"]["skill_id"] == official.id
    assert res_zh["data"]["content"] == "治理电报体与套词。"

    res_en = _payload(
        await mcp_tools.read_skill_resource(
            {"name": "Remove AI Tone", "path": "references/表达.md"},
            request_context=polish_ctx,
        )
    )
    assert res_en["status"] == "success"
    assert res_en["data"]["content"] == "治理电报体与套词。"

    # Ordinary turn (ui_action=None): unadded official skill remains unavailable!
    normal_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="s-normal",
        ui_action=None,
    )
    denied = _payload(
        await mcp_tools.read_skill_resource(
            {"name": "去AI味", "path": "references/表达.md"},
            request_context=normal_ctx,
        )
    )
    assert denied["status"] == "error"


# ===========================================================================
# Item 5: query_files(section=...) heading matcher & short-file fallback
# ===========================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("section", "expected_prefix", "absent_text"),
    [
        ("卷四", "## 卷四 决战前夜", "卷五"),
        ("第4卷", "## 卷四 决战前夜", "卷五"),
        ("第3章", "**第3章 风雨欲来**", "卷四"),
        ("一、第一卷逐章", "一、第一卷逐章大纲", "二、人物关系"),
        ("面板基础板块", "【面板基础板块】", "【进阶词条】"),
        ("【面板基础板块】", "【面板基础板块】", "【进阶词条】"),
        ("世界观总览", "### 世界观总览", "一、第一卷逐章"),
    ],
)
def test_content_range_recognizes_extended_heading_formats(section, expected_prefix, absent_text):
    body = (
        "### 世界观总览\n天地灵气分为五系。\n\n"
        "一、第一卷逐章大纲\n第一章起势。\n\n"
        "二、人物关系\n师徒反目。\n\n"
        "【面板基础板块】\n境界与数值说明。\n\n"
        "【进阶词条】\n高阶能力。\n\n"
        "**第3章 风雨欲来**\n夜雨倾盆。\n\n"
        "## 卷四 决战前夜\n城门紧闭。\n\n"
        "## 卷五 终局\n尘埃落定。"
    )
    row = apply_content_range({"id": "f", "content": body}, {"section": section})
    assert row["content"].startswith(expected_prefix)
    assert absent_text not in row["content"]
    assert row["content_partial"] is True


@pytest.mark.unit
def test_content_range_short_single_chapter_section_miss_falls_back_to_full_text():
    single_chapter = "　　辰时刚过，正屋里已经围满了人。姜七按住腰间短刀，没吭声。"
    assert len(single_chapter) <= SHORT_FILE_FULL_RETURN_MAX_CHARS

    row = apply_content_range({"id": "f", "content": single_chapter}, {"section": "被围"})
    assert row["content"] == single_chapter
    assert row["content_partial"] is False
    assert row["content_range"] == {"start": 0, "end": len(single_chapter)}
    assert "单章正文" in row["note"]

    # Long file miss (> 12000 chars) still hard-fails with ContentRangeError!
    long_file = "卷一 开端\n" + ("正文字段。" * 3000) + "\n卷二 承转\n后续。"
    assert len(long_file) > SHORT_FILE_FULL_RETURN_MAX_CHARS
    with pytest.raises(ContentRangeError, match="没有找到"):
        apply_content_range({"id": "f", "content": long_file}, {"section": "卷九"})


@pytest.mark.unit
def test_query_files_section_schema_guides_against_single_chapter_draft():
    section_desc = QUERY_FILES_TOOL["input_schema"]["properties"]["section"]["description"]
    assert "单章" in section_desc
    assert "draft" in section_desc
