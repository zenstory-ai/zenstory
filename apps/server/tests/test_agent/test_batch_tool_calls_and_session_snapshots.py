"""Tests for Batch B: session snapshot carryover, batch query_files(ids), and edit_file batching guidance."""

from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlmodel import Session

from agent.core.session_loader import SESSION_SNAPSHOT_MAX_FILES, SessionLoader
from agent.loop.engine import (
    _BODY_SAVED_CONTEXT,
    _BODY_SAVED_REPLIED_CONTEXT,
)
from agent.loop.turn_context import build_loop_turn_context
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.tool_failure_breaker import _with_recovery_hint
from agent.prompts.base import LOOP_TOOL_USAGE_PROTOCOL, UNIFIED_EXECUTION_PROTOCOL
from agent.tools import mcp_tools
from agent.tools.file_ops.edit import _approximate_not_found_advice
from agent.tools.tool_schemas import EDIT_FILE_TOOL
from config.datetime_utils import normalize_datetime_to_utc
from models import ChatMessage, ChatSession, File, Project, User
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService, content_sha256

SAMPLE_BODY = (
    "　　雨下了一整夜，“补纸斋”的门一直关着。\n\n"
    "　　温梨把伞收了，靠在摊子边上，看那些旧书一本一本被雨打湿。\n\n"
    "　　阿满趴在回声桥的栏杆上，数到第七条船，又数了一遍。"
)


@pytest.fixture
def env(db_session, monkeypatch):
    user = User(
        username="batch-b-user",
        email="batch-b-user@example.test",
        hashed_password="unused",
        email_verified=True,
    )
    other_user = User(
        username="batch-b-other",
        email="batch-b-other@example.test",
        hashed_password="unused",
        email_verified=True,
    )
    project = Project(name="云栖镇", owner_id=user.id)
    other_project = Project(name="他山录", owner_id=other_user.id)
    chat_session = ChatSession(user_id=user.id, project_id=project.id)
    db_session.add_all([user, other_user, project, other_project, chat_session])
    db_session.commit()
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    return user, project, other_project, chat_session


def _make_file(
    session: Session,
    project: Project,
    *,
    title: str,
    content: str = SAMPLE_BODY,
    file_type: str = "draft",
    with_ai_version: bool = True,
    is_deleted: bool = False,
) -> File:
    file = File(
        project_id=project.id,
        title=title,
        content=content,
        file_type=file_type,
        is_deleted=is_deleted,
    )
    session.add(file)
    session.flush()
    if with_ai_version and file_type != "folder" and not is_deleted:
        FileVersionService().create_version(
            session, file.id, content, change_type="create", change_source="ai", commit=False
        )
    session.commit()
    session.refresh(file)
    return file


def _add_assistant_turn(
    session: Session,
    chat_session: ChatSession,
    tool_calls: list[dict],
    *,
    created_at=None,
    metadata: dict | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        session_id=chat_session.id,
        role="assistant",
        content="处理完成。",
        tool_calls=json.dumps(tool_calls, ensure_ascii=False),
        message_metadata=json.dumps(metadata or {"engine": "loop"}, ensure_ascii=False),
        **({"created_at": created_at} if created_at is not None else {}),
    )
    session.add(msg)
    session.commit()
    session.refresh(msg)
    return msg


def _make_tool_context(session: Session, user: User, project: Project, *, read_snapshots: dict[str, str] | None = None):
    engine = session.get_bind()
    return mcp_tools.AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id=None,
        create_session_func=lambda: Session(engine),
        author_message="修改一下",
        author_steering=[],
        read_snapshots=read_snapshots,
    )


# =====================================================================
# Rank 1b: Session snapshot carryover
# =====================================================================


async def test_session_carryover_unchanged_query_files_allows_fuzzy_edit(db_session, env):
    user, project, _, chat_session = env
    chapter = _make_file(db_session, project, title="第一章 雨夜")
    _add_assistant_turn(
        db_session,
        chat_session,
        [
            {
                "id": "tc-1",
                "name": "query_files",
                "arguments": {"id": chapter.id},
                "status": "success",
                "result": [
                    {
                        "id": chapter.id,
                        "title": chapter.title,
                        "file_type": "draft",
                        "content": chapter.content,
                        "updated_at": chapter.updated_at.isoformat(),
                    }
                ],
            }
        ],
    )

    turn = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={},
        language="zh",
        author_message="把温梨那句改短一点",
        chat_session_id=chat_session.id,
    )
    assert chapter.id in turn.read_snapshots
    assert chapter.id in turn.session_carried_snapshot_ids

    req_ctx = _make_tool_context(db_session, user, project, read_snapshots=turn.read_snapshots)
    try:
        # 标点略不同（英文逗号/句号）：若未结转快照会被 exact_only 拒绝。
        raw = await mcp_tools.edit_file(
            {
                "id": chapter.id,
                "edits": [
                    {
                        "op": "replace",
                        "old": "温梨把伞收了, 靠在摊子边上, 看那些旧书一本一本被雨打湿.",
                        "new": "温梨收了伞。",
                    }
                ],
            },
            request_context=req_ctx,
        )
        payload = json.loads(raw["content"][0]["text"])
        assert payload["status"] == "success", payload
        assert payload["data"]["details"][0]["match_mode"] == "fuzzy"
    finally:
        req_ctx.clear()


