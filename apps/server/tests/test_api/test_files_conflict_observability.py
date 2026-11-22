"""Tests for server-side 409 stale_write structured observability in api/files.py."""

import logging
from datetime import timedelta

import pytest
from httpx import AsyncClient

from api.files import (
    get_stale_write_conflict_metrics,
    reset_stale_write_conflict_metrics,
)
from models import File, Project, User
from services.core.auth_service import hash_password


@pytest.mark.integration
async def test_stale_write_409_emits_structured_log_and_increments_counter_without_pii(
    client: AsyncClient,
    db_session,
    caplog: pytest.LogCaptureFixture,
):
    """409 stale_write must emit structured observability fields with zero content/title/PII."""
    reset_stale_write_conflict_metrics()

    secret_email = "secret-author-conflict@example.com"
    secret_username = "secret_author_conflict"
    secret_title = "绝密章节标题：雨夜惊变"
    secret_server_content = "服务端刚刚保存的绝密AI正文"
    secret_client_content = "作者本地尚未保存的绝密草稿"

    user = User(
        username=secret_username,
        email=secret_email,
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": secret_username, "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project = Project(name="Conflict Observability Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    target_file = File(
        project_id=project.id,
        title=secret_title,
        file_type="draft",
        content=secret_server_content,
    )
    db_session.add(target_file)
    db_session.commit()
    db_session.refresh(target_file)

    stale_token = (target_file.updated_at - timedelta(seconds=5)).isoformat()

    with caplog.at_level(logging.WARNING, logger="api.files"):
        resp_edit = await client.put(
            f"/api/v1/files/{target_file.id}",
            json={
                "content": secret_client_content,
                "change_type": "edit",
                "base_updated_at": stale_token,
            },
            headers=headers,
        )
        resp_auto = await client.put(
            f"/api/v1/files/{target_file.id}",
            json={
                "content": secret_client_content,
                "change_type": "auto_save",
                "base_updated_at": stale_token,
            },
            headers=headers,
        )

    assert resp_edit.status_code == 409
    assert resp_auto.status_code == 409

    # A fresh save must succeed and must NOT increment the conflict counter.
    resp_ok = await client.put(
        f"/api/v1/files/{target_file.id}",
        json={
            "content": "Fresh update",
            "change_type": "edit",
            "base_updated_at": target_file.updated_at.isoformat(),
        },
        headers=headers,
    )
    assert resp_ok.status_code == 200

    metrics = get_stale_write_conflict_metrics()
    assert metrics == {
        "total": 2,
        "by_change_type": {"edit": 1, "auto_save": 1},
    }

    conflict_records = [
        record for record in caplog.records if record.getMessage() == "Editor save conflict 409"
    ]
    assert len(conflict_records) == 2

    first_fields = getattr(conflict_records[0], "custom_fields", {})
    second_fields = getattr(conflict_records[1], "custom_fields", {})

    assert first_fields["event"] == "editor_save_conflict_409"
    assert first_fields["project_id"] == project.id
    assert first_fields["file_id"] == target_file.id
    assert first_fields["change_type"] == "edit"
    assert first_fields["reason"] == "stale_write"
    assert first_fields["process_conflict_count"] == 1
    assert isinstance(first_fields["stale_delta_ms"], int)
    assert first_fields["stale_delta_ms"] >= 5000

    assert second_fields["change_type"] == "auto_save"
    assert second_fields["process_conflict_count"] == 2

    # Ensure zero file content, title, or user PII leaked into log records.
    for record in conflict_records:
        serialized = f"{record.getMessage()} {getattr(record, 'custom_fields', {})}"
        assert secret_title not in serialized
        assert secret_server_content not in serialized
        assert secret_client_content not in serialized
        assert secret_email not in serialized
        assert secret_username not in serialized
