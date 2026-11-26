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
])
def test_range_picks_exactly_the_requested_part(args, content, section):
    body = "第1章 雨\n甲甲甲\n\n第2章 风\n乙乙乙\n\n第13章 雪\n丙丙\n第3章 霜\n丁丁"

    row = apply_content_range({"id": "f", "content": body}, args)

    assert row["content"] == content
    assert row["content_partial"] is True and row["content_length"] == len(body)
    assert body[row["content_range"]["start"]:row["content_range"]["end"]] == content
    assert row.get("section") == section


def test_start_char_slices_long_file_but_returns_short_file_full():
    short_body = "第1章 雨\n甲甲甲\n\n第2章 风\n乙乙乙"
    short_row = apply_content_range({"id": "f", "content": short_body}, {"start_char": 2, "max_chars": 3})
    assert short_row["content"] == short_body
    assert short_row["content_partial"] is False
    assert short_row["content_range"] == {"start": 0, "end": len(short_body)}
    assert "note" not in short_row

    long_body = "第1章 雨\n" + ("甲" * 15000)
    long_row = apply_content_range({"id": "f", "content": long_body}, {"start_char": 2, "max_chars": 3})
    assert long_row["content"] == "章 雨"
    assert long_row["content_partial"] is True
    assert long_row["content_range"] == {"start": 2, "end": 5}


@pytest.mark.parametrize(("args", "message"), [
    ({"section": "第1章"}, "多个标题"),  # two 第1章 headings
    ({"tail_chars": 0}, "正整数"),
    ({"start_char": 999}, "超出正文长度"),
    ({"section": "第1章", "tail_chars": 5}, "一次只能用一个"),
])
def test_unreadable_ranges_are_errors_not_guesses(args, message):
    body = "第1章 上\n甲\n第1章 下\n乙"
    with pytest.raises(ContentRangeError, match=message):
        apply_content_range({"id": "f", "content": body}, args)


def test_missing_section_errors_on_long_file_but_returns_full_text_on_short_file():
    short_body = "第1章 上\n甲\n第2章 下\n乙"
    row = apply_content_range({"id": "f", "content": short_body}, {"section": "第9章"})
    assert row["content"] == short_body
    assert row["content_partial"] is False
    assert row["content_range"] == {"start": 0, "end": len(short_body)}
    assert "未找到" in row["note"]

    long_body = "第1章 上\n" + ("甲" * 15000) + "\n第2章 下\n乙"
    with pytest.raises(ContentRangeError, match="没有找到"):
        apply_content_range({"id": "f", "content": long_body}, {"section": "第9章"})


def test_whole_file_range_is_not_marked_partial():
    row = apply_content_range({"id": "f", "content": "短文"}, {"tail_chars": 100})
    assert row["content_partial"] is False and "note" not in row


def test_section_hierarchy_stops_only_at_same_or_higher_heading():
    outline = (
        "## 一、第一卷逐章大纲\n"
        "### 第1章 起势\n主角雨夜进城。\n\n"
        "### 第2章 试探\n茶楼对峙。\n\n"
        "## 二、第二卷逐章大纲\n"
        "### 第3章 破局\n"
    )
    row = apply_content_range({"id": "f", "content": outline}, {"section": "一、第一卷逐章大纲"})
    assert "### 第1章 起势" in row["content"]
    assert "### 第2章 试探" in row["content"]
    assert "## 二、第二卷逐章大纲" not in row["content"]


def test_section_bold_sublabels_do_not_truncate_parent_heading():
    lore = (
        "### 世界观总览\n"
        "**核心规则**\n"
        "灵气守恒，不可凭空造物。\n"
        "**代价**\n"
        "透支寿元。\n\n"
        "### 势力分布\n"
        "北境三宗。"
    )
    row = apply_content_range({"id": "f", "content": lore}, {"section": "世界观总览"})
    assert "**核心规则**" in row["content"]
    assert "透支寿元。" in row["content"]
    assert "### 势力分布" not in row["content"]


def test_section_narrative_line_starting_with_juan_number_does_not_truncate_chapter():
    body = (
        "第1章 夜雨\n"
        "窗外雷声滚滚。\n"
        "卷一角衣袖，他转身离开。\n"
        "身后灯火熄灭。\n\n"
        "第2章 天明\n"
        "晨雾散尽。"
    )
    row = apply_content_range({"id": "f", "content": body}, {"section": "第1章"})
    assert "卷一角衣袖，他转身离开。" in row["content"]
    assert "身后灯火熄灭。" in row["content"]
    assert "第2章 天明" not in row["content"]


def test_section_prefers_exact_heading_match_before_prefix_match():
    body = (
        "### 人物\n主角：林舟。\n\n"
        "### 人物关系\n林舟与陈明是旧识。\n\n"
        "### 地点\n青州港。"
    )
    for query in ("人物", "### 人物"):
        row = apply_content_range({"id": "f", "content": body}, {"section": query})
        assert row["section"] == "### 人物"
        assert "主角：林舟。" in row["content"]
        assert "### 人物关系" not in row["content"]


def test_content_range_error_carries_structured_reason():
    long_body = "第1章 上\n" + ("甲" * 15000) + "\n第1章 下\n乙"
    with pytest.raises(ContentRangeError) as not_found_exc:
        apply_content_range({"id": "f", "content": long_body}, {"section": "第9章"})
    assert not_found_exc.value.reason == "not_found"

    with pytest.raises(ContentRangeError) as ambiguous_exc:
        apply_content_range({"id": "f", "content": long_body}, {"section": "第1章"})
    assert ambiguous_exc.value.reason == "ambiguous"


@pytest.fixture
def owner_project(db_session, monkeypatch):
    user = User(username="range-owner", email="range-owner@example.test", hashed_password="x", email_verified=True)
    project = Project(name="范围读取", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    return user, project


def _file(session, project, content):
    file = File(project_id=project.id, title="合集", content=content, file_type="draft")
    session.add(file)
    session.commit()
    session.refresh(file)
    return file


async def _calls(session, user, project, calls, between=None):
    engine = session.get_bind()
    req_ctx = mcp_tools.AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id=None,
        create_session_func=lambda: Session(engine),
        read_snapshots={},
    )
    out = []
    try:
        for index, (tool, args) in enumerate(calls):
            req_ctx.promote_pending_reads()
            if between and index in between:
                between[index]()
            raw = await tool(args, request_context=req_ctx)
            out.append(json.loads(raw["content"][0]["text"]))
    finally:
        req_ctx.clear()
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
    req_ctx = mcp_tools.AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id=None,
        create_session_func=lambda: Session(engine),
        read_snapshots={},
    )
    try:
        await mcp_tools.query_files({"id": file.id, "tail_chars": 50}, request_context=req_ctx)
        req_ctx.promote_pending_reads()
        assert req_ctx.has_seen_file(file.id) is False
        assert req_ctx.read_snapshot(file.id) is not None
    finally:
        req_ctx.clear()