async def test_session_carryover_author_edit_between_turns_never_bypasses_safety(db_session, env):
    """作者在轮间手改了文件（无论改内容还是改时间戳），绝不能把作者未见改动结转为“已看过”。"""
    user, project, _, chat_session = env
    ch_read = _make_file(db_session, project, title="第一章 读过后被作者改")
    ch_edit = _make_file(db_session, project, title="第二章 改过后被作者改")
    ch_created = _make_file(db_session, project, title="第三章 新写后被作者改")

    msg_time = normalize_datetime_to_utc(ch_created.updated_at).replace(tzinfo=None) + timedelta(seconds=5)
    _add_assistant_turn(
        db_session,
        chat_session,
        [
            {
                "id": "tc-read",
                "name": "query_files",
                "arguments": {"id": ch_read.id},
                "status": "success",
                "result": [
                    {
                        "id": ch_read.id,
                        "title": ch_read.title,
                        "file_type": "draft",
                        "content": ch_read.content,
                        "updated_at": ch_read.updated_at.isoformat(),
                    }
                ],
            },
            {
                "id": "tc-edit",
                "name": "edit_file",
                "arguments": {"id": ch_edit.id, "edits": []},
                "status": "success",
                "result": {
                    "id": ch_edit.id,
                    "mutation_applied": True,
                    "undo": {
                        "before_version_number": 1,
                        "expected_after_updated_at": normalize_datetime_to_utc(ch_edit.updated_at).isoformat(),
                        "expected_after_content_sha256": content_sha256(ch_edit.content),
                    },
                },
            },
            {
                "id": "tc-create",
                "name": "write_chapter",
                "arguments": {"chapter": 3, "title": "第三章", "file_type": "draft"},
                "status": "success",
                "result": {
                    "id": ch_created.id,
                    "action": "created",
                    "awaiting_body": True,
                    "body_saved": True,
                },
            },
        ],
        created_at=msg_time,
    )

    # 1) ch_read：作者改内容 + 推进 updated_at
    ch_read.content = SAMPLE_BODY.replace("温梨把伞收了", "温梨把伞折好了")
    ch_read.updated_at = ch_read.updated_at + timedelta(seconds=30)
    # 2) ch_edit：作者改内容但未推进 updated_at（模拟测试/边界情况）
    ch_edit.content = SAMPLE_BODY.replace("数到第七条船", "数到第八条船")
    # 3) ch_created：作者保存了一个 user 版本
    created_author_body = SAMPLE_BODY.replace("雨下了一整夜", "雪下了一整夜")
    FileVersionService().create_version(
        db_session, ch_created.id, created_author_body, change_type="edit", change_source="user", skip_quota=True
    )
    ch_created.content = created_author_body
    db_session.add_all([ch_read, ch_edit, ch_created])
    db_session.commit()

    turn = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={},
        language="zh",
        author_message="继续修改",
        chat_session_id=chat_session.id,
    )
    assert ch_read.id not in turn.read_snapshots
    assert ch_edit.id not in turn.read_snapshots
    assert ch_created.id not in turn.read_snapshots

    # 未结转的文件保持 exact_only=True，模型不能借近似匹配覆盖作者刚改的句子。
    req_ctx = _make_tool_context(db_session, user, project, read_snapshots=turn.read_snapshots)
    try:
        raw = await mcp_tools.edit_file(
            {
                "id": ch_read.id,
                "edits": [
                    {
                        "op": "replace",
                        "old": "温梨把伞收了, 靠在摊子边上, 看那些旧书一本一本被雨打湿.",
                        "new": "温梨收了伞。",
                    }
                ],
            },
            request_context=req_ctx,
        )
        payload = json.loads(raw["content"][0]["text"])
        assert payload["status"] == "error"
        assert payload["error_type"] == "anchor_not_found"
        assert "read_files" in payload["error"]
    finally:
        req_ctx.clear()


