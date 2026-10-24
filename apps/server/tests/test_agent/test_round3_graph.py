"""第三轮 review 回归测试：agent/graph/writing_graph.py 的三个确认缺陷。

覆盖：
- #13 空文件纠偏轮吞掉同轮的显式 handoff（以及顺带挤掉自动质检）
- #16 运行期 steering 的追加轮硬编码 writer，把只读工作流升级成有写权限的轮次
- #29 纠偏配额用尽后 pending-empty-file 标记永不清除，后续 create_file 全被硬拒
"""

from contextlib import nullcontext
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.tools.mcp_tools import ToolContext
from tests.test_agent.body_write_events import edit_file_events
from tests.test_agent.file_body_resume_helpers import accept_resumes, resumable_target

LONG_TEXT = "第五章的正文。" * 200


def _text(text: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.TEXT, data={"text": text})


def _create_file_done() -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.TOOL_USE,
        data={"id": "t1", "name": "create_file", "status": "complete"},
    )


def _handoff(target: str, *, context: str = "请审查《第五章》") -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.HANDOFF,
        data={
            "target_agent": target,
            "reason": "正文已超阈值，按 WRITER_PROMPT 要求送审",
            "context": context,
            "handoff_packet": {
                "target_agent": target,
                "reason": "正文已超阈值",
                "context": context,
                "completed": ["写完第五章正文"],
                "todo": ["检查节奏与人设一致性"],
                "evidence": ["file_id=file-X"],
            },
        },
    )


async def _run_graph(
    fake_agent,
    *,
    initial_agent: str = "writer",
    router_result: dict | None = None,
    get_steering_messages=None,
    auto_review_threshold: int = 100000,
    session=None,
    resume_ok: bool = True,
) -> list[StreamEvent]:
    """跑一次工作流，返回全部 SSE 事件。

    resume_ok：替身假定空文件纠偏目标核验通过、StreamAdapter 已恢复捕获（见 file_body_resume_helpers）。
    """
    from agent.graph.writing_graph import run_writing_workflow_streaming

    state = {"user_message": "写第五章", "messages": [], "system_prompt": ""}
    ToolContext.set_context(session=session, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch(
                "agent.graph.writing_graph.router_node",
                AsyncMock(return_value=router_result or {}),
            ),
            patch("agent.graph.writing_graph.get_next_node", return_value=initial_agent),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
            resumable_target() if resume_ok else nullcontext(),
        ):
            events = run_writing_workflow_streaming(
                state=state,
                thread_id="t",
                auto_review_threshold=auto_review_threshold,
                get_steering_messages=get_steering_messages,
            )
            return [event async for event in (accept_resumes(events) if resume_ok else events)]
    finally:
        ToolContext.clear_context()


