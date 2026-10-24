"""Trusted resume scope survives graph timing and service persistence boundaries."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import desc, select

from agent.core.run_meter import AgentRunMeter
from agent.core.workflow_events import StreamEvent, StreamEventType
from models import ChatMessage, ChatSession
from tests.test_agent.test_service import (
    mock_agent_service as _mock_agent_service_fixture,
)
from tests.test_agent.test_service import (
    test_user_with_project as _test_user_with_project_fixture,
)
from tests.test_agent.test_stage_timing_integration import _SyntheticResult

mock_agent_service = _mock_agent_service_fixture
test_user_with_project = _test_user_with_project_fixture


def _router_response(payload: dict[str, object]) -> dict[str, object]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    }


@pytest.mark.asyncio
@pytest.mark.integration
async def test_router_off_trusted_resume_keeps_scope_and_records_router_and_writer_timing():
    from agent.graph.writing_graph import run_writing_workflow_streaming

    meter = AgentRunMeter()
    route_model = AsyncMock(
        return_value=_router_response(
            {
                "agent_type": "writer",
                "workflow_type": "quick",
                "turn_disposition": "resume_interrupted_task",
                "read_only": False,
                "write_content": True,
                "scope": "不要改前两章",
            }
        )
    )

    async def sdk_events():
        yield SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(
                type="response.output_text.delta",
                delta="第三章已按原范围续写。[TASK_COMPLETE]",
            ),
        )

    sdk_result = _SyntheticResult(sdk_events)
    state = {
        "user_message": "继续",
        "router_message": "继续",
        "messages": [
            {"role": "user", "content": "只写第三章，保留第一人称"},
            {
                "role": "assistant",
                "content": "第三章尚未写完。",
                "stop_reason": "no_progress",
                "routing": {
                    "initial_agent": "writer",
                    "workflow_type": "quick",
                    "last_agent": "writer",
                    "owner_agent": "writer",
                    "read_only": False,
                    "write_content": True,
                    "scope": "只写第三章",
                    "scope_constraints": ["保留第一人称"],
                    "requested_file_types": ["draft"],
                },
            },
            {"role": "user", "content": "继续"},
        ],
        "system_prompt": "",
        "run_meter": meter,
    }

    with (
        patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "off"}),
        patch("agent.graph.router._route_with_chat_model", route_model),
        patch("agents.Runner.run_streamed", return_value=sdk_result) as sdk_runner,
    ):
        events = [
            event
            async for event in run_writing_workflow_streaming(
                state=state,
                thread_id="timing-trusted-resume",
            )
        ]

    route_model.assert_awaited_once()
    sdk_runner.assert_called_once()
    decided = next(event for event in events if event.type == StreamEventType.ROUTER_DECIDED)
    assert decided.data["routing_metadata"]["scope_constraints"] == [
        "只写第三章",
        "保留第一人称",
        "不要改前两章",
    ]
    assert decided.data["routing_metadata"]["requested_file_types"] == ["draft"]
    timings = meter.timing_report()["stage_timings"]
    assert timings["router"]["count"] == 1
    assert timings["agent_writer"]["count"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("writing_prompt_configs")
async def test_process_stream_persists_full_resumed_scope_contract(
    mock_agent_service,
    test_user_with_project,
    db_session,
):
    service, _context_assembler = mock_agent_service
    project = test_user_with_project["project"]
    user = test_user_with_project["user"]
    routing_metadata = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "read_only": False,
        "write_content": True,
        "scope": "只写第三章",
        "scope_constraints": ["只写第三章", "保留第一人称", "不要改前两章"],
        "requested_file_types": ["draft"],
        "turn_disposition": "resume_interrupted_task",
    }

    async def workflow_events():
        yield StreamEvent(
            type=StreamEventType.ROUTER_DECIDED,
            data={
                "initial_agent": "writer",
                "workflow_plan": "quick",
                "workflow_agents": [],
                "routing_metadata": routing_metadata,
            },
        )
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第三章续写完成。"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "no_progress"})

    with patch(
        "agent.service.run_writing_workflow_streaming",
        return_value=workflow_events(),
    ):
        async for _event in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="继续",
            session=db_session,
        ):
            pass

    assistant = db_session.exec(
        select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.project_id == str(project.id))
        .where(ChatMessage.role == "assistant")
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
    ).first()
    assert assistant is not None
    metadata = json.loads(assistant.message_metadata or "{}")
    assert metadata["stop_reason"] == "no_progress"
    assert metadata["routing"] == {
        "initial_agent": "writer",
        "workflow_type": "quick",
        "read_only": False,
        "write_content": True,
        "scope": "只写第三章",
        "scope_constraints": ["只写第三章", "保留第一人称", "不要改前两章"],
        "requested_file_types": ["draft"],
        "last_agent": "writer",
        "owner_agent": "writer",
    }
