"""单循环 agent：工具集、系统提示、历史重建与压缩、运行循环。全部用替身模型，不调用付费模型。

按章号写正文（write_chapter）与 edit_file 的正文守卫见 test_loop_write_chapter.py。
"""

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
    from agent.tools.registry import LOOP_TOOL_NAMES, get_exposed_tools, validate_registry_alignment
    from agent.tools.tool_schemas import TOOL_SCHEMAS

    excluded = {"handoff_to_agent", "request_clarification", "parallel_execute"}
    exposed = [schema["name"] for schema in get_exposed_tools()]
    built = [tool.name for tool in build_agent_function_tools()]

    # 顺序就是请求里的工具清单顺序（前缀缓存依赖它）。
    assert exposed == built == LOOP_TOOL_NAMES == [
        "create_file", "write_chapter", "edit_file", "delete_file", "rename_file", "query_files",
        "update_project", "load_skill", "read_skill_resource",
    ]
    assert not excluded & set(TOOL_SCHEMAS)
    # 单循环 agent 看到的 create_file 不建正文。
    [loop_create] = [schema for schema in get_exposed_tools() if schema["name"] == "create_file"]
    assert not {"draft", "script"} & set(loop_create["input_schema"]["properties"]["file_type"]["enum"])
    assert validate_registry_alignment() == {"missing_schemas": [], "missing_handlers": []}


# ---------------------------------------------------------------------------
# 系统提示
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_loop_system_prompt_merges_project_config_with_loop_rules(db_session, writing_prompt_configs):
    from agent.core.message_manager import AGENT_DIRECTIVE_HEADER, MessageManager
    from agent.loop.prompts import LOOP_WORKING_RULES

    user, project = _user_project(db_session)
    manager = MessageManager(project_id=project.id, user_id=user.id)
    loop_prompt = manager.build_system_prompt(db_session, language="zh")

    assert LOOP_WORKING_RULES in loop_prompt
    for removed in ("handoff_to_agent", "parallel_execute", "request_clarification", AGENT_DIRECTIVE_HEADER, "[TASK_COMPLETE]"):
        assert removed not in loop_prompt
    # 自查与真实字数两条规则来自原快速模式指令，文字里不提模式。
    assert "自查" in loop_prompt and "实际多少字" in loop_prompt
    # 正文按章号经 write_chapter 写；不再有「用 edit_file 把稿子整体替换进该章」「old 写原文全文」的正文写法。
    assert "write_chapter" in loop_prompt and "大纲是节奏的上限也是底线" in loop_prompt
    assert "整体替换进该章" not in loop_prompt and "整章重写时用一个 replace" not in loop_prompt
    assert "不另存成素材、副本或备份文件" in loop_prompt
    # 录入作者资料：原文逐字存，AI 补充只放在单独标明的一节里并在回复里说明。
    assert "逐字存成" in loop_prompt and "AI 补充建议（待作者确认）" in loop_prompt
    assert "快速模式" not in loop_prompt and "生成模式" not in loop_prompt
    # 项目类型的写作配置（数据库行）在最前面。
    assert loop_prompt.split("\n", 1)[0] == "Test novel editor"


# ---------------------------------------------------------------------------
# 同一章只有一个文件
# ---------------------------------------------------------------------------


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


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
def test_loop_history_marks_rounds_that_stopped_before_finishing(db_session):
    from agent.loop.history import load_loop_history

    user, project = _user_project(db_session)
    chat = _session_rows(db_session, user, project)
    _add(db_session, chat, "user", "写第2章", offset=0)
    _add(
        db_session, chat, "assistant", "写到一半", offset=1,
        message_metadata=json.dumps({"engine": "loop", "stop_reason": "user_stopped",
                                     "display_events": [{"type": "content", "content": "写到一半"}]}),
    )
    history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert history.items[-2:] == [
        {"role": "assistant", "content": "写到一半"},
        {"role": "assistant", "content": "（这一轮没做完：作者停止了生成。）"},
    ]


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


