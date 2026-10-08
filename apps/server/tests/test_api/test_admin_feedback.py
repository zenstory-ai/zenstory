"""Tests for admin feedback management endpoints."""

import hashlib
import io
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from api.admin.feedback import _resolve_existing_feedback_screenshot
from models import User, UserFeedback
from services.core.auth_service import hash_password
from services.infra.upload_storage import StoredObject

VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd\x8d\xe1"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def create_user(
    db_session: Session,
    username: str,
    email: str,
    password: str = "password123",
    is_superuser: bool = False,
) -> User:
    """Create a user for tests."""
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


async def login_user(client: AsyncClient, username: str, password: str = "password123") -> str:
    """Login and return access token."""
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    """Build auth headers."""
    return {"Authorization": f"Bearer {token}"}


def create_feedback(
    db_session: Session,
    user_id: str,
    issue_text: str,
    source_page: str = "dashboard",
    status: str = "open",
    screenshot_path: str | None = None,
) -> UserFeedback:
    """Insert a feedback record for tests."""
    feedback = UserFeedback(
        user_id=user_id,
        source_page=source_page,
        issue_text=issue_text,
        status=status,
        screenshot_path=screenshot_path,
        screenshot_original_name="screen.png" if screenshot_path else None,
        screenshot_content_type="image/png" if screenshot_path else None,
        screenshot_size_bytes=12 if screenshot_path else None,
    )
    db_session.add(feedback)
    db_session.commit()
    db_session.refresh(feedback)
    return feedback


@pytest.mark.integration
async def test_private_object_feedback_roundtrip_requires_admin_and_listing_does_not_get(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    class _MemoryStorage:
        def __init__(self):
            self.objects: dict[str, bytes] = {}
            self.read_calls = 0

        def put_feedback(self, *, object_name, content, **_kwargs):
            reference = f"s3://test-bucket/feedback/{object_name}"
            self.objects[reference] = content
            return StoredObject(
                reference,
                len(content),
                hashlib.sha256(content).hexdigest(),
            )

        def feedback_reference_exists(self, reference):
            return reference.startswith("s3://test-bucket/feedback/")

        def read_feedback(self, reference):
            self.read_calls += 1
            return self.objects[reference]

    storage = _MemoryStorage()
    monkeypatch.setattr("api.feedback._storage", lambda: storage)
    monkeypatch.setattr("api.admin.feedback._storage", lambda: storage)
    admin = await create_user(
        db_session, "admin_s3_feedback", "admin_s3_feedback@example.com", is_superuser=True
    )
    author = await create_user(db_session, "author_s3_feedback", "author_s3_feedback@example.com")
    admin_token = await login_user(client, admin.username)
    author_token = await login_user(client, author.username)

    submitted = await client.post(
        "/api/v1/feedback",
        data={"issue_text": "private image", "source_page": "editor"},
        files={"screenshot": ("screen.png", io.BytesIO(VALID_PNG_BYTES), "image/png")},
        headers=auth_headers(author_token),
    )
    assert submitted.status_code == 200
    feedback_id = submitted.json()["id"]

    listed = await client.get("/api/admin/feedback", headers=auth_headers(admin_token))
    assert listed.status_code == 200
    assert storage.read_calls == 0
    assert next(item for item in listed.json()["items"] if item["id"] == feedback_id)[
        "has_screenshot"
    ]

    forbidden = await client.get(
        f"/api/admin/feedback/{feedback_id}/screenshot",
        headers=auth_headers(author_token),
    )
    assert forbidden.status_code == 403
    downloaded = await client.get(
        f"/api/admin/feedback/{feedback_id}/screenshot",
        headers=auth_headers(admin_token),
    )
    assert downloaded.status_code == 200
    assert downloaded.content == VALID_PNG_BYTES
    assert storage.read_calls == 1


@pytest.mark.unit
def test_resolve_existing_feedback_screenshot_returns_none_for_empty_path():
    resolved, found_outside_root = _resolve_existing_feedback_screenshot(None)

    assert resolved is None
    assert found_outside_root is False


@pytest.mark.unit
def test_resolve_existing_feedback_screenshot_recovers_legacy_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    upload_root = tmp_path / "uploads" / "feedback"
    upload_root.mkdir(parents=True, exist_ok=True)
    recovered_file = upload_root / "legacy-screen.png"
    recovered_file.write_bytes(b"legacy")
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(upload_root))

    resolved, found_outside_root = _resolve_existing_feedback_screenshot(
        "/legacy/runtime/uploads/feedback/legacy-screen.png"
    )

    assert resolved == recovered_file.resolve()
    assert found_outside_root is True


