"""query_files range reads for long files (#274)."""

import json

import pytest
from sqlmodel import Session

from agent.tools import mcp_tools
from agent.tools.file_ops.content_range import ContentRangeError, apply_content_range
from models import File, Project, User
from services.features.activation_event_service import activation_event_service

PARA = "雨还在下，她把信折好放回抽屉。"


def _collection(chapters: int) -> str:
    return "\n\n".join(f"第{n}章 雨夜{n}\n" + PARA * 30 for n in range(1, chapters + 1))


@pytest.mark.parametrize(("args", "content", "section"), [
    ({"section": "第3章"}, "第3章 霜\n丁丁", "第3章 霜"),
    ({"section": "第十三章"}, "第13章 雪\n丙丙\n", "第13章 雪"),
    ({"tail_chars": 4}, "霜\n丁丁", None),
    ({"start_char": 2, "max_chars": 3}, "章 雨", None),
])
def test_range_picks_exactly_the_requested_part(args, content, section):
    body = "第1章 雨\n甲甲甲\n\n第2章 风\n乙乙乙\n\n第13章 雪\n丙丙\n第3章 霜\n丁丁"

    row = apply_content_range({"id": "f", "content": body}, args)

    assert row["content"] == content
    assert row["content_partial"] is True and row["content_length"] == len(body)
    assert body[row["content_range"]["start"]:row["content_range"]["end"]] == content
    assert row.get("section") == section


@pytest.mark.parametrize(("args", "message"), [
    ({"section": "第9章"}, "没有找到"),
    ({"section": "第1章"}, "多个标题"),  # two 第1章 headings
    ({"tail_chars": 0}, "正整数"),
    ({"start_char": 999}, "超出正文长度"),
    ({"section": "第1章", "tail_chars": 5}, "一次只能用一个"),
])
def test_unreadable_ranges_are_errors_not_guesses(args, message):
    body = "第1章 上\n甲\n第1章 下\n乙"
    with pytest.raises(ContentRangeError, match=message):
        apply_content_range({"id": "f", "content": body}, args)


def test_whole_file_range_is_not_marked_partial():
    row = apply_content_range({"id": "f", "content": "短文"}, {"tail_chars": 100})
    assert row["content_partial"] is False and "note" not in row


@pytest.fixture
def owner_project(db_session, monkeypatch):
    user = User(username="range-owner", email="range-owner@example.test", hashed_password="x", email_verified=True)
    project = Project(name="范围读取", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **_kw: None)
    return user, project


def _file(session, project, content):
    file = File(project_id=project.id, title="合集", content=content, file_type="draft")
    session.add(file)
    session.commit()
    session.refresh(file)
    return file


async def _calls(session, user, project, calls, between=None):
    engine = session.get_bind()
    mcp_tools.ToolContext.set_context(None, user.id, project.id, None, create_session_func=lambda: Session(engine), read_snapshots={})
    out = []
    try:
        for index, (tool, args) in enumerate(calls):
            mcp_tools.ToolContext.promote_pending_reads()
            if between and index in between:
                between[index]()
            raw = await tool(args)
            out.append(json.loads(raw["content"][0]["text"]))
    finally:
        mcp_tools.ToolContext.clear_context()
    return out


@pytest.mark.parametrize("chapters", [8, 40])
async def test_section_read_stays_the_same_size_as_the_collection_grows(db_session, owner_project, chapters):
    user, project = owner_project
    file = _file(db_session, project, _collection(chapters))

    [payload] = await _calls(db_session, user, project, [(mcp_tools.query_files, {"id": file.id, "section": "第5章"})])

    [row] = payload["data"]
    assert row["content"].startswith("第5章 雨夜5") and "第6章" not in row["content"]
    assert len(row["content"]) < 600 and row["content_length"] == len(file.content)
    assert row["content_partial"] is True


async def test_author_edit_after_a_range_read_still_blocks_a_stale_edit(db_session, owner_project):
    user, project = owner_project
    file = _file(db_session, project, _collection(8))

    def author_edits():
        with Session(db_session.get_bind()) as other:
            row = other.get(File, file.id)
            row.content = row.content.replace("第8章 雨夜8", "第8章 作者改的标题")
            other.add(row)
            other.commit()

    _read, edit = await _calls(
        db_session, user, project,
        [
            (mcp_tools.query_files, {"id": file.id, "tail_chars": 200}),
            (mcp_tools.edit_file, {"id": file.id, "edits": [{"op": "append", "text": "\n\n第9章 新章"}]}),
        ],
        between={1: author_edits},
    )

    assert edit["status"] == "error"
    assert "stale_content" in json.dumps(edit, ensure_ascii=False)


async def test_range_read_allows_an_exact_edit_but_is_not_a_full_read(db_session, owner_project):
    user, project = owner_project
    file = _file(db_session, project, _collection(8))

    _read, edit = await _calls(
        db_session, user, project,
        [
            (mcp_tools.query_files, {"id": file.id, "section": "第8章"}),
            (mcp_tools.edit_file, {"id": file.id, "edits": [
                {"op": "replace", "old": "第8章 雨夜8", "new": "第8章 天亮"},
            ]}),
        ],
    )

    assert edit["status"] == "success"
    engine = db_session.get_bind()
    mcp_tools.ToolContext.set_context(None, user.id, project.id, None, create_session_func=lambda: Session(engine), read_snapshots={})
    try:
        await mcp_tools.query_files({"id": file.id, "tail_chars": 50})
        mcp_tools.ToolContext.promote_pending_reads()
        assert mcp_tools.ToolContext.has_seen_file(file.id) is False
        assert mcp_tools.ToolContext.read_snapshot(file.id) is not None
    finally:
        mcp_tools.ToolContext.clear_context()