async def test_session_carryover_mid_turn_author_edit_still_triggers_stale_content(db_session, env):
    """轮间未改的文件结转快照后，如果作者在本轮执行中途修改了文件，edit_file 仍必须触发 StaleContentError。"""
    user, project, _, chat_session = env
    chapter = _make_file(db_session, project, title="第一章 轮中冲突")
    _add_assistant_turn(
        db_session,
        chat_session,
        [
            {
                "id": "tc-edit",
                "name": "edit_file",
                "arguments": {"id": chapter.id, "edits": []},
                "status": "success",
                "result": {
                    "id": chapter.id,
                    "mutation_applied": True,
                    "undo": {
                        "before_version_number": 1,
                        "expected_after_updated_at": normalize_datetime_to_utc(chapter.updated_at).isoformat(),
                        "expected_after_content_sha256": content_sha256(chapter.content),
                    },
                },
            }
        ],
    )

    turn = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={},
        language="zh",
        author_message="改一下第一章",
        chat_session_id=chat_session.id,
    )
    assert chapter.id in turn.read_snapshots

    req_ctx = _make_tool_context(db_session, user, project, read_snapshots=turn.read_snapshots)
    try:
        # 模拟作者在本轮生成中途修改了该文件
        chapter.content = SAMPLE_BODY.replace("数到第七条船", "数到第九条船")
        chapter.updated_at = chapter.updated_at + timedelta(seconds=10)
        db_session.add(chapter)
        db_session.commit()

        raw = await mcp_tools.edit_file(
            {
                "id": chapter.id,
                "edits": [{"op": "replace", "old": "雨下了一整夜", "new": "雨停了"}],
            },
            request_context=req_ctx,
        )
        payload = json.loads(raw["content"][0]["text"])
        assert payload["status"] == "error"
        assert payload["error_type"] == "stale_content"
        assert "第九条船" in payload["error"]
    finally:
        req_ctx.clear()


def test_session_carryover_unchanged_streamed_write_chapter_and_compaction_boundary(db_session, env):
    user, project, other_project, chat_session = env
    before_compaction = _make_file(db_session, project, title="压缩前的章")
    after_compaction = _make_file(db_session, project, title="压缩后新写的章")
    foreign_file = _make_file(db_session, other_project, title="他山项目文件")
    deleted_file = _make_file(db_session, project, title="已删除章节", is_deleted=True)

    t0 = normalize_datetime_to_utc(after_compaction.updated_at).replace(tzinfo=None) + timedelta(seconds=1)
    first_msg = _add_assistant_turn(
        db_session,
        chat_session,
        [
            {
                "id": "tc-old",
                "name": "query_files",
                "arguments": {"ids": [before_compaction.id, foreign_file.id, deleted_file.id]},
                "status": "success",
                "result": [
                    {
                        "id": before_compaction.id,
                        "content": before_compaction.content,
                        "updated_at": before_compaction.updated_at.isoformat(),
                    },
                    {
                        "id": foreign_file.id,
                        "content": foreign_file.content,
                        "updated_at": foreign_file.updated_at.isoformat(),
                    },
                    {
                        "id": deleted_file.id,
                        "content": deleted_file.content,
                        "updated_at": deleted_file.updated_at.isoformat(),
                    },
                ],
            }
        ],
        created_at=t0,
    )
    _add_assistant_turn(
        db_session,
        chat_session,
        [
            {
                "id": "tc-new",
                "name": "write_chapter",
                "arguments": {"chapter": 2, "title": "压缩后新写的章", "file_type": "draft"},
                "status": "success",
                "result": {
                    "id": after_compaction.id,
                    "action": "created",
                    "awaiting_body": True,
                    "body_saved": True,
                },
            }
        ],
        created_at=t0 + timedelta(seconds=10),
        metadata={
            "engine": "loop",
            "compaction": {
                "summary": "已写完前文。",
                "covers_through_message_id": first_msg.id,
            },
        },
    )

    loader = SessionLoader(project_id=project.id, user_id=user.id)
    carried = loader.session_read_snapshots(db_session, chat_session_id=chat_session.id)
    assert after_compaction.id in carried
    assert before_compaction.id not in carried
    assert foreign_file.id not in carried
    assert deleted_file.id not in carried


def test_session_carryover_single_batched_select_and_cap_and_drop_on_compaction(db_session, env):
    user, project, _, chat_session = env
    files = [_make_file(db_session, project, title=f"第{i}章", content=f"正文 {i}") for i in range(25)]
    focus = _make_file(db_session, project, title="当前焦点", content="焦点全文")

    _add_assistant_turn(
        db_session,
        chat_session,
        [
            {
                "id": f"tc-{i}",
                "name": "query_files",
                "arguments": {"id": f.id},
                "status": "success",
                "result": [
                    {
                        "id": f.id,
                        "title": f.title,
                        "file_type": "draft",
                        "content": f.content,
                        "updated_at": f.updated_at.isoformat(),
                    }
                ],
            }
            for i, f in enumerate(files)
        ],
    )

    loader = SessionLoader(project_id=project.id, user_id=user.id)
    file_selects: list[str] = []

    def _on_sql(_conn, _cursor, statement, _params, _context, _execmany):
        lower = statement.lower()
        if lower.lstrip().startswith("select") and re.search(r"\bfrom\s+\"?file\"?\b", lower):
            file_selects.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _on_sql)
    try:
        carried = loader.session_read_snapshots(db_session, chat_session_id=chat_session.id)
    finally:
        event.remove(engine, "before_cursor_execute", _on_sql)

    assert len(carried) == SESSION_SNAPSHOT_MAX_FILES
    assert len(file_selects) == 1

    # drop_initial_read_snapshots 只撤销结转快照，保留 <turn_context> 里的焦点文件。
    turn = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": focus.id},
        language="zh",
        author_message="继续",
        chat_session_id=chat_session.id,
    )
    assert focus.id in turn.read_snapshots
    assert focus.id not in turn.session_carried_snapshot_ids
    assert len(turn.session_carried_snapshot_ids) == SESSION_SNAPSHOT_MAX_FILES

    req_ctx = _make_tool_context(db_session, user, project, read_snapshots=turn.read_snapshots)
    try:
        req_ctx.drop_initial_read_snapshots(turn.session_carried_snapshot_ids)
        assert req_ctx.has_seen_file(focus.id) is True
        for carried_id in turn.session_carried_snapshot_ids:
            assert req_ctx.has_seen_file(carried_id) is False
    finally:
        req_ctx.clear()


