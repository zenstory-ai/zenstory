"""单循环 agent：工具集、系统提示、同章守卫、历史重建与压缩、运行循环。全部用替身模型，不调用付费模型。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.core.workflow_events import StreamEvent, StreamEventType
from models import ChatMessage, ChatSession, File, Project, User


def _user_project(db_session: Session, project_type: str = "novel") -> tuple[User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"loop-{suffix}@example.com",
        username=f"loop_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name=f"Loop {suffix}", owner_id=user.id, project_type=project_type)
    db_session.add(project)
    db_session.commit()
    return user, project


# ---------------------------------------------------------------------------
# 工具集
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_loop_toolset_excludes_multi_agent_control_flow():
    from agent.openai_agents.tools_adapter import build_agent_function_tools
    from agent.tools.registry import (
        LOOP_AGENT_TYPE,
        get_agent_tools,
        get_exposed_tools,
        validate_registry_alignment,
    )

    excluded = {"handoff_to_agent", "request_clarification", "parallel_execute"}
    exposed = {schema["name"] for schema in get_exposed_tools(LOOP_AGENT_TYPE)}
    permitted = {schema["name"] for schema in get_agent_tools(LOOP_AGENT_TYPE)}
    built = {tool.name for tool in build_agent_function_tools(LOOP_AGENT_TYPE)}

    for names in (exposed, permitted, built):
        assert not names & excluded
        assert {
            "create_file", "edit_file", "query_files", "update_project", "load_skill",
            "read_skill_resource", "delete_file", "rename_file",
        } <= names
    assert validate_registry_alignment()["invalid_loop_tools"] == []
    # 图的各角色仍看到完整清单（回滚路径不变）。
    assert excluded <= {schema["name"] for schema in get_exposed_tools("writer")}


# ---------------------------------------------------------------------------
# 系统提示
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_loop_system_prompt_merges_project_config_with_loop_rules(db_session, writing_prompt_configs):
    from agent.core.message_manager import AGENT_DIRECTIVE_HEADER, MessageManager
    from agent.loop.prompts import LOOP_WORKING_RULES
    from agent.prompts.base import TOOL_USAGE_PROTOCOL

    user, project = _user_project(db_session)
    manager = MessageManager(project_id=project.id, user_id=user.id)
    loop_prompt = manager.build_system_prompt(db_session, language="zh", loop=True)
    graph_prompt = manager.build_system_prompt(db_session, language="zh")

    assert LOOP_WORKING_RULES in loop_prompt
    for removed in ("handoff_to_agent", "parallel_execute", "request_clarification", AGENT_DIRECTIVE_HEADER, "[TASK_COMPLETE]"):
        assert removed not in loop_prompt
    # 自查与真实字数两条规则来自原快速模式指令，文字里不提模式。
    assert "自查" in loop_prompt and "实际多少字" in loop_prompt
    assert "快速模式" not in loop_prompt and "生成模式" not in loop_prompt
    # 项目类型的写作配置（数据库行）照样在前面。
    assert graph_prompt.split("\n", 1)[0] == loop_prompt.split("\n", 1)[0]
    # 图的提示逐字不变（含 parallel_execute 一节）。
    assert TOOL_USAGE_PROTOCOL in graph_prompt and "parallel_execute" in TOOL_USAGE_PROTOCOL


# ---------------------------------------------------------------------------
# 同一章只有一个文件
# ---------------------------------------------------------------------------


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_create_file_refuses_second_file_for_existing_chapter(db_session):
    from agent.tools.mcp_tools import CHAPTER_FILE_EXISTS_ERROR_TYPE, ToolContext, create_file

    user, project = _user_project(db_session)
    folder = File(project_id=project.id, title="正文", file_type="folder")
    db_session.add(folder)
    db_session.commit()
    existing = File(
        project_id=project.id, title="第3章 夜航", content="合成正文。", file_type="draft", parent_id=folder.id
    )
    db_session.add(existing)
    db_session.commit()

    ToolContext.set_context(session=db_session, user_id=user.id, project_id=project.id, session_id="loop-guard")
    ToolContext.set_current_agent("loop")
    try:
        refused = _payload(await create_file({"title": "第三章 夜航（新版）", "file_type": "draft", "parent_id": folder.id}))
        other_chapter = _payload(
            await create_file({"title": "第4章 靠岸", "file_type": "draft", "parent_id": folder.id, "content": "合成"})
        )
        other_folder = File(project_id=project.id, title="第二卷", file_type="folder")
        db_session.add(other_folder)
        db_session.commit()
        other_volume = _payload(
            await create_file({"title": "第3章 新卷", "file_type": "draft", "parent_id": other_folder.id, "content": "合成"})
        )
        outline = _payload(
            await create_file({"title": "第3章 细纲", "file_type": "outline", "parent_id": folder.id, "content": "合成"})
        )
    finally:
        ToolContext.clear_context()

    assert refused["status"] == "error"
    assert refused["error_type"] == CHAPTER_FILE_EXISTS_ERROR_TYPE
    assert refused["id"] == existing.id
    assert "edit_file" in refused["error"]
    assert other_chapter["status"] == "success"
    assert other_volume["status"] == "success"
    assert outline["status"] == "success"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_graph_roles_keep_existing_create_file_behaviour(db_session):
    from agent.tools.mcp_tools import ToolContext, create_file

    user, project = _user_project(db_session)
    db_session.add(File(project_id=project.id, title="第1章", content="合成正文。", file_type="draft"))
    db_session.commit()
    ToolContext.set_context(session=db_session, user_id=user.id, project_id=project.id, session_id="graph-guard")
    ToolContext.set_current_agent("writer")
    try:
        result = _payload(await create_file({"title": "第1章（另存）", "file_type": "draft", "content": "合成"}))
    finally:
        ToolContext.clear_context()
    assert result["status"] == "success"


# ---------------------------------------------------------------------------
# 历史重建
# ---------------------------------------------------------------------------


def _session_rows(db_session: Session, user: User, project: Project) -> ChatSession:
    chat = ChatSession(user_id=user.id, project_id=project.id, title="loop", is_active=True)
    db_session.add(chat)
    db_session.commit()
    return chat


def _add(db_session: Session, chat: ChatSession, role: str, content: str, *, offset: int, **fields) -> ChatMessage:
    from datetime import datetime, timedelta

    row = ChatMessage(
        session_id=chat.id,
        role=role,
        content=content,
        created_at=datetime(2026, 11, 1, 8, 0, 0) + timedelta(seconds=offset),
        **fields,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _loop_assistant(db_session, chat, content_before: str, content_after: str, *, offset: int, **metadata):
    tool_calls = [
        {
            "id": "provider-call-1",
            "name": "create_file",
            "arguments": {"title": "第1章", "file_type": "draft"},
            "status": "success",
            "result": {"id": "f-1", "title": "第1章", "awaiting_body": True, "next_step": "写正文", "content": ""},
            "error": None,
        },
        {"id": "provider-call-2", "name": "query_files", "arguments": {"id": "f-1"}, "status": "pending"},
    ]
    display_events = [
        {"type": "thinking_content", "content": "合成思考"},
        {"type": "content", "content": content_before},
        {"type": "tool_call", "tool_call_index": 0},
        {"type": "tool_call", "tool_call_index": 1},
        {"type": "content", "content": content_after},
    ]
    return _add(
        db_session,
        chat,
        "assistant",
        content_before + content_after,
        offset=offset,
        tool_calls=json.dumps(tool_calls, ensure_ascii=False),
        reasoning_content="合成思考",
        message_metadata=json.dumps(
            {"engine": "loop", "display_events": display_events, "stop_reason": "end_turn", **metadata},
            ensure_ascii=False,
        ),
    )


@pytest.mark.unit
def test_loop_history_replays_loop_rows_as_paired_tool_items_and_old_graph_rows_as_text(db_session):
    from agent.loop.history import load_loop_history

    user, project = _user_project(db_session)
    chat = _session_rows(db_session, user, project)
    _add(db_session, chat, "user", "旧会话：写个开头", offset=0)
    _add(
        db_session,
        chat,
        "assistant",
        "旧会话的回复",
        offset=1,
        message_metadata=json.dumps(
            {
                "routing": {"initial_agent": "writer", "last_agent": "quality_reviewer"},
                "display_events": [
                    {"type": "router_decided", "data": {}},
                    {"type": "agent_selected", "data": {"agent_type": "writer"}},
                    {"type": "content", "content": "旧会话的回复"},
                ],
            },
            ensure_ascii=False,
        ),
    )
    _add(db_session, chat, "user", "写第1章", offset=2)
    loop_row = _loop_assistant(db_session, chat, "开始。", "写好了。", offset=3, prompt_tokens=777)

    history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)

    assert history.last_message_id == loop_row.id
    assert history.last_prompt_tokens == 777
    assert history.author_messages == ["旧会话：写个开头", "写第1章"]
    items = history.items
    assert items[0] == {"role": "user", "content": "旧会话：写个开头"}
    assert items[1] == {"role": "assistant", "content": "旧会话的回复"}
    assert items[2] == {"role": "user", "content": "写第1章"}
    assert items[3] == {"role": "assistant", "content": "开始。"}
    calls = [item for item in items if item.get("type") == "function_call"]
    outputs = [item for item in items if item.get("type") == "function_call_output"]
    assert [c["call_id"] for c in calls] == [o["call_id"] for o in outputs] == [f"h{loop_row.id}_0", f"h{loop_row.id}_1"]
    create_output = json.loads(outputs[0]["output"])
    assert create_output["data"]["body_saved"] is True
    assert "next_step" not in create_output["data"] and "awaiting_body" not in create_output["data"]
    assert json.loads(outputs[1]["output"])["status"] == "unknown"
    assert items[-1] == {"role": "assistant", "content": "写好了。"}
    # 思考内容不跨轮回放。
    assert "合成思考" not in json.dumps(items, ensure_ascii=False)


@pytest.mark.unit
def test_loop_history_rebuilds_from_compaction_marker(db_session):
    from agent.loop.history import load_loop_history
    from agent.loop.prompts import SUMMARY_PREFIX

    user, project = _user_project(db_session)
    chat = _session_rows(db_session, user, project)
    _add(db_session, chat, "user", "很早的要求", offset=0)
    _loop_assistant(db_session, chat, "早期回复", "", offset=1)
    _add(db_session, chat, "user", "第二个要求", offset=2)
    covered = _loop_assistant(db_session, chat, "第二轮回复", "", offset=3)
    _add(db_session, chat, "user", "压缩后的第一条消息", offset=4)
    _loop_assistant(
        db_session,
        chat,
        "压缩后的回复",
        "",
        offset=5,
        compaction={"summary": "合成摘要", "covers_through_message_id": covered.id, "created_at": "2026-11-01T08:00:05"},
    )

    history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert history.compacted is True
    contents = [item.get("content") for item in history.items if item.get("role")]
    assert contents[:3] == ["很早的要求", "第二个要求", f"{SUMMARY_PREFIX}合成摘要"]
    assert contents[3] == "压缩后的第一条消息"
    assert "早期回复" not in json.dumps(history.items, ensure_ascii=False)
    assert "第二轮回复" not in json.dumps(history.items, ensure_ascii=False)

    # 作者原话的保留预算很小时只留最近的几条。
    small = load_loop_history(
        db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id, keep_user_tokens=5
    )
    small_contents = [item.get("content") for item in small.items if item.get("role")]
    assert small_contents[0] == f"{SUMMARY_PREFIX}合成摘要" or small_contents[0] == "第二个要求"
    assert "很早的要求" not in small_contents


@pytest.mark.unit
def test_loop_history_ignores_a_compaction_marker_whose_anchor_is_gone(db_session):
    from agent.loop.history import load_loop_history

    user, project = _user_project(db_session)
    chat = _session_rows(db_session, user, project)
    _add(db_session, chat, "user", "第一条", offset=0)
    _loop_assistant(
        db_session, chat, "回复", "", offset=1,
        compaction={"summary": "合成摘要", "covers_through_message_id": "missing-row", "created_at": "x"},
    )
    history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert history.compacted is False
    assert history.items[0] == {"role": "user", "content": "第一条"}


# ---------------------------------------------------------------------------
# 压缩
# ---------------------------------------------------------------------------


def _completion(content: str, finish_reason: str = "stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=900, completion_tokens=60, total_tokens=960, prompt_tokens_details=None),
    )


def _history(**kwargs):
    from agent.loop.history import LoopHistory

    return LoopHistory(
        items=[
            {"role": "user", "content": "写第1章"},
            {"type": "function_call", "call_id": "h1", "name": "query_files", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "h1", "output": "{}"},
            {"role": "assistant", "content": "写好了"},
        ],
        last_message_id="row-9",
        author_messages=["写第1章"],
        **kwargs,
    )


@pytest.mark.unit
def test_compaction_triggers_on_measured_prompt_tokens():
    from agent.loop.compaction import should_compact

    assert should_compact(_history(last_prompt_tokens=700_000), threshold=600_000) is True
    assert should_compact(_history(last_prompt_tokens=10_000), threshold=600_000) is False
    # 没有实测值时按历史估算兜底。
    assert should_compact(_history(), threshold=1) is True
    from agent.loop.history import LoopHistory

    assert should_compact(LoopHistory(), threshold=0) is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_compaction_returns_record_and_compacted_items():
    from agent.loop.compaction import compact_history
    from agent.loop.prompts import COMPACT_PROMPT, SUMMARY_PREFIX

    create = AsyncMock(return_value=(_completion("合成交接摘要"), SimpleNamespace(name="p", model="m")))
    with patch("agent.core.llm_failover.acreate_chat_completion", create):
        outcome = await compact_history(_history(last_prompt_tokens=700_000), system_prompt="系统", session_id="s")

    assert outcome.record["summary"] == "合成交接摘要"
    assert outcome.record["covers_through_message_id"] == "row-9"
    assert outcome.items == [
        {"role": "user", "content": "写第1章"},
        {"role": "user", "content": f"{SUMMARY_PREFIX}合成交接摘要"},
    ]
    kwargs = create.await_args.kwargs
    assert kwargs["tool_choice"] == "none"
    assert kwargs["session_id"] == "s"
    assert kwargs["messages"][0] == {"role": "system", "content": "系统"}
    assert kwargs["messages"][-1] == {"role": "user", "content": COMPACT_PROMPT}
    # 历史里的工具调用成对转成 chat 消息。
    assert any(m.get("role") == "tool" for m in kwargs["messages"])
    assert outcome.usage["output_tokens"] == 60


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    "responses,expected_calls",
    [
        ([_completion(""), _completion("   ")], 2),
        ([_completion("被截断的摘要", "length"), _completion("", "stop")], 2),
    ],
)
async def test_compaction_skips_when_summary_is_empty_or_truncated(responses, expected_calls):
    from agent.loop.compaction import SKIPPED_EMPTY_OR_TRUNCATED, compact_history

    provider = SimpleNamespace(name="p", model="m")
    create = AsyncMock(side_effect=[(response, provider) for response in responses])
    with patch("agent.core.llm_failover.acreate_chat_completion", create):
        outcome = await compact_history(_history(), system_prompt="系统", session_id="s")
    assert outcome.record is None and outcome.items is None
    assert outcome.skipped == SKIPPED_EMPTY_OR_TRUNCATED
    assert create.await_count == expected_calls
    # 第二次降低思考强度。
    assert create.await_args_list[0].kwargs["reasoning_effort"] is None
    assert create.await_args_list[1].kwargs["reasoning_effort"] == "low"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_compaction_retry_at_lower_effort_can_succeed():
    from agent.loop.compaction import compact_history

    provider = SimpleNamespace(name="p", model="m")
    create = AsyncMock(side_effect=[(_completion("截断", "length"), provider), (_completion("第二次的摘要"), provider)])
    with patch("agent.core.llm_failover.acreate_chat_completion", create):
        outcome = await compact_history(_history(), system_prompt="系统", session_id="s")
    assert outcome.record["summary"] == "第二次的摘要"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_compaction_provider_error_skips_compaction():
    from agent.loop.compaction import SKIPPED_PROVIDER_ERROR, compact_history

    with patch("agent.core.llm_failover.acreate_chat_completion", AsyncMock(side_effect=RuntimeError("synthetic"))):
        outcome = await compact_history(_history(), system_prompt="系统", session_id="s")
    assert outcome.skipped == SKIPPED_PROVIDER_ERROR and outcome.record is None


@pytest.mark.unit
def test_compaction_input_drops_oldest_items_without_orphan_outputs():
    from agent.loop.compaction import _bounded_items

    items = [
        {"type": "function_call", "call_id": "a", "name": "query_files", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "a", "output": "x" * 4000},
        {"role": "user", "content": "最近的消息"},
    ]
    bounded = _bounded_items(items, max_tokens=50)
    assert bounded == [{"role": "user", "content": "最近的消息"}]


# ---------------------------------------------------------------------------
# 运行循环
# ---------------------------------------------------------------------------


def _fake_runner(runs: list[list[StreamEvent]], seen: list[dict]):
    pending = list(runs)

    async def fake(*, state, agent_type, system_prompt, get_steering_messages=None, **_kwargs):
        seen.append({
            "agent_type": agent_type,
            "user_message": state.get("user_message"),
            "continue": state.get("continue_native_run"),
            "history_items": list(state.get("history_items") or []),
        })
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": agent_type})
        for event in pending.pop(0):
            yield event
        state["native_run_holder"].update(agent_type=agent_type, items=[{"role": "user", "content": "x"}])
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    return fake


async def _collect(state, *, get_steering_messages=None):
    from agent.loop.engine import run_loop_agent_streaming

    return [event async for event in run_loop_agent_streaming(state, get_steering_messages=get_steering_messages)]


@pytest.fixture
def loop_tool_context():
    from agent.tools.mcp_tools import ToolContext

    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    yield
    ToolContext.clear_context()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_runs_once_and_emits_no_router_or_agent_events(loop_tool_context):
    seen: list[dict] = []
    state = {"user_message": "写第1章", "messages": [], "system_prompt": "系统", "history_items": [{"role": "user", "content": "旧"}]}
    with patch(
        "agent.loop.engine.run_openai_agents_streaming_agent",
        _fake_runner([[StreamEvent(type=StreamEventType.TEXT, data={"text": "好的。"})]], seen),
    ):
        events = await _collect(state)

    assert [s["agent_type"] for s in seen] == ["loop"]
    assert seen[0]["history_items"] == [{"role": "user", "content": "旧"}]
    types = {event.type for event in events}
    assert not types & {
        StreamEventType.ROUTER_THINKING, StreamEventType.ROUTER_DECIDED, StreamEventType.AGENT_SELECTED,
        StreamEventType.HANDOFF, StreamEventType.WORKFLOW_COMPLETE, StreamEventType.CHAT_HOLD,
    }


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_continues_once_for_steering_that_arrived_during_the_run(loop_tool_context):
    from agent.graph.writing_graph import STEERING_FOLLOWUP_CONTEXT

    seen: list[dict] = []
    drains = iter([[{"id": "st-1", "content": "改成第一人称"}], [], []])

    async def steering():
        return next(drains, [])

    state = {"user_message": "写第1章", "messages": [], "system_prompt": "系统"}
    with patch(
        "agent.loop.engine.run_openai_agents_streaming_agent",
        _fake_runner([[StreamEvent(type=StreamEventType.TEXT, data={"text": "写好了。"})], []], seen),
    ):
        events = await _collect(state, get_steering_messages=steering)

    assert len(seen) == 2
    assert seen[1]["continue"] is True
    assert seen[1]["user_message"] == STEERING_FOLLOWUP_CONTEXT
    assert state["messages"][-1] == {"role": "user", "content": "改成第一人称"}
    assert any(getattr(event.type, "value", "") == "steering_received" for event in events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_stops_starting_runs_near_the_wall_clock(loop_tool_context):
    from agent.core.events import WALL_CLOCK_LIMIT
    from agent.core.run_meter import AgentRunMeter

    meter = AgentRunMeter(wrap_up_after_s=0)
    seen: list[dict] = []
    drains = iter([[{"id": "st-1", "content": "再加一段"}]])

    async def steering():
        return next(drains, [])

    state = {"user_message": "写第1章", "messages": [], "system_prompt": "系统", "run_meter": meter}
    with (
        patch("agent.loop.engine.run_openai_agents_streaming_agent", _fake_runner([[]], seen)),
        patch("agent.graph.writing_graph._ai_messages_left_today", return_value=True),
    ):
        events = await _collect(state, get_steering_messages=steering)

    assert len(seen) == 1
    [card] = [event for event in events if event.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card.data["limit"] == WALL_CLOCK_LIMIT
    assert card.data["layer"] == "collaboration"
    assert card.data["last_agent"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_continues_after_a_run_that_stopped_on_a_saved_file_body(loop_tool_context):
    from agent.graph.writing_graph import FILE_STREAM_CONTINUATION_MARKER

    seen: list[dict] = []
    create = [
        StreamEvent(type=StreamEventType.TOOL_USE, data={"id": "c1", "name": "create_file", "status": "complete", "input": {}}),
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={"tool_use_id": "c1", "name": "create_file", "result": {"content": [{"type": "text", "text": json.dumps({"status": "success"})}]}},
        ),
        StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n合成正文。\n</file>"}),
    ]
    state = {"user_message": "写第1章", "messages": [], "system_prompt": "系统"}
    with patch(
        "agent.loop.engine.run_openai_agents_streaming_agent",
        _fake_runner([create, [StreamEvent(type=StreamEventType.TEXT, data={"text": "第1章写好了。"})]], seen),
    ):
        await _collect(state)

    assert len(seen) == 2
    assert seen[1]["user_message"].startswith(FILE_STREAM_CONTINUATION_MARKER)
