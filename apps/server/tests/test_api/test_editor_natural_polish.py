"""Tests for /api/v1/editor/natural-polish."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import Project, User
from services.core.auth_service import hash_password
from services.features.natural_polish_service import NaturalPolishResult

_CHARGED_PERIOD = datetime(2026, 10, 5, 16, tzinfo=UTC)


def _create_user(
    db_session: Session,
    *,
    username: str,
    email: str,
    is_superuser: bool,
) -> User:
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _login(client: AsyncClient, username: str) -> str:
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _create_project(db_session: Session, owner_id: str, name: str = "np project") -> Project:
    project = Project(name=name, owner_id=owner_id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.mark.integration
async def test_natural_polish_success(client: AsyncClient, db_session: Session):
    user = _create_user(
        db_session,
        username="np_user_success",
        email="np_user_success@example.com",
        is_superuser=False,
    )
    project = _create_project(db_session, owner_id=user.id)
    token = await _login(client, user.username)

    with (
        patch(
            "api.editor.quota_service.check_ai_conversation_quota",
            return_value=(True, 0, 20),
        ) as mock_check,
        patch(
            "api.editor.quota_service.reserve_ai_conversation",
            return_value=_CHARGED_PERIOD,
        ) as mock_consume,
        patch(
            "api.editor.natural_polish_service.natural_polish",
            new=AsyncMock(return_value=NaturalPolishResult(polished_text="rewritten text", model="test-model")),
        ) as mock_polish,
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": str(project.id),
                "selected_text": "原始文本",
                "metadata": {"source": "editor_natural_polish"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == {"text": "rewritten text", "model": "test-model"}
    mock_check.assert_called_once_with(db_session, user.id)
    mock_consume.assert_called_once_with(db_session, user.id)
    mock_polish.assert_awaited_once_with(selected_text="原始文本", language="zh")


@pytest.mark.integration
async def test_natural_polish_requires_auth(client: AsyncClient):
    response = await client.post(
        "/api/v1/editor/natural-polish",
        json={
            "project_id": "any",
            "selected_text": "text",
        },
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_natural_polish_project_access_forbidden(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_project_forbidden",
        email="np_admin_project_forbidden@example.com",
        is_superuser=True,
    )
    token = await _login(client, admin.username)

    with patch("api.editor.quota_service.check_ai_conversation_quota") as mock_check:
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": "00000000-0000-0000-0000-000000000000",
                "selected_text": "text",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "ERR_NOT_AUTHORIZED"
    mock_check.assert_not_called()


@pytest.mark.integration
async def test_natural_polish_quota_denied(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_quota_denied",
        email="np_admin_quota_denied@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    with (
        patch(
            "api.editor.quota_service.check_ai_conversation_quota",
            return_value=(False, 3, 3),
        ) as mock_check,
        patch("api.editor.quota_service.reserve_ai_conversation") as mock_consume,
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": str(project.id),
                "selected_text": "text",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 402
    assert response.json()["detail"] == "ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED"
    mock_check.assert_called_once_with(db_session, admin.id)
    mock_consume.assert_not_called()


@pytest.mark.integration
async def test_natural_polish_consume_denied_after_check(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_consume_denied",
        email="np_admin_consume_denied@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    with (
        patch(
            "api.editor.quota_service.check_ai_conversation_quota",
            return_value=(True, 0, 20),
        ) as mock_check,
        patch(
            "api.editor.quota_service.reserve_ai_conversation",
            return_value=None,
        ) as mock_consume,
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": str(project.id),
                "selected_text": "text",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 402
    assert response.json()["detail"] == "ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED"
    mock_check.assert_called_once_with(db_session, admin.id)
    mock_consume.assert_called_once_with(db_session, admin.id)


@pytest.mark.integration
async def test_natural_polish_selected_text_empty(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_empty",
        email="np_admin_empty@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    response = await client.post(
        "/api/v1/editor/natural-polish",
        json={
            "project_id": str(project.id),
            "selected_text": "   ",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "ERR_VALIDATION_ERROR"


@pytest.mark.integration
async def test_natural_polish_selected_text_too_long(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_too_long",
        email="np_admin_too_long@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    response = await client.post(
        "/api/v1/editor/natural-polish",
        json={
            "project_id": str(project.id),
            "selected_text": "x" * 6001,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "ERR_VALIDATION_ERROR"


@pytest.mark.integration
async def test_natural_polish_selected_text_6000_is_allowed(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_6000_ok",
        email="np_admin_6000_ok@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    with (
        patch(
            "api.editor.quota_service.check_ai_conversation_quota",
            return_value=(True, 0, 20),
        ),
        patch(
            "api.editor.quota_service.reserve_ai_conversation",
            return_value=_CHARGED_PERIOD,
        ),
        patch(
            "api.editor.natural_polish_service.natural_polish",
            new=AsyncMock(return_value=NaturalPolishResult(polished_text="ok", model="test-model")),
        ),
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": str(project.id),
                "selected_text": "x" * 6000,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "ok"


@pytest.mark.integration
async def test_natural_polish_llm_failure_returns_500(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_llm_fail",
        email="np_admin_llm_fail@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    with (
        patch(
            "api.editor.quota_service.check_ai_conversation_quota",
            return_value=(True, 0, 20),
        ),
        patch(
            "api.editor.quota_service.reserve_ai_conversation",
            return_value=_CHARGED_PERIOD,
        ),
        patch(
            "api.editor.quota_service.release_ai_conversation",
            return_value=True,
        ) as mock_refund,
        patch(
            "api.editor.natural_polish_service.natural_polish",
            new=AsyncMock(side_effect=RuntimeError("llm boom")),
        ),
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": str(project.id),
                "selected_text": "text",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "ERR_INTERNAL_SERVER_ERROR"
    mock_refund.assert_called_once_with(
        db_session,
        admin.id,
        period_start=_CHARGED_PERIOD,
    )