@pytest.mark.unit
class TestRound3EmptyFileCorrectionKeepsHandoff:
    """#13：空文件纠偏轮必须保留本轮的显式 handoff，而不是静默吞掉。"""

    @pytest.mark.asyncio
    async def test_explicit_handoff_survives_empty_file_correction(self):
        """writer 同一轮里既留了空文件、又按 prompt 送审 quality_reviewer。

        纠偏轮 continue 会跳过本轮末尾的交接决策，若不暂存 handoff，
        quality_reviewer 永远不会运行，HANDOFF SSE 也永远不会发出。
        """
        calls: list[dict] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append({"agent": agent_type, "user_message": state.get("user_message", "")})
            if agent_type == "writer" and len(calls) == 1:
                ToolContext.set_pending_empty_file("file-X", "第五章")
                yield _create_file_done()
                yield _text(LONG_TEXT)
                yield _handoff("quality_reviewer")
            elif agent_type == "writer":
                ToolContext.clear_pending_empty_file()
                yield _text("已用 edit_file 补齐正文。")
            else:
                yield _text("审查完毕。[TASK_COMPLETE]")

        events = await _run_graph(fake_agent)

        agents = [c["agent"] for c in calls]
        assert agents == ["writer", "writer", "quality_reviewer"], (
            "纠偏轮之后必须继续执行本轮请求的显式 handoff 目标"
        )

        handoffs = [e for e in events if e.type == StreamEventType.HANDOFF]
        assert len(handoffs) == 1, "被暂存的 handoff 必须补发 HANDOFF SSE"
        packet = handoffs[0].data["handoff_packet"]
        assert packet["completed"] == ["写完第五章正文"]
        assert packet["todo"] == ["检查节奏与人设一致性"]
        # 交接上下文要么带原文案，要么带待审查正文；总之不能只剩纠偏文案
        reviewer_message = calls[2]["user_message"]
        assert "请审查《第五章》" in reviewer_message
        assert "正文仍为空" not in reviewer_message

    @pytest.mark.asyncio
    async def test_correction_round_does_not_squeeze_out_auto_review(self):
        """即使没有显式 handoff，纠偏轮也不能把本轮的自动质检挤掉。

        质检门按本轮写进正文类文件的字数判定：正文是纠偏轮用 edit_file 补进去的，
        纠偏轮结束后照样送审。
        """
        calls: list[str] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            if agent_type == "writer" and len(calls) == 1:
                ToolContext.set_pending_empty_file("file-X", "第五章")
                yield _create_file_done()
                yield _text(LONG_TEXT)
            elif agent_type == "writer":
                ToolContext.clear_pending_empty_file()
                for event in edit_file_events("fix-1", LONG_TEXT, file_id="file-X"):
                    yield event
                yield _text("已补齐正文。")
            else:
                yield _text("审查完毕。[TASK_COMPLETE]")

        events = await _run_graph(fake_agent, auto_review_threshold=100)

        assert calls == ["writer", "writer", "quality_reviewer"], (
            "纠偏轮结束后自动质检仍须触发"
        )
        assert any(e.type == StreamEventType.HANDOFF for e in events)

    @pytest.mark.asyncio
    async def test_empty_file_is_corrected_by_creating_agent_then_handoff_resumes(self):
        """planner 建了空文件并交接给 writer：纠偏轮由 planner 自己补写，之后再执行它的交接。

        旧行为是纠偏轮硬编码 writer：writer 去补 planner 的大纲文件，planner 的
        「请按大纲写正文」交接被当成自交接丢弃——writer 实际收到的只有纠偏提示。
        """
        calls: list[dict] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append({"agent": agent_type, "user_message": state.get("user_message", "")})
            if agent_type == "planner" and len(calls) == 1:
                ToolContext.set_pending_empty_file("file-X", "第一章大纲")
                yield _create_file_done()
                yield _text("大纲文件已建好。")
                yield _handoff("writer", context="请按大纲写正文")
            elif agent_type == "planner":
                ToolContext.clear_pending_empty_file()
                yield _text("大纲正文补齐了。")
            else:
                yield _text("第一章正文写好了。")

        events = await _run_graph(
            fake_agent,
            initial_agent="planner",
            router_result={
                "current_agent": "planner",
                "workflow_plan": "quick",
                "workflow_agents": [],
                "routing_metadata": {},
            },
        )

        assert [c["agent"] for c in calls] == ["planner", "planner", "writer"]
        assert "正文仍为空" in calls[1]["user_message"], "纠偏提示发给建文件的 planner"
        assert "请按大纲写正文" in calls[2]["user_message"], "writer 收到的是 planner 的交接"
        handoffs = [e for e in events if e.type == StreamEventType.HANDOFF]
        assert [h.data["target_agent"] for h in handoffs] == ["writer"]

    @pytest.mark.asyncio
    async def test_planned_writer_runs_after_planner_correction(self):
        """standard 工作流：planner 留了空文件时先由 planner 补写，再按计划交接 writer。

        旧行为：纠偏轮硬编码 writer，随后计划序列弹出的 writer 与当前 agent 相同，
        被判「无效自动交接」整轮停掉（真实 deepseek-flash 复现过）。
        """
        calls: list[str] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            if agent_type == "planner" and len(calls) == 1:
                ToolContext.set_pending_empty_file("file-X", "第一章大纲")
                yield _create_file_done()
                yield _text("大纲文件已建好。")
            elif agent_type == "planner":
                ToolContext.clear_pending_empty_file()
                yield _text("大纲正文补齐了。")
            else:
                yield _text("第一章正文写好了。")

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

        assert calls == ["planner", "planner", "writer"]
        assert not [e for e in events if e.type == StreamEventType.WORKFLOW_STOPPED]