def test_session_carryover_ignores_partial_or_truncated_reads(db_session, env):
    user, project, _, chat_session = env
    partial_file = _make_file(db_session, project, title="范围读文件")
    truncated_file = _make_file(db_session, project, title="截断读文件")
    summary_file = _make_file(db_session, project, title="摘要读文件")

    _add_assistant_turn(
        db_session,
        chat_session,
        [
            {
                "id": "tc-1",
                "name": "query_files",
                "arguments": {"id": partial_file.id, "tail_chars": 20},
                "status": "success",
                "result": [
                    {
                        "id": partial_file.id,
                        "content": partial_file.content[-20:],
                        "content_partial": True,
                        "updated_at": partial_file.updated_at.isoformat(),
                    }
                ],
            },
            {
                "id": "tc-2",
                "name": "query_files",
                "arguments": {"id": truncated_file.id},
                "status": "success",
                "result": [
                    {
                        "id": truncated_file.id,
                        "content": truncated_file.content[:20],
                        "content_truncated": True,
                        "updated_at": truncated_file.updated_at.isoformat(),
                    }
                ],
            },
            {
                "id": "tc-3",
                "name": "query_files",
                "arguments": {"id": summary_file.id, "response_mode": "summary"},
                "status": "success",
                "result": [
                    {
                        "id": summary_file.id,
                        "content_preview": summary_file.content[:20],
                        "updated_at": summary_file.updated_at.isoformat(),
                    }
                ],
            },
        ],
    )

    carried = SessionLoader(project_id=project.id, user_id=user.id).session_read_snapshots(
        db_session, chat_session_id=chat_session.id
    )
    assert carried == {}


# =====================================================================
# Rank 6: Batch query_files(ids=[...])
# =====================================================================


async def test_query_files_batch_ids_single_sql_preserves_order_and_tracks_snapshots(db_session, env):
    user, project, _, _ = env
    f1 = _make_file(db_session, project, title="第一章", content="第一章全文。", with_ai_version=False)
    f2 = _make_file(db_session, project, title="第二章", content="第二章全文。", with_ai_version=False)
    f3 = _make_file(db_session, project, title="第三章", content="第三章全文。", with_ai_version=False)
    f4 = _make_file(db_session, project, title="第四章", content="第四章全文。", with_ai_version=False)

    requested = [f3.id, f1.id, f4.id, f2.id]
    file_selects: list[str] = []

    def _on_sql(_conn, _cursor, statement, _params, _context, _execmany):
        lower = statement.lower()
        if lower.lstrip().startswith("select") and re.search(r"\bfrom\s+\"?file\"?\b", lower):
            file_selects.append(statement)

    req_ctx = _make_tool_context(db_session, user, project, read_snapshots={})
    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _on_sql)
    try:
        raw = await mcp_tools.query_files({"ids": requested}, request_context=req_ctx)
        payload = json.loads(raw["content"][0]["text"])
        assert payload["status"] == "success", payload
        assert "missing_ids" not in payload
        assert "truncated_ids" not in payload
        assert [row["id"] for row in payload["data"]] == requested
        assert [row["content"] for row in payload["data"]] == ["第三章全文。", "第一章全文。", "第四章全文。", "第二章全文。"]
        assert len(file_selects) == 1

        # 促进待生效读取后，每个返回的文件都已登记进 _ReadSnapshots，可做模糊 edit_file。
        req_ctx.promote_pending_reads()
        for f in (f1, f2, f3, f4):
            assert req_ctx.has_seen_file(f.id) is True
            assert req_ctx.read_snapshot(f.id) == f.content
    finally:
        event.remove(engine, "before_cursor_execute", _on_sql)
        req_ctx.clear()


