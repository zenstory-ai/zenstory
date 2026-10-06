"""
Request-driven admin feedback and materials upload/retry e2e workflows.

These tests cover one admin review loop and one materials lifecycle path
through the real HTTP layer while stubbing external orchestration only where
the app would otherwise leave the request boundary.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import User, UserFeedback
from models.material_models import IngestionJob, Novel
from models.subscription import SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password

pytestmark = pytest.mark.e2e

VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd\x8d\xe1"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _identity(prefix: str) -> tuple[str, str]:
    suffix = uuid4().hex[:8]
    username = f"{prefix}_{suffix}"
    return username, f"{username}@example.com"


async def _create_user(
    db_session: Session,
    *,
    prefix: str,
    is_superuser: bool = False,
    password: str = "password123",
) -> User:
    username, email = _identity(prefix)
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        email_verified=True,
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _login(client: AsyncClient, *, identifier: str, password: str = "password123") -> dict:
    response = await client.post(
        "/api/auth/login",
        data={"username": identifier, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _attach_materials_access_subscription(db_session: Session, *, user_id: str) -> None:
    plan = SubscriptionPlan(
        name="pro",
        display_name="Pro",
        display_name_en="Pro",
        price_monthly_cents=4900,
        price_yearly_cents=39900,
        features={
            "materials_library_access": True,
            "material_uploads": 5,
            "material_decompositions": 5,
            "max_projects": 3,
        },
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)

    now = datetime.utcnow()
    db_session.add(
        UserSubscription(
            user_id=user_id,
            plan_id=plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
    )
    db_session.commit()


@pytest.mark.asyncio
async def test_admin_feedback_review_loop_covers_list_detail_status_and_screenshot_download(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(tmp_path))

    reporter = await _create_user(db_session, prefix="feedback_reporter")
    admin = await _create_user(db_session, prefix="feedback_admin", is_superuser=True)

    reporter_login = await _login(client, identifier=reporter.username)
    admin_login = await _login(client, identifier=admin.email)

    submit_response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Admin review flow should expose this issue.",
            "source_page": "dashboard",
            "source_route": "/dashboard/projects",
            "trace_id": "trace-admin-feedback",
        },
        files={"screenshot": ("feedback.png", VALID_PNG_BYTES, "image/png")},
        headers=_auth_headers(reporter_login["access_token"]),
    )
    assert submit_response.status_code == 200
    feedback_id = submit_response.json()["id"]

    list_response = await client.get(
        "/api/admin/feedback?status=open&source_page=dashboard&has_screenshot=true&search=review",
        headers=_auth_headers(admin_login["access_token"]),
    )
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["total"] == 1
    item = list_payload["items"][0]
    assert item["id"] == feedback_id
    assert item["username"] == reporter.username
    assert item["has_screenshot"] is True
    assert item["trace_id"] == "trace-admin-feedback"

    detail_response = await client.get(
        f"/api/admin/feedback/{feedback_id}",
        headers=_auth_headers(admin_login["access_token"]),
    )
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["issue_text"] == "Admin review flow should expose this issue."
    assert detail_payload["status"] == "open"

    status_response = await client.patch(
        f"/api/admin/feedback/{feedback_id}/status",
        json={"status": "resolved"},
        headers=_auth_headers(admin_login["access_token"]),
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "resolved"

    screenshot_response = await client.get(
        f"/api/admin/feedback/{feedback_id}/screenshot",
        headers=_auth_headers(admin_login["access_token"]),
    )
    assert screenshot_response.status_code == 200
    assert screenshot_response.headers["content-type"] == "image/png"
    assert screenshot_response.content == VALID_PNG_BYTES

    stored_feedback = db_session.get(UserFeedback, feedback_id)
    assert stored_feedback is not None
    assert stored_feedback.status == "resolved"


@pytest.mark.asyncio
async def test_materials_upload_and_retry_lifecycle_roundtrip(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from config.material_settings import material_settings

    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))
    background_runner = AsyncMock(return_value="flow-run-test")
    monkeypatch.setattr("api.materials.upload._start_flow_deployment", background_runner)

    user = await _create_user(db_session, prefix="materials_upload")
    _attach_materials_access_subscription(db_session, user_id=user.id)
    login_payload = await _login(client, identifier=user.username)
    headers = _auth_headers(login_payload["access_token"])

    upload_response = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("novel.txt", io.BytesIO(("Chapter 1\n\n" + "Upload test content. " * 10).encode()), "text/plain")},
        params={"title": "Upload Novel", "author": "Upload Author"},
        headers=headers,
    )
    assert upload_response.status_code == 200
    upload_payload = upload_response.json()
    assert upload_payload["title"] == "Upload Novel"
    assert upload_payload["status"] == "pending"

    novel = db_session.get(Novel, upload_payload["novel_id"])
    assert novel is not None
    latest_job = db_session.get(IngestionJob, upload_payload["job_id"])
    assert latest_job is not None
    assert latest_job.status == "pending"
    background_runner.assert_awaited_once()

    source_meta = novel.source_meta or ""
    assert "novel.txt" in source_meta
    stored_path = Path(json.loads(source_meta)["file_path"])
    assert stored_path.exists()

    latest_job.status = "failed"
    latest_job.error_message = "Prefect deployment unavailable"
    db_session.add(latest_job)
    db_session.commit()

    retry_response = await client.post(
        f"/api/v1/materials/{novel.id}/retry",
        headers=headers,
    )
    assert retry_response.status_code == 200
    retry_payload = retry_response.json()
    assert retry_payload["status"] == "pending"
    assert retry_payload["job_id"] != latest_job.id

    jobs = db_session.exec(
        select(IngestionJob)
        .where(IngestionJob.novel_id == novel.id)
        .order_by(IngestionJob.created_at.asc())
    ).all()
    assert len(jobs) == 2
    assert jobs[-1].status == "pending"
    assert jobs[-1].source_path == latest_job.source_path
    assert background_runner.await_count == 2
