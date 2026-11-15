import pytest
from httpx import AsyncClient

from tests.real_llm.sse_utils import collect_sse_events, event_names


@pytest.mark.integration
@pytest.mark.real_llm
async def test_chat_session_lifecycle_after_real_stream(
    client: AsyncClient,
    real_auth_context: dict,
    require_deepseek_key,
):
    context = real_auth_context
    project = context["project"]
    project_id = str(project.id)

    session_response = await client.get(
        f"/api/v1/chat/session/{project_id}",
        headers=context["headers"],
    )
    assert session_response.status_code == 200
    first_session_id = session_response.json()["id"]

    stream_response = await client.post(
        "/api/v1/agent/stream",
        json={
            "project_id": project_id,
            "message": "Please answer with one sentence about the next plot point.",
        },
        headers=context["headers"],
    )
    assert stream_response.status_code == 200
    events = await collect_sse_events(stream_response, timeout_seconds=300.0)
    names = event_names(events)
    assert names
    assert any(
        name in {"done", "error", "workflow_stopped", "workflow_complete"}
        for name in names
    ), f"No terminal event found in SSE stream: {names[-20:]}"

    messages_response = await client.get(
        f"/api/v1/chat/session/{project_id}/recent?limit=20",
        headers=context["headers"],
    )
    assert messages_response.status_code == 200
    messages = messages_response.json()
    assert any(msg["role"] == "user" for msg in messages)
    assert any(msg["role"] == "assistant" for msg in messages)

    clear_response = await client.delete(
        f"/api/v1/chat/session/{project_id}",
        headers=context["headers"],
    )
    assert clear_response.status_code == 200
    assert clear_response.json().get("success") is True

    cleared_messages_response = await client.get(
        f"/api/v1/chat/session/{project_id}/recent?limit=20",
        headers=context["headers"],
    )
    assert cleared_messages_response.status_code == 200
    assert cleared_messages_response.json() == []

    new_session_response = await client.post(
        f"/api/v1/chat/session/{project_id}/new",
        headers=context["headers"],
    )
    assert new_session_response.status_code == 200
    new_session_id = new_session_response.json()["id"]
    assert new_session_id != first_session_id
