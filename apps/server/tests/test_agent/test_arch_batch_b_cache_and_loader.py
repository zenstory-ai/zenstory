"""Regression tests for Arch Batch B:
- P0-1: Compact UTF-8 JSON serialization in MessageManager (no \\uXXXX TOAST bloat)
- P0-4: Global Static Prefix -> Project Static Suffix system prompt ordering & stable _get_folder_ids
- P0-3 / P1-2: SessionLoader batched SQL, keyset pagination, FileVersion pre-query, and zero SAVEPOINTs on healthy paths
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from os.path import commonprefix
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlmodel import Session

from agent.core.message_manager import MessageManager
from agent.core.session_loader import (
    SESSION_WRITTEN_FILES_PAGE_ROWS,
    SessionLoader,
)
from agent.loop.prompts import LOOP_INPUT_LAYOUT_SECTION, LOOP_WORKING_RULES
from agent.prompts import get_genre_norms
from agent.prompts.base import (
    CONTENT_RED_LINES,
    IMPACT_ANALYSIS_EXAMPLES,
    LOOP_AUTHOR_REPLY_PROTOCOL,
    LOOP_TOOL_USAGE_PROTOCOL,
    OUTPUT_PROTOCOL,
    PRODUCT_IDENTITY_PROTOCOL,
    UNIFIED_EXECUTION_PROTOCOL,
)
from models import ChatMessage, ChatSession, File, Project, User
from services.features.file_version_service import FileVersionService


def _seed_user_and_project(db_session: Session, *, project_type: str = "novel") -> tuple[User, Project, ChatSession]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"arch_b_{suffix}",
        email=f"arch-b-{suffix}@example.test",
        hashed_password="unused",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    project = Project(name=f"测试作品_{suffix}", owner_id=user.id, project_type=project_type)
    db_session.add(project)
    db_session.flush()
    chat = ChatSession(user_id=user.id, project_id=project.id, title="测试会话", is_active=True)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(project)
    db_session.refresh(chat)
    return user, project, chat


# ---------------------------------------------------------------------------
# P0-1: UTF-8 compact JSON serialization in MessageManager
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_message_manager_serializes_utf8_compact_json_without_ascii_escaping() -> None:
    """P0-1: _serialize_tool_calls and _serialize_message_metadata must use ensure_ascii=False
    and compact separators=(',', ':') to cut Chinese TOAST payload size by ~50%."""
    manager = MessageManager(project_id="proj-utf8", user_id="user-utf8")
    chinese_body = "夜雨落在青石巷里，温梨收起纸伞，推开了补纸斋的木门。" * 30

    tool_calls = [
        {
            "id": "call-1",
            "name": "edit_file",
            "arguments": {"id": "file-1", "edits": [{"op": "replace", "old": "旧句", "new": chinese_body}]},
            "status": "success",
            "result": {"title": "第一章 雨夜", "summary": chinese_body},
            "error": None,
        }
    ]
    serialized_calls = manager._serialize_tool_calls(tool_calls)  # noqa: SLF001
    assert serialized_calls is not None
    assert "\\u" not in serialized_calls
    assert "第一章 雨夜" in serialized_calls
    assert '": "' not in serialized_calls
    assert json.loads(serialized_calls)[0]["result"]["title"] == "第一章 雨夜"
    legacy_calls_bytes = len(json.dumps(json.loads(serialized_calls)).encode("utf-8"))
    compact_calls_bytes = len(serialized_calls.encode("utf-8"))
    assert compact_calls_bytes < legacy_calls_bytes * 0.6

    serialized_meta = manager._serialize_message_metadata(  # noqa: SLF001
        stop_reason="end_turn",
        usage={"input_tokens": 100, "output_tokens": 50},
        display_events=[
            {"type": "thinking_content", "content": chinese_body},
            {"type": "content", "content": "已完成第一章润色。"},
        ],
        extra_metadata={"engine": "loop"},
    )
    assert serialized_meta is not None
    assert "\\u" not in serialized_meta
    assert "已完成第一章润色。" in serialized_meta
    assert '": "' not in serialized_meta
    legacy_meta_bytes = len(json.dumps(json.loads(serialized_meta)).encode("utf-8"))
    compact_meta_bytes = len(serialized_meta.encode("utf-8"))
    assert compact_meta_bytes < legacy_meta_bytes * 0.6


# ---------------------------------------------------------------------------
# P0-4: Global Static Prefix -> Project Static Suffix + stable _get_folder_ids
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs")
@pytest.mark.parametrize("project_type", ["novel", "short", "screenplay"])
def test_build_system_prompt_shares_full_global_static_prefix_across_projects(
    db_session: Session, project_type: str
) -> None:
    """P0-4: Two distinct projects of the same type share 100% of global static sections
    (BASE + GENRE_NORMS + language + LOOP_INPUT_LAYOUT + LOOP_WORKING_RULES + skill_catalog)
    before ANY project UUID or folder UUID appears."""
    user_a, proj_a, _ = _seed_user_and_project(db_session, project_type=project_type)
    user_b, proj_b, _ = _seed_user_and_project(db_session, project_type=project_type)

    catalog = "## 可用写作技能\n\n- **去AI味**: 去除常见AI表达痕迹\n- **节奏打磨**: 提升场景冲突密度"
    prompt_a = MessageManager(proj_a.id, user_a.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=catalog
    )
    prompt_b = MessageManager(proj_b.id, user_b.id).build_system_prompt(
        session=db_session, language="zh", skill_catalog=catalog
    )

    shared = commonprefix([prompt_a, prompt_b])

    # Every global static section must live inside the cross-project shared prefix!
    for static_block in (
        PRODUCT_IDENTITY_PROTOCOL,
        CONTENT_RED_LINES,
        OUTPUT_PROTOCOL,
        LOOP_AUTHOR_REPLY_PROTOCOL,
        UNIFIED_EXECUTION_PROTOCOL,
        LOOP_TOOL_USAGE_PROTOCOL.rstrip(),
        IMPACT_ANALYSIS_EXAMPLES,
        get_genre_norms(project_type),
        "## 回复语言",
        LOOP_INPUT_LAYOUT_SECTION,
        LOOP_WORKING_RULES,
        catalog,
    ):
        assert static_block in shared, f"Global static block fell after project UUIDs: {static_block[:60]!r}"

    # No project UUIDs may appear in the shared prefix, and project ID sits at the very end.
    assert proj_a.id not in shared
    assert proj_b.id not in shared
    assert prompt_a.endswith(f"## 当前项目 ID: {proj_a.id}")
    assert prompt_b.endswith(f"## 当前项目 ID: {proj_b.id}")
    assert prompt_a.index(catalog) < prompt_a.index("### 创建文件时的 parent_id") < prompt_a.index(proj_a.id)


@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs")
def test_folder_ids_and_system_prompt_hash_stay_immutable_across_mid_session_writes(
    db_session: Session,
) -> None:
    """P0-4: Creating/moving files into a duplicate canonical folder mid-session never flips
    folder_ids or changes system_prompt_sha256."""
    user, project, _ = _seed_user_and_project(db_session, project_type="novel")
    t0 = datetime(2026, 2, 1, 8, 0, 0)
    folder_old = File(
        id="root-draft-a",
        project_id=project.id,
        title="正文",
        file_type="folder",
        parent_id=None,
        created_at=t0,
    )
    folder_new = File(
        id=f"{project.id}-draft-folder",
        project_id=project.id,
        title="正文",
        file_type="folder",
        parent_id=None,
        created_at=t0 + timedelta(seconds=10),
    )
    db_session.add_all([folder_old, folder_new])
    db_session.commit()

    manager = MessageManager(project.id, user.id)
    prompt_before = manager.build_system_prompt(session=db_session, language="zh")
    sha_before = hashlib.sha256(prompt_before.encode("utf-8")).hexdigest()

    # Mid-session: add 10 draft files under the newer duplicate folder.
    for i in range(10):
        db_session.add(
            File(
                id=f"ch-{i}",
                project_id=project.id,
                title=f"第{i + 1}章",
                file_type="draft",
                parent_id=folder_new.id,
                content=f"第{i + 1}章正文",
            )
        )
    db_session.commit()

    prompt_after = manager.build_system_prompt(session=db_session, language="zh")
    sha_after = hashlib.sha256(prompt_after.encode("utf-8")).hexdigest()

    assert sha_after == sha_before
    assert manager._get_folder_ids(session=db_session, project_type="novel")["draft"] == folder_old.id  # noqa: SLF001


# ---------------------------------------------------------------------------
# P0-3 & P1-2: SessionLoader SQL batching, keyset pagination & zero SAVEPOINTs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_session_written_file_ids_uses_per_page_batched_file_query_early_stop_and_keyset(
    db_session: Session,
) -> None:
    """P0-3 / P1-1: session_written_file_ids batches SELECT File.id once per 20-row page (no N+1),
    uses keyset pagination (no non-zero OFFSET), and stops early once limit is satisfied."""
    user, project, chat = _seed_user_and_project(db_session)
    base_time = datetime(2026, 11, 20, 10, 0, 0)

    # Create 45 files across 45 assistant rows (> 2 pages of SESSION_WRITTEN_FILES_PAGE_ROWS=20).
    total_rows = SESSION_WRITTEN_FILES_PAGE_ROWS * 2 + 5
    expected_drafts_newest_first: list[str] = []
    for idx in range(total_rows):
        ftype = "draft" if idx % 5 != 0 else "outline"
        f = File(
            id=f"written-f-{idx:02d}",
            project_id=project.id,
            title=f"文件{idx}",
            file_type=ftype,
            content="正文",
        )
        db_session.add(f)
        if ftype == "draft":
            expected_drafts_newest_first.append(f.id)
        db_session.add(
            ChatMessage(
                id=f"msg-w-{idx:02d}",
                session_id=chat.id,
                role="assistant",
                content="完成",
                tool_calls=json.dumps(
                    [{"id": f"tc-{idx}", "name": "edit_file", "arguments": {"id": f.id}, "status": "success", "result": {"id": f.id}}],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                created_at=base_time + timedelta(seconds=idx),
            )
        )
    db_session.commit()
    expected_drafts_newest_first.reverse()

    engine = db_session.get_bind()
    loader = SessionLoader(project_id=project.id, user_id=user.id)

    # 1) Early stop: limit=10 is already satisfied by page 1 (16 drafts in newest 20 rows) ->
    #    ONLY 1 ChatMessage query and 1 batched File query (never scans pages 2–3!).
    early_sql: list[tuple[str, Any]] = []

    def _on_early_sql(_conn, _cursor, statement, params, _context, _execmany):
        early_sql.append((statement, params))

    event.listen(engine, "before_cursor_execute", _on_early_sql)
    try:
        early_result = loader.session_written_file_ids(
            db_session,
            chat_session_id=chat.id,
            limit=10,
            file_types=("draft", "script"),
        )
    finally:
        event.remove(engine, "before_cursor_execute", _on_early_sql)

    assert early_result == expected_drafts_newest_first[:10]
    early_msg_queries = [s for s, _p in early_sql if "from chat_message" in s.lower()]
    early_file_selects = [
        s for s, _p in early_sql
        if s.lstrip().lower().startswith("select") and re.search(r"\bfrom\s+\"?file\"?\b", s.lower())
    ]
    assert len(early_msg_queries) == 1, f"Expected early stop after 1 page, got {len(early_msg_queries)}"
    assert len(early_file_selects) == 1, f"Expected 1 batched File SELECT for page 1, got {len(early_file_selects)}"

    # 2) Multi-page scan: limit=25 spans 2 pages -> 2 ChatMessage keyset queries + 2 batched File SELECTs (not 40 N+1!).
    executed_sql: list[tuple[str, Any]] = []

    def _on_sql(_conn, _cursor, statement, params, _context, _execmany):
        executed_sql.append((statement, params))

    event.listen(engine, "before_cursor_execute", _on_sql)
    try:
        result = loader.session_written_file_ids(
            db_session,
            chat_session_id=chat.id,
            limit=25,
            file_types=("draft", "script"),
        )
    finally:
        event.remove(engine, "before_cursor_execute", _on_sql)

    assert result == expected_drafts_newest_first[:25]
    file_selects = [
        s for s, _p in executed_sql
        if s.lstrip().lower().startswith("select") and re.search(r"\bfrom\s+\"?file\"?\b", s.lower())
    ]
    assert len(file_selects) == 2, f"Expected 1 batched File SELECT per scanned page (2), got {len(file_selects)}"
    msg_queries = [(s, p) for s, p in executed_sql if "from chat_message" in s.lower()]
    assert len(msg_queries) == 2
    assert "chat_message.created_at <" in msg_queries[1][0].lower()
    assert "chat_message.id <" in msg_queries[1][0].lower()
    for stmt, params in msg_queries:
        if "offset ?" in stmt.lower():
            assert params[-1] == 0, f"Expected OFFSET 0 with keyset pagination, got {params!r}"


@pytest.mark.unit
def test_session_read_snapshots_uses_keyset_pagination_without_offset(db_session: Session) -> None:
    """P0-3: session_read_snapshots paginates across pages via keyset cursor with no OFFSET."""
    user, project, chat = _seed_user_and_project(db_session)
    base_time = datetime(2026, 11, 20, 12, 0, 0)
    old_file = File(
        id="snap-page2-file",
        project_id=project.id,
        title="早期读取章",
        file_type="draft",
        content="早期章节正文",
        updated_at=base_time,
    )
    db_session.add(old_file)
    db_session.commit()
    db_session.refresh(old_file)

    # Put the query_files turn BEFORE 45 empty user messages so it falls on page 2.
    db_session.add(
        ChatMessage(
            id="msg-snap-00",
            session_id=chat.id,
            role="assistant",
            content="已读取。",
            tool_calls=json.dumps(
                [
                    {
                        "id": "tc-old",
                        "name": "query_files",
                        "arguments": {"id": old_file.id},
                        "status": "success",
                        "result": [
                            {
                                "id": old_file.id,
                                "title": old_file.title,
                                "file_type": "draft",
                                "content": old_file.content,
                                "updated_at": old_file.updated_at.isoformat(),
                            }
                        ],
                    }
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            message_metadata=json.dumps({"engine": "loop"}, separators=(",", ":")),
            created_at=base_time + timedelta(seconds=1),
        )
    )
    for i in range(45):
        db_session.add(
            ChatMessage(
                id=f"msg-pad-{i:03d}",
                session_id=chat.id,
                role="user",
                content=f"继续 {i}",
                created_at=base_time + timedelta(seconds=10 + i),
            )
        )
    db_session.commit()

    executed_sql: list[tuple[str, Any]] = []

    def _on_sql(_conn, _cursor, statement, params, _context, _execmany):
        executed_sql.append((statement, params))

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _on_sql)
    try:
        carried = SessionLoader(project.id, user.id).session_read_snapshots(
            db_session, chat_session_id=chat.id
        )
    finally:
        event.remove(engine, "before_cursor_execute", _on_sql)

    assert carried == {old_file.id: old_file.content}
    msg_queries = [(s, p) for s, p in executed_sql if "from chat_message" in s.lower()]
    assert len(msg_queries) == 2
    assert "chat_message.created_at <" in msg_queries[1][0].lower()
    assert "chat_message.id <" in msg_queries[1][0].lower()
    for stmt, params in msg_queries:
        if "offset ?" in stmt.lower():
            assert params[-1] == 0, f"Expected OFFSET 0 with keyset pagination, got {params!r}"


@pytest.mark.unit
def test_author_edits_for_files_prequeries_versions_skips_unedited_and_emits_no_savepoints(
    db_session: Session,
) -> None:
    """P1-2: Pre-query FileVersion summary in 1 batched SQL, skip files without non-AI edits
    (while still catching unversioned author edits!), and emit ZERO SAVEPOINTs on healthy paths."""
    user, project, _ = _seed_user_and_project(db_session)
    versions = FileVersionService()

    # 1) 4 files with zero FileVersion rows
    no_ver_files = [
        File(id=f"nover-{i}", project_id=project.id, title=f"无版本{i}", file_type="draft", content=f"正文{i}")
        for i in range(4)
    ]
    # 2) 4 author-only / imported files (no AI versions)
    author_only_files = [
        File(id=f"orig-{i}", project_id=project.id, title=f"原稿{i}", file_type="draft", content=f"原稿{i}")
        for i in range(4)
    ]
    # 3) 4 pure-AI untouched files
    ai_untouched_files = [
        File(id=f"ai-clean-{i}", project_id=project.id, title=f"AI纯写{i}", file_type="draft", content=f"AI写下的第{i}章。")
        for i in range(4)
    ]
    # 4) 1 AI file edited by author with version
    ai_edited = File(id="ai-edited", project_id=project.id, title="手改章", file_type="draft", content="老周在收摊。")
    # 5) 1 AI file edited by author WITHOUT creating a new FileVersion (unversioned edit!)
    ai_unversioned = File(id="ai-unver", project_id=project.id, title="无版本手改章", file_type="draft", content="旧台词。")

    db_session.add_all([*no_ver_files, *author_only_files, *ai_untouched_files, ai_edited, ai_unversioned])
    db_session.commit()

    for f in author_only_files:
        versions.create_version(db_session, f.id, f.content, change_type="create", change_source="system")
        versions.create_version(db_session, f.id, f.content + "补充", change_type="edit", change_source="user")
        f.content = f.content + "补充"
        db_session.add(f)

    for f in ai_untouched_files:
        versions.create_version(db_session, f.id, f.content, change_type="create", change_source="ai")

    versions.create_version(db_session, ai_edited.id, "老周在收摊。", change_type="create", change_source="ai")
    ai_edited.content = "老秦在收摊。"
    db_session.add(ai_edited)
    db_session.commit()
    versions.create_version(db_session, ai_edited.id, "老秦在收摊。", change_type="edit", change_source="user")

    versions.create_version(db_session, ai_unversioned.id, "旧台词。", change_type="create", change_source="ai")
    ai_unversioned.content = "作者无版本手改后的新台词。"
    db_session.add(ai_unversioned)
    db_session.commit()

    all_ids = [
        *[f.id for f in no_ver_files],
        *[f.id for f in author_only_files],
        *[f.id for f in ai_untouched_files],
        ai_edited.id,
        ai_unversioned.id,
    ]
    executed_sql: list[str] = []

    def _on_sql(_conn, _cursor, statement, _params, _context, _execmany):
        executed_sql.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _on_sql)
    try:
        block, edited_ids, by_id = SessionLoader(project.id, user.id).author_edits_for_files(
            db_session, all_ids, {}
        )
    finally:
        event.remove(engine, "before_cursor_execute", _on_sql)

    assert edited_ids == {ai_edited.id, ai_unversioned.id}
    assert by_id is not None and len(by_id) == len(all_ids)
    assert ai_edited.id in block and "手改章" in block
    assert ai_unversioned.id in block and "无版本手改章" in block
    assert "自你上次写入后已被作者手动修改" in block

    # Zero SAVEPOINTs emitted on healthy paths!
    assert not any("savepoint" in s.lower() for s in executed_sql), f"Unexpected SAVEPOINT SQL: {executed_sql}"

    # 1 batched FileVersion metadata query (zero .content projection) + at most 1 batched
    # reconstruction query for the single equal-length candidate (ai_edited), never per-file N+1.
    fv_selects = [
        s for s in executed_sql if s.lstrip().lower().startswith("select") and "file_version" in s.lower()
    ]
    assert len(fv_selects) <= 2, f"Expected <= 2 batched FileVersion SQL queries, got {len(fv_selects)}"
    assert ".content" not in fv_selects[0].lower()


@pytest.mark.unit
def test_author_edits_prequery_catches_same_length_unversioned_none_ts_reviewed_and_system_restore(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0-1 regression: FileVersion pre-query must NEVER skip:
    (a) same-length unversioned edits ('林墨'->'陆沉') within 1s or with updated_at=None,
    (b) legacy create/user -> ai_edit/user/'AI edit (reviewed)' -> edit/user promotion chains,
    (c) create/ai -> restore/system chains that remove AI paragraphs."""
    user, project, _ = _seed_user_and_project(db_session)
    versions = FileVersionService()

    # (a1) Same-length unversioned edit within 500ms after AI write
    same_len_fast = File(
        id="same-len-fast",
        project_id=project.id,
        title="同长秒改",
        file_type="draft",
        content="林墨推门走进雨里。",
    )
    # (a2) Same-length unversioned edit when updated_at is None on loaded object (fail-closed)
    same_len_none_ts = File(
        id="same-len-none-ts",
        project_id=project.id,
        title="无时间戳同长改",
        file_type="draft",
        content="林墨推门走进雨里。",
    )
    # (b) Legacy create/user -> ai_edit/user/"AI edit (reviewed)" -> edit/user
    legacy_reviewed = File(
        id="legacy-reviewed",
        project_id=project.id,
        title="旧润色链",
        file_type="draft",
        content="作者原稿第一句。",
    )
    # (c) create/ai -> restore/system deleting an AI paragraph
    system_restore = File(
        id="sys-restore",
        project_id=project.id,
        title="系统恢复章",
        file_type="draft",
        content="第一段。\nAI写下的第二段。",
    )
    db_session.add_all([same_len_fast, same_len_none_ts, legacy_reviewed, system_restore])
    db_session.commit()

    v_fast = versions.create_version(
        db_session, same_len_fast.id, "林墨推门走进雨里。", change_type="create", change_source="ai"
    )
    same_len_fast.content = "陆沉推门走进雨里。"
    same_len_fast.updated_at = v_fast.created_at + timedelta(milliseconds=500)
    db_session.add(same_len_fast)

    v_none = versions.create_version(
        db_session, same_len_none_ts.id, "林墨推门走进雨里。", change_type="create", change_source="ai"
    )
    same_len_none_ts.content = "陆沉推门走进雨里。"
    same_len_none_ts.updated_at = v_none.created_at - timedelta(seconds=10)
    db_session.add(same_len_none_ts)
    db_session.commit()

    versions.create_version(
        db_session, legacy_reviewed.id, "作者原稿第一句。", change_type="create", change_source="user"
    )
    versions.create_version(
        db_session,
        legacy_reviewed.id,
        "AI润色后的第一句。",
        change_type="ai_edit",
        change_source="user",
        change_summary="AI edit (reviewed)",
    )
    versions.create_version(
        db_session, legacy_reviewed.id, "作者二次手改第一句。", change_type="edit", change_source="user"
    )
    legacy_reviewed.content = "作者二次手改第一句。"
    db_session.add(legacy_reviewed)

    versions.create_version(
        db_session, system_restore.id, "第一段。\nAI写下的第二段。", change_type="create", change_source="ai"
    )
    versions.create_version(
        db_session, system_restore.id, "第一段。", change_type="restore", change_source="system"
    )
    system_restore.content = "第一段。"
    db_session.add(system_restore)
    db_session.commit()

    # Simulate updated_at=None on same_len_none_ts when read from session (column is NOT NULL in SQLite).
    real_exec = db_session.exec

    class _NullUpdatedAtResult:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def all(self) -> list[Any]:
            rows = self._inner.all()
            for row in rows:
                if isinstance(row, File) and row.id == same_len_none_ts.id:
                    row.updated_at = None  # type: ignore[assignment]
            return rows

        def first(self) -> Any:
            return self._inner.first()

    def wrapped_exec(statement: Any, *args: Any, **kwargs: Any) -> Any:
        return _NullUpdatedAtResult(real_exec(statement, *args, **kwargs))

    monkeypatch.setattr(db_session, "exec", wrapped_exec)

    ordered = [same_len_fast.id, same_len_none_ts.id, legacy_reviewed.id, system_restore.id]
    block, edited_ids, _by_id = SessionLoader(project.id, user.id).author_edits_for_files(
        db_session, ordered, {}
    )

    assert edited_ids == set(ordered)
    for fid in ordered:
        assert fid in block
    assert "自你上次写入后已被作者手动修改" in block