@pytest.mark.unit
def test_loop_history_reads_a_skip_record_carried_from_a_retried_message(db_session):
    """重试删掉的作者消息上记着「没压缩成」：照样退避，不在重试时马上再付一次摘要。"""
    from agent.loop.history import load_loop_history

    user, project = _user_project(db_session)
    chat = _session_rows(db_session, user, project)
    _add(db_session, chat, "user", "第一条", offset=0)
    _loop_assistant(db_session, chat, "回复", "", offset=1)
    history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
        carried_metadata={"compaction_skipped": {"reason": "provider_error", "at_prompt_tokens": 4321}},
    )
    assert history.compaction_skipped_at_tokens == 4321
    assert history.compacted is False
    assert [item.get("content") for item in history.items if item.get("role")][:2] == ["第一条", "回复"]


def _author_text(turn: int, sentences: int) -> str:
    return "".join(f"第{turn}轮第{i}句：雾港的灯塔又亮了一次，信差把第{i}封信放在窗台上。" for i in range(sentences))


async def _compact_for_turn(history, keep: int, summary: str):
    from agent.loop.compaction import compact_history

    create = AsyncMock(return_value=(_completion(summary), SimpleNamespace(name="p", model="m")))
    with patch("agent.core.llm_failover.acreate_chat_completion", create):
        return await compact_history(history, system_prompt="系统", session_id="s", keep_user_tokens=keep)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("keep", [1500, 2000, 2500])
async def test_compacted_prefix_stays_byte_identical_into_later_turns(db_session, keep):
    """压缩那一轮发出的 [保留的作者原话][摘要]，之后每一轮重建的历史都逐字以它开头（前缀缓存）。

    前三轮的作者消息很长、之后很短：第二次压缩时最老的保留原话已经被第一次截断过、又只留得下一部分，
    按行重新截断会把截断点挪走，前缀从第 0 条起全部 miss。
    """
    from agents.models.chatcmpl_converter import Converter

    from agent.loop.history import load_loop_history

    user, project = _user_project(db_session)
    chat = _session_rows(db_session, user, project)

    def load():
        return load_loop_history(
            db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id, keep_user_tokens=keep
        )

    def messages(items):
        return json.loads(json.dumps(Converter.items_to_messages(items), ensure_ascii=False))

    sent_prefix: list | None = None
    checked = 0
    for turn in range(1, 11):
        history = load()
        if sent_prefix is not None:
            assert messages(history.items)[: len(sent_prefix)] == sent_prefix, f"turn {turn}"
            checked += 1
        metadata: dict = {}
        if turn in (4, 6, 8):
            outcome = await _compact_for_turn(history, keep, f"合成摘要{turn}")
            assert outcome.record is not None and outcome.items is not None
            metadata["compaction"] = outcome.record
            sent_prefix = messages(outcome.items)
        _add(db_session, chat, "user", _author_text(turn, 30 if turn <= 3 else 1), offset=turn * 10)
        _loop_assistant(db_session, chat, f"第{turn}轮回复", "", offset=turn * 10 + 1, **metadata)
    assert checked == 6


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stored_kept_author_messages_win_over_later_row_edits(db_session):
    """压缩点记下的作者原话原样回放：之后改了被覆盖的行，模型看到的前缀也不变。"""
    from agent.loop.history import load_loop_history
    from agent.loop.prompts import SUMMARY_PREFIX

    user, project = _user_project(db_session)
    chat = _session_rows(db_session, user, project)
    first = _add(db_session, chat, "user", "很早的要求", offset=0)
    covered = _loop_assistant(db_session, chat, "早期回复", "", offset=1)
    history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert history.last_message_id == covered.id
    outcome = await _compact_for_turn(history, 20_000, "合成摘要")
    assert outcome.record["kept_author_messages"] == ["很早的要求"]
    _add(db_session, chat, "user", "压缩后的消息", offset=2)
    _loop_assistant(db_session, chat, "压缩后的回复", "", offset=3, compaction=outcome.record)

    first.content = "改过的早期要求"
    db_session.add(first)
    db_session.commit()

    rebuilt = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    contents = [item.get("content") for item in rebuilt.items if item.get("role")]
    assert contents[:3] == ["很早的要求", f"{SUMMARY_PREFIX}合成摘要", "压缩后的消息"]
    assert rebuilt.author_messages == ["很早的要求", "压缩后的消息"]

    # 没有这个字段的旧压缩点照旧按行重算。
    legacy = {key: value for key, value in outcome.record.items() if key != "kept_author_messages"}
    _add(db_session, chat, "user", "再下一条", offset=4)
    _loop_assistant(db_session, chat, "回复", "", offset=5, compaction={**legacy, "kept_author_messages": "bad"})
    legacy_rebuilt = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    legacy_contents = [item.get("content") for item in legacy_rebuilt.items if item.get("role")]
    assert legacy_contents[0] == "改过的早期要求"


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
    from agent.loop.history import bounded_items as _bounded_items

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
    from agent.loop.run_support import STEERING_FOLLOWUP_CONTEXT

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
        patch("agent.loop.run_support._ai_messages_left_today", return_value=True),
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
    # 续跑说明相当于写正文的工具结果：已保存、先自查（edit_file 改正）、再回复作者。
    followup = seen[1]["user_message"]
    assert followup.startswith("[系统提醒] 上一次回复写完的正文已保存")
    assert "自查" in followup and "edit_file" in followup