async def test_query_files_batch_ids_dedupe_cap_missing_and_cross_project_isolation(db_session, env):
    user, project, other_project, _ = env
    files = [_make_file(db_session, project, title=f"文件{i}", content=f"内容{i}") for i in range(7)]
    other_file = _make_file(db_session, other_project, title="他山私密文件", content="绝不能泄露")
    deleted_file = _make_file(db_session, project, title="已删文件", content="已删除内容", is_deleted=True)

    req_ctx = _make_tool_context(db_session, user, project, read_snapshots={})
    try:
        # 1) 去重后正好 5 个：不截断
        dedupe_raw = await mcp_tools.query_files(
            {"ids": [files[0].id, files[1].id, files[0].id, files[2].id, files[3].id, files[4].id, files[1].id]},
            request_context=req_ctx,
        )
        dedupe_payload = json.loads(dedupe_raw["content"][0]["text"])
        assert dedupe_payload["status"] == "success"
        assert [r["id"] for r in dedupe_payload["data"]] == [files[i].id for i in range(5)]
        assert "truncated_ids" not in dedupe_payload

        # 2) 超过 5 个唯一 ID：截断到前 5 个并报告 truncated_ids
        cap_raw = await mcp_tools.query_files({"ids": [f.id for f in files]}, request_context=req_ctx)
        cap_payload = json.loads(cap_raw["content"][0]["text"])
        assert cap_payload["status"] == "success"
        assert [r["id"] for r in cap_payload["data"]] == [files[i].id for i in range(5)]
        assert cap_payload["truncated_ids"] == [files[5].id, files[6].id]
        assert "note" in cap_payload

        # 3) 跨项目隔离 + 已删除 + 不存在：仅返回本项目存活文件，其余进 missing_ids
        iso_raw = await mcp_tools.query_files(
            {"ids": [files[0].id, other_file.id, deleted_file.id, "nonexistent-id"]},
            request_context=req_ctx,
        )
        iso_payload = json.loads(iso_raw["content"][0]["text"])
        assert iso_payload["status"] == "success"
        assert [r["id"] for r in iso_payload["data"]] == [files[0].id]
        assert iso_payload["missing_ids"] == [other_file.id, deleted_file.id, "nonexistent-id"]
        assert "绝不能泄露" not in iso_raw["content"][0]["text"]

        # 4) 全部缺失 / 跨项目：返回 files_not_found 错误
        none_raw = await mcp_tools.query_files({"ids": [other_file.id, "missing-x"]}, request_context=req_ctx)
        none_payload = json.loads(none_raw["content"][0]["text"])
        assert none_payload["status"] == "error"
        assert none_payload["error_type"] == "files_not_found"
        assert none_payload["missing_ids"] == [other_file.id, "missing-x"]

        # 5) 校验错误：空列表、非列表、多 id 带范围参数
        empty_payload = json.loads((await mcp_tools.query_files({"ids": []}, request_context=req_ctx))["content"][0]["text"])
        assert empty_payload["status"] == "error"
        bad_type_payload = json.loads((await mcp_tools.query_files({"ids": "not-a-list"}, request_context=req_ctx))["content"][0]["text"])
        assert bad_type_payload["status"] == "error"
        range_payload = json.loads(
            (await mcp_tools.query_files({"ids": [files[0].id, files[1].id], "tail_chars": 10}, request_context=req_ctx))["content"][0]["text"]
        )
        assert range_payload["status"] == "error"

        # 6) 向后兼容：单个 id 行为不变（含未找到返回空 data）
        single_ok = json.loads((await mcp_tools.query_files({"id": files[0].id}, request_context=req_ctx))["content"][0]["text"])
        assert single_ok["status"] == "success" and [r["id"] for r in single_ok["data"]] == [files[0].id]
        single_miss = json.loads((await mcp_tools.query_files({"id": "missing-only"}, request_context=req_ctx))["content"][0]["text"])
        assert single_miss["status"] == "success" and single_miss["data"] == []
    finally:
        req_ctx.clear()


async def test_query_files_batch_truncated_oversize_does_not_record_snapshots(db_session, env, monkeypatch):
    user, project, _, _ = env
    f1 = _make_file(db_session, project, title="长文件一", content="甲" * 400)
    f2 = _make_file(db_session, project, title="长文件二", content="乙" * 400)
    monkeypatch.setattr(mcp_tools, "TOOL_RESULT_MAX_CHARS", 300)

    req_ctx = _make_tool_context(db_session, user, project, read_snapshots={})
    try:
        await mcp_tools.query_files({"ids": [f1.id, f2.id]}, request_context=req_ctx)
        req_ctx.promote_pending_reads()
        assert req_ctx.has_seen_file(f1.id) is False
        assert req_ctx.has_seen_file(f2.id) is False
    finally:
        req_ctx.clear()