@pytest.mark.unit
class TestRound3SteeringFollowupKeepsAgent:
    """#16：运行期 steering 的追加轮必须沿用当前 agent，不能硬编码 writer。"""

    @staticmethod
    def _steering_once(payload: list[dict]):
        polls = {"n": 0}

        async def get_steering_messages():
            polls["n"] += 1
            return payload if polls["n"] == 1 else []

        return get_steering_messages

    @pytest.mark.asyncio
    async def test_review_only_steering_stays_quality_reviewer(self):
        """review_only 工作流里发一条引导，不得被升级成有写权限的 writer 轮。"""
        calls: list[dict] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append({"agent": agent_type, "user_message": state.get("user_message", "")})
            yield StreamEvent(type=StreamEventType.MESSAGE_START, data={})
            yield _text("第三章审查报告：节奏偏慢。")

        await _run_graph(
            fake_agent,
            initial_agent="quality_reviewer",
            router_result={
                "current_agent": "quality_reviewer",
                "workflow_plan": "review_only",
                "workflow_agents": [],
                "routing_metadata": {},
            },
            get_steering_messages=self._steering_once(
                [{"id": "s1", "content": "顺便说一下，主角名字我改叫林砚了"}]
            ),
        )

        assert [c["agent"] for c in calls] == ["quality_reviewer", "quality_reviewer"], (
            "只读工作流的 steering 追加轮必须仍是 quality_reviewer"
        )
        followup = calls[1]["user_message"]
        assert "继续你的审查" in followup
        assert "继续调整或补充" not in followup, "不得对只读 agent 下达改写指令"
        assert "handoff_to_agent" in followup, "要改正文只能显式交接给 writer"

    @pytest.mark.asyncio
    async def test_writer_steering_followup_still_writer(self):
        """有写权限的 agent 走原来的文案与原来的 agent，不能被改坏。"""
        calls: list[dict] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append({"agent": agent_type, "user_message": state.get("user_message", "")})
            yield StreamEvent(type=StreamEventType.MESSAGE_START, data={})
            yield _text("第五章正文。")

        await _run_graph(
            fake_agent,
            get_steering_messages=self._steering_once(
                [{"id": "s2", "content": "结尾加一个反转"}]
            ),
        )

        assert [c["agent"] for c in calls] == ["writer", "writer"]
        assert "继续调整或补充" in calls[1]["user_message"]

    @pytest.mark.asyncio
    async def test_planner_steering_followup_stays_planner(self):
        """planner 单跑时的追加轮同样沿用 planner，而不是跳成 writer。"""
        calls: list[str] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            yield StreamEvent(type=StreamEventType.MESSAGE_START, data={})
            yield _text("大纲草案。")

        await _run_graph(
            fake_agent,
            initial_agent="planner",
            router_result={
                "current_agent": "planner",
                "workflow_plan": "quick",
                "workflow_agents": [],
                "routing_metadata": {},
            },
            get_steering_messages=self._steering_once(
                [{"id": "s3", "content": "第三幕再加一条支线"}]
            ),
        )

        assert calls == ["planner", "planner"]

    def test_agent_write_capability_follows_registry(self):
        """写权限判定必须跟着 registry 的工具映射走，而不是硬编码 agent 名。"""
        from agent.graph.writing_graph import (
            STEERING_FOLLOWUP_CONTEXT,
            STEERING_FOLLOWUP_CONTEXT_READONLY,
            _agent_can_write_files,
            _steering_followup_context,
        )

        assert _agent_can_write_files("writer") is True
        assert _agent_can_write_files("planner") is True
        assert _agent_can_write_files("hook_designer") is True
        assert _agent_can_write_files("quality_reviewer") is False
        assert _agent_can_write_files(None) is False

        assert _steering_followup_context("writer") == STEERING_FOLLOWUP_CONTEXT
        assert (
            _steering_followup_context("quality_reviewer")
            == STEERING_FOLLOWUP_CONTEXT_READONLY
        )