# ---------------------------------------------------------------------------
# 复审修正的回归
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_loop_history_marks_skill_and_failed_results_as_not_kept(db_session):
    from agent.loop.history import load_loop_history

    user, project = _user_project(db_session)
    chat = _session_rows(db_session, user, project)
    _add(db_session, chat, "user", "按方法写", offset=0)
    tool_calls = [
        {
            "id": "c1",
            "name": "load_skill",
            "arguments": {"skill_id": "s-1"},
            "status": "success",
            "result": {"skill_id": "s-1", "skill_name": "合成技能", "instructions_chars": 900, "resources": []},
        },
        {
            "id": "c2",
            "name": "edit_file",
            "arguments": {"id": "f-1"},
            "status": "error",
            "result": None,
            "error": "没有找到要改的那段文字。",
        },
    ]
    _add(
        db_session,
        chat,
        "assistant",
        "好的",
        offset=1,
        tool_calls=json.dumps(tool_calls, ensure_ascii=False),
        message_metadata=json.dumps(
            {
                "engine": "loop",
                "display_events": [
                    {"type": "tool_call", "tool_call_index": 0},
                    {"type": "tool_call", "tool_call_index": 1},
                    {"type": "content", "content": "好的"},
                ],
            },
            ensure_ascii=False,
        ),
    )
    history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    outputs = [json.loads(item["output"]) for item in history.items if item.get("type") == "function_call_output"]
    assert "load_skill" in outputs[0]["note"]
    assert outputs[1]["status"] == "error" and "重新读取" in outputs[1]["note"]


@pytest.mark.unit
def test_loop_history_reads_skip_records_truncation_streak_and_user_row_markers(db_session):
    from agent.loop.history import load_loop_history
    from agent.loop.prompts import SUMMARY_PREFIX

    user, project = _user_project(db_session)
    chat = _session_rows(db_session, user, project)
    _add(db_session, chat, "user", "要求一", offset=0)
    first = _add(
        db_session, chat, "assistant", "半截", offset=1,
        message_metadata=json.dumps({"engine": "loop", "stop_reason": "output_truncated", "prompt_tokens": 700_000}),
    )
    _add(
        db_session, chat, "user", "继续", offset=2,
        message_metadata=json.dumps({"compaction_skipped": {"reason": "provider_error", "at_prompt_tokens": 700_000}}),
    )
    _add(
        db_session, chat, "assistant", "又半截", offset=3,
        message_metadata=json.dumps({"engine": "loop", "stop_reason": "output_truncated", "prompt_tokens": 710_000}),
    )
    history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert history.trailing_output_truncations == 2
    assert history.compaction_skipped_at_tokens == 700_000
    assert history.last_prompt_tokens == 710_000

    # 这一轮没有 assistant 行：压缩点记在作者那条消息上，下一轮照样从它重建。
    _add(
        db_session, chat, "user", "写第2章", offset=4,
        message_metadata=json.dumps({
            "compaction": {"summary": "合成摘要", "covers_through_message_id": first.id, "created_at": "x"},
        }),
    )
    rebuilt = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    contents = [item.get("content") for item in rebuilt.items]
    assert rebuilt.compacted is True
    assert f"{SUMMARY_PREFIX}合成摘要" in contents
    assert contents[-1] == "写第2章"
    # 成功的压缩点比没压缩成的记录新：不再退避。
    assert rebuilt.compaction_skipped_at_tokens == 0


