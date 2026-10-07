"""Tests for in-app feedback submission API."""

import io
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlmodel import select
from starlette.datastructures import Headers, UploadFile

from api.feedback import submit_feedback
from core.error_codes import ErrorCode
from models import User, UserFeedback
from services.core.auth_service import hash_password

VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd\x8d\xe1"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.parametrize("failure_point", ["commit", "refresh"])
@pytest.mark.asyncio
async def test_submit_feedback_removes_new_screenshot_when_persistence_fails(
    monkeypatch, tmp_path, failure_point
):
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr("api.feedback._build_screenshot_filename", lambda *_args: "owned.png")

    class _FailingSession:
        def __init__(self):
            self.rollback_calls = 0

        def add(self, _feedback):
            return None

        def commit(self):
            if failure_point == "commit":
                raise RuntimeError("commit failed")

        def flush(self):
            return None

        def refresh(self, _feedback):
            if failure_point == "refresh":
                raise RuntimeError("refresh failed")

        def rollback(self):
            self.rollback_calls += 1

    session = _FailingSession()
    screenshot = UploadFile(
        filename="bug.png",
        file=io.BytesIO(VALID_PNG_BYTES),
        headers=Headers({"content-type": "image/png"}),
    )

    with pytest.raises(RuntimeError, match=f"{failure_point} failed"):
        submit_feedback(
            issue_text="persistence failure",
            source_page="editor",
            source_route=None,
            trace_id=None,
            request_id=None,
            agent_run_id=None,
            project_id=None,
            agent_session_id=None,
            screenshot=screenshot,
            current_user=SimpleNamespace(id="user-1"),
            session=session,
        )

    assert session.rollback_calls == 1
    assert not (tmp_path / "owned.png").exists()


@pytest.mark.asyncio
async def test_submit_feedback_never_deletes_a_preexisting_filename(monkeypatch, tmp_path):
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr("api.feedback._build_screenshot_filename", lambda *_args: "existing.png")
    existing = tmp_path / "existing.png"
    existing.write_bytes(b"not owned by this request")

    screenshot = UploadFile(
        filename="bug.png",
        file=io.BytesIO(VALID_PNG_BYTES),
        headers=Headers({"content-type": "image/png"}),
    )
    session = SimpleNamespace(
        add=lambda _feedback: None,
        flush=lambda: None,
        refresh=lambda _feedback: None,
        commit=lambda: None,
        rollback=lambda: None,
    )

    with pytest.raises(FileExistsError):
        submit_feedback(
            issue_text="collision",
            source_page="editor",
            source_route=None,
            trace_id=None,
            request_id=None,
            agent_run_id=None,
            project_id=None,
            agent_session_id=None,
            screenshot=screenshot,
            current_user=SimpleNamespace(id="user-1"),
            session=session,
        )

    assert existing.read_bytes() == b"not owned by this request"


async def create_test_user(
    client: AsyncClient,
    db_session,
    username: str = "feedback_user",
) -> tuple[User, str]:
    """Create a verified test user and return (user, access_token)."""
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    login_response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return user, token


@pytest.mark.integration
async def test_submit_feedback_text_only_success(client: AsyncClient, db_session):
    """Submitting feedback with text only should succeed."""
    user, token = await create_test_user(client, db_session, "feedback_text_only")

    response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Dashboard list flickers after refresh.",
            "source_page": "dashboard",
            "source_route": "/dashboard/projects",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Feedback submitted successfully."
    assert payload["id"]

    feedback = db_session.exec(
        select(UserFeedback).where(UserFeedback.id == payload["id"])
    ).first()
    assert feedback is not None
    assert feedback.user_id == user.id
    assert feedback.source_page == "dashboard"
    assert feedback.source_route == "/dashboard/projects"
    assert feedback.issue_text == "Dashboard list flickers after refresh."
    assert feedback.screenshot_path is None


@pytest.mark.integration
async def test_submit_feedback_persists_debug_fields(client: AsyncClient, db_session):
    """Submitting feedback with debug fields should persist correlation metadata."""
    user, token = await create_test_user(client, db_session, "feedback_debug_meta")

    response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Agent output looks wrong for this prompt.",
            "source_page": "editor",
            "source_route": "/project/project-123",
            "trace_id": "trace-test-001",
            "request_id": "req-test-abc",
            "agent_run_id": "run-test-xyz",
            "project_id": "project-123",
            "agent_session_id": "session-123",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"]

    feedback = db_session.exec(
        select(UserFeedback).where(UserFeedback.id == payload["id"])
    ).first()
    assert feedback is not None
    assert feedback.user_id == user.id
    assert feedback.trace_id == "trace-test-001"
    assert feedback.request_id == "req-test-abc"
    assert feedback.agent_run_id == "run-test-xyz"
    assert feedback.project_id == "project-123"
    assert feedback.agent_session_id == "session-123"