@pytest.mark.unit
class TestRound3PendingEmptyFileGuardPreservesFailureEvidence:
    """An unfinished artifact is rolled back or remains guarded and reported."""

    @pytest.mark.asyncio
    async def test_unverifiable_guard_reported_after_correction_quota_exhausted(self):
        from agent.graph.writing_graph import MAX_FILE_CORRECTION_ATTEMPTS

        calls: list[str] = []
        guard_seen_by_reviewer: list[bool] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            if agent_type == "quality_reviewer":
                guard_seen_by_reviewer.append(ToolContext.has_pending_empty_file())
                yield _text("审查完毕。[TASK_COMPLETE]")
                return
            # writer 每一轮都建空文件、都没写正文
            ToolContext.set_pending_empty_file(f"file-{len(calls)}", "第五章")
            yield _create_file_done()
            yield _text("又建了一个空文件。")
            if len(calls) == 1 + MAX_FILE_CORRECTION_ATTEMPTS:
                # 配额用尽的这一轮请求送审，后续轮次才能观察到残留的标记
                yield _handoff("quality_reviewer")

        events = await _run_graph(fake_agent)

        assert calls == ["writer"] * (1 + MAX_FILE_CORRECTION_ATTEMPTS) + [
            "quality_reviewer"
        ]
        assert guard_seen_by_reviewer == [True]
        exhausted = [
            event
            for event in events
            if event.type == StreamEventType.ITERATION_EXHAUSTED
            and event.data.get("layer") == "empty_file_correction"
        ]
        assert exhausted
        assert exhausted[0].data["unfinished_files"]

    @pytest.mark.asyncio
    async def test_unverifiable_target_is_reported_without_a_correction_round(self):
        """核验不了纠偏目标（没有数据库 / 适配器没恢复捕获）：不跑纠偏轮，保留守卫并报告未完成。"""
        calls: list[str] = []
        guard_seen_by_reviewer: list[bool] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            if agent_type == "quality_reviewer":
                guard_seen_by_reviewer.append(ToolContext.has_pending_empty_file())
                yield _text("审查完毕。[TASK_COMPLETE]")
                return
            ToolContext.set_pending_empty_file("file-1", "第五章")
            yield _create_file_done()
            yield _text("只建了空文件。")
            yield _handoff("quality_reviewer")

        events = await _run_graph(fake_agent, resume_ok=False)

        assert calls == ["writer", "quality_reviewer"]
        assert guard_seen_by_reviewer == [True]
        exhausted = [
            event
            for event in events
            if event.type == StreamEventType.ITERATION_EXHAUSTED
            and event.data.get("layer") == "empty_file_correction"
        ]
        assert exhausted
        assert exhausted[0].data["unfinished_files"]

    @pytest.mark.asyncio
    async def test_final_iteration_reports_unverifiable_unfinished_file(self):
        """最后一轮不能把无法核验的失败证据静默清掉。"""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        calls: list[str] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            ToolContext.set_pending_empty_file("file-last", "第五章")
            yield _create_file_done()
            yield _text("只建了文件。")

        state = {"user_message": "写第五章", "messages": [], "system_prompt": ""}
        ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
        try:
            with (
                patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
                patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
                patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
            ):
                events = [
                    event
                    async for event in run_writing_workflow_streaming(
                        state=state, thread_id="t", max_iterations=1,
                    )
                ]
            assert calls == ["writer"]
            assert ToolContext.has_pending_empty_file() is True
            assert any(
                event.type == StreamEventType.ITERATION_EXHAUSTED
                and event.data.get("layer") == "empty_file_correction"
                for event in events
            )
        finally:
            ToolContext.clear_context()


