"""Regression tests for post-#497 agent edit-safety issues (Issues 1-4).

1. `batch_replace_files` repeating replacements when `new_text` contains `old_text`
   (e.g. 陈砚→陈砚之, 小雨→林小雨, …→……, a→aba) and 100-chapter pagination without `file_ids`.
2. `edit_file` `replace(new="")` enforcing `_apply_delete`'s stricter approximate-matching rules
   (<=12% error, >=15 chars, sentence boundaries) while preserving exact verbatim deletion.
3. Paragraph stats marker `[Pxx · N字] ` stripped line-by-line on write inputs (`edit_file` and
   `batch_replace_files`) only when the original file does not already contain that exact marker,
   leaving `[P1]` / `[P05]` / `[P05 备注]` and pre-existing `[Pxx · N字] ` files untouched.
4. `edit_file` multi-match ambiguity errors (exact, normalized_exact, fuzzy) scoping
   `batch_replace_files` guidance to `file_ids=["<current_file_id>"]` and prompt/schema alignment.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.loop.prompts import LOOP_WORKING_RULES
from agent.prompts.base import LOOP_TOOL_USAGE_PROTOCOL
from agent.tools import mcp_tools
from agent.tools.file_ops.edit import (
    EDIT_ERROR_ANCHOR_AMBIGUOUS,
    EDIT_ERROR_ANCHOR_NOT_FOUND,
    EditBatchError,
    FileEditor,
)
from agent.tools.tool_context import AgentRequestContext
from agent.tools.tool_inputs import EditFileInput, validate_tool_input
from agent.tools.tool_schemas import TOOL_SCHEMAS
from models import File, Project, User
from services.features.activation_event_service import activation_event_service

pytestmark = pytest.mark.unit


def _parse_mcp(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["content"][0]["text"])


def _make_user_project(db_session: Session, tag: str) -> tuple[User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"safety-{tag}-{suffix}@example.com",
        username=f"safety_{tag}_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(name=f"Safety {tag} {suffix}", owner_id=user.id, project_type="novel")
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
    order: int = 0,
) -> File:
    f = File(
        project_id=project_id,
        title=title,
        content=content,
        file_type=file_type,
        order=order,
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
# Issue 1: batch_replace_files when new_text contains old_text
# ===========================================================================


@pytest.mark.asyncio
async def test_issue1_batch_replace_100_chapters_suffix_overlap_without_file_ids(db_session):
    """100-chapter 陈砚→陈砚之 across repeated unscoped calls advances 1-30->31-60->61-90->91-100->0, never 陈砚之之."""
    user, project = _make_user_project(db_session, "i1-100ch")
    created_ids: list[str] = []
    for idx in range(1, 101):
        # Chapter 1 already has one existing "陈砚之" plus one bare "陈砚" to verify pre-existing new_text is untouched.
        body = (
            "　　陈砚站在雨里，听见旁人唤了一声陈砚之。"
            if idx == 1
            else f"　　第{idx}章：陈砚推开门，陈砚没有说话。"
        )
        f = File(
            project_id=project.id,
            title=f"第{idx}章",
            file_type="draft",
            order=idx,
            content=body,
        )
        db_session.add(f)
        created_ids.append(f.id)
    db_session.commit()

    req_ctx = _make_req_ctx(db_session, user, project)

    # dry_run must count ONLY effective matches (ch1 has 1 effective match, NOT 2).
    dry = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "陈砚", "new_text": "陈砚之", "dry_run": True},
            request_context=req_ctx,
        )
    )
    assert dry["status"] == "success"
    assert dry["mutation_applied"] is False
    assert dry["data"]["matched_files"] == 100
    assert dry["data"]["files"][0]["id"] == created_ids[0]
    assert dry["data"]["files"][0]["replacements"] == 1
    assert dry["data"]["remaining_count"] == 70

    # 4 unscoped apply calls cleanly cover 1-30, 31-60, 61-90, 91-100 without repeating or starving.
    expected_slices = [
        (created_ids[0:30], 70, True),
        (created_ids[30:60], 40, True),
        (created_ids[60:90], 10, True),
        (created_ids[90:100], 0, False),
    ]
    for expected_ids, expected_rem, expected_trunc in expected_slices:
        res = _parse_mcp(
            await mcp_tools.batch_replace_files(
                {"old_text": "陈砚", "new_text": "陈砚之"},
                request_context=req_ctx,
            )
        )
        assert res["status"] == "success"
        assert res["mutation_applied"] is True
        assert [item["id"] for item in res["data"]["files"]] == expected_ids
        assert res["data"]["remaining_count"] == expected_rem
        assert res["data"]["truncated"] is expected_trunc

    # 5th call in same request: 0 matches, no mutation.
    fifth = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "陈砚", "new_text": "陈砚之"},
            request_context=req_ctx,
        )
    )
    assert fifth["status"] == "success"
    assert fifth["mutation_applied"] is False
    assert fifth["data"]["matched_files"] == 0
    assert fifth["data"]["modified_files"] == 0

    # Cross-request idempotency: brand-new AgentRequestContext also sees 0 effective matches!
    fresh_ctx = _make_req_ctx(db_session, user, project)
    sixth = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "陈砚", "new_text": "陈砚之"},
            request_context=fresh_ctx,
        )
    )
    assert sixth["status"] == "success"
    assert sixth["mutation_applied"] is False
    assert sixth["data"]["matched_files"] == 0
    assert sixth["data"]["modified_files"] == 0

    # Verify DB content of all 100 chapters: every chapter updated once, zero "陈砚之之".
    db_session.expire_all()
    assert db_session.get(File, created_ids[0]).content == "　　陈砚之站在雨里，听见旁人唤了一声陈砚之。"
    for idx, fid in enumerate(created_ids[1:], start=2):
        content = db_session.get(File, fid).content
        assert content == f"　　第{idx}章：陈砚之推开门，陈砚之没有说话。"
        assert "陈砚之之" not in content


@pytest.mark.asyncio
async def test_issue1_batch_replace_prefix_repeated_circumfix_and_normal_rename(db_session):
    """Covers 小雨→林小雨, …→……, a→aba, cross-request idempotency, and normal 陈砚→陆沉."""
    user, project = _make_user_project(db_session, "i1-patterns")
    f_prefix = _add_file(
        db_session,
        project.id,
        title="第1章 前缀",
        order=1,
        content="　　小雨看着林小雨，小雨笑了一声。",
    )
    f_repeat = _add_file(
        db_session,
        project.id,
        title="第2章 省略号",
        order=2,
        content="　　他说…停顿片刻……然后又说…",
    )
    f_circum = _add_file(
        db_session,
        project.id,
        title="第3章 环绕",
        order=3,
        content="x a y aba z",
    )
    f_normal = _add_file(
        db_session,
        project.id,
        title="第4章 常规改名",
        order=4,
        content="　　陈砚走进客栈，陈砚点了一壶茶。",
    )

    # 1) Prefix: 小雨 -> 林小雨
    res_p1 = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "小雨", "new_text": "林小雨", "file_ids": [f_prefix.id]},
            request_context=_make_req_ctx(db_session, user, project),
        )
    )
    assert res_p1["mutation_applied"] is True
    assert res_p1["data"]["total_replacements"] == 2
    db_session.expire_all()
    assert db_session.get(File, f_prefix.id).content == "　　林小雨看着林小雨，林小雨笑了一声。"

    # Cross-request repeat -> 0 matches, never 林林小雨.
    res_p2 = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "小雨", "new_text": "林小雨", "file_ids": [f_prefix.id]},
            request_context=_make_req_ctx(db_session, user, project),
        )
    )
    assert res_p2["mutation_applied"] is False
    assert res_p2["data"]["matched_files"] == 0

    # 2) Repeated: … -> ……
    res_r1 = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "…", "new_text": "……", "file_ids": [f_repeat.id]},
            request_context=_make_req_ctx(db_session, user, project),
        )
    )
    assert res_r1["mutation_applied"] is True
    assert res_r1["data"]["total_replacements"] == 2
    db_session.expire_all()
    assert db_session.get(File, f_repeat.id).content == "　　他说……停顿片刻……然后又说……"

    res_r2 = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "…", "new_text": "……", "file_ids": [f_repeat.id]},
            request_context=_make_req_ctx(db_session, user, project),
        )
    )
    assert res_r2["mutation_applied"] is False
    assert res_r2["data"]["matched_files"] == 0

    # 3) Circumfix: a -> aba
    res_c1 = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "a", "new_text": "aba", "file_ids": [f_circum.id]},
            request_context=_make_req_ctx(db_session, user, project),
        )
    )
    assert res_c1["mutation_applied"] is True
    assert res_c1["data"]["total_replacements"] == 1
    db_session.expire_all()
    assert db_session.get(File, f_circum.id).content == "x aba y aba z"

    res_c2 = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "a", "new_text": "aba", "file_ids": [f_circum.id]},
            request_context=_make_req_ctx(db_session, user, project),
        )
    )
    assert res_c2["mutation_applied"] is False
    assert res_c2["data"]["matched_files"] == 0

    # 4) Normal non-overlapping rename: 陈砚 -> 陆沉
    res_n = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "陈砚", "new_text": "陆沉", "file_ids": [f_normal.id]},
            request_context=_make_req_ctx(db_session, user, project),
        )
    )
    assert res_n["mutation_applied"] is True
    assert res_n["data"]["total_replacements"] == 2
    db_session.expire_all()
    assert db_session.get(File, f_normal.id).content == "　　陆沉走进客栈，陆沉点了一壶茶。"

    # 5) Regex metacharacter overlap: ? -> ?? must be safely escaped (zero ReDoS / regex error).
    f_meta = _add_file(db_session, project.id, title="第5章 标点", order=5, content="为什么?真的吗??确定?")
    res_m1 = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {"old_text": "?", "new_text": "??", "file_ids": [f_meta.id]},
            request_context=_make_req_ctx(db_session, user, project),
        )
    )
    assert res_m1["mutation_applied"] is True
    assert res_m1["data"]["total_replacements"] == 2
    db_session.expire_all()
    assert db_session.get(File, f_meta.id).content == "为什么??真的吗??确定??"


@pytest.mark.asyncio
async def test_issue1_batch_replace_tracks_processed_ids_in_context_and_invalidates_on_edit(db_session):
    """Same-request unscoped pagination tracks processed IDs (even on pathological ab->bab) and invalidates on edit."""
    user, project = _make_user_project(db_session, "i1-ctx-track")
    ids: list[str] = []
    for idx in range(1, 36):
        f = File(project_id=project.id, title=f"第{idx}章", file_type="draft", order=idx, content="aab")
        db_session.add(f)
        ids.append(f.id)
    db_session.commit()

    req_ctx = _make_req_ctx(db_session, user, project)
    r1 = _parse_mcp(await mcp_tools.batch_replace_files({"old_text": "ab", "new_text": "bab"}, request_context=req_ctx))
    assert [item["id"] for item in r1["data"]["files"]] == ids[:30]
    assert r1["data"]["remaining_count"] == 5

    # Second unscoped call skips 1-30 cleanly even though "aab" -> "abab" contains a boundary "ab".
    r2 = _parse_mcp(await mcp_tools.batch_replace_files({"old_text": "ab", "new_text": "bab"}, request_context=req_ctx))
    assert [item["id"] for item in r2["data"]["files"]] == ids[30:]
    assert r2["data"]["remaining_count"] == 0

    # If edit_file subsequently writes a new bare match to chapter 1, processed tracking is invalidated for chapter 1.
    ch1 = db_session.get(File, ids[0])
    ch1_before = ch1.content
    edit_res = _parse_mcp(
        await mcp_tools.edit_file(
            {"id": ids[0], "edits": [{"op": "append", "text": " 陈砚"}]},
            request_context=req_ctx,
        )
    )
    assert edit_res["status"] == "success"
    assert ids[0] not in req_ctx.batch_replace_processed_ids("ab", "bab")
    db_session.expire_all()
    assert db_session.get(File, ids[0]).content.startswith(ch1_before)


# ===========================================================================
# Issue 2: edit_file replace(new="") enforces _apply_delete's strict guards
# ===========================================================================


def test_issue2_replace_empty_new_rejects_sloppy_short_and_mid_sentence_approx(db_session):
    """replace(new="") MUST reject sloppy (>12%), short (<15 chars), and mid-sentence approx matches identically to delete."""
    user, project = _make_user_project(db_session, "i2-delete-guards")
    body = (
        "　　林舟轻轻推开那扇老旧的木门走了进去。\n\n"
        "　　窗外雨声淅沥，檐下铜铃被风吹得晃动个不停，桌上那盏油灯微微发亮。\n\n"
        "　　门后空无一人。"
    )
    chapter = _add_file(db_session, project.id, title="第1章", content=body)
    editor = FileEditor(db_session, user.id)

    # (a) >12% error (within 25%): 17 chars with 3 substitutions (~17.6% error) -> previously succeeded on replace(new="")!
    sloppy_old = "林舟慢慢拉开那扇破旧的木门走了进去。"
    with pytest.raises(EditBatchError) as exc_sloppy:
        editor.edit_file(chapter.id, [{"op": "replace", "old": sloppy_old, "new": ""}])
    assert exc_sloppy.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND
    with pytest.raises(EditBatchError) as exc_sloppy_del:
        editor.edit_file(chapter.id, [{"op": "delete", "old": sloppy_old}])
    assert exc_sloppy_del.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND

    # (b) Short (<15 chars) approximate match (10 chars, 1 char wrong = 10% <= 12%, but < 15 chars min_pattern_len).
    short_approx_old = "林舟轻轻推开那扇老旧的铁门"
    assert 8 <= len(short_approx_old) < 15
    with pytest.raises(EditBatchError) as exc_short:
        editor.edit_file(chapter.id, [{"op": "replace", "old": short_approx_old, "new": ""}])
    assert exc_short.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND
    with pytest.raises(EditBatchError) as exc_short_del:
        editor.edit_file(chapter.id, [{"op": "delete", "old": short_approx_old}])
    assert exc_short_del.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND

    # (c) Mid-sentence approximate match (>=15 chars, <=12% error, but starts/ends mid-sentence:
    # starts at 声, ends at 盏, with 1 typo 铜->铁 so it takes the approximate path).
    mid_sentence_approx = "声淅沥，檐下铁铃被风吹得晃动个不停，桌上那盏"
    assert len(mid_sentence_approx) >= 15
    with pytest.raises(EditBatchError) as exc_mid:
        editor.edit_file(chapter.id, [{"op": "replace", "old": mid_sentence_approx, "new": ""}])
    assert exc_mid.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND
    with pytest.raises(EditBatchError) as exc_mid_del:
        editor.edit_file(chapter.id, [{"op": "delete", "old": mid_sentence_approx}])
    assert exc_mid_del.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND

    # (d) Alias to="" / text="" also enforces the same strict delete guards.
    with pytest.raises(EditBatchError) as exc_alias:
        editor.edit_file(chapter.id, [{"op": "replace", "old": sloppy_old, "to": ""}])
    assert exc_alias.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND

    # File remains completely untouched after all rejected attempts.
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == body


def test_issue2_replace_empty_new_preserves_exact_short_and_clean_sentence_approx_delete(db_session):
    """Exact short verbatim replace(new="") and clean <=12% >=15-char sentence-boundary approx delete still succeed."""
    user, project = _make_user_project(db_session, "i2-delete-happy")
    body = (
        "　　林舟轻轻推开那扇老旧的木门走了进去。\n\n"
        "　　门后空无一人。"
    )
    chapter = _add_file(db_session, project.id, title="第1章", content=body)
    editor = FileEditor(db_session, user.id)

    # 1) Clean sentence-boundary approximate delete (>=15 chars, 1 char typo = ~5.5% <= 12%).
    res_approx = editor.edit_file(
        chapter.id,
        [{"op": "replace", "old": "林舟轻轻推开那扇老旧的铁门走了进去。", "new": ""}],
    )
    assert res_approx["mutation_applied"] is True
    assert res_approx["details"][0]["op"] == "replace"
    assert res_approx["details"][0]["match_mode"] == "approximate"
    db_session.expire_all()
    assert "木门走了进去" not in db_session.get(File, chapter.id).content

    # 2) Short exact verbatim replace(new="") still deletes cleanly.
    res_exact = editor.edit_file(
        chapter.id,
        [{"op": "replace", "old": "空无一人", "new": ""}],
    )
    assert res_exact["mutation_applied"] is True
    assert res_exact["details"][0]["op"] == "replace"
    assert res_exact["details"][0]["match_mode"] == "exact"
    db_session.expire_all()
    assert "空无一人" not in db_session.get(File, chapter.id).content

    # 3) Coupled move detection in EditFileInput keeps continue_on_error=False for replace(new="") + insert_after.
    parsed, err = validate_tool_input(
        EditFileInput,
        {
            "id": chapter.id,
            "edits": [
                {"op": "replace", "old": "门后。", "new": ""},
                {"op": "insert_after", "anchor": "结尾", "text": "门后。"},
            ],
        },
        tool_name="edit_file",
    )
    assert err is None and parsed is not None
    assert parsed.continue_on_error is False


# ===========================================================================
# Issue 3: Paragraph stats marker `[Pxx · N字] ` stripping on write inputs
# ===========================================================================


@pytest.mark.asyncio
async def test_issue3_paragraph_stats_marker_stripped_in_all_leak_cases(db_session):
    """Cases (a) partial-marked expansion, (b) clean old + marked new, (c) marked append/insert/prepend, (d) batch_replace."""
    user, project = _make_user_project(db_session, "i3-strip")
    p1 = "　　窗外雨声淅沥。"
    p2 = "　　他合上手里的册子。"
    chapter = _add_file(db_session, project.id, title="第1章", content=f"{p1}\n\n{p2}")
    editor = FileEditor(db_session, user.id)

    # Case (a): partial-marked multi-paragraph expansion (line 1 marked, line 2 unmarked).
    res_a = editor.edit_file(
        chapter.id,
        [
            {
                "op": "replace",
                "old": f"[P01 · 8字] {p1}",
                "new": "[P01 · 10字] 　　窗外雨声渐渐停了。\n\n　　新扩写的第二段没有前缀。",
            }
        ],
    )
    assert res_a["mutation_applied"] is True
    db_session.expire_all()
    saved_a = db_session.get(File, chapter.id).content
    assert "[P01" not in saved_a
    assert "　　窗外雨声渐渐停了。\n\n　　新扩写的第二段没有前缀。" in saved_a

    # Case (b): clean old (succeeds on 1st attempt) + marked new.
    res_b = editor.edit_file(
        chapter.id,
        [
            {
                "op": "replace",
                "old": p2,
                "new": "[P03 · 12字] 　　他轻轻合上手里的册子。",
            }
        ],
    )
    assert res_b["mutation_applied"] is True
    db_session.expire_all()
    saved_b = db_session.get(File, chapter.id).content
    assert "[P03" not in saved_b
    assert "　　他轻轻合上手里的册子。" in saved_b

    # Case (c): marked append, insert_after, insert_before, prepend.
    res_c = editor.edit_file(
        chapter.id,
        [
            {"op": "append", "text": "[P04 · 9字] 　　楼下传来脚步声。\n\n　　没有前缀的续段。"},
            {"op": "insert_after", "anchor": "他轻轻合上手里的册子。", "text": "\n\n[P05 · 7字] 　　灯花爆了一声。"},
            {"op": "prepend", "text": "[P00 · 5字] 　　序幕拉开。\n\n"},
        ],
    )
    assert res_c["mutation_applied"] is True
    db_session.expire_all()
    saved_c = db_session.get(File, chapter.id).content
    assert "[P00" not in saved_c
    assert "[P04" not in saved_c
    assert "[P05" not in saved_c
    assert "　　序幕拉开。" in saved_c
    assert "　　楼下传来脚步声。" in saved_c
    assert "　　灯花爆了一声。" in saved_c

    # Case (d): batch_replace_files with marked new_text on a clean file.
    req_ctx = _make_req_ctx(db_session, user, project)
    res_d = _parse_mcp(
        await mcp_tools.batch_replace_files(
            {
                "old_text": "脚步声",
                "new_text": "[P04 · 9字] 敲门声",
                "file_ids": [chapter.id],
            },
            request_context=req_ctx,
        )
    )
    assert res_d["mutation_applied"] is True
    db_session.expire_all()
    saved_d = db_session.get(File, chapter.id).content
    assert "[P04" not in saved_d
    assert "敲门声" in saved_d

    # Case (e): marker-only append text is rejected rather than appending an empty string.
    with pytest.raises(EditBatchError) as exc_empty_marker:
        editor.edit_file(chapter.id, [{"op": "append", "text": "[P09 · 0字] "}])
    assert exc_empty_marker.value.failed_edits[0]["error_type"] == "invalid_edit"


@pytest.mark.asyncio
async def test_issue3_authentic_markers_and_preexisting_stats_markers_preserved(db_session):
    """[P1]/[P05]/[P05 备注] and files that already contain `[P05 · 84字] ` MUST NEVER be stripped."""
    user, project = _make_user_project(db_session, "i3-preserve")
    lore = _add_file(
        db_session,
        project.id,
        title="设定笔记",
        file_type="lore",
        content="[P1] 第一条设定\n[P05] 第五条设定\n[P05 备注] 补充说明",
    )
    stats_doc = _add_file(
        db_session,
        project.id,
        title="格式说明文档",
        file_type="document",
        content="[P05 · 84字] 原文示例段落\n说明文字",
    )
    editor = FileEditor(db_session, user.id)

    # 1) Authentic [P1] / [P05] / [P05 备注] must never be stripped on edit_file or batch_replace_files.
    editor.edit_file(
        lore.id,
        [
            {"op": "replace", "old": "[P1] 第一条设定", "new": "[P1] 更新后的设定"},
            {"op": "append", "text": "\n[P05 备注] 新增备注\n[P05] 新增条目"},
        ],
    )
    db_session.expire_all()
    saved_lore = db_session.get(File, lore.id).content
    assert "[P1] 更新后的设定" in saved_lore
    assert "[P05 备注] 新增备注" in saved_lore
    assert "[P05] 新增条目" in saved_lore

    req_ctx = _make_req_ctx(db_session, user, project)
    await mcp_tools.batch_replace_files(
        {"old_text": "[P05] 第五条设定", "new_text": "[P05] 修改后第五条", "file_ids": [lore.id]},
        request_context=req_ctx,
    )
    db_session.expire_all()
    assert "[P05] 修改后第五条" in db_session.get(File, lore.id).content

    # 2) File that originally already contains `[P05 · 84字] ` must keep markers untouched on edit_file & batch_replace.
    editor.edit_file(
        stats_doc.id,
        [
            {"op": "replace", "old": "说明文字", "new": "[P06 · 12字] 新示例段落"},
            {"op": "append", "text": "\n[P07 · 8字] 追加示例"},
        ],
    )
    db_session.expire_all()
    saved_stats = db_session.get(File, stats_doc.id).content
    assert "[P05 · 84字] 原文示例段落" in saved_stats
    assert "[P06 · 12字] 新示例段落" in saved_stats
    assert "[P07 · 8字] 追加示例" in saved_stats

    await mcp_tools.batch_replace_files(
        {
            "old_text": "[P07 · 8字] 追加示例",
            "new_text": "[P07 · 9字] 替换后示例",
            "file_ids": [stats_doc.id],
        },
        request_context=req_ctx,
    )
    db_session.expire_all()
    assert "[P07 · 9字] 替换后示例" in db_session.get(File, stats_doc.id).content


# ===========================================================================
# Issue 4: edit_file multi-match ambiguity errors scope batch_replace_files
# ===========================================================================


def test_issue4_edit_file_all_three_ambiguity_paths_scope_batch_replace_to_current_file(db_session):
    """Exact, normalized_exact, and fuzzy multi-match ambiguity errors MUST include file_ids=["<file_id>"]."""
    user, project = _make_user_project(db_session, "i4-ambiguous")
    # Two occurrences separated by different spacing/punctuation so we can trigger all 3 paths cleanly:
    # - Exact: "他默默地点了点头。" appears twice verbatim
    # - Normalized exact: "他  默默地点了点头。" (extra spaces -> fails exact, hits normalized_exact with 2 spans)
    # - Fuzzy: "他默默地，点了点头。" (extra comma -> fails exact & normalized_exact, hits fuzzy with 2 spans)
    chapter = _add_file(
        db_session,
        project.id,
        title="第1章",
        content="第一段：他默默地点了点头。\n\n第二段：他默默地点了点头。",
    )
    editor = FileEditor(db_session, user.id)
    expected_scoped_call = f'batch_replace_files(file_ids=["{chapter.id}"]'

    cases = [
        ("exact", "他默默地点了点头。"),
        ("normalized_exact", "他  默默地点了点头。"),
        ("fuzzy", "他默默地，点了点头。"),
    ]
    for label, old_text in cases:
        with pytest.raises(EditBatchError) as exc_info:
            editor.edit_file(chapter.id, [{"op": "replace", "old": old_text, "new": "他摇了摇头。"}])
        failed = exc_info.value.failed_edits[0]
        assert failed["error_type"] == EDIT_ERROR_ANCHOR_AMBIGUOUS, f"{label} should be ambiguous"
        msg = failed["error"]
        assert "occurrence=N" in msg, f"{label} missing occurrence=N"
        assert expected_scoped_call in msg, f"{label} missing scoped file_ids hint: {msg}"
        assert msg.index("occurrence=N") < msg.index("batch_replace_files")
        assert "省略" in msg and "file_ids" in msg, f"{label} missing warning not to omit file_ids: {msg}"


def test_issue4_prompts_and_schemas_consistently_scope_single_file_batch_replace():
    """tool_schemas.py, tool_inputs.py, prompts/base.py, and loop/prompts.py align on file_ids scoping."""
    br_tool = TOOL_SCHEMAS["batch_replace_files"]
    assert "file_ids" in br_tool["description"]
    assert "单文件" in br_tool["description"] or "单个文件" in br_tool["description"]
    file_ids_desc = br_tool["input_schema"]["properties"]["file_ids"]["description"]
    assert "单文件" in file_ids_desc or "必传" in file_ids_desc
    assert "file_ids" in LOOP_TOOL_USAGE_PROTOCOL
    assert "file_ids" in LOOP_WORKING_RULES