@pytest.mark.unit
def test_resolve_existing_feedback_screenshot_reports_outside_root_when_candidates_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    upload_root = tmp_path / "uploads" / "feedback"
    upload_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(upload_root))

    resolved, found_outside_root = _resolve_existing_feedback_screenshot(
        str((tmp_path / "outside.png").resolve())
    )

    assert resolved is None
    assert found_outside_root is True


@pytest.mark.integration
async def test_admin_feedback_list_forbidden_for_non_superuser(client: AsyncClient, db_session: Session):
    """Non-superuser should not access admin feedback list."""
    normal_user = await create_user(db_session, "normal_feedback_user", "normal_feedback_user@example.com")
    token = await login_user(client, normal_user.username)

    response = await client.get("/api/admin/feedback", headers=auth_headers(token))

    assert response.status_code == 403


@pytest.mark.integration
async def test_admin_feedback_list_and_filters(client: AsyncClient, db_session: Session):
    """Superuser should list feedback and apply filters."""
    admin = await create_user(db_session, "admin_feedback_1", "admin_feedback_1@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_1", "writer_feedback_1@example.com")
    create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Dashboard crashes when switching tabs",
        source_page="dashboard",
        status="open",
    )
    create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Editor toolbar overlaps in mobile",
        source_page="editor",
        status="processing",
    )
    token = await login_user(client, admin.username)

    response = await client.get(
        "/api/admin/feedback?status=processing&source_page=editor&search=toolbar",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["source_page"] == "editor"
    assert item["status"] == "processing"
    assert item["username"] == target_user.username


@pytest.mark.integration
async def test_admin_feedback_list_invalid_status_filter(client: AsyncClient, db_session: Session):
    """Invalid status filter should return 422."""
    admin = await create_user(db_session, "admin_feedback_4", "admin_feedback_4@example.com", is_superuser=True)
    token = await login_user(client, admin.username)

    response = await client.get(
        "/api/admin/feedback?status=invalid",
        headers=auth_headers(token),
    )

    assert response.status_code == 422


@pytest.mark.integration
async def test_admin_feedback_list_invalid_source_page_filter(client: AsyncClient, db_session: Session):
    """Invalid source_page filter should return 422."""
    admin = await create_user(db_session, "admin_feedback_7", "admin_feedback_7@example.com", is_superuser=True)
    token = await login_user(client, admin.username)

    response = await client.get(
        "/api/admin/feedback?source_page=invalid",
        headers=auth_headers(token),
    )

    assert response.status_code == 422


@pytest.mark.integration
async def test_admin_feedback_list_filters_has_screenshot(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """List feedback should support has_screenshot true/false filtering."""
    admin = await create_user(db_session, "admin_feedback_8", "admin_feedback_8@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_8", "writer_feedback_8@example.com")
    upload_root = tmp_path / "uploads" / "feedback"
    upload_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(upload_root))

    screenshot_file = upload_root / "screenshot-attached.png"
    screenshot_file.write_bytes(b"attached")

    without_screenshot = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="No screenshot attached",
        source_page="dashboard",
        status="open",
    )
    with_screenshot = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Screenshot attached",
        source_page="editor",
        status="open",
        screenshot_path=str(screenshot_file.resolve()),
    )
    token = await login_user(client, admin.username)

    response_with_screenshot = await client.get(
        "/api/admin/feedback?has_screenshot=true",
        headers=auth_headers(token),
    )
    assert response_with_screenshot.status_code == 200
    with_payload = response_with_screenshot.json()
    assert with_payload["total"] == 1
    assert with_payload["items"][0]["id"] == with_screenshot.id
    assert with_payload["items"][0]["has_screenshot"] is True

    response_without_screenshot = await client.get(
        "/api/admin/feedback?has_screenshot=false",
        headers=auth_headers(token),
    )
    assert response_without_screenshot.status_code == 200
    without_payload = response_without_screenshot.json()
    assert without_payload["total"] == 1
    assert without_payload["items"][0]["id"] == without_screenshot.id
    assert without_payload["items"][0]["has_screenshot"] is False