def test_repeat_read_guard_tracks_batch_ids_per_file_and_shares_key_with_single_id():
    guard = RepeatReadGuard()

    def _ok_rows(*items: tuple[str, str]) -> str:
        return json.dumps(
            {"status": "success", "data": [{"id": fid, "title": title, "content": "正文"} for fid, title in items]},
            ensure_ascii=False,
        )

    # 第 1 次：批量读 f1、f2
    p1 = guard.plan("query_files", json.dumps({"ids": ["f1", "f2"]}))
    assert p1.blocked_output is None
    out1 = json.loads(guard.observe("query_files", p1, _ok_rows(("f1", "第一章"), ("f2", "第二章"))))
    assert "read_hint" not in out1
    assert set(guard.files_read) == {"f1", "f2"}

    # 第 2 次：单 id 读 f1，共享同一个 ReadKey → 触发第 2 次提示
    p2 = guard.plan("query_files", json.dumps({"id": "f1"}))
    out2 = json.loads(guard.observe("query_files", p2, _ok_rows(("f1", "第一章"))))
    assert out2["repeated_reads"] == 2
    assert "已读取过" in out2["read_hint"]

    # 第 3 次：批量读 f1、f2 → f1 到第 3 次（强警告），f2 到第 2 次
    p3 = guard.plan("query_files", json.dumps({"ids": ["f1", "f2"]}))
    out3 = json.loads(guard.observe("query_files", p3, _ok_rows(("f1", "第一章"), ("f2", "第二章"))))
    assert out3["repeated_reads"] == 3
    assert "警告" in out3["read_hint"]

    # 第 4 次：批量读 f1（已达上限）和 f3（首次）→ 仅跳过 f1、改写参数只查 f3
    p4 = guard.plan("query_files", json.dumps({"ids": ["f1", "f3"]}))
    assert p4.blocked_output is None
    assert json.loads(p4.arguments)["ids"] == ["f3"]
    out4 = json.loads(guard.observe("query_files", p4, _ok_rows(("f3", "第三章"))))
    assert out4["blocked_ids"] == ["f1"]
    assert "第三章" in guard.files_read.values()
    assert "read_hint" in out4

    # 写入 f1 后，只清零 f1 计数，不影响 f2
    w_plan = guard.plan("edit_file", json.dumps({"id": "f1", "edits": []}))
    guard.observe("edit_file", w_plan, json.dumps({"status": "success", "mutation_applied": True, "data": {"id": "f1"}}))
    p5 = guard.plan("query_files", json.dumps({"id": "f1"}))
    assert p5.blocked_output is None
    out5 = json.loads(guard.observe("query_files", p5, _ok_rows(("f1", "第一章"))))
    assert "read_hint" not in out5

    # f2 读到第 3 次后，再批量只读 f2 → 整次被拦下
    p6 = guard.plan("query_files", json.dumps({"ids": ["f2"]}))
    guard.observe("query_files", p6, _ok_rows(("f2", "第二章")))
    p7 = guard.plan("query_files", json.dumps({"ids": ["f2"]}))
    assert p7.blocked_output is not None
    assert json.loads(p7.blocked_output)["error_type"] == "repeated_read"


# =====================================================================
# Rank 5 & 6: Prompt and schema contract assertions
# =====================================================================


@pytest.mark.unit
def test_batch_edit_and_start_char_guidance_and_ids_schema():
    edit_props = EDIT_FILE_TOOL["input_schema"]["properties"]
    continue_desc = edit_props["continue_on_error"]["description"]
    edits_desc = edit_props["edits"]["description"]
    approx_advice = _approximate_not_found_advice({}, label="old")
    recovery_hint = json.loads(
        _with_recovery_hint(json.dumps({"status": "error", "error": "x"}), "anchor_not_found", 2, 3)
    )["retry_hint"]

    for text in (
        LOOP_TOOL_USAGE_PROTOCOL,
        continue_desc,
        edits_desc,
        approx_advice,
        recovery_hint,
        _BODY_SAVED_CONTEXT,
        _BODY_SAVED_REPLIED_CONTEXT,
    ):
        assert "改动多时拆成几次" not in text
        assert "不要在一次调用里堆很多条" not in text
        assert "不在一次调用里堆很多条" not in text

    # start_char 仅用于超长文件 (>25000 字)
    from agent.tools.tool_schemas import READ_FILES_TOOL

    rf_props = READ_FILES_TOOL["input_schema"]["properties"]
    assert "25000" in READ_FILES_TOOL["description"]
    assert "25000" in rf_props["start_char"]["description"]
    assert "25000" in LOOP_TOOL_USAGE_PROTOCOL
    assert "25000" in approx_advice
    assert "30 处" in LOOP_TOOL_USAGE_PROTOCOL
    assert "30" in edits_desc

    # read_files batch ids schema
    assert "ids" in rf_props
    assert rf_props["ids"]["type"] == "array"
    assert rf_props["ids"]["items"]["type"] == "string"
    assert rf_props["ids"]["maxItems"] == 5
    assert "read_files(ids=[...])" in UNIFIED_EXECUTION_PROTOCOL
    assert "read_files(ids=[...])" in LOOP_TOOL_USAGE_PROTOCOL


# =====================================================================
# Verifier regression tests (#345)
# =====================================================================