@pytest.mark.unit
def test_compaction_backs_off_after_a_skipped_attempt():
    from agent.loop.compaction import should_compact, skipped_record

    skipped = _history(last_prompt_tokens=700_000, compaction_skipped_at_tokens=650_000)
    assert should_compact(skipped, threshold=600_000, retry_growth=100_000) is False
    grown = _history(last_prompt_tokens=760_000, compaction_skipped_at_tokens=650_000)
    assert should_compact(grown, threshold=600_000, retry_growth=100_000) is True
    record = skipped_record(_history(last_prompt_tokens=700_000), "provider_error")
    assert record["reason"] == "provider_error" and record["at_prompt_tokens"] == 700_000


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_finalizes_the_task_board_on_normal_completion(loop_tool_context):
    seen: list[dict] = []
    state = {"user_message": "写第1章", "messages": [], "system_prompt": "系统"}
    board_event = StreamEvent(type=StreamEventType.TOOL_RESULT, data={"tool_use_id": "workflow_auto_completion"})
    finalize = AsyncMock(return_value=[board_event])
    with (
        patch(
            "agent.loop.engine.run_openai_agents_streaming_agent",
            _fake_runner([[StreamEvent(type=StreamEventType.TEXT, data={"text": "好了。"})]], seen),
        ),
        patch("agent.core.task_board_completion.auto_finalize_task_board_on_completion", finalize),
    ):
        events = await _collect(state)
    finalize.assert_awaited_once()
    assert board_event in events


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_asks_for_a_self_check_once_per_chapter(loop_tool_context, monkeypatch):
    """自查后把同一章又写了一遍：第二次续跑只说已保存，不再要求自查（防止自查—重写循环）。"""
    from agent.loop import engine

    class _Guard(engine.RepeatReadGuard):
        def __init__(self) -> None:
            super().__init__()
            self.files_written["ch1"] = "第1章"

    monkeypatch.setattr(engine, "RepeatReadGuard", _Guard)
    monkeypatch.setattr(engine, "_word_count_note", lambda guard: "")
    seen: list[dict] = []
    write = [
        StreamEvent(type=StreamEventType.TOOL_USE, data={"id": "c1", "name": "write_chapter", "status": "complete", "input": {}}),
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={"tool_use_id": "c1", "name": "write_chapter", "result": {"content": [{"type": "text", "text": json.dumps({"status": "success"})}]}},
        ),
        StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n合成正文。\n</file>"}),
    ]
    state = {"user_message": "写第1章", "messages": [], "system_prompt": "系统"}
    with patch(
        "agent.loop.engine.run_openai_agents_streaming_agent",
        _fake_runner([write, list(write), [StreamEvent(type=StreamEventType.TEXT, data={"text": "第1章写好了。"})]], seen),
    ):
        await _collect(state)

    assert len(seen) == 3
    assert "自查" in seen[1]["user_message"] and "edit_file" in seen[1]["user_message"]
    assert "已经自查过" in seen[2]["user_message"]


# ---------------------------------------------------------------------------
# 本轮上下文：作者在 AI 写过之后亲手改了什么（<author_edits>）
# ---------------------------------------------------------------------------


def _ai_then_author(db_session: Session, project: Project, title: str, ai_text: str, author_text: str) -> File:
    from services.features.file_version_service import FileVersionService

    file = File(project_id=project.id, title=title, content=ai_text, file_type="draft")
    db_session.add(file)
    db_session.flush()
    service = FileVersionService()
    service.create_version(db_session, file.id, ai_text, change_type="create", change_source="ai", commit=False, skip_quota=True)
    db_session.commit()
    service.create_version(db_session, file.id, author_text, change_type="edit", change_source="user", skip_quota=True)
    file = db_session.get(File, file.id)
    file.content = author_text
    db_session.add(file)
    db_session.commit()
    return file