@pytest.mark.integration
async def test_admin_feedback_detail_success(client: AsyncClient, db_session: Session):
    """Superuser should fetch single feedback detail."""
    admin = await create_user(db_session, "admin_feedback_5", "admin_feedback_5@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_5", "writer_feedback_5@example.com")
    feedback = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Need detailed issue lookup",
        source_page="dashboard",
        status="open",
    )
    token = await login_user(client, admin.username)

    response = await client.get(
        f"/api/admin/feedback/{feedback.id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == feedback.id
    assert payload["username"] == target_user.username
    assert payload["issue_text"] == "Need detailed issue lookup"


@pytest.mark.integration
async def test_admin_feedback_detail_not_found(client: AsyncClient, db_session: Session):
    """Unknown feedback id should return 404."""
    admin = await create_user(db_session, "admin_feedback_9", "admin_feedback_9@example.com", is_superuser=True)
    token = await login_user(client, admin.username)

    response = await client.get(
        "/api/admin/feedback/missing-feedback-id",
        headers=auth_headers(token),
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_admin_feedback_detail_owner_not_found(client: AsyncClient, db_session: Session):
    """Feedback detail should return 404 when owner is missing."""
    admin = await create_user(db_session, "admin_feedback_10", "admin_feedback_10@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_10", "writer_feedback_10@example.com")
    feedback = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Owner will be missing",
        source_page="dashboard",
        status="open",
    )
    feedback.user_id = "missing-feedback-owner"
    db_session.add(feedback)
    db_session.commit()
    token = await login_user(client, admin.username)

    response = await client.get(
        f"/api/admin/feedback/{feedback.id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_admin_feedback_update_status(client: AsyncClient, db_session: Session):
    """Superuser should update feedback status."""
    admin = await create_user(db_session, "admin_feedback_2", "admin_feedback_2@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_2", "writer_feedback_2@example.com")
    feedback = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Issue pending triage",
        status="open",
    )
    token = await login_user(client, admin.username)

    response = await client.patch(
        f"/api/admin/feedback/{feedback.id}/status",
        headers=auth_headers(token),
        json={"status": "resolved"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "resolved"


@pytest.mark.integration
async def test_admin_feedback_update_status_not_found(client: AsyncClient, db_session: Session):
    """Unknown feedback id should return 404 on status update."""
    admin = await create_user(db_session, "admin_feedback_11", "admin_feedback_11@example.com", is_superuser=True)
    token = await login_user(client, admin.username)

    response = await client.patch(
        "/api/admin/feedback/missing-feedback-id/status",
        headers=auth_headers(token),
        json={"status": "resolved"},
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_admin_feedback_update_status_owner_not_found(client: AsyncClient, db_session: Session):
    """Status update should return 404 when feedback owner is missing."""
    admin = await create_user(db_session, "admin_feedback_12", "admin_feedback_12@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_12", "writer_feedback_12@example.com")
    feedback = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Status update owner missing",
        status="open",
    )
    feedback.user_id = "missing-feedback-owner-for-status"
    db_session.add(feedback)
    db_session.commit()
    token = await login_user(client, admin.username)

    response = await client.patch(
        f"/api/admin/feedback/{feedback.id}/status",
        headers=auth_headers(token),
        json={"status": "processing"},
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_admin_feedback_screenshot_download_success(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Superuser should download feedback screenshot."""
    admin = await create_user(db_session, "admin_feedback_3", "admin_feedback_3@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_3", "writer_feedback_3@example.com")

    screenshot_file = tmp_path / "screen.png"
    screenshot_bytes = b"fake-png-content"
    screenshot_file.write_bytes(screenshot_bytes)
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(tmp_path))

    feedback = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Issue with screenshot",
        status="open",
        screenshot_path=str(screenshot_file.resolve()),
    )
    token = await login_user(client, admin.username)

    response = await client.get(
        f"/api/admin/feedback/{feedback.id}/screenshot",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.content == screenshot_bytes


@pytest.mark.integration
async def test_admin_feedback_screenshot_download_with_relative_upload_root(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Relative FEEDBACK_UPLOAD_DIR should resolve from current working directory."""
    admin = await create_user(db_session, "admin_feedback_13", "admin_feedback_13@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_13", "writer_feedback_13@example.com")

    monkeypatch.chdir(tmp_path)
    relative_dir = Path("relative-feedback-uploads")
    relative_dir.mkdir(parents=True, exist_ok=True)
    screenshot_file = relative_dir / "relative-screen.png"
    screenshot_bytes = b"relative-png-content"
    screenshot_file.write_bytes(screenshot_bytes)
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(relative_dir))

    feedback = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Issue with relative upload dir screenshot",
        status="open",
        screenshot_path=str(screenshot_file.resolve()),
    )
    token = await login_user(client, admin.username)

    response = await client.get(
        f"/api/admin/feedback/{feedback.id}/screenshot",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.content == screenshot_bytes


@pytest.mark.integration
async def test_admin_feedback_screenshot_not_found(client: AsyncClient, db_session: Session):
    """Unknown feedback id should return 404 when downloading screenshot."""
    admin = await create_user(db_session, "admin_feedback_14", "admin_feedback_14@example.com", is_superuser=True)
    token = await login_user(client, admin.username)

    response = await client.get(
        "/api/admin/feedback/missing-feedback-id/screenshot",
        headers=auth_headers(token),
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_admin_feedback_screenshot_path_missing(client: AsyncClient, db_session: Session):
    """Feedback without screenshot_path should return 404."""
    admin = await create_user(db_session, "admin_feedback_15", "admin_feedback_15@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_15", "writer_feedback_15@example.com")
    feedback = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="No screenshot on this feedback",
        status="open",
        screenshot_path=None,
    )
    token = await login_user(client, admin.username)

    response = await client.get(
        f"/api/admin/feedback/{feedback.id}/screenshot",
        headers=auth_headers(token),
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_admin_feedback_screenshot_file_missing_under_upload_root(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Missing screenshot file under upload root should return 404."""
    admin = await create_user(db_session, "admin_feedback_16", "admin_feedback_16@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_16", "writer_feedback_16@example.com")

    upload_root = tmp_path / "feedback-uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    missing_file = upload_root / "missing-screen.png"
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(upload_root))

    feedback = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Screenshot file is missing on disk",
        status="open",
        screenshot_path=str(missing_file.resolve()),
    )
    token = await login_user(client, admin.username)

    response = await client.get(
        f"/api/admin/feedback/{feedback.id}/screenshot",
        headers=auth_headers(token),
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_admin_feedback_screenshot_rejects_outside_upload_root(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Screenshot outside configured upload root should be forbidden."""
    admin = await create_user(db_session, "admin_feedback_6", "admin_feedback_6@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_6", "writer_feedback_6@example.com")

    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    outside_file = tmp_path / "outside.png"
    outside_file.write_bytes(b"outside")
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(upload_root))

    feedback = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="outside screenshot path",
        status="open",
        screenshot_path=str(outside_file.resolve()),
    )
    token = await login_user(client, admin.username)

    response = await client.get(
        f"/api/admin/feedback/{feedback.id}/screenshot",
        headers=auth_headers(token),
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_admin_feedback_list_hides_missing_screenshot_file(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """List endpoint should not expose screenshot actions when file no longer exists."""
    admin = await create_user(db_session, "admin_feedback_7", "admin_feedback_7@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_7", "writer_feedback_7@example.com")

    upload_root = tmp_path / "uploads" / "feedback"
    upload_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(upload_root))

    missing_file = upload_root / "missing.png"
    feedback = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Screenshot file missing on disk",
        status="open",
        screenshot_path=str(missing_file.resolve()),
    )
    token = await login_user(client, admin.username)

    response = await client.get("/api/admin/feedback", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    item = next(row for row in payload["items"] if row["id"] == feedback.id)
    assert item["has_screenshot"] is False
    assert item["screenshot_download_url"] is None


@pytest.mark.integration
async def test_admin_feedback_screenshot_download_supports_legacy_absolute_path(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Download should recover legacy absolute paths by resolving to current upload root."""
    admin = await create_user(db_session, "admin_feedback_8", "admin_feedback_8@example.com", is_superuser=True)
    target_user = await create_user(db_session, "writer_feedback_8", "writer_feedback_8@example.com")

    upload_root = tmp_path / "uploads" / "feedback"
    upload_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(upload_root))

    screenshot_bytes = b"legacy-png-content"
    actual_file = upload_root / "legacy_screen.png"
    actual_file.write_bytes(screenshot_bytes)

    legacy_path = "/legacy/runtime/uploads/feedback/legacy_screen.png"
    feedback = create_feedback(
        db_session,
        user_id=target_user.id,
        issue_text="Legacy screenshot path",
        status="open",
        screenshot_path=legacy_path,
    )
    token = await login_user(client, admin.username)

    response = await client.get(
        f"/api/admin/feedback/{feedback.id}/screenshot",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.content == screenshot_bytes

    db_session.refresh(feedback)
    assert feedback.screenshot_path == str(actual_file.resolve())