def test_repeat_read_guard_partial_block_preserves_truncated_ids_and_blocked_ids_on_not_found():
    guard = RepeatReadGuard()

    def _ok_rows(*items: tuple[str, str]) -> str:
        return json.dumps(
            {"status": "success", "data": [{"id": fid, "title": title, "content": "正文"} for fid, title in items]},
            ensure_ascii=False,
        )

    # 先把 f1 读满 3 次，使第 4 次被拦
    for _ in range(3):
        p = guard.plan("query_files", json.dumps({"id": "f1"}))
        guard.observe("query_files", p, _ok_rows(("f1", "第一章")))

    # 1) 传 6 个 id 且 f1 被拦：改写后只查 f2..f5，observe 必须保留 truncated_ids=["f6"] 与 blocked_ids=["f1"]
    plan_partial = guard.plan("query_files", json.dumps({"ids": ["f1", "f2", "f3", "f4", "f5", "f6"]}))
    assert plan_partial.blocked_output is None
    assert json.loads(plan_partial.arguments)["ids"] == ["f2", "f3", "f4", "f5"]
    out_partial = json.loads(
        guard.observe(
            "query_files",
            plan_partial,
            _ok_rows(("f2", "二"), ("f3", "三"), ("f4", "四"), ("f5", "五")),
        )
    )
    assert out_partial["blocked_ids"] == ["f1"]
    assert out_partial["truncated_ids"] == ["f6"]
    assert "note" in out_partial
    assert "read_hint" in out_partial

    # 2) 部分拦截后剩余允许的 id 全部 files_not_found：仍保留 blocked_ids 与 truncated_ids
    plan_missing = guard.plan("query_files", json.dumps({"ids": ["f1", "missing-a", "missing-b", "missing-c", "missing-d", "f7"]}))
    assert plan_missing.blocked_output is None
    not_found_output = json.dumps(
        {
            "status": "error",
            "error_type": "files_not_found",
            "error": "未找到请求的任意文件",
            "missing_ids": ["missing-a", "missing-b", "missing-c", "missing-d"],
        },
        ensure_ascii=False,
    )
    out_missing = json.loads(guard.observe("query_files", plan_missing, not_found_output))
    assert out_missing["status"] == "error"
    assert out_missing["blocked_ids"] == ["f1"]
    assert out_missing["truncated_ids"] == ["f7"]
    assert "read_hint" in out_missing
    assert "note" in out_missing

    # 3) 前 5 个全部被拦且第 6 个被截断：blocked_output 同样保留 truncated_ids
    guard2 = RepeatReadGuard()
    for fid in ("a1", "a2", "a3", "a4", "a5"):
        for _ in range(3):
            p = guard2.plan("query_files", json.dumps({"id": fid}))
            guard2.observe("query_files", p, _ok_rows((fid, fid)))
    plan_all_blocked = guard2.plan("query_files", json.dumps({"ids": ["a1", "a2", "a3", "a4", "a5", "a6"]}))
    assert plan_all_blocked.blocked_output is not None
    blocked_payload = json.loads(plan_all_blocked.blocked_output)
    assert blocked_payload["error_type"] == "repeated_read"
    assert blocked_payload["truncated_ids"] == ["a6"]


def test_session_carryover_respects_user_row_compaction_carried_compaction_and_skips_legacy_rows(db_session, env):
    user, project, _, chat_session = env
    pre_user_compaction = _make_file(db_session, project, title="用户行压缩前文件")
    legacy_file = _make_file(db_session, project, title="旧图引擎文件")
    focus_file = _make_file(db_session, project, title="当前焦点文件")

    t0 = normalize_datetime_to_utc(focus_file.updated_at).replace(tzinfo=None) + timedelta(seconds=1)
    # 1) 旧图引擎（非 engine="loop"）的 assistant 行：只回放面包屑无原文，不能结转
    _add_assistant_turn(
        db_session,
        chat_session,
        [
            {
                "id": "tc-legacy",
                "name": "query_files",
                "arguments": {"id": legacy_file.id},
                "status": "success",
                "result": [
                    {
                        "id": legacy_file.id,
                        "content": legacy_file.content,
                        "updated_at": legacy_file.updated_at.isoformat(),
                    }
                ],
            }
        ],
        created_at=t0,
        metadata={"stop_reason": "end_turn"},
    )
    loader = SessionLoader(project_id=project.id, user_id=user.id)
    assert loader.session_read_snapshots(db_session, chat_session_id=chat_session.id) == {}

    # 2) 单循环 assistant 行之后出现一条带有效 compaction 的 user 行（无助手回复的取消/失败轮）：
    #    必须在该 user 行处停下，绝不结转其之前的单循环文件。
    loop_msg = _add_assistant_turn(
        db_session,
        chat_session,
        [
            {
                "id": "tc-loop",
                "name": "query_files",
                "arguments": {"id": pre_user_compaction.id},
                "status": "success",
                "result": [
                    {
                        "id": pre_user_compaction.id,
                        "content": pre_user_compaction.content,
                        "updated_at": pre_user_compaction.updated_at.isoformat(),
                    }
                ],
            }
        ],
        created_at=t0 + timedelta(seconds=5),
        metadata={"engine": "loop"},
    )
    assert pre_user_compaction.id in loader.session_read_snapshots(db_session, chat_session_id=chat_session.id)

    # 2a) retry_unanswered 传入 carried_compaction 时，build_loop_turn_context 直接跳过会话结转（仍保留焦点文件）
    turn_with_carried = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": focus_file.id},
        language="zh",
        author_message="重试一下",
        chat_session_id=chat_session.id,
        carried_compaction={
            "compaction": {
                "summary": "已压缩摘要。",
                "covers_through_message_id": loop_msg.id,
            }
        },
    )
    assert focus_file.id in turn_with_carried.read_snapshots
    assert pre_user_compaction.id not in turn_with_carried.read_snapshots
    assert turn_with_carried.session_carried_snapshot_ids == set()

    # 2b) 压缩记录落在 user 行上时，session_read_snapshots 遇到该 user 行立即停止
    compacted_user_row = ChatMessage(
        session_id=chat_session.id,
        role="user",
        content="继续",
        message_metadata=json.dumps(
            {
                "compaction": {
                    "summary": "已压缩摘要。",
                    "covers_through_message_id": loop_msg.id,
                }
            },
            ensure_ascii=False,
        ),
        created_at=t0 + timedelta(seconds=10),
    )
    db_session.add(compacted_user_row)
    db_session.commit()
    assert loader.session_read_snapshots(db_session, chat_session_id=chat_session.id) == {}


