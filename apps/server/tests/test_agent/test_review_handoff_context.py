"""Review and rework handoffs carry the text each role needs, so it is not re-read (#275)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.graph.writing_graph import REWORK_CURRENT_TEXT_HEADER, _review_payload_block
from tests.test_agent.body_write_events import edit_file_events
from tests.test_agent.test_author_reply_shaping import _handoff, _run_graph, _text, _writer_writes_chapter

PARA = "林舟沿着城墙走，雨水顺着青砖往下淌，他想起父亲留下的那把铜钥匙。"


def _collection(chapters: int, *, paras: int = 4) -> str:
    return "\n\n".join(f"第{n}章 旧城{n}\n" + PARA * paras for n in range(1, chapters + 1))


def test_appended_chapter_is_reviewed_with_the_two_chapters_before_it():
    before = _collection(40, paras=12)
    after = before + "\n\n第41章 门\n" + "门开了。" * 80

    block = _review_payload_block("旧城合集", after, before, max_chars=9000)

    assert "第39章 旧城39" in block and "第40章 旧城40" in block and "第41章 门" in block
    assert "第38章" not in block
    assert "改动之前的 2 个章节" in block and "不要整份重读" in block


def test_falls_back_to_a_short_window_when_the_chapters_do_not_fit():
    before = _collection(10, paras=200)  # each chapter far larger than the budget
    after = before + "\n\n第11章 门\n" + "门开了。" * 40

    block = _review_payload_block("旧城合集", after, before, max_chars=3000)

    assert "第11章 门" in block and "第9章" not in block
    assert "改动之前的" not in block and "前后少量原文" in block


def test_short_files_are_still_sent_whole():
    before = _collection(2)
    after = before + "\n\n第3章 门\n门开了。"

    block = _review_payload_block("短篇", after, before, max_chars=9000)

    assert block.endswith(after.strip()) and "没有附上" not in block


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rework_writer_gets_the_current_text_of_what_was_reviewed():
    messages: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        messages.append(state["user_message"])
        if agent_type == "writer" and len(messages) == 1:
            # The request-level write ledger records the chapter (the runner does this from tool results).
            state["repeat_read_guard"].files_written["draft-3"] = "第三章"
            for event in _writer_writes_chapter():
                yield event
            return
        if agent_type == "quality_reviewer":
            yield _handoff("writer", "把结尾那句换个写法")
            return
        for event in edit_file_events("fix-1", "她把炉门带上。", file_id="draft-3", title="第三章"):
            yield event
        yield _text("第三章结尾改好了。")

    payload = "[《第三章》编辑器字数 2781]\n当前落库的正文片段"
    with patch("agent.graph.writing_graph._review_payload_text", AsyncMock(return_value=payload)):
        await _run_graph(fake_agent)

    assert len(messages) == 3
    assert REWORK_CURRENT_TEXT_HEADER in messages[2] and "当前落库的正文片段" in messages[2]
    # The reviewer task message (the old copy) is still not replayed to the writer.
    assert "[质量检查任务]" not in messages[2]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rework_attaches_only_the_chapters_the_reviewer_named(db_session):
    """Two chapters share the 9000-char budget; rework gets the named one whole, not two excerpts."""
    from models import File, Project, User

    user = User(username="rework-focus", email="rework-focus@example.test", hashed_password="x", email_verified=True)
    project = Project(name="返修聚焦", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    five_body, six_body = "五" + PARA * 170, "六" + PARA * 170  # ~5.6k chars each: both together exceed 9000
    five = File(project_id=project.id, title="第五章", content=five_body, file_type="draft")
    six = File(project_id=project.id, title="第六章", content=six_body, file_type="draft")
    db_session.add_all([five, six])
    db_session.commit()
    messages: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        messages.append(state["user_message"])
        if agent_type == "writer" and len(messages) == 1:
            for chapter in (five, six):
                state["repeat_read_guard"]._record_write(chapter.id, chapter.title)
                for event in edit_file_events(f"w-{chapter.id}", chapter.content, file_id=chapter.id, title=chapter.title):
                    yield event
            return
        if agent_type == "quality_reviewer":
            yield _handoff("writer", "第六章结尾那句和第六章开头重复，换个写法")
            return
        yield _text("改好了。")

    await _run_graph(fake_agent, session=db_session, project_id=project.id, session_id="s")

    assert len(messages) == 3
    rework = messages[2].split(REWORK_CURRENT_TEXT_HEADER, 1)[1]
    assert six_body.strip() in rework  # the named chapter arrives whole, quotable for edit_file
    assert five_body.strip()[:200] not in rework
    assert "审稿意见没有提到 《第五章》" in rework
