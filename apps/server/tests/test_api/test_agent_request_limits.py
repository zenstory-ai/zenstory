"""AgentRequest 的长度/数量上限：超限在入口直接 422，绝不进入计费与 LLM 调用。"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from api.agent import (
    AGENT_MESSAGE_MAX_CHARS,
    AGENT_METADATA_MAX_CHARS,
    AGENT_METADATA_MAX_LIST_ITEMS,
    AGENT_SELECTED_TEXT_MAX_CHARS,
    AgentRequest,
)
from models import Project, User
from services.core.auth_service import hash_password


def test_limits_accept_boundary_values():
    request = AgentRequest(
        project_id="p1",
        message="字" * AGENT_MESSAGE_MAX_CHARS,
        selected_text="选" * AGENT_SELECTED_TEXT_MAX_CHARS,
        metadata={"attached_file_ids": [f"f{i}" for i in range(AGENT_METADATA_MAX_LIST_ITEMS)]},
    )
    assert len(request.message) == AGENT_MESSAGE_MAX_CHARS


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "字" * (AGENT_MESSAGE_MAX_CHARS + 1)},
        {"message": "hi", "selected_text": "选" * (AGENT_SELECTED_TEXT_MAX_CHARS + 1)},
        {
            "message": "hi",
            "metadata": {"text_quotes": [{"text": "x"}] * (AGENT_METADATA_MAX_LIST_ITEMS + 1)},
        },
        {
            "message": "hi",
            "metadata": {"attached_file_ids": [f"f{i}" for i in range(AGENT_METADATA_MAX_LIST_ITEMS + 1)]},
        },
        {"message": "hi", "metadata": {"blob": "x" * (AGENT_METADATA_MAX_CHARS + 1)}},
    ],
)
def test_limits_reject_oversized_payloads(payload):
    with pytest.raises(ValidationError):
        AgentRequest(project_id="p1", **payload)


@pytest.mark.integration
async def test_stream_rejects_oversized_message_with_422_before_charging(
    client: AsyncClient, db_session
):
    user = User(
        username="agent_limit_user",
        email="agent_limit_user@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Limits", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    login = await client.post(
        "/api/auth/login", data={"username": "agent_limit_user", "password": "password123"}
    )
    token = login.json()["access_token"]

    with patch("api.agent.quota_service.reserve_ai_conversation") as consume:
        too_long = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": project.id, "message": "字" * (AGENT_MESSAGE_MAX_CHARS + 1)},
            headers={"Authorization": f"Bearer {token}"},
        )
        too_many = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": project.id,
                "message": "hi",
                "metadata": {
                    "attached_file_ids": [
                        f"f{i}" for i in range(AGENT_METADATA_MAX_LIST_ITEMS + 1)
                    ]
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert too_long.status_code == 422
    # metadata 校验失败也必须是可序列化的 422，而不是 500。
    assert too_many.status_code == 422
    assert too_many.json()["error_code"] == "ERR_VALIDATION_ERROR"
    consume.assert_not_called()
