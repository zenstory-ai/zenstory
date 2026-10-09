"""交接原因、审稿意见发给前端前换成作者口径（2026-10-09 审计 N5）。

模型写给下一个 agent 的交接说明里有文件 id、word_count=、[query_files]、「阻断 / 返工」。
前端把 HANDOFF 的 reason 拼进「接下来由{{agent}}继续：{{reason}}」，审查轮数用完时
审稿意见也会显示给作者。只改发给前端的那一份，交给下一个 agent 的内容保持原样。
"""

import re
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.author_facing_text import author_facing_handoff_reason, author_facing_text
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.tools.mcp_tools import ToolContext

FILE_UUID = "3f2a9c1e-1b2c-4d5e-8f90-123456789abc"
LONG_TEXT = "第三章的正文。" * 200
RAW_REASON = (
    f"存在阻断问题需返工：第3章（id={FILE_UUID}）word_count=812 不足 3000 字，"
    "[query_files] 显示 content_length=900"
)
RAW_CONTEXT = f"请 writer 返工第3章 file_id={FILE_UUID}：补写结尾，用 edit_file 追加"

INTERNAL_PATTERNS = (
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-"),
    re.compile(r"id\s*[=:]"),
    re.compile(r"word_count|content_length|query_files|edit_file"),
    re.compile(r"\bwriter\b|quality_reviewer"),
    re.compile("阻断|返工|返修|交接|送审"),
)


def _assert_author_facing(text: str) -> None:
    for pattern in INTERNAL_PATTERNS:
        assert not pattern.search(text), f"{pattern.pattern!r} leaked into {text!r}"


@pytest.mark.unit
class TestAuthorFacingText:
    def test_internal_fields_and_flow_words_are_removed(self):
        text = author_facing_handoff_reason(RAW_REASON)

        _assert_author_facing(text)
        assert text == "存在需要先修正的问题需要再改一轮：第3章不足 3000 字"

    def test_tool_names_with_their_verbs_and_bare_uuids_are_removed(self):
        assert author_facing_text("先调用 [query_files] 读第3章再改") == "先读第3章再改"
        assert (
            author_facing_text("第二章字数不够（word_count=1800，目标 3000），请补写。用 edit_file 追加。")
            == "第二章字数不够，请补写。"
        )
        assert author_facing_text(f"第2章 {FILE_UUID} 的对白重复") == "第2章的对白重复"
        assert author_facing_text("第3章节奏拖沓，[query_files] 显示。") == "第3章节奏拖沓。"

    def test_agent_ids_become_role_names(self):
        assert author_facing_text("交接给 writer 返工第二章") == "交给内容创作者再改一轮第二章"
        assert author_facing_text("请 quality_reviewer 送审《雾港来信》") == "请质量审稿人送去检查《雾港来信》"

    def test_system_reasons_use_fixed_author_wording(self):
        # 计划交接不写原因，前端只显示「接下来由X继续」。
        assert author_facing_handoff_reason("工作流自动交接") == ""
        assert author_facing_handoff_reason("自动质量门控") == "正文写好了，检查一遍质量"

    def test_plain_author_text_is_unchanged(self):
        for text in (
            "节奏拖沓，第三段需要压缩",
            "主角年龄前后矛盾（第1章 17 岁，第3章 19 岁）",
            "正文已写完，自动进入质量检查",
            "Note: the pacing drags in scene 2",
        ):
            assert author_facing_text(text) == text

    def test_list_items_keep_their_bullets(self):
        assert (
            author_facing_text(f"- 修正 id={FILE_UUID} 的错别字\n- 第二段对白重复")
            == "- 修正的错别字\n- 第二段对白重复"
        )

    def test_non_string_input_is_empty(self):
        assert author_facing_text(None) == ""
        assert author_facing_handoff_reason(None) == ""


def _text(text: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.TEXT, data={"text": text})


def _write_done(call_id: str) -> list[StreamEvent]:
    return [
        StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={"id": call_id, "name": "edit_file", "status": "complete"},
        ),
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": call_id,
                "name": "edit_file",
                "result": {"content": [{"type": "text", "text": '{"status": "success"}'}]},
            },
        ),
    ]


