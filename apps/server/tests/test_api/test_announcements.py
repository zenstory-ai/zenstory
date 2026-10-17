"""API contract for the single lightweight in-app announcement."""

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from models import AdminAuditLog, AnnouncementConfig, User
from services.core.auth_service import hash_password


async def _create_user(
    db_session: Session,
    username: str,
    *,
    is_superuser: bool = False,
) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "写作工作台已更新",
        "description": "现在可以更顺畅地整理故事线。",
        "action_label": "查看说明",
        "action_url": "/docs/workbench",
        "enabled": True,
        "expected_version": 0,
        "expected_updated_at": None,
    }
    payload.update(overrides)
    return payload


@pytest.mark.integration
async def test_current_announcement_requires_login_and_hides_disabled_config(
    client: AsyncClient,
    db_session: Session,
):
    unauthenticated = await client.get("/api/announcements/current")
    assert unauthenticated.status_code == 401

    await _create_user(db_session, "reader")
    reader_headers = await _login(client, "reader")
    response = await client.get("/api/announcements/current", headers=reader_headers)
    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.integration
async def test_admin_empty_state_and_superuser_permission(
    client: AsyncClient,
    db_session: Session,
):
    await _create_user(db_session, "reader")
    await _create_user(db_session, "admin", is_superuser=True)
    reader_headers = await _login(client, "reader")
    admin_headers = await _login(client, "admin")

    forbidden = await client.get(
        "/api/admin/announcements/current", headers=reader_headers
    )
    assert forbidden.status_code == 403

    response = await client.get(
        "/api/admin/announcements/current", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json() == {
        "title": "",
        "description": "",
        "action_label": None,
        "action_url": None,
        "enabled": False,
        "version": 0,
        "updated_at": None,
        "updated_by": None,
    }


@pytest.mark.integration
async def test_publish_update_stop_and_reenable_preserve_content_version(
    client: AsyncClient,
    db_session: Session,
):
    admin = await _create_user(db_session, "admin", is_superuser=True)
    await _create_user(db_session, "reader")
    admin_headers = await _login(client, "admin")
    reader_headers = await _login(client, "reader")

    published = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(),
    )
    assert published.status_code == 200
    assert published.json()["version"] == 1
    assert published.json()["updated_by"] == admin.id

    public = await client.get("/api/announcements/current", headers=reader_headers)
    assert public.status_code == 200
    assert public.json() == {
        "title": "写作工作台已更新",
        "description": "现在可以更顺畅地整理故事线。",
        "action_label": "查看说明",
        "action_url": "/docs/workbench",
        "version": 1,
        "updated_at": published.json()["updated_at"],
    }
    assert "updated_by" not in public.json()
    assert "enabled" not in public.json()

    stopped = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(
            enabled=False,
            expected_version=1,
            expected_updated_at=published.json()["updated_at"],
        ),
    )
    assert stopped.status_code == 200
    assert stopped.json()["version"] == 1
    assert stopped.json()["title"] == "写作工作台已更新"
    hidden = await client.get("/api/announcements/current", headers=reader_headers)
    assert hidden.status_code == 200
    assert hidden.json() is None

    reenabled = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(
            enabled=True,
            expected_version=1,
            expected_updated_at=stopped.json()["updated_at"],
        ),
    )
    assert reenabled.status_code == 200
    assert reenabled.json()["version"] == 1

    changed = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(
            description="现在可以更顺畅地整理人物关系。",
            expected_version=1,
            expected_updated_at=reenabled.json()["updated_at"],
        ),
    )
    assert changed.status_code == 200
    assert changed.json()["version"] == 2


@pytest.mark.integration
async def test_stale_content_version_conflicts_without_writing_audit(
    client: AsyncClient,
    db_session: Session,
):
    await _create_user(db_session, "admin", is_superuser=True)
    admin_headers = await _login(client, "admin")
    created = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(),
    )
    assert created.status_code == 200

    first_update = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(
            title="第一版修订",
            expected_version=1,
            expected_updated_at=created.json()["updated_at"],
        ),
    )
    assert first_update.status_code == 200

    stale = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(
            title="过期修订",
            expected_version=1,
            expected_updated_at=created.json()["updated_at"],
        ),
    )
    assert stale.status_code == 409
    assert stale.json()["error_code"] == "ERR_RESOURCE_CONFLICT"
    assert stale.json()["error_detail"]["current_version"] == 2

    audits = db_session.exec(
        select(AdminAuditLog).where(AdminAuditLog.resource_type == "announcement")
    ).all()
    assert len(audits) == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "", "enabled": True},
        {"description": "", "enabled": True},
        {"action_label": "查看", "action_url": None},
        {"action_label": None, "action_url": "/docs"},
        {"action_url": "http://example.com/news"},
        {"action_url": "//example.com/news"},
        {"action_url": "javascript:alert(1)"},
        {"action_url": "https://user:secret@example.com/news"},
        {"action_url": "/docs\\evil"},
        {"action_url": "/docs\nnext"},
        {"action_url": "/docs\x00next"},
        {"action_url": "https://example.com/news\x1bnext"},
        {"action_url": "https://example.com/has space"},
    ],
)
async def test_invalid_announcement_content_is_rejected(
    client: AsyncClient,
    db_session: Session,
    overrides: dict[str, object],
):
    await _create_user(db_session, "admin", is_superuser=True)
    admin_headers = await _login(client, "admin")

    response = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(**overrides),
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_disabled_empty_config_is_valid_and_audited_atomically(
    client: AsyncClient,
    db_session: Session,
):
    admin = await _create_user(db_session, "admin", is_superuser=True)
    admin_headers = await _login(client, "admin")

    response = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json={
            "title": "",
            "description": "",
            "action_label": None,
            "action_url": None,
            "enabled": False,
            "expected_version": 0,
            "expected_updated_at": None,
        },
    )
    assert response.status_code == 200
    assert response.json()["version"] == 0

    audit = db_session.exec(
        select(AdminAuditLog).where(AdminAuditLog.resource_type == "announcement")
    ).one()
    assert audit.admin_user_id == admin.id
    assert audit.action == "create_announcement"
    assert audit.new_value["enabled"] is False
    assert audit.new_value["version"] == 0