@pytest.mark.integration
async def test_submit_feedback_with_screenshot_success(
    client: AsyncClient,
    db_session,
    monkeypatch,
    tmp_path,
):
    """Submitting feedback with a valid screenshot should persist metadata and file."""
    _, token = await create_test_user(client, db_session, "feedback_with_image")
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(tmp_path))

    screenshot_bytes = VALID_PNG_BYTES
    response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Toolbar button overlaps at 125% zoom.",
            "source_page": "editor",
            "source_route": "/project/test-id",
        },
        files={
            "screenshot": (
                "nested/../../dangerous-looking.PnG",
                io.BytesIO(screenshot_bytes),
                "image/png",
            )
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    feedback = db_session.exec(
        select(UserFeedback).where(UserFeedback.id == payload["id"])
    ).first()
    assert feedback is not None
    assert feedback.screenshot_original_name == "nested/../../dangerous-looking.PnG"
    assert feedback.screenshot_content_type == "image/png"
    assert feedback.screenshot_size_bytes == len(screenshot_bytes)
    assert feedback.screenshot_path is not None
    stored_path = Path(feedback.screenshot_path)
    assert stored_path.parent == tmp_path
    assert re.fullmatch(r"[0-9a-f]{32}\.png", stored_path.name)
    assert stored_path.exists()


@pytest.mark.integration
async def test_submit_feedback_invalid_source_page(client: AsyncClient, db_session):
    """Invalid source_page should return validation error."""
    _, token = await create_test_user(client, db_session, "feedback_invalid_source")

    response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Some issue",
            "source_page": "home",
            "source_route": "/",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error_code"] == ErrorCode.VALIDATION_ERROR


@pytest.mark.integration
async def test_submit_feedback_invalid_screenshot_type(client: AsyncClient, db_session):
    """Unsupported screenshot file extension should be rejected."""
    _, token = await create_test_user(client, db_session, "feedback_invalid_type")

    response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Issue with unsupported file",
            "source_page": "editor",
        },
        files={"screenshot": ("bad.gif", io.BytesIO(b"gif"), "image/gif")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error_code"] == ErrorCode.FILE_TYPE_INVALID


@pytest.mark.integration
async def test_submit_feedback_rejects_spoofed_image_content(client: AsyncClient, db_session):
    """Fake bytes with image extension/content-type should be rejected."""
    _, token = await create_test_user(client, db_session, "feedback_spoofed_image")

    response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Uploaded file is not a real image",
            "source_page": "editor",
        },
        files={"screenshot": ("bad.png", io.BytesIO(b"not-a-real-image"), "image/png")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error_code"] == ErrorCode.FILE_TYPE_INVALID


@pytest.mark.integration
async def test_submit_feedback_oversize_screenshot(client: AsyncClient, db_session):
    """Screenshot larger than 5MB should be rejected."""
    _, token = await create_test_user(client, db_session, "feedback_oversize")
    oversized = b"a" * (5 * 1024 * 1024 + 1)

    response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Issue with oversized screenshot",
            "source_page": "dashboard",
        },
        files={"screenshot": ("large.png", io.BytesIO(oversized), "image/png")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error_code"] == ErrorCode.FILE_TOO_LARGE


@pytest.mark.integration
async def test_submit_feedback_empty_screenshot_file(client: AsyncClient, db_session):
    """Empty screenshot file should be rejected."""
    _, token = await create_test_user(client, db_session, "feedback_empty_screenshot")

    response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Screenshot upload fails silently.",
            "source_page": "editor",
        },
        files={"screenshot": ("empty.png", io.BytesIO(b""), "image/png")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error_code"] == ErrorCode.VALIDATION_ERROR


@pytest.mark.integration
async def test_submit_feedback_source_route_too_long(client: AsyncClient, db_session):
    """source_route longer than 255 chars should fail validation."""
    _, token = await create_test_user(client, db_session, "feedback_route_too_long")
    too_long_route = "/" + "a" * 255

    response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Route metadata exceeds length limit.",
            "source_page": "dashboard",
            "source_route": too_long_route,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error_code"] == ErrorCode.VALIDATION_ERROR