def _tool_result(call_id: str, payload: dict) -> StreamEvent:
    import json

    return StreamEvent(
        type=StreamEventType.TOOL_RESULT,
        data={
            "tool_use_id": call_id,
            "name": "edit_file",
            "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]},
        },
    )


def _edit_file_done(call_id: str) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.TOOL_USE,
        data={
            "id": call_id,
            "name": "edit_file",
            "status": "complete",
            "input": {"id": "file-X", "edits": [{"op": "append", "text": LONG_TEXT}]},
        },
    )


@pytest.mark.unit
class TestRequestScopedFailureBreakerAndWriteSignals:
    """工具失败熔断按请求共享；写入全部失败时不触发自动质检。"""

    @pytest.mark.asyncio
    async def test_every_agent_run_shares_one_request_breaker(self):
        from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker

        seen: list[object] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            seen.append(state.get("tool_failure_breaker"))
            yield _text(f"{agent_type} 完成。")

        await _run_graph(
            fake_agent,
            initial_agent="planner",
            router_result={
                "current_agent": "planner",
                "workflow_plan": "standard",
                "workflow_agents": ["writer"],
                "routing_metadata": {},
            },
        )

        assert len(seen) == 2
        assert isinstance(seen[0], ToolFailureBreaker)
        # writer 拿到的是同一个熔断器：planner 阶段的失败计数不会在交接时归零
        assert seen[1] is seen[0]

    @pytest.mark.asyncio
    async def test_auto_review_skipped_when_every_writer_write_failed(self):
        calls: list[str] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            for idx in range(3):
                yield _edit_file_done(f"e{idx}")
                yield _tool_result(f"e{idx}", {"status": "error", "error": "database is locked"})
            yield _text(LONG_TEXT)

        await _run_graph(fake_agent, auto_review_threshold=100)

        # 正文一个字都没落库：不送审（否则审稿人会把 writer 叫回来重试同一个失败写入）
        assert calls == ["writer"]

    @pytest.mark.asyncio
    async def test_auto_review_still_fires_when_one_write_succeeded(self):
        calls: list[str] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            if agent_type == "writer":
                yield _edit_file_done("e0")
                yield _tool_result("e0", {"status": "error", "error": "找不到片段"})
                yield _edit_file_done("e1")
                yield _tool_result("e1", {"status": "success", "data": {"id": "file-X", "file_type": "draft"}})
            yield _text(LONG_TEXT)

        await _run_graph(fake_agent, auto_review_threshold=100)

        assert calls == ["writer", "quality_reviewer"]