@pytest.mark.unit
def test_loop_turn_context_lists_author_edits_on_chapters_the_ai_wrote_this_session(db_session, monkeypatch):
    from agent.loop.turn_context import build_loop_turn_context
    from services.features.file_version_service import FileVersionService

    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    user, project = _user_project(db_session)
    ai_text = "第一段，老周推门进来。\n第二段，雨还在下。"
    author_text = "第一段，老陈推门进来。\n第二段，雨还在下。"
    chapter = _ai_then_author(db_session, project, "第1章 雨夜", ai_text, author_text)
    untouched = _ai_then_author(db_session, project, "第2章 渡口", "合成第二章。", "合成第二章。")
    chat = _session_rows(db_session, user, project)
    tool_calls = [
        {
            "id": "provider-call-1",
            "name": "write_chapter",
            "arguments": {"chapter": 1, "title": "第1章 雨夜", "file_type": "draft"},
            "status": "success",
            "result": {"id": chapter.id, "title": chapter.title, "action": "created", "body_saved": True},
        },
        {
            "id": "provider-call-2",
            "name": "write_chapter",
            "arguments": {"chapter": 2, "title": "第2章 渡口", "file_type": "draft"},
            "status": "success",
            "result": {"id": untouched.id, "title": untouched.title, "action": "created", "body_saved": True},
        },
    ]
    _add(db_session, chat, "user", "写前两章", offset=0)
    _add(db_session, chat, "assistant", "写好了。", offset=1, tool_calls=json.dumps(tool_calls, ensure_ascii=False))

    turn = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={},
        language="zh",
        author_message="继续写第3章",
        chat_session_id=chat.id,
    )

    # 作者没打开、也没附加这一章：仍列出作者把「老周」改成了「老陈」。
    assert "<author_edits>" in turn.text
    block = turn.text.split("<author_edits>", 1)[1].split("</author_edits>", 1)[0]
    assert chapter.id in block and "老陈" in block
    assert untouched.id not in block

    # 没有会话（或不是作者的会话）时不读别处的写入记录。
    without_session = build_loop_turn_context(
        db_session, project_id=project.id, user_id=user.id, metadata={}, language="zh", author_message="继续写第3章"
    )
    assert "<author_edits>" not in without_session.text
    # 作者正在看的文件同样核对。
    focused = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": chapter.id},
        language="zh",
        author_message="改一下这一章",
    )
    assert "<author_edits>" in focused.text and "老陈" in focused.text.split("<author_edits>", 1)[1]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_passes_the_truncation_resume_directive_to_the_first_run_only(loop_tool_context):
    from agent.core.output_truncation import OUTPUT_TRUNCATED_RESUME

    directives: list[str] = []
    runs = [
        [StreamEvent(type=StreamEventType.TEXT, data={"text": "先看一下。"})],
        [StreamEvent(type=StreamEventType.TEXT, data={"text": "好的。"})],
    ]

    async def fake(*, state, agent_type, system_prompt, get_steering_messages=None, agent_directive="", **_kwargs):
        directives.append(agent_directive)
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": agent_type})
        for event in runs.pop(0):
            yield event
        state["native_run_holder"].update(agent_type=agent_type, items=[{"role": "user", "content": "x"}])
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    steering = [[{"id": "m1", "content": "顺便改个标题"}], []]

    async def get_steering():
        return steering.pop(0) if steering else []

    state = {"user_message": "继续", "messages": [], "system_prompt": "系统", "previous_output_truncations": 1}
    with patch("agent.loop.engine.run_openai_agents_streaming_agent", fake):
        await _collect(state, get_steering_messages=get_steering)

    assert len(directives) == 2
    assert OUTPUT_TRUNCATED_RESUME in directives[0]
    assert directives[1] == ""


# ---------------------------------------------------------------------------
# 收尾提醒：文案指向 write_chapter（没有 parallel_execute）
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("trigger", ["turns", "time"])
def test_landing_reminders_name_write_chapter(trigger):
    from agents.run_config import ModelInputData

    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.runner import LOOP_SOFT_LANDING_PROMPT, LOOP_TIME_LANDING_PROMPT, _ModelCallCountingFilter

    if trigger == "turns":
        meter, max_turns = AgentRunMeter(max_model_calls=10, model_calls=8), 60
    else:
        meter, max_turns = AgentRunMeter(wrap_up_after_s=0), 60
    turn_filter = _ModelCallCountingFilter(lambda data: data.model_data, meter, max_turns=max_turns)
    sent = turn_filter(SimpleNamespace(model_data=ModelInputData(input=[], instructions="sys")))
    text = sent.input[-1]["content"]

    assert "write_chapter" in text and "create_file" in text
    assert "parallel_execute" not in text
    assert text == (LOOP_SOFT_LANDING_PROMPT.format(remaining=1) if trigger == "turns" else LOOP_TIME_LANDING_PROMPT)