@pytest.mark.unit
def test_author_edits_rollback_isolation_survives_orm_expiration(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 regression: when the batched FileVersion query triggers a DB error + session.rollback()
    on a session with expire_on_commit=True, author_edits_for_files degrades cleanly to FAILED_LINE
    without firing lazy SELECT file.* refreshes on expired ORM instances."""
    from agent.tools.author_changes import FAILED_LINE

    user, project, _ = _seed_user_and_project(db_session)
    versions = FileVersionService()

    f1 = File(id="iso-f1", project_id=project.id, title="故障章", file_type="draft", content="旧一。")
    f2 = File(id="iso-f2", project_id=project.id, title="正常章", file_type="draft", content="老周在收摊。")
    db_session.add_all([f1, f2])
    db_session.commit()

    versions.create_version(db_session, f1.id, "旧一。", change_type="create", change_source="ai")
    versions.create_version(db_session, f1.id, "新一。", change_type="edit", change_source="user")
    f1.content = "新一。"
    versions.create_version(db_session, f2.id, "老周在收摊。", change_type="create", change_source="ai")
    versions.create_version(db_session, f2.id, "老秦在收摊。", change_type="edit", change_source="user")
    f2.content = "老秦在收摊。"
    db_session.add_all([f1, f2])
    db_session.commit()

    bind = db_session.get_bind()
    file_selects: list[str] = []

    def _on_sql(_conn, _cursor, statement, _params, _context, _execmany):
        lower = statement.lower()
        if lower.lstrip().startswith("select") and re.search(r"\bfrom\s+\"?file\"?\b", lower):
            file_selects.append(statement)

    # Use a fresh Session with expire_on_commit=True (production default) so rollback() expires ORM rows.
    with Session(bind) as prod_session:
        assert prod_session.expire_on_commit is True
        real_exec = prod_session.exec
        exec_calls = {"n": 0}

        def flaky_exec(statement: Any, *args: Any, **kwargs: Any) -> Any:
            exec_calls["n"] += 1
            if exec_calls["n"] == 2:
                raise RuntimeError("simulated sql error on FileVersion metadata query")
            return real_exec(statement, *args, **kwargs)

        monkeypatch.setattr(prod_session, "exec", flaky_exec)
        event.listen(bind, "before_cursor_execute", _on_sql)
        try:
            block, edited_ids, by_id = SessionLoader(project.id, user.id).author_edits_for_files(
                prod_session, [f1.id, f2.id], {}
            )
        finally:
            event.remove(bind, "before_cursor_execute", _on_sql)

    assert by_id is not None and set(by_id) == {f1.id, f2.id}
    assert edited_ids == set()
    assert FAILED_LINE in block
    # Exactly ONE initial SELECT FROM file (zero lazy ORM reloads after session.rollback()!).
    assert len(file_selects) == 1, f"Expected 1 File SELECT (no lazy reloads after rollback), got {len(file_selects)}"
