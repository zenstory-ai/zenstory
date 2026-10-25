import asyncio
import uuid

import pytest
from httpx import AsyncClient

from agent.core.steering import has_active_runs_async
from tests.real_llm.sse_utils import collect_sse_events, event_names


@pytest.mark.integration
@pytest.mark.real_llm
async def test_steer_real_llm_injected_during_stream(
    client: AsyncClient,
    real_auth_context: dict,
    require_deepseek_key,
):
    context = real_auth_context
    project = context["project"]
    session_id = str(uuid.uuid4())

    # /steer only queues for a run that will still read it (F-28): start the stream
    # first and steer once its run holds the session.
    stream_task = asyncio.create_task(
        client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "session_id": session_id,
                "message": "请分三步给我写作建议，每一步先想清楚再写。",
            },
            headers=context["headers"],
        )
    )
    async with asyncio.timeout(60):
        while not await has_active_runs_async(session_id):
            assert not stream_task.done(), "stream ended before its run registered"
            await asyncio.sleep(0.05)

    steering_message = "请在后续回答中保持一句话总结。"
    steer_response = await client.post(
        "/api/v1/agent/steer",
        json={
            "session_id": session_id,
            "message": steering_message,
        },
        headers=context["headers"],
    )
    # A fast reply can reach the run's sealing drain before the steer lands: then the
    # steer is refused with 404 (the frontend sends it as the next message) instead of
    # being acknowledged and never read. Both outcomes are correct.
    assert steer_response.status_code in (200, 404), steer_response.text
    steer_acknowledged = steer_response.status_code == 200
    if steer_acknowledged:
        steer_json = steer_response.json()
        assert steer_json.get("queued") is True
        assert steer_json.get("message_id")

    stream_response = await stream_task
    assert stream_response.status_code == 200
    assert "text/event-stream" in stream_response.headers["content-type"]

    events = await collect_sse_events(stream_response, timeout_seconds=300.0)
    names = event_names(events)
    assert names
    assert "session_started" in names
    if steer_acknowledged:
        assert "steering_received" in names, (
            f"Expected steering_received in events, got: {names[-20:]}"
        )
    assert any(
        name in {"done", "error", "workflow_stopped", "workflow_complete"}
        for name in names
    ), f"No terminal event found in SSE stream: {names[-20:]}"