@pytest.mark.unit
def test_loop_author_edits_keep_a_chapter_behind_many_newer_non_body_writes(db_session, monkeypatch):
    """之后又写了很多大纲 / 设定文件：它们不占正文文件的名额，AI 写过的章节照样核对。"""
    from agent.core.session_loader import AUTHOR_EDITS_MAX_FILES
    from agent.loop.turn_context import build_loop_turn_context
    from services.features.file_version_service import FileVersionService

    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    user, project = _user_project(db_session)
    chapter = _ai_then_author(
        db_session, project, "第1章 雨夜", "第一段，老周推门进来。", "第一段，老陈推门进来。"
    )
    chat = _session_rows(db_session, user, project)
    _add(db_session, chat, "user", "写第一章", offset=0)
    _add(db_session, chat, "assistant", "写好了。", offset=1, tool_calls=json.dumps([{
        "id": "provider-call-1",
        "name": "write_chapter",
        "arguments": {"chapter": 1, "title": "第1章 雨夜", "file_type": "draft"},
        "status": "success",
        "result": {"id": chapter.id, "title": chapter.title, "action": "created", "body_saved": True},
    }], ensure_ascii=False))
    notes = []
    for index in range(AUTHOR_EDITS_MAX_FILES + 2):
        note = File(project_id=project.id, title=f"设定{index}", content="合成设定。", file_type="lore")
        db_session.add(note)
        notes.append(note)
    db_session.commit()
    _add(db_session, chat, "user", "补设定", offset=2)
    _add(db_session, chat, "assistant", "补好了。", offset=3, tool_calls=json.dumps([
        {
            "id": f"provider-call-note-{index}",
            "name": "create_file",
            "arguments": {"title": note.title, "file_type": "lore"},
            "status": "success",
            "result": {"id": note.id, "title": note.title},
        }
        for index, note in enumerate(notes)
    ], ensure_ascii=False))

    turn = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={},
        language="zh",
        author_message="继续写第2章",
        chat_session_id=chat.id,
    )

    assert "<author_edits>" in turn.text
    block = turn.text.split("<author_edits>", 1)[1].split("</author_edits>", 1)[0]
    assert chapter.id in block and "老陈" in block


@pytest.mark.unit
def test_session_written_file_ids_reads_assistant_rows_in_pages(db_session):
    """会话很长时不一次读出所有 assistant 行的 tool_calls：按页读，凑够名额就停。"""
    from sqlalchemy import event

    from agent.core.session_loader import SessionLoader

    user, project = _user_project(db_session)
    chat = _session_rows(db_session, user, project)
    chapters = []
    for index in range(60):
        chapter = File(project_id=project.id, title=f"第{index + 1}章", content="合成正文。", file_type="draft")
        db_session.add(chapter)
        chapters.append(chapter)
    db_session.commit()
    for index, chapter in enumerate(chapters):
        _add(db_session, chat, "assistant", "写好了。", offset=index, tool_calls=json.dumps([{
            "id": f"provider-call-{index}",
            "name": "write_chapter",
            "arguments": {"chapter": index + 1, "title": chapter.title, "file_type": "draft"},
            "status": "success",
            "result": {"id": chapter.id, "title": chapter.title, "action": "created", "body_saved": True},
        }], ensure_ascii=False))

    engine = db_session.get_bind()
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_rest):
        if "tool_calls" in statement and "chat_message" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        ids = SessionLoader(project_id=project.id, user_id=user.id).session_written_file_ids(
            db_session, chat_session_id=chat.id, limit=3, file_types=("draft", "script")
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    # 新 → 旧，凑够 3 个就停在第一页。
    assert ids == [chapter.id for chapter in reversed(chapters[-3:])]
    assert len(statements) == 1 and "LIMIT" in statements[0].upper()

    # 跨页时照样新 → 旧、不重复。
    many = SessionLoader(project_id=project.id, user_id=user.id).session_written_file_ids(
        db_session, chat_session_id=chat.id, limit=45, file_types=("draft", "script")
    )
    assert many == [chapter.id for chapter in reversed(chapters[-45:])]
