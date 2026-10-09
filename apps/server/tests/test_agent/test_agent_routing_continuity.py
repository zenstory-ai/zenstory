"""跨轮路由延续、审稿闸门与交接细节的回归测试（全部 mock，不调用 DeepSeek）。

- 路由结果随 assistant 消息落库，下一轮「继续」/回答提问时读回（router / session_loader / service）
- 快速模式不送审、返工稿不自动送审（writing_graph / nodes）
- 工具调用轮数耗尽时回滚空文件、交接包 todo 渲染给下一个 agent（writing_graph）
- 守卫改写过的 parallel_execute 面包屑按类型配对（session_loader）
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, desc, select

from agent.core.workflow_events import StreamEvent, StreamEventType

LONG_TEXT = "第五章的正文。" * 200


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


def _handoff(target: str, context: str, todo: list[str] | None = None) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.HANDOFF,
        data={
            "target_agent": target,
            "reason": "交接",
            "context": context,
            "handoff_packet": {
                "target_agent": target,
                "reason": "交接",
                "context": context,
                "completed": [],
                "todo": todo or [],
                "evidence": ["第3段：“他慢慢地走了过去”"],
            },
        },
    )


def _state(user_message: str, previous: dict | None = None, **extra):
    messages: list[dict] = [{"role": "user", "content": "上一轮的请求"}]
    if previous is not None:
        messages.append({"role": "assistant", **previous})
    messages.append({"role": "user", "content": user_message})
    state = {
        "user_message": user_message,
        "router_message": user_message,
        "messages": messages,
        "system_prompt": "",
    }
    state.update(extra)
    return state


async def _run_graph(fake_agent, state, *, router=None, auto_review_threshold=100):
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext

    router = router or AsyncMock(
        return_value={"current_agent": "writer", "workflow_plan": "quick", "workflow_agents": []}
    )
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch("agent.graph.writing_graph.router_node", router),
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
                    state=state, thread_id="t", auto_review_threshold=auto_review_threshold
                )
            ]
    finally:
        ToolContext.clear_context()


# ------------------------------------------------------------ 「继续」直达


@pytest.mark.unit
def test_continue_after_read_only_review_resumes_reviewer_with_its_scope():
    """正文非空时也能拿到上一个 agent：只读审查被截停后「继续」不会变成 writer。"""
    from agent.graph.router import resume_route_after_exhaustion

    previous = {
        "content": "阶段总结：前 30 章已核对……\n\n[此前的工具操作]\n- 读取了《第一章》(id=c1)",
        "stop_reason": "max_turns_exceeded",
        "last_agent": "quality_reviewer",
        "routing": {
            "initial_agent": "quality_reviewer",
            "workflow_type": "review_only",
            "read_only": True,
            "write_content": False,
            "scope": "前60章一致性",
        },
    }
    result = resume_route_after_exhaustion(_state("继续", previous))

    assert result is not None
    assert result["current_agent"] == "quality_reviewer"
    assert result["workflow_plan"] == "review_only"
    metadata = result["routing_metadata"]
    assert metadata["read_only"] is True
    assert metadata["write_content"] is False
    assert metadata["scope"] == "前60章一致性"


@pytest.mark.unit
def test_continue_falls_back_to_persisted_routing_agents():
    from agent.graph.router import resume_route_after_exhaustion

    routing = {"initial_agent": "planner", "workflow_type": "standard", "write_content": True}
    previous = {"content": "规划到一半……", "stop_reason": "no_progress", "routing": routing}
    assert resume_route_after_exhaustion(_state("继续", previous))["current_agent"] == "planner"

    previous["routing"] = {**routing, "last_agent": "writer"}
    assert resume_route_after_exhaustion(_state("继续", previous))["current_agent"] == "writer"

    previous.pop("routing")
    assert resume_route_after_exhaustion(_state("继续", previous))["current_agent"] == "writer"


@pytest.mark.unit
@pytest.mark.parametrize(
    "stop_reason", ["model_call_budget_exhausted", "run_deadline_exceeded"]
)
def test_budget_and_deadline_stops_are_resumable(stop_reason):
    from agent.graph.router import resume_route_after_exhaustion

    previous = {"content": "写到一半", "stop_reason": stop_reason}
    assert resume_route_after_exhaustion(_state("继续", previous)) is not None


@pytest.mark.unit
def test_user_cancelled_turn_is_not_resumed_by_bypass():
    from agent.graph.router import resume_route_after_exhaustion

    previous = {"content": "写到一半", "stop_reason": "cancelled"}
    assert resume_route_after_exhaustion(_state("继续", previous)) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [
        "继续",
        "continue",
        "Continue.",
        "请基于上一步结果继续完成剩余任务，优先最关键目标。",
        "Please continue from the previous result and finish the remaining work, "
        "prioritizing the most critical goal.",
    ],
)
def test_continue_button_new_and_old_prompts_both_take_the_bypass(message):
    from agent.graph.router import resume_route_after_exhaustion

    previous = {"content": "写到一半", "stop_reason": "max_turns_exceeded"}
    assert resume_route_after_exhaustion(_state(message, previous)) is not None


# ------------------------------------------------- 回答提问时沿用上一轮路由


@pytest.mark.unit
def test_reply_to_clarification_card_inherits_full_routing():
    from agent.graph.router import inherit_routing_after_clarification

    previous = {
        "content": "[workflow_stopped]\nreason: clarification_needed\nquestion: 主角叫什么？",
        "clarification_pending": True,
        "routing": {
            "initial_agent": "planner",
            "workflow_type": "standard",
            "read_only": False,
            "write_content": False,
            "scope": "只要两个主角人设",
            "last_agent": "planner",
        },
    }
    result = inherit_routing_after_clarification(_state("主角叫陈默，金手指是读心", previous))

    assert result is not None
    assert result["current_agent"] == "planner"
    assert result["workflow_plan"] == "standard"
    assert result["workflow_agents"] == [], "write_content=false 仍剔除 writer"
    assert result["routing_metadata"]["scope"] == "只要两个主角人设"
    assert result["routing_metadata"]["reason"] == "inherit_after_clarification"


@pytest.mark.unit
def test_short_yes_to_trailing_question_keeps_writer_and_drops_delivered_scope():
    from agent.graph.router import inherit_routing_after_clarification

    previous = {
        # session_loader 把工具面包屑追加在正文之后：结尾判断要先剥掉它
        "content": "第一章写好了。需要我继续写第二章吗？\n\n[此前的工具操作]\n- 创建了《第一章》(id=c1)",
        "routing": {
            "initial_agent": "writer",
            "workflow_type": "quick",
            "read_only": False,
            "write_content": True,
            "scope": "只写第1章",
            "last_agent": "writer",
        },
    }
    result = inherit_routing_after_clarification(_state("可以", previous))

    assert result is not None
    assert result["current_agent"] == "writer"
    assert result["workflow_plan"] == "quick"
    assert result["routing_metadata"]["write_content"] is True
    assert result["routing_metadata"]["scope"] == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    ("reply", "previous_routing", "content"),
    [
        # 带了新要求的长回复交给 LLM 路由
        ("可以，不过第二章要把师父的身世也交代清楚，再加一场山门外的打斗，节奏快一点，结尾留个钩子", None, None),
        # 明确收窄
        ("可以，先别写正文", None, None),
        ("只要大纲", None, None),
        # 上一轮是只读：问话很可能在征求放开限制，不能沿用成禁令
        ("可以", {"read_only": True}, None),
        ("可以", {"write_content": False}, None),
        # 上一轮没有提问
        ("可以", None, "第一章写好了。"),
    ],
)
def test_reply_routes_normally_when_inheritance_is_unsafe(reply, previous_routing, content):
    from agent.graph.router import inherit_routing_after_clarification

    routing = {
        "initial_agent": "writer",
        "workflow_type": "quick",
        "read_only": False,
        "write_content": True,
        "scope": "",
        "last_agent": "writer",
    }
    routing.update(previous_routing or {})
    previous = {"content": content or "第一章写好了。需要我继续写第二章吗？", "routing": routing}
    assert inherit_routing_after_clarification(_state(reply, previous)) is None


_WRITER_QUESTION_TURN = {
    "content": "第一章写好了，已保存。要不要接着写第二章？",
    "routing": {
        "initial_agent": "writer",
        "workflow_type": "quick",
        "read_only": False,
        "write_content": True,
        "scope": "",
        "last_agent": "writer",
    },
}


@pytest.mark.unit
@pytest.mark.parametrize(
    "reply",
    [
        # 只读 / 审查意图：沿用 writer 会让它在用户说了不改之后照样改稿
        "只审不改，看看第一章",
        "帮我审查一下第一章",
        "第一章有没有问题？",
        "先别改，只帮我看看",
        "Just review chapter 1, don't change it",
        # 换成规划类任务：交给 LLM 路由重新选 agent
        "给后面十章列个大纲",
        "先补一下反派的人设",
        "outline the next arc",
    ],
)
def test_reply_switching_to_review_or_planning_after_writer_question_routes_normally(reply):
    from agent.graph.router import inherit_routing_after_clarification

    assert inherit_routing_after_clarification(_state(reply, _WRITER_QUESTION_TURN)) is None


@pytest.mark.unit
@pytest.mark.parametrize("reply", ["只审不改", "帮我审查一下第一章", "先别改，看看就行"])
def test_review_reply_to_writer_clarification_card_routes_normally(reply):
    from agent.graph.router import inherit_routing_after_clarification

    previous = {
        **_WRITER_QUESTION_TURN,
        "content": "[workflow_stopped]\nreason: clarification_needed\nquestion: 要写哪一章？",
        "clarification_pending": True,
    }
    assert inherit_routing_after_clarification(_state(reply, previous)) is None


@pytest.mark.unit
def test_planning_words_inside_a_clarification_answer_still_inherit():
    from agent.graph.router import inherit_routing_after_clarification

    previous = {
        "content": "[workflow_stopped]\nreason: clarification_needed\nquestion: 主角人设按哪版来？",
        "clarification_pending": True,
        "routing": {**_WRITER_QUESTION_TURN["routing"], "initial_agent": "planner", "last_agent": "planner"},
    }
    result = inherit_routing_after_clarification(_state("按第二版人设", previous))

    assert result is not None
    assert result["current_agent"] == "planner"


@pytest.mark.unit
def test_reply_without_persisted_routing_routes_normally():
    from agent.graph.router import inherit_routing_after_clarification

    previous = {"content": "需要我继续写第二章吗？"}
    assert inherit_routing_after_clarification(_state("可以", previous)) is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_graph_reuses_previous_routing_without_calling_the_router():
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        yield _text("大纲已按陈默的设定补全。")

    previous = {
        "content": "",
        "clarification_pending": True,
        "routing": {
            "initial_agent": "planner",
            "workflow_type": "standard",
            "read_only": False,
            "write_content": False,
            "scope": "只要大纲",
            "last_agent": "planner",
        },
    }
    router = AsyncMock(return_value={"current_agent": "writer", "workflow_agents": []})
    events = await _run_graph(
        fake_agent, _state("主角叫陈默", previous, generation_mode="quality"), router=router
    )

    router.assert_not_called()
    assert calls == ["planner"]
    decided = next(e for e in events if e.type == StreamEventType.ROUTER_DECIDED)
    assert decided.data["routing_metadata"]["write_content"] is False


# ---------------------------------------------------------- 审稿闸门（F9）


@pytest.mark.asyncio
@pytest.mark.unit
async def test_fast_mode_drops_writer_handoff_to_reviewer():
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        for event in _write_done("w1"):
            yield event
        yield _text(LONG_TEXT)
        yield _handoff("quality_reviewer", "请审查第五章")

    events = await _run_graph(fake_agent, _state("写第五章", generation_mode="fast"))

    assert calls == ["writer"]
    assert not any(e.type == StreamEventType.HANDOFF for e in events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_fast_mode_tells_non_reviewer_agents_not_to_hand_off_for_review():
    from agent.graph.nodes import FAST_MODE_DIRECTIVE, run_streaming_agent

    prompts: dict[str, str] = {}

    async def fake_runner(*, state, agent_type, system_prompt, get_steering_messages=None):
        prompts[agent_type] = system_prompt
        yield _text("ok")

    with patch("agent.graph.nodes.run_openai_agents_streaming_agent", new=fake_runner):
        for agent_type, mode in (("writer", "fast"), ("quality_reviewer", "fast"), ("planner", "quality")):
            async for _ in run_streaming_agent({"generation_mode": mode}, agent_type):
                pass

    assert FAST_MODE_DIRECTIVE in prompts["writer"]
    assert FAST_MODE_DIRECTIVE not in prompts["quality_reviewer"]
    assert FAST_MODE_DIRECTIVE not in prompts["planner"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rework_after_review_is_not_auto_reviewed_again():
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "writer":
            for event in _write_done(f"w{len(calls)}"):
                yield event
            yield _text(LONG_TEXT)
            return
        yield _text("第三段节奏拖沓。")
        yield _handoff("writer", "第三段节奏拖沓，需要压缩")

    events = await _run_graph(fake_agent, _state("写第五章", generation_mode="quality"))

    assert calls == ["writer", "quality_reviewer", "writer"], "首稿自动送审，返工稿不再送审"
    handoff_targets = [e.data["target_agent"] for e in events if e.type == StreamEventType.HANDOFF]
    assert handoff_targets == ["quality_reviewer", "writer"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rework_stays_unreviewed_across_empty_file_correction_round():
    """返工 writer 留下空文件时，纠偏轮之后也不能把返工稿再送审。"""
    from agent.tools.mcp_tools import ToolContext

    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "quality_reviewer":
            yield _text("第三段节奏拖沓。")
            yield _handoff("writer", "第三段节奏拖沓，需要压缩")
            return
        for event in _write_done(f"w{len(calls)}"):
            yield event
        if len(calls) == 3:
            # 返工这一轮建了空文件就收尾，触发纠偏轮
            ToolContext.set_pending_empty_file("f9", "第五章（修订）")
        else:
            ToolContext.clear_pending_empty_file()
        yield _text(LONG_TEXT)

    with patch("agent.graph.writing_graph._probe_pending_file_body", return_value="empty"):
        await _run_graph(fake_agent, _state("写第五章", generation_mode="quality"))

    assert calls == ["writer", "quality_reviewer", "writer", "writer"]


# ------------------------------------------------- 交接包 todo / evidence（L5）


@pytest.mark.asyncio
@pytest.mark.unit
async def test_reviewer_todo_and_evidence_reach_the_next_agent():
    seen: dict[str, str] = {}

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        seen[agent_type] = state["user_message"]
        if agent_type == "quality_reviewer":
            yield _text("问题如下。")
            yield _handoff("writer", "按问题清单修改", todo=["把第3段的“慢慢地”删掉", "第7段补一句动机"])
            return
        yield _text("已修改。")

    await _run_graph(
        fake_agent,
        _state("检查第三章", generation_mode="quality"),
        router=AsyncMock(
            return_value={
                "current_agent": "quality_reviewer",
                "workflow_plan": "review_only",
                "workflow_agents": [],
            }
        ),
    )

    writer_message = seen["writer"]
    assert "- 把第3段的“慢慢地”删掉" in writer_message
    assert "- 第7段补一句动机" in writer_message
    assert "第3段：“他慢慢地走了过去”" in writer_message


@pytest.mark.unit
def test_internal_evidence_markers_are_not_rendered():
    from agent.graph.writing_graph import _format_handoff_packet_items

    rendered = _format_handoff_packet_items(
        {"todo": [], "evidence": ["workflow_plan=standard", "auto_checks=repeat:2,facts:1"]}
    )
    assert rendered == ""


# ------------------------------------------- 完成标记的中文别名


@pytest.mark.unit
@pytest.mark.parametrize(
    "content",
    ["审查通过，可以发布。\n[任务完成]", "审查通过。【任务完成】", "审查通过。[TASK_COMPLETE]\n"],
)
def test_chinese_task_complete_marker_counts_as_completion(content):
    from agent.graph.nodes import detect_task_complete, ends_with_question_to_user

    result = detect_task_complete(content, "quality_reviewer")
    assert result.is_complete is True
    assert result.reason == "explicit_complete_marker"
    assert ends_with_question_to_user(content) is False


@pytest.mark.unit
def test_chinese_marker_mid_text_is_not_completion():
    from agent.graph.nodes import detect_task_complete

    assert detect_task_complete("上一轮写着[任务完成]，这一轮还要继续改第二段。", "writer").is_complete is False


@pytest.mark.unit
def test_question_before_chinese_marker_still_counts_as_question():
    from agent.graph.nodes import ends_with_question_to_user

    assert ends_with_question_to_user("第一章写好了。需要我继续写第二章吗？\n[任务完成]") is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_writer_ending_with_chinese_marker_stops_planned_handoff():
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        yield _text("大纲已整理好。\n[任务完成]")

    events = await _run_graph(
        fake_agent,
        _state("整理大纲"),
        router=AsyncMock(
            return_value={"current_agent": "planner", "workflow_plan": "standard", "workflow_agents": ["writer"]}
        ),
    )

    assert calls == ["planner"]
    complete = [e for e in events if e.type == StreamEventType.WORKFLOW_COMPLETE]
    assert complete and complete[-1].data["reason"] == "task_complete"


# ------------------------------------------- 轮数耗尽时的空文件回滚（L3）


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_call_exhaustion_rolls_back_pending_empty_file():
    from agent.tools.mcp_tools import ToolContext

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        ToolContext.set_pending_empty_file("f6", "第六章")
        yield StreamEvent(
            type=StreamEventType.ITERATION_EXHAUSTED,
            data={"layer": "tool_call", "iterations_used": 60, "max_iterations": 60, "last_agent": "writer"},
        )

    rollback = MagicMock(return_value={"f6"})
    with (
        patch("agent.graph.writing_graph._probe_pending_file_body", return_value="empty"),
        patch("agent.graph.writing_graph._rollback_unfinished_empty_files", rollback),
    ):
        events = await _run_graph(fake_agent, _state("写第六章"))

    rollback.assert_called_once_with([{"file_id": "f6", "title": "第六章"}])
    notice = "".join(e.data.get("text", "") for e in events if e.type == StreamEventType.TEXT)
    assert "《第六章》" in notice and "已撤销" in notice


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_call_exhaustion_keeps_files_whose_body_was_written():
    from agent.tools.mcp_tools import ToolContext

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        ToolContext.set_pending_empty_file("f6", "第六章")
        yield StreamEvent(
            type=StreamEventType.ITERATION_EXHAUSTED,
            data={"layer": "tool_call", "iterations_used": 60, "max_iterations": 60},
        )

    rollback = MagicMock(return_value=set())
    with (
        patch("agent.graph.writing_graph._probe_pending_file_body", return_value="written"),
        patch("agent.graph.writing_graph._rollback_unfinished_empty_files", rollback),
    ):
        events = await _run_graph(fake_agent, _state("写第六章"))

    rollback.assert_not_called()
    assert not any(e.type == StreamEventType.TEXT for e in events)


# ----------------------------------------- session_loader：路由信号与面包屑


def _history_row(content: str, metadata: dict | None = None, tool_calls=None):
    return SimpleNamespace(
        id="m1",
        role="assistant",
        content=content,
        message_metadata=json.dumps(metadata) if metadata is not None else None,
        reasoning_content=None,
        tool_calls=json.dumps(tool_calls) if tool_calls is not None else None,
    )


@pytest.mark.unit
def test_history_dict_exposes_routing_last_agent_and_clarification():
    from agent.core.session_loader import SessionLoader

    loader = SessionLoader("p", "u")
    routing = {"initial_agent": "quality_reviewer", "read_only": True}
    exhausted = loader._format_chat_message_for_history(
        _history_row(
            "阶段总结",
            {
                "stop_reason": "max_turns_exceeded",
                "routing": routing,
                "status_cards": [{"type": "iteration_exhausted", "layer": "tool_call", "lastAgent": "quality_reviewer"}],
            },
        )
    )
    assert exhausted["routing"] == routing
    assert exhausted["last_agent"] == "quality_reviewer"
    assert exhausted["clarification_pending"] is False

    clarification = loader._format_chat_message_for_history(
        _history_row(
            "",
            {"status_cards": [{"type": "workflow_stopped", "reason": "clarification_needed", "question": "主角叫什么？"}]},
        )
    )
    assert clarification["clarification_pending"] is True
    assert "routing" not in clarification


@pytest.mark.unit
def test_guard_rewritten_parallel_batch_pairs_results_by_type():
    """守卫拦下第 1 个读取后实际只执行 2 个子任务：不能把后面的结果安到前面的子任务上。"""
    from agent.core.session_loader import SessionLoader

    tool_call = {
        "name": "parallel_execute",
        "status": "success",
        "arguments": {
            "tasks": [
                {"type": "query_files", "description": "重读第一章", "params": {"id": "c1"}},
                {"type": "query_files", "description": "读第二章", "params": {"id": "c2"}},
                {"type": "edit_file", "description": "改第三章", "params": {"id": "c3"}},
            ]
        },
        "result": {
            "tasks": [
                {
                    "type": "query_files",
                    "status": "completed",
                    "result": {"status": "success", "data": [{"id": "c2", "title": "第二章"}]},
                },
                {"type": "edit_file", "status": "failed", "error": "找不到原文", "result": None},
            ]
        },
    }

    actions = SessionLoader("p", "u")._extract_parallel_file_actions(tool_call)

    assert actions == [{"name": "query_files", "file_id": "c2", "title": "第二章"}]


# ------------------------------------------------------- service：路由落库


@pytest.mark.unit
def test_partial_save_stop_reason_distinguishes_deadline_from_user_stop():
    from agent.service import partial_save_stop_reason

    assert partial_save_stop_reason(1200.5, 1200) == "run_deadline_exceeded"
    assert partial_save_stop_reason(30.0, 1200) == "cancelled"
    assert partial_save_stop_reason(5000.0, 0) == "cancelled"


@pytest.fixture
def routing_service_project(db_session: Session):
    from models import Project, User
    from services.core.auth_service import hash_password

    user = User(
        email="routing_continuity@example.com",
        username="routingcontinuity",
        hashed_password=hash_password("password123"),
        name="Routing Continuity",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="Routing Continuity", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return {"user": user, "project": project}


@pytest.fixture
def routing_service():
    from agent.schemas.context import ContextData
    from agent.service import AgentService

    assembler = MagicMock()
    assembler.assemble.return_value = ContextData(items=[], context="", token_estimate=0)
    with patch("agent.service.get_context_assembler", return_value=assembler):
        yield AgentService(context_assembler=assembler)


def _router_decided(agent: str, workflow: str, **routing) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.ROUTER_DECIDED,
        data={
            "initial_agent": agent,
            "workflow_plan": workflow,
            "workflow_agents": [],
            "routing_metadata": {"agent_type": agent, "workflow_type": workflow, **routing},
        },
    )


def _latest_assistant(db_session: Session, project_id: str):
    from models import ChatMessage, ChatSession

    db_session.rollback()
    return db_session.exec(
        select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.project_id == project_id)
        .where(ChatMessage.role == "assistant")
        .order_by(desc(ChatMessage.created_at))
    ).first()


@pytest.mark.asyncio
@pytest.mark.usefixtures("writing_prompt_configs")
async def test_routing_round_trips_from_save_to_next_turn_resume(
    routing_service, routing_service_project, db_session: Session
):
    """本轮落库的路由，下一轮加载历史后能让「继续」恢复成只读审稿人。"""
    from agent.core.session_loader import SessionLoader
    from agent.graph.router import resume_route_after_exhaustion

    project = routing_service_project["project"]
    user = routing_service_project["user"]

    async def workflow():
        yield _router_decided(
            "quality_reviewer", "review_only", read_only=True, write_content=False, scope="前60章"
        )
        yield StreamEvent(type=StreamEventType.AGENT_SELECTED, data={"agent_type": "quality_reviewer"})
        yield _text("阶段总结：已核对前 30 章。")
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "max_turns_exceeded"})

    with patch("agent.service.run_writing_workflow_streaming", return_value=workflow()):
        async for _ in routing_service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="检查前60章一致性，不要改文件",
            session=db_session,
        ):
            pass

    saved = _latest_assistant(db_session, str(project.id))
    routing = json.loads(saved.message_metadata)["routing"]
    assert routing == {
        "initial_agent": "quality_reviewer",
        "workflow_type": "review_only",
        "read_only": True,
        "write_content": False,
        "scope": "前60章",
        "last_agent": "quality_reviewer",
    }

    history = SessionLoader(str(project.id), str(user.id)).load_chat_session(db_session).history_messages
    state = {
        "user_message": "继续",
        "router_message": "继续",
        "messages": history + [{"role": "user", "content": "继续"}],
    }
    resumed = resume_route_after_exhaustion(state)
    assert resumed is not None
    assert resumed["current_agent"] == "quality_reviewer"
    assert resumed["routing_metadata"]["read_only"] is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("writing_prompt_configs")
@pytest.mark.parametrize(
    ("timeout_s", "expected"), [(1200, "cancelled"), (0.000001, "run_deadline_exceeded")]
)
async def test_cancelled_partial_save_persists_stop_reason_and_routing(
    routing_service, routing_service_project, db_session: Session, timeout_s, expected
):
    from models import ChatMessage

    project = routing_service_project["project"]
    user = routing_service_project["user"]
    started = asyncio.Event()

    async def slow_workflow():
        yield _router_decided("writer", "quick", write_content=True, scope="")
        yield StreamEvent(type=StreamEventType.AGENT_SELECTED, data={"agent_type": "writer"})
        yield _text("写到一半")
        started.set()
        await asyncio.sleep(3600)

    async def consume():
        async for _ in routing_service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="写第六章",
            session=db_session,
        ):
            pass

    with (
        patch("agent.service.run_writing_workflow_streaming", return_value=slow_workflow()),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
        patch("agent.service.AGENT_RUN_WALL_CLOCK_TIMEOUT_S", timeout_s),
    ):
        task = asyncio.create_task(consume())
        await started.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        saved: ChatMessage | None = None
        for _ in range(100):
            await asyncio.sleep(0.05)
            saved = _latest_assistant(db_session, str(project.id))
            if saved is not None:
                break

    assert saved is not None
    metadata = json.loads(saved.message_metadata)
    assert metadata["stop_reason"] == expected
    assert metadata["routing"]["last_agent"] == "writer"