def test_session_carryover_undo_write_requires_sha256_and_rejects_undone_cards(db_session, env):
    user, project, _, chat_session = env
    ts_only_file = _make_file(db_session, project, title="仅时间戳卡片")
    undone_file = _make_file(db_session, project, title="已撤销卡片")
    replaced_undone_file = _make_file(db_session, project, title="已撤销整章替换")

    _add_assistant_turn(
        db_session,
        chat_session,
        [
            # 更早的 query_files：应被后面较新的不可验证/已撤销写卡片挡住，不能退回旧读记录
            {
                "id": "tc-old-read-1",
                "name": "query_files",
                "arguments": {"ids": [ts_only_file.id, undone_file.id, replaced_undone_file.id]},
                "status": "success",
                "result": [
                    {
                        "id": ts_only_file.id,
                        "content": ts_only_file.content,
                        "updated_at": ts_only_file.updated_at.isoformat(),
                    },
                    {
                        "id": undone_file.id,
                        "content": undone_file.content,
                        "updated_at": undone_file.updated_at.isoformat(),
                    },
                    {
                        "id": replaced_undone_file.id,
                        "content": replaced_undone_file.content,
                        "updated_at": replaced_undone_file.updated_at.isoformat(),
                    },
                ],
            },
            # 1) 仅有 expected_after_updated_at、缺少 expected_after_content_sha256 → fail closed
            {
                "id": "tc-ts-only",
                "name": "edit_file",
                "arguments": {"id": ts_only_file.id, "edits": []},
                "status": "success",
                "result": {
                    "id": ts_only_file.id,
                    "mutation_applied": True,
                    "undo": {
                        "before_version_number": 1,
                        "expected_after_updated_at": normalize_datetime_to_utc(ts_only_file.updated_at).isoformat(),
                    },
                },
            },
            # 2) undo.undone=True 的 edit_file → 即使哈希匹配也必须 fail closed
            {
                "id": "tc-undone-edit",
                "name": "edit_file",
                "arguments": {"id": undone_file.id, "edits": []},
                "status": "success",
                "result": {
                    "id": undone_file.id,
                    "mutation_applied": True,
                    "undo": {
                        "before_version_number": 1,
                        "expected_after_updated_at": normalize_datetime_to_utc(undone_file.updated_at).isoformat(),
                        "expected_after_content_sha256": content_sha256(undone_file.content),
                        "undone": True,
                    },
                },
            },
            # 3) undo.undone=True 的 write_chapter(replaced) → fail closed
            {
                "id": "tc-undone-replace",
                "name": "write_chapter",
                "arguments": {"chapter": 3, "title": "第三章", "file_type": "draft"},
                "status": "success",
                "result": {
                    "id": replaced_undone_file.id,
                    "action": "replaced",
                    "body_saved": True,
                    "undo": {
                        "before_version_number": 1,
                        "expected_after_updated_at": normalize_datetime_to_utc(replaced_undone_file.updated_at).isoformat(),
                        "expected_after_content_sha256": content_sha256(replaced_undone_file.content),
                        "undone": True,
                    },
                },
            },
        ],
    )

    carried = SessionLoader(project_id=project.id, user_id=user.id).session_read_snapshots(
        db_session, chat_session_id=chat_session.id
    )
    assert carried == {}