def _raw_handoff(target: str) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.HANDOFF,
        data={
            "target_agent": target,
            "reason": RAW_REASON,
            "context": RAW_CONTEXT,
            "handoff_packet": {
                "target_agent": target,
                "reason": RAW_REASON,
                "context": RAW_CONTEXT,
                "completed": [],
                "todo": [f"用 edit_file 补写第3章（id={FILE_UUID}）的结尾"],
                "evidence": ["word_count=812"],
            },
        },
    )


async def _run_graph(fake_agent, *, initial_agent="writer", router_result=None):
    from agent.graph.writing_graph import run_writing_workflow_streaming

    state = {"user_message": "写第三章", "messages": [], "system_prompt": ""}
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=router_result or {})),
            patch("agent.graph.writing_graph.get_next_node", return_value=initial_agent),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
            patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
            patch(
                "agent.graph.writing_graph._auto_finalize_task_board_on_completion",
                AsyncMock(return_value=[]),
            ),
        ):
            return [
                event
                async for event in run_writing_workflow_streaming(
                    state=state, thread_id="t", auto_review_threshold=100000
                )
            ]
    finally:
        ToolContext.clear_context()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handoff_event_reason_is_author_facing_but_next_agent_gets_raw_context():
    writer_messages: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        if agent_type == "quality_reviewer":
            yield _text("第3章结尾没写完。")
            yield _raw_handoff("writer")
        else:
            writer_messages.append(state["user_message"])
            yield _text("已补写结尾。[TASK_COMPLETE]")

    events = await _run_graph(fake_agent, initial_agent="quality_reviewer")

    handoffs = [e for e in events if e.type == StreamEventType.HANDOFF]
    assert len(handoffs) == 1
    data = handoffs[0].data
    _assert_author_facing(data["reason"])
    _assert_author_facing(data["context"])
    assert "第3章" in data["reason"]
    # 交接包不动：它是给下一个 agent 的。
    assert data["handoff_packet"]["reason"] == RAW_REASON
    assert data["handoff_packet"]["context"] == RAW_CONTEXT
    # writer 收到的交接信息保持原样，id 和待办都在。
    assert writer_messages, "writer 必须运行"
    assert FILE_UUID in writer_messages[0]
    assert "edit_file" in writer_messages[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_planned_handoff_has_no_internal_reason():
    async def fake_agent(state, agent_type, *_args, **_kwargs):
        if agent_type == "planner":
            yield _text("大纲写好了。")
        else:
            yield _text("第一章写好了。[TASK_COMPLETE]")

    events = await _run_graph(
        fake_agent,
        initial_agent="planner",
        router_result={
            "current_agent": "planner",
            "workflow_plan": "standard",
            "workflow_agents": ["writer"],
            "routing_metadata": {},
        },
    )

    handoffs = [e for e in events if e.type == StreamEventType.HANDOFF]
    assert [h.data["target_agent"] for h in handoffs] == ["writer"]
    assert handoffs[0].data["reason"] == ""
    _assert_author_facing(handoffs[0].data["context"])
    assert "planner" not in handoffs[0].data["context"]
    assert handoffs[0].data["handoff_packet"]["reason"] == "工作流自动交接"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_review_limit_notes_shown_to_author_are_author_facing(monkeypatch):
    from agent.graph.writing_graph import MAX_REVIEW_ROUNDS

    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "writer":
            for event in _write_done(f"w{len(calls)}"):
                yield event
            yield _text(LONG_TEXT)
            yield StreamEvent(
                type=StreamEventType.HANDOFF,
                data={"target_agent": "quality_reviewer", "reason": "请复审", "context": "请复审"},
            )
            return
        yield _text("结尾没写完。")
        yield _raw_handoff("writer")

    events = await _run_graph(fake_agent)

    assert calls == ["writer", "quality_reviewer"] * MAX_REVIEW_ROUNDS
    complete = [e for e in events if e.type == StreamEventType.WORKFLOW_COMPLETE]
    assert complete and complete[-1].data["reason"] == "review_round_limit"
    notes = complete[-1].data["review_notes"]
    _assert_author_facing(notes)
    assert "补写第3章" in notes
    surfaced = "".join(e.data.get("text", "") for e in events if e.type == StreamEventType.TEXT)
    assert FILE_UUID not in surfaced
    assert "edit_file" not in surfaced