@pytest.mark.integration
async def test_initial_create_collision_returns_version_conflict(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    await _create_user(db_session, "admin", is_superuser=True)
    admin_headers = await _login(client, "admin")
    original_flush = db_session.flush

    def collide_on_insert(*_args: object, **_kwargs: object) -> None:
        raise IntegrityError("INSERT announcement_config", {}, Exception("duplicate"))

    monkeypatch.setattr(db_session, "flush", collide_on_insert)
    response = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(),
    )
    monkeypatch.setattr(db_session, "flush", original_flush)

    assert response.status_code == 409
    assert response.json()["error_code"] == "ERR_RESOURCE_CONFLICT"
    assert db_session.get(AnnouncementConfig, "current") is None


@pytest.mark.integration
@pytest.mark.parametrize("audit_error", [
    RuntimeError("audit unavailable"),
    IntegrityError("INSERT admin_audit_log", {}, Exception("audit integrity failure")),
])
@pytest.mark.parametrize("already_published", [False, True])
async def test_audit_failure_rolls_back_announcement_save(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    audit_error: Exception,
    already_published: bool,
):
    await _create_user(db_session, "admin", is_superuser=True)
    admin_headers = await _login(client, "admin")
    original = None
    if already_published:
        published = await client.put(
            "/api/admin/announcements/current", headers=admin_headers, json=_payload()
        )
        assert published.status_code == 200
        original = published.json()

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise audit_error

    monkeypatch.setattr(
        "services.features.announcement_service.admin_audit_service.log_action",
        fail_audit,
    )
    response = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(**({
            "title": "这个更改应当回滚",
            "expected_version": original["version"],
            "expected_updated_at": original["updated_at"],
        } if original else {})),
    )

    assert response.status_code == 500
    current = db_session.get(AnnouncementConfig, "current")
    if original:
        assert current is not None
        assert current.title == original["title"]
        assert current.version == original["version"]
    else:
        assert current is None
    audits = db_session.exec(
        select(AdminAuditLog).where(AdminAuditLog.resource_type == "announcement")
    ).all()
    assert len(audits) == (1 if original else 0)


@pytest.mark.integration
async def test_stale_save_after_stop_cannot_silently_reenable(
    client: AsyncClient,
    db_session: Session,
):
    await _create_user(db_session, "admin", is_superuser=True)
    admin_headers = await _login(client, "admin")
    published = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(),
    )
    assert published.status_code == 200
    stale_token = published.json()["updated_at"]

    stopped = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(
            enabled=False,
            expected_version=1,
            expected_updated_at=stale_token,
        ),
    )
    assert stopped.status_code == 200
    assert stopped.json()["version"] == 1

    stale_save = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(
            description="旧页面里的修改",
            enabled=True,
            expected_version=1,
            expected_updated_at=stale_token,
        ),
    )
    assert stale_save.status_code == 409

    current = await client.get(
        "/api/admin/announcements/current", headers=admin_headers
    )
    assert current.status_code == 200
    assert current.json()["enabled"] is False
    assert current.json()["description"] == "现在可以更顺畅地整理故事线。"
    audits = db_session.exec(
        select(AdminAuditLog).where(AdminAuditLog.resource_type == "announcement")
    ).all()
    assert len(audits) == 2


@pytest.mark.integration
async def test_stale_visibility_updates_conflict_without_extra_audits(
    client: AsyncClient,
    db_session: Session,
):
    await _create_user(db_session, "admin", is_superuser=True)
    admin_headers = await _login(client, "admin")
    published = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(),
    )
    assert published.status_code == 200
    published_token = published.json()["updated_at"]

    missing_token = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(enabled=False, expected_version=1),
    )
    assert missing_token.status_code == 409

    stopped = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(
            enabled=False,
            expected_version=1,
            expected_updated_at=published_token,
        ),
    )
    assert stopped.status_code == 200

    stale_enable = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(
            enabled=True,
            expected_version=1,
            expected_updated_at=published_token,
        ),
    )
    assert stale_enable.status_code == 409

    reenabled = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(
            enabled=True,
            expected_version=1,
            expected_updated_at=stopped.json()["updated_at"],
        ),
    )
    assert reenabled.status_code == 200

    stale_stop = await client.put(
        "/api/admin/announcements/current",
        headers=admin_headers,
        json=_payload(
            enabled=False,
            expected_version=1,
            expected_updated_at=stopped.json()["updated_at"],
        ),
    )
    assert stale_stop.status_code == 409

    current = await client.get(
        "/api/admin/announcements/current", headers=admin_headers
    )
    assert current.json()["enabled"] is True
    assert current.json()["version"] == 1
    audits = db_session.exec(
        select(AdminAuditLog).where(AdminAuditLog.resource_type == "announcement")
    ).all()
    assert len(audits) == 3