@pytest.mark.unit
class TestCorrectionRoundKeepsUserScope:
    """空文件纠偏轮不能冲掉「等用户回答」，只读请求不安排补写。"""

    @pytest.mark.asyncio
    async def test_question_prose_does_not_cancel_plan_after_correction_round(self):
        """planner 的普通问句不是控制信号；纠偏补齐后仍按计划运行 writer。"""
        calls: list[str] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            if agent_type == "planner" and len(calls) == 1:
                ToolContext.set_pending_empty_file("file-X", "大纲")
                yield _create_file_done()
                yield _text("大纲方向如上。这个方向可以吗？")
            elif agent_type == "planner":
                ToolContext.clear_pending_empty_file()
                yield _text("已补齐大纲正文。")
            else:
                yield _text("第一章正文写好了。")

        await _run_graph(
            fake_agent,
            initial_agent="planner",
            router_result={
                "current_agent": "planner",
                "workflow_plan": "standard",
                "workflow_agents": ["writer"],
                "routing_metadata": {},
            },
        )

        assert calls == ["planner", "planner", "writer"]

    @pytest.mark.asyncio
    async def test_writer_question_survives_correction_but_auto_review_still_fires(self):
        """writer 的普通问句不妨碍纠偏后的自动质检。"""
        calls: list[str] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            if len(calls) == 1:
                ToolContext.set_pending_empty_file("file-X", "第五章")
                yield _create_file_done()
                yield _text(LONG_TEXT + "\n\n结尾要走悲剧还是圆满？")
            elif agent_type == "writer":
                ToolContext.clear_pending_empty_file()
                for event in edit_file_events("fix-1", LONG_TEXT, file_id="file-X"):
                    yield event
                yield _text("已补写第五章。")
            else:
                yield _text("审查完毕。[TASK_COMPLETE]")

        await _run_graph(fake_agent, auto_review_threshold=100)

        assert calls == ["writer", "writer", "quality_reviewer"]

    @pytest.mark.asyncio
    async def test_writer_question_prose_does_not_block_planned_handoff_after_correction(self):
        """普通问句不是控制信号；纠偏后仍执行计划内 quality_reviewer。"""
        calls: list[str] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            if len(calls) == 1:
                ToolContext.set_pending_empty_file("file-X", "第五章")
                yield _create_file_done()
                yield _text(LONG_TEXT + "\n\n结尾要走悲剧还是圆满？")
            elif agent_type == "writer":
                ToolContext.clear_pending_empty_file()
                yield _text("已补写第五章。")
            else:
                yield _text("审查完毕。[TASK_COMPLETE]")

        await _run_graph(
            fake_agent,
            router_result={
                "current_agent": "writer",
                "workflow_plan": "full",
                "workflow_agents": ["quality_reviewer"],
                "routing_metadata": {},
            },
        )

        assert calls == ["writer", "writer", "quality_reviewer"]

    @pytest.mark.asyncio
    async def test_structured_clarification_in_correction_round_blocks_planned_handoff(self):
        """纠偏轮明确发出 clarification_needed 时，必须停下等待回答。"""
        calls: list[str] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            if len(calls) == 1:
                ToolContext.set_pending_empty_file("file-X", "第五章")
                yield _create_file_done()
                yield _text(LONG_TEXT)
            elif agent_type == "writer":
                ToolContext.clear_pending_empty_file()
                yield _text("正文已补齐，但结局方向需要确认。")
                yield StreamEvent(
                    type=StreamEventType.WORKFLOW_STOPPED,
                    data={
                        "reason": "clarification_needed",
                        "agent_type": "writer",
                        "message": "结尾要走悲剧还是圆满？",
                    },
                )
            else:
                yield _text("不应运行审稿。")

        events = await _run_graph(
            fake_agent,
            router_result={
                "current_agent": "writer",
                "workflow_plan": "full",
                "workflow_agents": ["quality_reviewer"],
                "routing_metadata": {},
            },
        )

        assert calls == ["writer", "writer"]
        assert any(
            event.type == StreamEventType.WORKFLOW_STOPPED
            and event.data.get("reason") == "clarification_needed"
            for event in events
        )

    @pytest.mark.asyncio
    async def test_read_only_request_rolls_back_instead_of_correcting(self):
        calls: list[str] = []
        states: list[dict] = []

        async def fake_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            states.append(dict(state))
            ToolContext.set_pending_empty_file("file-X", "笔记")
            yield _create_file_done()
            yield _text("我的分析如上。")

        with patch(
            "agent.graph.writing_graph._rollback_unfinished_empty_files",
            return_value={"file-X"},
        ) as rollback:
            await _run_graph(
                fake_agent,
                router_result={
                    "current_agent": "writer",
                    "workflow_plan": "quick",
                    "workflow_agents": [],
                    "routing_metadata": {"read_only": True},
                },
            )

        assert calls == ["writer"]
        rollback.assert_called_once()
        # runner 据此让写文件工具的调用一律被拒绝
        assert states[0].get("read_only") is True
