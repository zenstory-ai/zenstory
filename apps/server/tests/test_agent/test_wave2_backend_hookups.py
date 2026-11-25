"""Regression tests for Wave 2 Backend Cross-File Integration Hookups & P1 Runaway Closures.

Covers all 9 Wave 2 hookups:
1. runner.py -> IntraRunToolOutputTrimmer(run_meter=run_meter) wrap-up skip
2. runner.py -> stream_adapter.py -> api/agent.py + chat_message_ux_summary (server_draining & wall_clock_landing_exhausted)
3. service.py -> untrimmed effective_prompt_tokens persistence in message_metadata.prompt_tokens
4. service.py -> ui_action pass-through to AgentRequestContext -> read_skill_resource(PublicSkill('去AI味'))
5. mcp_tools.py -> create_file(file_type='folder') succeeds while a <file> body is pending
6. stream_adapter.py -> skip _handle_edit_file_result when guard_skipped=True
7. repeat_edit_guard.py + tools_adapter.py + runner.py + engine.py -> blocked-edits spin early stop (>=3)
8. service.py + history.py + compaction.py -> high-reasoning (>60k) post-turn background compaction
9. engine.py + run_support.py -> 5-min unit-boundary structural checkpoint (zero interference with short turns)
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from agent.core.events import SERVER_DRAINING_LIMIT, WALL_CLOCK_LIMIT
from agent.core.request_context import AgentRequestContext
from agent.core.round_outcome import RUN_DEADLINE_STOP_REASON, SERVER_DRAINING_STOP_REASON, RunOutcome
from agent.core.run_meter import MAX_WALL_CLOCK_LANDING_CALLS, AgentRunMeter
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop import compaction as compaction_mod
from agent.loop import engine as engine_mod
from agent.loop.compaction import (
    CompactionOutcome,
    get_inflight_compaction,
    reset_inflight_compactions,
    should_compact_post_turn,
)
from agent.loop.history import estimate_wire_reasoning_tokens
from agent.loop.ui_actions import OFFICIAL_NATURAL_POLISH_SKILL_NAME, UI_ACTION_NATURAL_POLISH
from agent.openai_agents import runner as runner_mod
from agent.openai_agents.repeat_edit_guard import (
    EDIT_REPEAT_LIMIT_ERROR_TYPE,
    MAX_BLOCKED_EDITS_PER_REQUEST,
    RepeatEditGuard,
)
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker
from agent.openai_agents.tools_adapter import build_agent_function_tools
from agent.service import AgentService, _has_blocking_wire_prefix_status_cards
from agent.stream_adapter import create_stream_adapter
from agent.tools.mcp_tools import create_file, read_skill_resource
from agent.tools.tool_context import _ReadSnapshots
from api.agent import _AgentStreamSettlement
from config import agent_runtime
from models import ChatMessage, ChatSession, File, Project, PublicSkill, SkillResource, User
from models.chat_message_ux_summary import classify_round, iteration_exhausted_abnormal_reason
from services.core.auth_service import hash_password


def _make_user_project_session(db_session: Session, suffix: str = "w2") -> tuple[User, Project, ChatSession]:
    user = User(
        username=f"user_{suffix}",
        email=f"user_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    chat = ChatSession(
        user_id=user.id,
        project_id=project.id,
        title="Wave 2 Test Session",
        is_active=True,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    return user, project, chat


def _parse_sse_frame(frame: str) -> tuple[str, dict[str, Any]]:
    event_name = ""
    payload: dict[str, Any] = {}
    for line in frame.splitlines():
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            payload = json.loads(line[len("data:") :].strip())
    return event_name, payload


# ---------------------------------------------------------------------------
# 1. runner.py -> IntraRunToolOutputTrimmer(run_meter=run_meter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_wires_run_meter_into_intra_run_trimmer(monkeypatch):
    """runner passes run_meter into IntraRunToolOutputTrimmer so trimming is skipped during wrap-up."""
    captured: dict[str, Any] = {}

    class FakeRunResult:
        def __init__(self) -> None:
            self.raw_responses: list[Any] = []
            self.is_complete = True

        async def stream_events(self):
            if False:
                yield None

        def to_input_list(self) -> list[dict[str, Any]]:
            return [{"role": "user", "content": "hi"}]

    def fake_run_streamed(_agent, *, input, run_config, **_kwargs):
        captured["filter"] = run_config.call_model_input_filter
        return FakeRunResult()

    monkeypatch.setattr("agents.Runner.run_streamed", fake_run_streamed)
    monkeypatch.setattr(runner_mod, "_preferred_model_name", AsyncMock(return_value="MiniMax-M3.1-Flash"))
    monkeypatch.setattr(runner_mod, "_build_agent", lambda *_a, **_kw: SimpleNamespace(model=SimpleNamespace()))

    meter = AgentRunMeter(started_at=time.monotonic() - 600, wrap_up_after_s=540)
    state: dict[str, Any] = {
        "user_message": "继续",
        "project_id": "p1",
        "user_id": "u1",
        "session_id": "s1",
        "run_meter": meter,
    }
    _ = [ev async for ev in runner_mod.run_openai_agents_streaming_agent(state, "loop", "sys")]

    turn_filter = captured["filter"]
    assert turn_filter._inner._run_meter is meter
    assert turn_filter._inner._is_past_wrap_up() is True


# ---------------------------------------------------------------------------
# 2. runner.py -> stream_adapter.py -> api/agent.py & chat_message_ux_summary
#    (server_draining & wall_clock_landing_exhausted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("draining", "landing_calls", "expected_limit", "expected_stop_reason", "expected_runaway", "expected_exhausted_flag"),
    [
        # Normal wall-clock soft-landing -> wall_clock, not runaway
        (False, 1, WALL_CLOCK_LIMIT, RUN_DEADLINE_STOP_REASON, False, False),
        # Wall-clock landing exhausted (>=3 tool-only calls after reminder) -> runaway_stop=True
        (False, MAX_WALL_CLOCK_LANDING_CALLS, WALL_CLOCK_LIMIT, RUN_DEADLINE_STOP_REASON, True, True),
        # Planned deploy drain before wall-clock -> server_draining, NOT runaway
        (True, 1, SERVER_DRAINING_LIMIT, SERVER_DRAINING_STOP_REASON, False, False),
        # Planned deploy drain even with exhausted landing -> still server_draining, NOT runaway
        (True, MAX_WALL_CLOCK_LANDING_CALLS, SERVER_DRAINING_LIMIT, SERVER_DRAINING_STOP_REASON, False, True),
    ],
)
async def test_runner_stream_adapter_settlement_and_ux_summary_for_wall_clock_and_drain(
    monkeypatch,
    draining: bool,
    landing_calls: int,
    expected_limit: str,
    expected_stop_reason: str,
    expected_runaway: bool,
    expected_exhausted_flag: bool,
):
    """End-to-end propagation of server_draining and wall_clock_landing_exhausted from runner -> SSE -> settlement -> UX summary."""
    from agents.run_config import CallModelData, ModelInputData

    monkeypatch.setattr("agent.core.run_meter.is_draining", lambda: draining)

    class FakeRunResult:
        def __init__(self) -> None:
            self.raw_responses: list[Any] = []
            self.is_complete = True

        async def stream_events(self):
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta="阶段总结已完成。"),
            )

        def to_input_list(self) -> list[dict[str, Any]]:
            return [{"role": "user", "content": "写五章"}, {"role": "assistant", "content": "阶段总结已完成。"}]

    def fake_run_streamed(_agent, *, input, run_config, **_kwargs):
        # Invoke the input filter landing_calls times to inject wall-clock / drain landing.
        md = ModelInputData(input=list(input), instructions="sys")
        for _ in range(landing_calls):
            run_config.call_model_input_filter(CallModelData(model_data=md, agent=None, context=None))
        return FakeRunResult()

    monkeypatch.setattr("agents.Runner.run_streamed", fake_run_streamed)
    monkeypatch.setattr(runner_mod, "_preferred_model_name", AsyncMock(return_value="MiniMax-M3.1-Flash"))
    monkeypatch.setattr(runner_mod, "_build_agent", lambda *_a, **_kw: SimpleNamespace(model=SimpleNamespace()))

    # For non-draining tests, elapsed >= wrap_up_after_s; for draining tests, elapsed < wrap_up_after_s.
    started_at = time.monotonic() - (10 if draining else 600)
    meter = AgentRunMeter(started_at=started_at, wrap_up_after_s=540)
    state: dict[str, Any] = {
        "user_message": "写五章",
        "project_id": "p1",
        "user_id": "u1",
        "session_id": "s1",
        "run_meter": meter,
    }

    wf_events = [ev async for ev in runner_mod.run_openai_agents_streaming_agent(state, "loop", "sys")]
    exhausted_ev = next(ev for ev in wf_events if ev.type == StreamEventType.ITERATION_EXHAUSTED)
    end_ev = next(ev for ev in wf_events if ev.type == StreamEventType.MESSAGE_END)

    assert exhausted_ev.data["limit"] == expected_limit
    assert exhausted_ev.data["wall_clock_landing_exhausted"] is expected_exhausted_flag
    if draining:
        assert exhausted_ev.data.get("server_draining") is True
    else:
        assert "server_draining" not in exhausted_ev.data
    assert end_ev.data["stop_reason"] == expected_stop_reason

    # Feed workflow events through StreamAdapter -> SSE frames -> _AgentStreamSettlement.
    adapter = create_stream_adapter(project_id="p1", user_id="u1", run_meter=meter)

    async def wf_gen():
        for ev in wf_events:
            yield ev

    sse_events = [ev async for ev in adapter.process_workflow_events(wf_gen())]
    assert adapter.get_last_message_metadata()["stop_reason"] == expected_stop_reason

    settlement = _AgentStreamSettlement(
        session=None,  # type: ignore[arg-type]
        user_id="u1",
        project_id="p1",
        agent_run_id="run-w2",
        charged_period_start=datetime.now(UTC),
        lang="zh",
        knows_stop_contract=True,
        activation_entry="workbench",
        run_outcome=RunOutcome(),
    )
    for sse_ev in sse_events:
        settlement.observe_frame(sse_ev.to_sse())

    assert settlement.wall_clock_card_seen is True
    assert settlement.runaway_stop is expected_runaway

    # Verify UX summary classification & wire-prefix cacheability for server_draining.
    card_meta = {
        "stop_reason": expected_stop_reason,
        "status_cards": [
            {
                "type": "iteration_exhausted",
                "layer": "tool_call",
                "limit": expected_limit,
            }
        ],
    }
    category, abnormal = classify_round(card_meta)
    assert category == "abnormal"
    assert abnormal == expected_stop_reason
    assert iteration_exhausted_abnormal_reason({"status_cards": card_meta["status_cards"]}) == expected_stop_reason
    assert _has_blocking_wire_prefix_status_cards(card_meta["status_cards"]) is False


# ---------------------------------------------------------------------------
# 3. service.py -> effective_prompt_tokens persisted in message_metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_service_persists_effective_untrimmed_prompt_tokens(db_session: Session):
    """When late intra-run trimming drops last_prompt_tokens from 425k -> 239k, message_metadata persists 425k."""
    user, project, chat = _make_user_project_session(db_session, "prompt_tokens")

    def fake_loop(state, **_kwargs):
        meter: AgentRunMeter = state["run_meter"]
        meter.record_prompt_tokens(425_000)
        meter.record_prompt_tokens(239_000)  # trimmed tail call

        async def stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "已完成修改。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return stream()

    service = AgentService()
    with (
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop),
        patch("agent.service.should_compact_post_turn", return_value=False),
    ):
        _ = [
            frame
            async for frame in service.process_stream(
                project_id=project.id,
                user_id=user.id,
                message="检查全文",
                session=db_session,
                session_id=chat.id,
            )
        ]

    assistant_row = db_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == chat.id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc())
    ).first()
    assert assistant_row is not None
    meta = json.loads(assistant_row.message_metadata or "{}")
    assert meta["prompt_tokens"] == 425_000


# ---------------------------------------------------------------------------
# 4. service.py -> ui_action pass-through to AgentRequestContext -> read_skill_resource
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_service_passes_ui_action_to_request_context_enabling_official_skill_resource(db_session: Session):
    """ui_action='natural_polish' reaches AgentRequestContext and allows read_skill_resource on PublicSkill('去AI味')."""
    user, project, chat = _make_user_project_session(db_session, "ui_action")

    # Create a focus file and an approved official PublicSkill('去AI味') with a reference resource
    # WITHOUT adding UserAddedSkill for this user.
    focus_file = File(
        project_id=project.id,
        title="第1章 试读",
        file_type="draft",
        content="他不由得微微一笑，心中百感交集。",
    )
    official_skill = PublicSkill(
        name=OFFICIAL_NATURAL_POLISH_SKILL_NAME,
        description="官方去AI味技能",
        instructions="按 references/checklist.md 检查AI腔。",
        source="official",
        status="approved",
    )
    db_session.add(focus_file)
    db_session.add(official_skill)
    db_session.commit()
    db_session.refresh(focus_file)
    db_session.refresh(official_skill)
    db_session.add(
        SkillResource(
            public_skill_id=official_skill.id,
            path="references/checklist.md",
            content="禁止使用「不由得」「微微一笑」。",
            size=16,
        )
    )
    db_session.commit()

    captured_ui_action: list[Any] = []
    resource_result: list[dict[str, Any]] = []

    def fake_loop(state, **_kwargs):
        req_ctx: AgentRequestContext = state["request_context"]
        captured_ui_action.append(req_ctx.ui_action)

        async def stream():
            # Invoke read_skill_resource directly with the real AgentRequestContext constructed by service.py
            # using the test DB session factory.
            local_ctx = AgentRequestContext(
                session=db_session,
                user_id=req_ctx.user_id,
                project_id=req_ctx.project_id,
                session_id=req_ctx.session_id,
                ui_action=req_ctx.ui_action,
            )
            raw = await read_skill_resource(
                {"name": OFFICIAL_NATURAL_POLISH_SKILL_NAME, "path": "references/checklist.md"},
                request_context=local_ctx,
            )
            payload = json.loads(raw["content"][0]["text"])
            resource_result.append(payload)
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "去AI味完成。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return stream()

    service = AgentService()
    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
        _ = [
            frame
            async for frame in service.process_stream(
                project_id=project.id,
                user_id=user.id,
                message="帮我去AI味",
                session=db_session,
                session_id=chat.id,
                metadata={"current_file_id": focus_file.id},
                ui_action=UI_ACTION_NATURAL_POLISH,
            )
        ]

    assert captured_ui_action == [UI_ACTION_NATURAL_POLISH]
    assert len(resource_result) == 1
    assert resource_result[0]["status"] == "success"
    assert "禁止使用" in resource_result[0]["data"]["content"]


# ---------------------------------------------------------------------------
# 5. mcp_tools.py -> folder creation succeeds while a <file> body is pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_folder_succeeds_while_another_file_awaits_body(db_session: Session):
    """create_file(file_type='folder') does not reserve/conflict with an active pending <file> body."""
    user, project, chat = _make_user_project_session(db_session, "folder_pending")
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id=chat.id,
    )
    try:
        # 1. Create an empty outline file -> enters pending <file> body state.
        outline_res = await create_file(
            {"title": "第一卷大纲", "file_type": "outline"},
            request_context=req_ctx,
        )
        outline_payload = json.loads(outline_res["content"][0]["text"])
        assert outline_payload["status"] == "success"
        assert outline_payload["data"]["awaiting_body"] is True
        outline_id = outline_payload["data"]["id"]
        assert req_ctx.has_pending_empty_file() is True

        # 2. Creating a folder while outline_id is awaiting <file> MUST succeed cleanly!
        folder_res = await create_file(
            {"title": "设定资料夹", "file_type": "folder"},
            request_context=req_ctx,
        )
        folder_payload = json.loads(folder_res["content"][0]["text"])
        assert folder_payload["status"] == "success"
        assert folder_payload["mutation_applied"] is True
        assert folder_payload["data"]["file_type"] == "folder"
        assert folder_payload["data"].get("awaiting_body") is not True

        # 3. The original pending empty file reservation remains intact (not cleared or overwritten).
        pending = req_ctx.get_pending_empty_files()
        assert [p["file_id"] for p in pending] == [outline_id]
    finally:
        req_ctx.clear()


# ---------------------------------------------------------------------------
# 6. stream_adapter.py -> guard_skipped=True skips _handle_edit_file_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_adapter_skips_file_edit_events_on_guard_skipped():
    """When RepeatEditGuard returns guard_skipped=True, StreamAdapter emits tool_result but NO file_edit_* events."""
    adapter = create_stream_adapter(project_id="p1", user_id="u1")
    skipped_payload = {
        "status": "skipped",
        "guard_skipped": True,
        "reason": "post_write_limit",
        "message": "已达上限",
        "error_type": EDIT_REPEAT_LIMIT_ERROR_TYPE,
        "tool_name": "edit_file",
        "id": "ch1",
        "edits_applied": 0,
        "mutation_applied": False,
        "user_message": "这一步的细微调整已跳过，之前的修改都已保存。",
    }

    async def wf_gen():
        yield StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": "call-skip-1",
                "name": "edit_file",
                "result": {"content": [{"type": "text", "text": json.dumps(skipped_payload, ensure_ascii=False)}]},
            },
        )
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    sse_events = [ev async for ev in adapter.process_workflow_events(wf_gen())]
    event_types = [ev.type.value for ev in sse_events]
    assert "tool_result" in event_types
    assert "file_edit_start" not in event_types
    assert "file_edit_applied" not in event_types
    assert "file_edit_end" not in event_types
    tool_res = next(ev for ev in sse_events if ev.type.value == "tool_result")
    assert tool_res.data["status"] == "success"
    assert tool_res.data["data"]["guard_skipped"] is True


# ---------------------------------------------------------------------------
# 7. repeat_edit_guard.py + tools_adapter.py + runner.py + engine.py
#    (blocked-edits spin early stop after >=3 blocks)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_repeat_edit_guard_spin_early_stop_in_adapter_and_runner():
    """After >=3 edit_repeat_limit blocks, spin_limit_reached short-circuits trailing calls and stops runner cleanly."""
    req_ctx = AgentRequestContext(
        session=None, user_id="u1", project_id="p1", session_id="s1", read_snapshots=_ReadSnapshots()
    )
    try:
        req_ctx.mark_chapter_body_saved("ch1", "第1章", "draft")
        breaker = ToolFailureBreaker()
        read_guard = RepeatReadGuard(request_context=req_ctx)
        edit_guard = RepeatEditGuard(request_context=req_ctx)
        query_invoked = 0

        async def fake_edit(_args, **_kwargs):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "status": "success",
                                "mutation_applied": True,
                                "data": {"id": "ch1", "title": "第1章", "new_length": 2000, "new_word_count": 1950},
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            }

        async def fake_query(_args, **_kwargs):
            nonlocal query_invoked
            query_invoked += 1
            return {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": []})}]}

        with patch.dict(
            "agent.openai_agents.tools_adapter.TOOL_FUNCTIONS",
            {"edit_file": fake_edit, "query_files": fake_query},
        ):
            tools = {
                t.name: t
                for t in build_agent_function_tools(
                    failure_breaker=breaker,
                    read_guard=read_guard,
                    edit_guard=edit_guard,
                )
            }
            raw_edit = json.dumps({"id": "ch1", "edits": [{"op": "replace", "old": "甲", "new": "乙"}]})
            # 2 allowed post-write edits
            await tools["edit_file"].on_invoke_tool(req_ctx, raw_edit)
            await tools["edit_file"].on_invoke_tool(req_ctx, raw_edit)
            assert edit_guard.blocked_edits == 0
            assert edit_guard.spin_limit_reached is False

            # 1st & 2nd blocked edits do NOT trip early stop yet
            for expected_blocks in (1, 2):
                out = json.loads(await tools["edit_file"].on_invoke_tool(req_ctx, raw_edit))
                assert out["guard_skipped"] is True
                assert edit_guard.blocked_edits == expected_blocks
                assert edit_guard.spin_limit_reached is False
                stop_check = runner_mod._stop_run_after_tools(
                    req_ctx, [], failure_breaker=breaker, read_guard=read_guard, edit_guard=edit_guard
                )
                assert stop_check.is_final_output is False

            # 3rd blocked edit hits MAX_BLOCKED_EDITS_PER_REQUEST -> spin_limit_reached!
            out3 = json.loads(await tools["edit_file"].on_invoke_tool(req_ctx, raw_edit))
            assert out3["guard_skipped"] is True
            assert edit_guard.blocked_edits == MAX_BLOCKED_EDITS_PER_REQUEST
            assert edit_guard.spin_limit_reached is True

            # Trailing tool call in the same turn is short-circuited without execution!
            trailing = json.loads(await tools["query_files"].on_invoke_tool(req_ctx, json.dumps({"id": "other"})))
            assert trailing["guard_skipped"] is True
            assert trailing["status"] == "skipped"
            assert query_invoked == 0

            # _stop_run_after_tools synchronously ends the SDK run cleanly!
            stop_result = runner_mod._stop_run_after_tools(
                req_ctx, [], failure_breaker=breaker, read_guard=read_guard, edit_guard=edit_guard
            )
            assert stop_result.is_final_output is True
            assert breaker.is_open is False
    finally:
        req_ctx.clear()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_engine_does_not_continue_after_edit_spin_early_stop(monkeypatch):
    """When a run stops via edit_guard.spin_limit_reached, engine finishes cleanly instead of launching a self-check run."""
    runs_called = 0

    async def fake_runner(state, *_args, **_kwargs):
        nonlocal runs_called
        runs_called += 1
        req_ctx: AgentRequestContext = state["request_context"]
        req_ctx.mark_chapter_body_saved("ch1", "第1章", "draft")
        state["repeat_edit_guard"].blocked_edits = MAX_BLOCKED_EDITS_PER_REQUEST
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "之前的修改都已保存，本轮先停在这里。"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    monkeypatch.setattr(engine_mod, "run_openai_agents_streaming_agent", fake_runner)
    monkeypatch.setattr(
        engine_mod.task_board_completion,
        "auto_finalize_task_board_on_completion",
        AsyncMock(return_value=([], False)),
    )

    state: dict[str, Any] = {
        "user_message": "写第一章并润色",
        "project_id": "p1",
        "user_id": "u1",
        "session_id": "s1",
        "system_prompt": "sys",
    }
    events = [ev async for ev in engine_mod.run_loop_agent_streaming(state)]
    assert runs_called == 1
    assert not any(ev.type == StreamEventType.ERROR for ev in events)
    assert not any(ev.type == StreamEventType.ITERATION_EXHAUSTED for ev in events)


# ---------------------------------------------------------------------------
# 8. service.py + history.py + compaction.py (high-reasoning post-turn compaction)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_estimate_wire_reasoning_tokens_and_should_compact_post_turn(monkeypatch):
    """Accumulated reasoning items >= 60k estimated tokens trigger post-turn compaction even when prompt_tokens < 240k."""
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_COMPACT_AT_TOKENS", 240_000)
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_COMPACT_REASONING_TOKENS", 60_000)

    # ~100k Chinese chars -> ~65k provider tokens (> 60k threshold)
    heavy_wire_items = [
        {"role": "user", "content": "写第一章"},
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "思" * 100_000}]},
        {"role": "assistant", "content": "第一章正文"},
    ]
    light_wire_items = [
        {"role": "user", "content": "写第一章"},
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "简短思考"}]},
        {"role": "assistant", "content": "第一章正文"},
    ]

    heavy_tokens = estimate_wire_reasoning_tokens(heavy_wire_items)
    light_tokens = estimate_wire_reasoning_tokens(light_wire_items)
    assert heavy_tokens >= 60_000
    assert light_tokens < 1_000

    # Prompt under 240k (120k), heavy reasoning -> triggers post-turn compaction!
    assert should_compact_post_turn(last_prompt_tokens=120_000, reasoning_tokens=heavy_tokens) is True
    # Prompt under 240k (120k), light reasoning -> does NOT trigger!
    assert should_compact_post_turn(last_prompt_tokens=120_000, reasoning_tokens=light_tokens) is False
    # Disabled via threshold=0 -> does NOT trigger!
    assert should_compact_post_turn(
        last_prompt_tokens=120_000, reasoning_tokens=heavy_tokens, reasoning_threshold=0
    ) is False
    # Respects non-provider-error backoff
    assert should_compact_post_turn(
        last_prompt_tokens=120_000,
        reasoning_tokens=heavy_tokens,
        skipped_at_tokens=120_000,
        skipped_reason=compaction_mod.SKIPPED_EMPTY_OR_TRUNCATED,
    ) is False


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_service_triggers_post_turn_compaction_on_high_reasoning_under_240k(db_session: Session, monkeypatch):
    """End-to-end: clean turn with 130k prompt_tokens (<240k) and >60k wire reasoning schedules background compaction."""
    reset_inflight_compactions()
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_COMPACT_AT_TOKENS", 240_000)
    monkeypatch.setattr(agent_runtime, "AGENT_LOOP_COMPACT_REASONING_TOKENS", 60_000)
    try:
        user, project, chat = _make_user_project_session(db_session, "high_reasoning")
        compact_called = asyncio.Event()
        compact_mock = AsyncMock(
            return_value=CompactionOutcome(
                record={
                    "summary": "交接摘要",
                    "covers_through_message_id": "placeholder",
                    "kept_author_messages": ["分析剧情"],
                    "created_at": datetime.now(UTC).isoformat(),
                },
                items=[{"role": "assistant", "content": "交接摘要"}],
            )
        )

        async def _compact_side_effect(history, **_kwargs):
            compact_called.set()
            res = await compact_mock(history)
            res.record["covers_through_message_id"] = history.last_message_id
            return res

        def fake_loop(state, **_kwargs):
            state["run_meter"].record_prompt_tokens(130_000)  # well under 240k!
            state["run_meter"].last_served_provider_key = "minimax:MiniMax-M3.1-Flash:primary"
            state["run_items"] = [
                {"role": "user", "content": "分析剧情"},
                {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "推" * 100_000}]},
                {"role": "assistant", "content": "剧情分析完成。"},
            ]

            async def stream():
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "剧情分析完成。"})
                yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

            return stream()

        service = AgentService()
        with (
            patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop),
            patch("agent.service.compact_history", side_effect=_compact_side_effect),
            patch("agent.service.persist_post_turn_compaction_sync", return_value=True),
            patch("agent.service.astore_wire_prefix", AsyncMock(return_value=True)),
        ):
            frames = [
                frame
                async for frame in service.process_stream(
                    project_id=project.id,
                    user_id=user.id,
                    message="分析剧情",
                    session=db_session,
                    session_id=chat.id,
                )
            ]
            assert any(_parse_sse_frame(f)[0] == "done" for f in frames)
            inflight = get_inflight_compaction(chat.id)
            if inflight is not None:
                await asyncio.wait_for(inflight.task, timeout=2.0)
            else:
                await asyncio.wait_for(compact_called.wait(), timeout=2.0)

        assert compact_mock.await_count == 1
    finally:
        reset_inflight_compactions()


# ---------------------------------------------------------------------------
# 9. engine.py -> 5-min unit-boundary structural checkpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_engine_unit_checkpoint_stops_cleanly_at_5min_with_saved_chapter_and_incomplete_tasks(monkeypatch):
    """At elapsed_s >= 300s with >=1 saved chapter and incomplete tasks, engine checkpoints cleanly after run 1."""
    monkeypatch.setattr(agent_runtime, "AGENT_UNIT_CHECKPOINT_AFTER_S", 300.0)
    runs_called = 0

    async def fake_runner(state, *_args, **_kwargs):
        nonlocal runs_called
        runs_called += 1
        req_ctx: AgentRequestContext = state["request_context"]
        req_ctx.mark_chapter_body_saved("ch1", "第1章 启程", "draft")
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>第一章正文</file>"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    monkeypatch.setattr(engine_mod, "run_openai_agents_streaming_agent", fake_runner)
    monkeypatch.setattr(
        engine_mod.task_board_completion,
        "auto_finalize_task_board_on_completion",
        AsyncMock(return_value=([], True)),  # chapters 2-5 still incomplete
    )
    monkeypatch.setattr(engine_mod.run_support, "_ai_messages_left_today", lambda _uid: True)

    meter = AgentRunMeter(started_at=time.monotonic() - 320.0, wrap_up_after_s=540)
    state: dict[str, Any] = {
        "user_message": "连写五章",
        "project_id": "p1",
        "user_id": "u1",
        "session_id": "s1",
        "system_prompt": "sys",
        "run_meter": meter,
    }
    events = [ev async for ev in engine_mod.run_loop_agent_streaming(state)]
    assert runs_called == 1, "must not grind a 2nd run past 5-min checkpoint"
    exhausted = [ev for ev in events if ev.type == StreamEventType.ITERATION_EXHAUSTED]
    assert len(exhausted) == 1
    assert exhausted[0].data["layer"] == "collaboration"
    assert exhausted[0].data["limit"] == WALL_CLOCK_LIMIT
    assert any("第1章 启程" in str(ev.data.get("text", "")) for ev in events if ev.type == StreamEventType.TEXT)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_engine_unit_checkpoint_finishes_cleanly_without_pause_card_when_all_tasks_done(monkeypatch):
    """At elapsed_s >= 300s with >=1 saved chapter, pending_self_check, and NO incomplete tasks: clean end_turn, no pause card."""
    monkeypatch.setattr(agent_runtime, "AGENT_UNIT_CHECKPOINT_AFTER_S", 300.0)
    runs_called = 0

    async def fake_runner(state, *_args, **_kwargs):
        nonlocal runs_called
        runs_called += 1
        req_ctx: AgentRequestContext = state["request_context"]
        req_ctx.mark_chapter_body_saved("ch1", "第1章 启程", "draft")
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>第一章正文</file>"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    monkeypatch.setattr(engine_mod, "run_openai_agents_streaming_agent", fake_runner)
    monkeypatch.setattr(
        engine_mod.task_board_completion,
        "auto_finalize_task_board_on_completion",
        AsyncMock(return_value=([], False)),
    )

    meter = AgentRunMeter(started_at=time.monotonic() - 320.0, wrap_up_after_s=540)
    state: dict[str, Any] = {
        "user_message": "写第一章",
        "project_id": "p1",
        "user_id": "u1",
        "session_id": "s1",
        "system_prompt": "sys",
        "run_meter": meter,
    }
    events = [ev async for ev in engine_mod.run_loop_agent_streaming(state)]
    assert runs_called == 1
    assert not any(ev.type == StreamEventType.ITERATION_EXHAUSTED for ev in events)
    assert any("已保存" in str(ev.data.get("text", "")) for ev in events if ev.type == StreamEventType.TEXT)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_engine_unit_checkpoint_triggers_on_three_written_files_and_does_not_interfere_with_short_turns(
    monkeypatch,
):
    """Zero interference when elapsed_s < 300s; triggers when elapsed_s >= 300s and >=3 files written."""
    monkeypatch.setattr(agent_runtime, "AGENT_UNIT_CHECKPOINT_AFTER_S", 300.0)
    monkeypatch.setattr(
        engine_mod.task_board_completion,
        "auto_finalize_task_board_on_completion",
        AsyncMock(return_value=([], True)),
    )
    monkeypatch.setattr(engine_mod.run_support, "_ai_messages_left_today", lambda _uid: True)

    # Case A: short turn (elapsed_s ~ 0s < 300s) -> self-check run MUST proceed normally!
    short_runs = 0

    async def short_runner(state, *_args, **_kwargs):
        nonlocal short_runs
        short_runs += 1
        req_ctx: AgentRequestContext = state["request_context"]
        if short_runs == 1:
            req_ctx.mark_chapter_body_saved("ch1", "第1章", "draft")
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>正文</file>"})
        else:
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "自查完成，第一章已保存。"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    monkeypatch.setattr(engine_mod, "run_openai_agents_streaming_agent", short_runner)
    short_state: dict[str, Any] = {
        "user_message": "写第一章",
        "project_id": "p1",
        "user_id": "u1",
        "session_id": "s1",
        "system_prompt": "sys",
        "run_meter": AgentRunMeter(started_at=time.monotonic(), wrap_up_after_s=540),
    }
    short_events = [ev async for ev in engine_mod.run_loop_agent_streaming(short_state)]
    assert short_runs == 2, "short single-chapter turn must still run normal self-check"
    assert not any(ev.type == StreamEventType.ITERATION_EXHAUSTED for ev in short_events)

    # Case B: elapsed_s >= 300s with >= 3 written non-body files (outline/character/lore) -> checkpoints cleanly!
    batch_runs = 0

    async def batch_runner(state, *_args, **_kwargs):
        nonlocal batch_runs
        batch_runs += 1
        read_guard: RepeatReadGuard = state["repeat_read_guard"]
        read_guard.files_written["f1"] = "总纲"
        read_guard.files_written["f2"] = "主角卡"
        read_guard.files_written["f3"] = "世界观"
        yield StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={"id": "c1", "name": "create_file", "input": {"title": "世界观"}},
        )
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>世界观设定</file>"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    monkeypatch.setattr(engine_mod, "run_openai_agents_streaming_agent", batch_runner)
    batch_state: dict[str, Any] = {
        "user_message": "建整套设定集",
        "project_id": "p1",
        "user_id": "u1",
        "session_id": "s1",
        "system_prompt": "sys",
        "run_meter": AgentRunMeter(started_at=time.monotonic() - 320.0, wrap_up_after_s=540),
    }
    batch_events = [ev async for ev in engine_mod.run_loop_agent_streaming(batch_state)]
    assert batch_runs == 1
    exhausted = [ev for ev in batch_events if ev.type == StreamEventType.ITERATION_EXHAUSTED]
    assert len(exhausted) == 1
    assert exhausted[0].data["layer"] == "collaboration"
    assert exhausted[0].data["limit"] == WALL_CLOCK_LIMIT
