"""
Tests for Materials API - Material library management.

Tests material library endpoints:
- POST /api/v1/materials/upload - Upload material
- GET /api/v1/materials - Get material list
- GET /api/v1/materials/library-summary - Get library summary
- GET /api/v1/materials/search - Search materials
- GET /api/v1/materials/{novel_id} - Get material detail
- DELETE /api/v1/materials/{novel_id} - Delete material
"""

import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select
from starlette.datastructures import UploadFile

import api.materials.upload as materials_upload_api
from api.materials.constants import MAX_TEXT_CHARACTERS
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import File, Project, User
from models.material_models import (
    Chapter,
    Character,
    CharacterRelationship,
    GoldenFinger,
    IngestionJob,
    Novel,
    Story,
    StoryLine,
    WorldView,
)
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.material.novel_text import decode_novel_bytes

# ==================== Helper Functions ====================


def make_novel_text(label: str = "", chapters: int = 2) -> str:
    """A small novel the shared chapter splitter accepts (>= 100 chars per chapter)."""
    body = f"{label}这是一段用于测试的正文内容。" * 12
    return "\n".join(f"第{index}章 测试\n{body}" for index in range(1, chapters + 1))


NOVEL_BYTES = make_novel_text().encode("utf-8")


@pytest.fixture(autouse=True)
def stub_flow_dispatch(monkeypatch):
    async def _fake_start_flow_deployment(*args, **kwargs):
        return "flow-run-test"

    monkeypatch.setattr(
        materials_upload_api,
        "_start_flow_deployment",
        _fake_start_flow_deployment,
    )


async def create_test_user(client: AsyncClient, db_session, username: str = "testuser") -> tuple[User, str]:
    """Create a test user and return (user, token)."""
    from services.core.auth_service import hash_password

    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    paid_plan = db_session.exec(
        select(SubscriptionPlan).where(SubscriptionPlan.name == "pro")
    ).first()
    if paid_plan is None:
        paid_plan = SubscriptionPlan(
            name="pro",
            display_name="Pro",
            display_name_en="Pro",
            price_monthly_cents=4900,
            price_yearly_cents=39900,
            features={
                "materials_library_access": True,
                "material_uploads": 5,
                "material_decompositions": 5,
                "ai_conversations_per_day": -1,
                "max_projects": -1,
            },
            is_active=True,
        )
        db_session.add(paid_plan)
        db_session.commit()
        db_session.refresh(paid_plan)

    existing_subscription = db_session.exec(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    ).first()
    if existing_subscription is None:
        now = datetime.utcnow()
        db_session.add(
            UserSubscription(
                user_id=user.id,
                plan_id=paid_plan.id,
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
            )
        )
        db_session.commit()

    login_response = await client.post(
        "/api/auth/login", data={"username": username, "password": "password123"}
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return user, token


def create_test_novel(db_session, user_id: str, title: str = "Test Novel") -> Novel:
    """Create a test novel."""
    novel = Novel(user_id=user_id, title=title, author="Test Author")
    db_session.add(novel)
    db_session.commit()
    return novel


def create_test_job(db_session, novel_id: int, status: str = "completed") -> IngestionJob:
    """Create a test ingestion job."""
    job = IngestionJob(
        novel_id=novel_id,
        source_path="/tmp/test.txt",
        status=status,
        total_chapters=10,
        processed_chapters=10,
    )
    db_session.add(job)
    db_session.commit()
    return job


# ==================== Upload Tests ====================


def test_decode_upload_text_falls_back_to_gb18030_when_detector_is_low_confidence():
    """GBK/GB18030 novels should decode correctly even when chardet guesses wrong."""
    content = "字" * 32

    decoded, _encoding = decode_novel_bytes(content.encode("gbk"))

    assert decoded == content


def test_decode_upload_text_strips_utf16_bom_from_character_count():
    """UTF-16 BOM should not count as an extra character."""
    content = "字" * 32

    decoded, _encoding = decode_novel_bytes(content.encode("utf-16"))

    assert decoded == content


@pytest.mark.integration
async def test_upload_material_success(client: AsyncClient, db_session):
    """Test successful material upload."""
    user, token = await create_test_user(client, db_session, "uploaduser1")

    # Create a test file
    file_content = NOVEL_BYTES
    file = ("test_novel.txt", io.BytesIO(file_content), "text/plain")

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": file},
        params={"title": "My Test Novel", "author": "Test Author"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "My Test Novel"
    assert data["status"] == "pending"
    assert "novel_id" in data
    assert "job_id" in data

    quota = db_session.exec(
        select(UsageQuota).where(UsageQuota.user_id == user.id)
    ).first()
    assert quota is not None
    db_session.refresh(quota)
    assert quota.material_decompositions_used == 1
    job = db_session.get(IngestionJob, data["job_id"])
    billing = json.loads(job.stage_progress)["billing"]
    assert datetime.fromisoformat(billing["quota_period_start"]).tzinfo == UTC


@pytest.mark.integration
async def test_upload_material_passes_response_job_id_to_dispatch(
    client: AsyncClient,
    db_session,
    monkeypatch,
    tmp_path,
):
    from config.material_settings import material_settings

    _, token = await create_test_user(client, db_session, "upload_exact_job")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))
    dispatched_job_ids: list[int] = []

    async def _capture_dispatch(*args, **kwargs):
        dispatched_job_ids.append(kwargs["job_id"])
        return "flow-run-test"

    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _capture_dispatch)
    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("exact.txt", io.BytesIO(NOVEL_BYTES), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert dispatched_job_ids == [response.json()["job_id"]]


@pytest.mark.integration
async def test_upload_material_reserve_none_never_dispatches_or_refunds(
    client: AsyncClient,
    db_session,
    monkeypatch,
    tmp_path,
):
    from config.material_settings import material_settings

    _, token = await create_test_user(client, db_session, "upload_consume_false")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(materials_upload_api, "check_quota", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        materials_upload_api.quota_service,
        "reserve_feature_quota",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        materials_upload_api.quota_service,
        "check_feature_quota",
        lambda *args, **kwargs: (True, 4, 5),
    )
    dispatch_calls: list[int] = []
    refund_calls: list[int] = []

    async def _capture_dispatch(*args, **kwargs):
        dispatch_calls.append(1)
        return "flow-run-test"

    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _capture_dispatch)
    monkeypatch.setattr(
        materials_upload_api.quota_service,
        "release_feature_quota",
        lambda *args, **kwargs: refund_calls.append(1),
    )

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("denied.txt", io.BytesIO(NOVEL_BYTES), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 402
    assert dispatch_calls == []
    assert refund_calls == []
    assert db_session.exec(select(Novel)).all() == []


@pytest.mark.integration
async def test_upload_pre_job_failure_does_not_refund_a_new_month_charge(
    client: AsyncClient,
    db_session,
    monkeypatch,
):
    """A reservation keeps its old period even if file I/O starts next month."""
    user, _ = await create_test_user(client, db_session, "upload_cross_month_io")
    current_period = datetime(2026, 9, 30, 16)
    old_period = datetime(2026, 8, 31, 16, tzinfo=UTC)
    now = datetime(2026, 10, 1, 1, tzinfo=UTC)
    quota = UsageQuota(
        user_id=user.id,
        period_start=now,
        period_end=now + timedelta(days=1),
        material_decompositions_used=1,
        monthly_period_start=current_period,
        monthly_period_end=datetime(2026, 10, 31, 16),
        last_reset_at=now,
    )
    db_session.add(quota)
    db_session.commit()
    monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
    monkeypatch.setattr(materials_upload_api, "check_quota", lambda *a, **k: None)
    monkeypatch.setattr(
        materials_upload_api.quota_service,
        "reserve_feature_quota",
        lambda *a, **k: old_period,
    )

    def _fail_file_write(*args, **kwargs):
        raise OSError("file write crossed month")

    monkeypatch.setattr(materials_upload_api, "_write_upload_file_without_overwrite", _fail_file_write)

    with pytest.raises(OSError, match="crossed month"):
        await materials_upload_api.process_material_upload(
            file=UploadFile(io.BytesIO(NOVEL_BYTES), filename="test.txt"),
            title=None,
            author=None,
            current_user=user,
            session=db_session,
        )

    db_session.refresh(quota)
    assert quota.material_decompositions_used == 1
    assert db_session.exec(select(Novel).where(Novel.user_id == user.id)).all() == []


@pytest.mark.integration
async def test_upload_pre_job_failure_rolls_back_without_refund_io(client, db_session, monkeypatch):
    user, _ = await create_test_user(client, db_session, "upload_no_refund_gap")

    def fail_write(*args, **kwargs):
        raise OSError("file write failed")

    def unexpected_refund(*args, **kwargs):
        pytest.fail("No durable job exists; a rolled-back reservation must not be refunded")

    monkeypatch.setattr(materials_upload_api, "_write_upload_file_without_overwrite", fail_write)
    monkeypatch.setattr(materials_upload_api.quota_service, "release_feature_quota", unexpected_refund)
    with pytest.raises(OSError, match="file write failed"):
        await materials_upload_api.process_material_upload(
            file=UploadFile(io.BytesIO(NOVEL_BYTES), filename="test.txt"),
            title=None, author=None, current_user=user, session=db_session,
        )
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).one()
    assert quota.material_decompositions_used == 0
    assert db_session.exec(select(Novel).where(Novel.user_id == user.id)).all() == []


@pytest.mark.integration
async def test_upload_dispatch_failure_uses_period_reserved_before_job_creation(
    client: AsyncClient,
    db_session,
    monkeypatch,
    tmp_path,
):
    from config.material_settings import material_settings

    user, token = await create_test_user(client, db_session, "upload_cross_month_job")
    now = datetime(2026, 10, 1, 1, tzinfo=UTC)
    old_period = datetime(2026, 8, 31, 16, tzinfo=UTC)
    quota = UsageQuota(
        user_id=user.id,
        period_start=now,
        period_end=now + timedelta(days=1),
        material_decompositions_used=1,
        monthly_period_start=datetime(2026, 9, 30, 16),
        monthly_period_end=datetime(2026, 10, 31, 16),
        last_reset_at=now,
    )
    db_session.add(quota)
    db_session.commit()
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
    monkeypatch.setattr(materials_upload_api, "check_quota", lambda *a, **k: None)
    monkeypatch.setattr(
        materials_upload_api.quota_service,
        "reserve_feature_quota",
        lambda *a, **k: old_period,
    )

    async def _dispatch_failure(*args, **kwargs):
        return None

    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _dispatch_failure)

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("cross-month.txt", io.BytesIO(NOVEL_BYTES), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 503
    db_session.refresh(quota)
    assert quota.material_decompositions_used == 1
    job = db_session.exec(select(IngestionJob).order_by(IngestionJob.id.desc())).first()
    billing = json.loads(job.stage_progress)["billing"]
    assert datetime.fromisoformat(billing["quota_period_start"]) == old_period
    assert billing["quota_refunded"] is False


def test_material_decompose_atomic_quota_consumption_caps_concurrent_requests(
    db_session,
):
    """Two real DB consumers racing for the last unit cannot both succeed."""
    from services.core.auth_service import hash_password

    user = User(
        username="quota_atomic_cap",
        email="quota_atomic_cap@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    paid_plan = db_session.exec(
        select(SubscriptionPlan).where(SubscriptionPlan.name == "pro")
    ).first()
    if paid_plan is None:
        paid_plan = SubscriptionPlan(
            name="pro",
            display_name="Pro",
            display_name_en="Pro",
            price_monthly_cents=4900,
            price_yearly_cents=39900,
            features={"materials_library_access": True, "material_decompositions": 5},
            is_active=True,
        )
        db_session.add(paid_plan)
        db_session.commit()
        db_session.refresh(paid_plan)
    now = datetime.utcnow()
    db_session.add(
        UserSubscription(
            user_id=user.id,
            plan_id=paid_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
    )
    quota = UsageQuota(
        user_id=user.id,
        period_start=now,
        period_end=now + timedelta(days=30),
        material_decompositions_used=4,
        monthly_period_start=now - timedelta(days=1),
        monthly_period_end=now + timedelta(days=30),
        last_reset_at=now,
    )
    db_session.add(quota)
    db_session.commit()

    def _reserve() -> bool:
        with Session(db_session.get_bind()) as isolated_session:
            return materials_upload_api.quota_service.reserve_feature_quota(
                isolated_session,
                user.id,
                "material_decompose",
            ) is not None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: _reserve(), range(2)))

    assert sorted(results) == [False, True]
    db_session.expire_all()
    stored_quota = db_session.exec(
        select(UsageQuota).where(UsageQuota.user_id == user.id)
    ).one()
    assert stored_quota.material_decompositions_used == 5


@pytest.mark.integration
@pytest.mark.parametrize("dispatch_raises", [False, True])
async def test_upload_reconciles_refund_after_failure_status_poisoned_session(
    client: AsyncClient, db_session, monkeypatch, tmp_path, dispatch_raises,
):
    from config.material_settings import material_settings

    user, _ = await create_test_user(
        client, db_session, f"uploadstatusfail{int(dispatch_raises)}",
    )
    username, email, password = user.username, user.email, user.hashed_password
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))

    async def _dispatch(*args, **kwargs):
        if dispatch_raises:
            raise RuntimeError("SDK dispatch failed")
        return None

    def _mark_failed(*args, **kwargs):
        # A real constraint violation leaves this Session unusable until rollback.
        db_session.add(User(username=username, email=email, hashed_password=password))
        db_session.flush()

    refund_calls = []
    original_release = materials_upload_api.quota_service.release_feature_quota

    def _release(*args, **kwargs):
        refund_calls.append(1)
        return original_release(*args, **kwargs)

    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _dispatch)
    monkeypatch.setattr(materials_upload_api, "_mark_job_dispatch_failed", _mark_failed)
    monkeypatch.setattr(materials_upload_api.quota_service, "release_feature_quota", _release)

    expected_error = RuntimeError if dispatch_raises else APIException
    with pytest.raises(expected_error) as raised:
        await materials_upload_api.process_material_upload(
            file=UploadFile(io.BytesIO(NOVEL_BYTES), filename="test.txt"),
            title=None, author=None, current_user=user, session=db_session,
        )
    if dispatch_raises:
        assert str(raised.value) == "SDK dispatch failed"
    else:
        assert raised.value.status_code == 503
    # A quota-only commit while job state cannot be persisted would leave a
    # crash window for a duplicate refund. Preserve durable charged state.
    assert refund_calls == []
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).one()
    assert quota.material_decompositions_used == 1
    job = db_session.exec(select(IngestionJob).order_by(IngestionJob.created_at.desc())).first()
    assert job.status == "pending"
    assert materials_upload_api.IngestionJobsService.get_billing(job)["quota_charged"] is True
    job.updated_at = datetime.utcnow() - timedelta(minutes=11)
    db_session.add(job)
    db_session.commit()
    service = materials_upload_api.IngestionJobsService()
    service.reconcile_stale_job(db_session, job)
    service.reconcile_stale_job(db_session, job)
    assert job.status == "failed"
    assert service.get_billing(job)["quota_refunded"] is True
    assert refund_calls == [1]
    db_session.refresh(quota)
    assert quota.material_decompositions_used == 0


@pytest.mark.integration
async def test_upload_material_accepts_content_at_character_limit(
    client: AsyncClient,
    db_session,
    monkeypatch,
    tmp_path,
):
    """Materials upload should accept files up to the 300k-character cap."""
    from config.material_settings import material_settings

    _, token = await create_test_user(client, db_session, "uploaduser_limit_ok")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))

    file_content = ("第一章\n" + "字" * (MAX_TEXT_CHARACTERS - 4)).encode("utf-8")
    file = ("limit_ok.txt", io.BytesIO(file_content), "text/plain")

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": file},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


@pytest.mark.integration
async def test_upload_material_accepts_gbk_content_at_character_limit(
    client: AsyncClient,
    db_session,
    monkeypatch,
    tmp_path,
):
    """GBK-encoded novels at the limit should not be rejected as oversized."""
    from config.material_settings import material_settings

    _, token = await create_test_user(client, db_session, "uploaduser_limit_ok_gbk")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))

    file_content = ("第一章\n" + "字" * (MAX_TEXT_CHARACTERS - 4)).encode("gbk")
    file = ("limit_ok_gbk.txt", io.BytesIO(file_content), "text/plain")

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": file},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


@pytest.mark.integration
async def test_upload_material_accepts_utf16_content_at_character_limit(
    client: AsyncClient,
    db_session,
    monkeypatch,
    tmp_path,
):
    """UTF-16 BOM novels at the limit should not be rejected as oversized."""
    from config.material_settings import material_settings

    _, token = await create_test_user(client, db_session, "uploaduser_limit_ok_utf16")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))

    file_content = ("第一章\n" + "字" * (MAX_TEXT_CHARACTERS - 4)).encode("utf-16")
    file = ("limit_ok_utf16.txt", io.BytesIO(file_content), "text/plain")

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": file},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


@pytest.mark.integration
async def test_upload_material_rejects_content_over_character_limit(
    client: AsyncClient,
    db_session,
    monkeypatch,
    tmp_path,
):
    """Materials upload should reject files over the 300k-character cap."""
    from config.material_settings import material_settings

    _, token = await create_test_user(client, db_session, "uploaduser_limit_too_long")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))

    file_content = ("第一章\n" + "字" * (MAX_TEXT_CHARACTERS - 3)).encode("utf-8")
    file = ("limit_too_long.txt", io.BytesIO(file_content), "text/plain")

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": file},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"] == ErrorCode.FILE_CONTENT_TOO_LONG
    assert payload["error_code"] == ErrorCode.FILE_CONTENT_TOO_LONG


@pytest.mark.integration
async def test_upload_material_returns_503_when_flow_dispatch_fails(
    client: AsyncClient,
    db_session,
    monkeypatch,
    tmp_path,
):
    """Upload should fail visibly and leave a retryable failed material when dispatch fails."""
    from config.material_settings import material_settings

    user, token = await create_test_user(client, db_session, "upload_dispatch_fail")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))

    async def _failed_start_flow_deployment(*args, **kwargs):
        return None

    monkeypatch.setattr(
        materials_upload_api,
        "_start_flow_deployment",
        _failed_start_flow_deployment,
    )

    file_content = NOVEL_BYTES
    file = ("test_novel.txt", io.BytesIO(file_content), "text/plain")

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": file},
        params={"title": "Dispatch Failure Novel"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 503

    quota = db_session.exec(
        select(UsageQuota).where(UsageQuota.user_id == user.id)
    ).first()
    assert quota is not None
    assert quota.material_decompositions_used == 0

    novel = db_session.exec(
        select(Novel).where(Novel.user_id == user.id)
    ).first()
    assert novel is not None
    assert novel.title == "Dispatch Failure Novel"

    latest_job = db_session.exec(
        select(IngestionJob)
        .where(IngestionJob.novel_id == novel.id)
        .order_by(IngestionJob.created_at.desc())
    ).first()
    assert latest_job is not None
    assert latest_job.status == "failed"
    assert latest_job.error_message == ErrorCode.MATERIAL_DISPATCH_FAILED
    assert latest_job.completed_at is not None
    # The upload was refunded, so the job no longer holds a charged unit.
    billing = json.loads(latest_job.stage_progress)["billing"]
    assert billing["quota_charged"] is False
    assert billing["quota_refunded"] is True

    list_response = await client.get(
        "/api/v1/materials",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_response.status_code == 200
    assert list_response.json() == [
        {
            "id": novel.id,
            "title": "Dispatch Failure Novel",
            "author": None,
            "synopsis": None,
            "original_filename": "test_novel.txt",
            "created_at": novel.created_at.isoformat(),
            "updated_at": novel.updated_at.isoformat(),
            "status": "failed",
            "error_message": ErrorCode.MATERIAL_DISPATCH_FAILED,
            "chapters_count": 0,
            "enabled_stages": None,
        }
    ]


@pytest.mark.integration
async def test_upload_material_sanitizes_filename(client: AsyncClient, db_session, monkeypatch, tmp_path):
    """Upload should sanitize dangerous filename segments before writing to disk."""
    from config.material_settings import material_settings

    _, token = await create_test_user(client, db_session, "uploaduser_sanitize")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))

    file_content = NOVEL_BYTES
    file = ("../unsafe/../../novel.txt", io.BytesIO(file_content), "text/plain")

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": file},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    novel_id = response.json()["novel_id"]
    novel = db_session.get(Novel, novel_id)
    assert novel is not None

    source_meta = json.loads(novel.source_meta or "{}")
    file_path = source_meta.get("file_path", "")
    assert file_path
    assert str(tmp_path) in file_path
    assert ".." not in os.path.basename(file_path)


@pytest.mark.integration
async def test_upload_material_keeps_same_second_same_name_files_separate(
    client: AsyncClient,
    db_session,
    monkeypatch,
    tmp_path,
):
    """Same-user uploads with the same original filename must not overwrite each other."""
    from config.material_settings import material_settings

    user, token = await create_test_user(client, db_session, "uploaduser_same_second")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(
        materials_upload_api,
        "utcnow",
        lambda: datetime(2026, 1, 2, 3, 4, 5),
    )

    first_content = make_novel_text("First").encode("utf-8")
    second_content = make_novel_text("Second").encode("utf-8")

    first_response = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("same-name.txt", io.BytesIO(first_content), "text/plain")},
        params={"title": "First Same Name Upload"},
        headers={"Authorization": f"Bearer {token}"},
    )
    second_response = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("same-name.txt", io.BytesIO(second_content), "text/plain")},
        params={"title": "Second Same Name Upload"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    first_novel = db_session.get(Novel, first_response.json()["novel_id"])
    second_novel = db_session.get(Novel, second_response.json()["novel_id"])
    assert first_novel is not None
    assert second_novel is not None
    assert first_novel.user_id == user.id
    assert second_novel.user_id == user.id

    first_path = json.loads(first_novel.source_meta or "{}")["file_path"]
    second_path = json.loads(second_novel.source_meta or "{}")["file_path"]
    assert first_path != second_path
    assert os.path.basename(first_path).endswith("_same-name.txt")
    assert os.path.basename(second_path).endswith("_same-name.txt")

    with open(first_path, "rb") as f:
        assert f.read() == first_content
    with open(second_path, "rb") as f:
        assert f.read() == second_content


@pytest.mark.integration
async def test_upload_material_invalid_extension(client: AsyncClient, db_session):
    """Test upload with invalid file extension returns 400."""
    user, token = await create_test_user(client, db_session, "uploaduser2")

    # Try to upload a .pdf file (not allowed)
    file_content = b"PDF content"
    file = ("test.pdf", io.BytesIO(file_content), "application/pdf")

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": file},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400


@pytest.mark.integration
async def test_upload_material_requires_paid_materials_access(client: AsyncClient, db_session):
    """Free users should receive a feature-not-included error when uploading."""
    from services.core.auth_service import hash_password

    user = User(
        username="freeuploaduser",
        email="freeuploaduser@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": user.username, "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    file = ("test_novel.txt", io.BytesIO(b"hello"), "text/plain")
    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": file},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 402
    data = response.json()
    assert data["error_code"] == "ERR_FEATURE_NOT_INCLUDED"


async def _create_free_user_token(client: AsyncClient, db_session, username: str) -> str:
    from services.core.auth_service import hash_password

    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    login_response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert login_response.status_code == 200
    return login_response.json()["access_token"]


def _quota_used(db_session, user_id: str) -> int:
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user_id)).first()
    if quota is None:
        return 0
    db_session.refresh(quota)
    return quota.material_decompositions_used


@pytest.mark.integration
async def test_upload_entitlement_checked_before_body_is_read(
    client: AsyncClient, db_session, monkeypatch
):
    """Free users are rejected before the multipart body is parsed or read."""
    token = await _create_free_user_token(client, db_session, "free_upload_early")

    async def _must_not_parse(_request):
        raise AssertionError("body parsed before entitlement check")

    monkeypatch.setattr(materials_upload_api, "_read_upload_file", _must_not_parse)

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("big.txt", io.BytesIO(NOVEL_BYTES), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 402
    assert response.json()["error_code"] == "ERR_FEATURE_NOT_INCLUDED"


@pytest.mark.integration
async def test_upload_rejects_oversized_content_length_before_parsing(
    client: AsyncClient, db_session, monkeypatch
):
    user, token = await create_test_user(client, db_session, "upload_cl_precheck")
    monkeypatch.setattr(materials_upload_api, "MAX_UPLOAD_REQUEST_BYTES", 512)

    async def _must_not_parse(_request):
        raise AssertionError("oversized body was parsed")

    monkeypatch.setattr(materials_upload_api, "_read_upload_file", _must_not_parse)

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("big.txt", io.BytesIO(b"x" * 4096), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 413
    assert response.json()["error_code"] == ErrorCode.FILE_TOO_LARGE
    assert _quota_used(db_session, user.id) == 0


@pytest.mark.integration
async def test_upload_reads_at_most_max_file_size_plus_one(
    client: AsyncClient, db_session, monkeypatch
):
    user, token = await create_test_user(client, db_session, "upload_bounded_read")
    monkeypatch.setattr(materials_upload_api, "MAX_FILE_SIZE", 1000)
    read_sizes: list[int] = []
    original_read = UploadFile.read

    async def _tracking_read(self, size: int = -1):
        read_sizes.append(size)
        return await original_read(self, size)

    monkeypatch.setattr(UploadFile, "read", _tracking_read)

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("big.txt", io.BytesIO(b"x" * 5000), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == ErrorCode.FILE_TOO_LARGE
    assert read_sizes == [1001]
    assert _quota_used(db_session, user.id) == 0


def test_material_upload_size_limit_is_20mb():
    from api.materials.constants import MAX_FILE_SIZE

    assert MAX_FILE_SIZE == 20 * 1024 * 1024


@pytest.mark.integration
@pytest.mark.parametrize(
    "content",
    [
        "这是一篇没有任何章节标题的短文。" * 30,
        "第一章\n太短",
    ],
)
async def test_upload_without_chapters_is_rejected_without_charging(
    client: AsyncClient, db_session, monkeypatch, tmp_path, content
):
    from config.material_settings import material_settings

    user, token = await create_test_user(
        client, db_session, f"upload_no_chapters_{len(content)}"
    )
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("plain.txt", io.BytesIO(content.encode("utf-8")), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == ErrorCode.MATERIAL_NO_CHAPTERS
    assert _quota_used(db_session, user.id) == 0
    assert db_session.exec(select(Novel).where(Novel.user_id == user.id)).first() is None


@pytest.mark.integration
async def test_upload_over_chapter_limit_is_rejected_without_charging(
    client: AsyncClient, db_session, monkeypatch, tmp_path
):
    from config.material_settings import material_settings

    user, token = await create_test_user(client, db_session, "upload_too_many_chapters")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(material_settings, "MAX_CHAPTERS_PER_NOVEL", 2)

    response = await client.post(
        "/api/v1/materials/upload",
        files={
            "file": (
                "long.txt",
                io.BytesIO(make_novel_text(chapters=3).encode("utf-8")),
                "text/plain",
            )
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == ErrorCode.MATERIAL_TOO_MANY_CHAPTERS
    assert _quota_used(db_session, user.id) == 0


@pytest.mark.integration
async def test_upload_rejects_undecodable_bytes(client: AsyncClient, db_session):
    user, token = await create_test_user(client, db_session, "upload_bad_encoding")

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": ("bad.txt", io.BytesIO(bytes([0x81, 0x30, 0xFF, 0xFF]) * 50), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == ErrorCode.FILE_ENCODING_UNSUPPORTED
    assert _quota_used(db_session, user.id) == 0


@pytest.mark.integration
async def test_upload_is_rate_limited_per_user(client: AsyncClient, db_session, monkeypatch):
    _, token = await create_test_user(client, db_session, "upload_rate_limited")

    statuses = []
    for _ in range(materials_upload_api.UPLOAD_RATE_LIMIT_MAX_REQUESTS + 1):
        response = await client.post(
            "/api/v1/materials/upload",
            files={"file": ("wrong.pdf", io.BytesIO(b"x"), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"},
        )
        statuses.append(response.status_code)

    assert statuses[:-1] == [400] * materials_upload_api.UPLOAD_RATE_LIMIT_MAX_REQUESTS
    assert statuses[-1] == 429


def test_upload_and_retry_routes_have_per_user_rate_limits():
    routes = {
        (route.path, tuple(sorted(route.methods))): route
        for route in materials_upload_api.router.routes
    }
    for path in ("/upload", "/{novel_id}/retry"):
        route = routes[(path, ("POST",))]
        limiters = [
            dep.call
            for dep in route.dependant.dependencies
            if getattr(dep.call, "rate_limit_key", None) is not None
        ]
        assert len(limiters) == 1, f"{path} is missing a per-user rate limit"
        assert limiters[0].rate_limit_window_seconds > 0


@pytest.mark.integration
async def test_upload_material_without_auth(client: AsyncClient, db_session):
    """Test upload without authentication returns 401."""
    file_content = b"Test content"
    file = ("test.txt", io.BytesIO(file_content), "text/plain")

    response = await client.post(
        "/api/v1/materials/upload",
        files={"file": file},
    )

    assert response.status_code == 401


@pytest.mark.integration
async def test_internal_worker_download_success(client: AsyncClient, db_session, monkeypatch, tmp_path):
    """Worker internal endpoint should download file with valid internal token."""
    from config.material_settings import material_settings

    user, _ = await create_test_user(client, db_session, "workerdownload1")
    monkeypatch.setenv("MATERIAL_INTERNAL_TOKEN", "worker-token")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))

    filename = f"{user.id}_20260219_test.txt"
    file_path = tmp_path / filename
    file_path.write_text("worker file content", encoding="utf-8")

    response = await client.get(
        f"/api/v1/materials/internal/system/files/{filename}",
        params={"user_id": user.id},
        headers={"X-Internal-Token": "worker-token"},
    )

    assert response.status_code == 200
    assert response.text == "worker file content"


@pytest.mark.integration
async def test_internal_worker_download_invalid_token(client: AsyncClient, db_session, monkeypatch, tmp_path):
    """Worker internal endpoint should reject invalid token."""
    from config.material_settings import material_settings

    user, _ = await create_test_user(client, db_session, "workerdownload2")
    monkeypatch.setenv("MATERIAL_INTERNAL_TOKEN", "worker-token")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))

    filename = f"{user.id}_20260219_test.txt"
    file_path = tmp_path / filename
    file_path.write_text("worker file content", encoding="utf-8")

    response = await client.get(
        f"/api/v1/materials/internal/system/files/{filename}",
        params={"user_id": user.id},
        headers={"X-Internal-Token": "wrong-token"},
    )

    assert response.status_code == 401


@pytest.mark.integration
async def test_internal_worker_download_user_mismatch(client: AsyncClient, db_session, monkeypatch, tmp_path):
    """Worker internal endpoint should block cross-user file access."""
    from config.material_settings import material_settings

    owner, _ = await create_test_user(client, db_session, "workerdownload3")
    other_user, _ = await create_test_user(client, db_session, "workerdownload4")
    monkeypatch.setenv("MATERIAL_INTERNAL_TOKEN", "worker-token")
    monkeypatch.setattr(material_settings, "UPLOAD_FOLDER", str(tmp_path))

    filename = f"{owner.id}_20260219_test.txt"
    file_path = tmp_path / filename
    file_path.write_text("worker file content", encoding="utf-8")

    response = await client.get(
        f"/api/v1/materials/internal/system/files/{filename}",
        params={"user_id": other_user.id},
        headers={"X-Internal-Token": "worker-token"},
    )

    assert response.status_code == 403


# ==================== Get Materials List Tests ====================


@pytest.mark.integration
async def test_get_materials_empty(client: AsyncClient, db_session):
    """Test getting materials when user has none."""
    user, token = await create_test_user(client, db_session, "listuser1")

    response = await client.get(
        "/api/v1/materials",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.integration
async def test_get_materials_with_data(client: AsyncClient, db_session):
    """Test getting materials list with data."""
    user, token = await create_test_user(client, db_session, "listuser2")

    # Create novels with jobs
    novel1 = create_test_novel(db_session, user.id, "Novel 1")
    novel2 = create_test_novel(db_session, user.id, "Novel 2")
    novel1.source_meta = json.dumps({"original_filename": "novel1.txt"})
    create_test_job(db_session, novel1.id, "completed")
    processing_job = create_test_job(db_session, novel2.id, "processing")
    processing_job.error_message = "raw worker exception at /app/uploads/x.txt"
    db_session.add(processing_job)
    db_session.commit()

    # Add chapters to novel1
    chapter = Chapter(novel_id=novel1.id, chapter_number=1, title="Chapter 1")
    db_session.add(chapter)
    db_session.commit()

    response = await client.get(
        "/api/v1/materials",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    # Check that chapters_count is included
    novel1_item = next((item for item in data if item["title"] == "Novel 1"), None)
    assert novel1_item is not None
    assert novel1_item["chapters_count"] == 1
    assert novel1_item["original_filename"] == "novel1.txt"
    novel2_item = next((item for item in data if item["title"] == "Novel 2"), None)
    assert novel2_item is not None
    # Legacy free-text errors never reach users; they collapse to a code.
    assert novel2_item["error_message"] == ErrorCode.MATERIAL_DECOMPOSE_FAILED


@pytest.mark.integration
async def test_get_materials_excludes_deleted(client: AsyncClient, db_session):
    """Test that materials list excludes soft-deleted novels."""
    from datetime import datetime

    user, token = await create_test_user(client, db_session, "listuser3")

    novel1 = create_test_novel(db_session, user.id, "Active Novel")
    novel2 = create_test_novel(db_session, user.id, "Deleted Novel")
    novel2.deleted_at = datetime.utcnow()
    db_session.commit()

    create_test_job(db_session, novel1.id, "completed")
    create_test_job(db_session, novel2.id, "completed")

    response = await client.get(
        "/api/v1/materials",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Active Novel"


@pytest.mark.integration
async def test_get_materials_user_isolation(client: AsyncClient, db_session):
    """Test that users can only see their own materials."""
    user1, token1 = await create_test_user(client, db_session, "listuser4")
    user2, _ = await create_test_user(client, db_session, "listuser5")

    # Create novel for user2
    novel2 = create_test_novel(db_session, user2.id, "User2 Novel")
    create_test_job(db_session, novel2.id, "completed")

    # Get materials as user1
    response = await client.get(
        "/api/v1/materials",
        headers={"Authorization": f"Bearer {token1}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 0  # User1 should see nothing


# ==================== Library Summary Tests ====================


@pytest.mark.integration
async def test_get_library_summary_empty(client: AsyncClient, db_session):
    """Test library summary when user has no materials."""
    user, token = await create_test_user(client, db_session, "summaryuser1")

    response = await client.get(
        "/api/v1/materials/library-summary",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.integration
async def test_get_library_summary_only_completed(client: AsyncClient, db_session):
    """Test that library summary only includes completed novels."""
    user, token = await create_test_user(client, db_session, "summaryuser2")

    # Create novels with different statuses
    novel_completed = create_test_novel(db_session, user.id, "Completed Novel")
    novel_processing = create_test_novel(db_session, user.id, "Processing Novel")
    novel_failed = create_test_novel(db_session, user.id, "Failed Novel")

    create_test_job(db_session, novel_completed.id, "completed")
    create_test_job(db_session, novel_processing.id, "processing")
    create_test_job(db_session, novel_failed.id, "failed")

    response = await client.get(
        "/api/v1/materials/library-summary",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Completed Novel"


@pytest.mark.integration
async def test_get_library_summary_includes_completed_with_errors(client: AsyncClient, db_session):
    """Partially completed materials should remain available in reference library."""
    user, token = await create_test_user(client, db_session, "summaryuser_partial")

    novel = create_test_novel(db_session, user.id, "Partial Novel")
    create_test_job(db_session, novel.id, "completed_with_errors")

    character = Character(novel_id=novel.id, name="Partial Hero")
    db_session.add(character)
    db_session.commit()

    response = await client.get(
        "/api/v1/materials/library-summary",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Partial Novel"
    assert data[0]["status"] == "completed_with_errors"
    assert data[0]["counts"]["characters"] == 1


@pytest.mark.integration
async def test_get_library_summary_with_counts(client: AsyncClient, db_session):
    """Test library summary returns correct entity counts."""
    user, token = await create_test_user(client, db_session, "summaryuser3")

    novel = create_test_novel(db_session, user.id, "Novel With Entities")
    create_test_job(db_session, novel.id, "completed")

    # Add entities
    character1 = Character(novel_id=novel.id, name="Character 1")
    character2 = Character(novel_id=novel.id, name="Character 2")
    db_session.add_all([character1, character2])
    db_session.commit()  # Commit first to get character IDs

    worldview = WorldView(novel_id=novel.id, power_system="Test Power System")
    db_session.add(worldview)

    golden_finger = GoldenFinger(novel_id=novel.id, name="Golden Finger 1")
    db_session.add(golden_finger)

    storyline = StoryLine(novel_id=novel.id, title="Story Line 1")
    db_session.add(storyline)
    db_session.commit()
    db_session.refresh(storyline)

    db_session.add(
        Story(
            story_line_id=storyline.id,
            title="Story 1",
            synopsis="Story in summary counts",
        )
    )

    # Now create relationship with committed character IDs
    relationship = CharacterRelationship(
        novel_id=novel.id,
        character_a_id=character1.id,
        character_b_id=character2.id,
        relationship_type="friend",
    )
    db_session.add(relationship)
    db_session.commit()

    response = await client.get(
        "/api/v1/materials/library-summary",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    counts = data[0]["counts"]
    assert counts["characters"] == 2
    assert counts["worldview"] == 1
    assert counts["golden_fingers"] == 1
    assert counts["stories"] == 1
    assert counts["storylines"] == 1
    assert counts["relationships"] == 1


@pytest.mark.integration
async def test_get_material_status_reconciles_stale_pending_job(client: AsyncClient, db_session):
    """Status endpoint should fail stale pending jobs instead of leaving them hanging forever."""
    user, token = await create_test_user(client, db_session, "statususer1")

    novel = create_test_novel(db_session, user.id, "Stale Pending Novel")
    job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/test.txt",
        status="pending",
        total_chapters=10,
        processed_chapters=0,
    )
    stale_time = datetime.utcnow() - timedelta(hours=1)
    job.created_at = stale_time
    job.updated_at = stale_time
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    response = await client.get(
        f"/api/v1/materials/{novel.id}/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["error_message"] == ErrorCode.MATERIAL_DISPATCH_TIMEOUT


# ==================== Search Tests ====================


@pytest.mark.integration
async def test_search_materials_empty_query(client: AsyncClient, db_session):
    """Test search with no completed novels returns empty."""
    user, token = await create_test_user(client, db_session, "searchuser1")

    response = await client.get(
        "/api/v1/materials/search",
        params={"q": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.integration
async def test_search_materials_characters(client: AsyncClient, db_session):
    """Test searching for characters."""
    user, token = await create_test_user(client, db_session, "searchuser2")

    novel = create_test_novel(db_session, user.id, "Search Novel")
    create_test_job(db_session, novel.id, "completed")

    character = Character(
        novel_id=novel.id, name="Zhang San", description="A brave warrior"
    )
    db_session.add(character)
    db_session.commit()

    response = await client.get(
        "/api/v1/materials/search",
        params={"q": "Zhang"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(item["entity_type"] == "characters" for item in data)


@pytest.mark.integration
async def test_search_materials_includes_completed_with_errors(client: AsyncClient, db_session):
    """Search should include partially completed materials with usable entities."""
    user, token = await create_test_user(client, db_session, "searchuser_partial")

    novel = create_test_novel(db_session, user.id, "Partial Search Novel")
    create_test_job(db_session, novel.id, "completed_with_errors")

    character = Character(novel_id=novel.id, name="Partial Hero")
    db_session.add(character)
    db_session.commit()

    response = await client.get(
        "/api/v1/materials/search",
        params={"q": "Partial"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert any(
        item["novel_title"] == "Partial Search Novel"
        and item["entity_type"] == "characters"
        and item["name"] == "Partial Hero"
        for item in data
    )


@pytest.mark.integration
async def test_search_materials_stories(client: AsyncClient, db_session):
    """Search should return story entities shown in the reference library summary."""
    user, token = await create_test_user(client, db_session, "searchuser_stories")

    novel = create_test_novel(db_session, user.id, "Story Search Novel")
    create_test_job(db_session, novel.id, "completed")

    storyline = StoryLine(novel_id=novel.id, title="Main Arc")
    db_session.add(storyline)
    db_session.commit()
    db_session.refresh(storyline)

    story = Story(
        story_line_id=storyline.id,
        title="Moonlit Betrayal",
        synopsis="A secret betrayal changes the hero's route.",
    )
    db_session.add(story)
    db_session.commit()
    db_session.refresh(story)

    response = await client.get(
        "/api/v1/materials/search",
        params={"q": "Moonlit"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert any(
        item["novel_title"] == "Story Search Novel"
        and item["entity_type"] == "stories"
        and item["entity_id"] == story.id
        and item["name"] == "Moonlit Betrayal"
        for item in data
    )


@pytest.mark.integration
async def test_search_materials_only_completed(client: AsyncClient, db_session):
    """Test that search only includes completed novels."""
    user, token = await create_test_user(client, db_session, "searchuser3")

    novel_completed = create_test_novel(db_session, user.id, "Completed")
    novel_processing = create_test_novel(db_session, user.id, "Processing")

    create_test_job(db_session, novel_completed.id, "completed")
    create_test_job(db_session, novel_processing.id, "processing")

    # Add characters to both
    char1 = Character(novel_id=novel_completed.id, name="Hero")
    char2 = Character(novel_id=novel_processing.id, name="Villain")
    db_session.add_all([char1, char2])
    db_session.commit()

    response = await client.get(
        "/api/v1/materials/search",
        params={"q": "Hero"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    # Should only find Hero from completed novel
    assert any(item["name"] == "Hero" for item in data)


# ==================== Material Entity Tests ====================


@pytest.mark.integration
async def test_get_characters_includes_first_appearance_chapter_number(
    client: AsyncClient, db_session
):
    """Character list should expose chapter number, not only internal chapter ID."""
    user, token = await create_test_user(client, db_session, "characterschapter1")

    novel = create_test_novel(db_session, user.id, "Character Chapter Novel")
    chapter = Chapter(novel_id=novel.id, chapter_number=7, title="Chapter 7")
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)

    character = Character(
        novel_id=novel.id,
        name="Li Wei",
        first_appearance_chapter_id=chapter.id,
    )
    db_session.add(character)
    db_session.commit()

    response = await client.get(
        f"/api/v1/materials/{novel.id}/characters",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["name"] == "Li Wei"
    assert payload[0]["first_appearance_chapter_id"] == chapter.id
    assert payload[0]["first_appearance_chapter"] == 7


# ==================== Material Detail Tests ====================


@pytest.mark.integration
async def test_get_material_detail_success(client: AsyncClient, db_session):
    """Test getting material detail successfully."""
    user, token = await create_test_user(client, db_session, "detailuser1")

    novel = create_test_novel(db_session, user.id, "Detail Novel")
    novel.synopsis = "A test synopsis"
    create_test_job(db_session, novel.id, "completed")

    # Add entities
    chapter = Chapter(novel_id=novel.id, chapter_number=1, title="Chapter 1")
    character = Character(novel_id=novel.id, name="Character 1")
    storyline = StoryLine(novel_id=novel.id, title="Story Line 1")
    golden_finger = GoldenFinger(novel_id=novel.id, name="GF 1")
    worldview = WorldView(novel_id=novel.id, power_system="Power")
    db_session.add_all([chapter, character, storyline, golden_finger, worldview])
    db_session.commit()

    response = await client.get(
        f"/api/v1/materials/{novel.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == novel.id
    assert data["title"] == "Detail Novel"
    assert data["synopsis"] == "A test synopsis"
    assert data["status"] == "completed"
    assert data["chapters_count"] == 1
    assert data["characters_count"] == 1
    assert data["story_lines_count"] == 1
    assert data["golden_fingers_count"] == 1
    assert data["has_world_view"] is True


@pytest.mark.integration
async def test_get_material_detail_not_found(client: AsyncClient, db_session):
    """Test getting non-existent material returns 403."""
    user, token = await create_test_user(client, db_session, "detailuser2")

    response = await client.get(
        "/api/v1/materials/99999",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_get_material_detail_unauthorized(client: AsyncClient, db_session):
    """Test getting another user's material returns 403."""
    user1, token1 = await create_test_user(client, db_session, "detailuser3")
    user2, _ = await create_test_user(client, db_session, "detailuser4")

    novel = create_test_novel(db_session, user2.id, "User2 Novel")
    create_test_job(db_session, novel.id, "completed")

    response = await client.get(
        f"/api/v1/materials/{novel.id}",
        headers={"Authorization": f"Bearer {token1}"},
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_get_material_detail_deleted(client: AsyncClient, db_session):
    """Test getting soft-deleted material returns 403."""
    from datetime import datetime

    user, token = await create_test_user(client, db_session, "detailuser5")

    novel = create_test_novel(db_session, user.id, "Deleted Novel")
    novel.deleted_at = datetime.utcnow()
    db_session.commit()

    create_test_job(db_session, novel.id, "completed")

    response = await client.get(
        f"/api/v1/materials/{novel.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


# ==================== Preview & Import Tests ====================


@pytest.mark.integration
async def test_get_relationship_preview_returns_single_entity(client: AsyncClient, db_session):
    """Relationship preview should only include the requested relationship."""
    user, token = await create_test_user(client, db_session, "previewuser1")
    novel = create_test_novel(db_session, user.id, "Preview Novel")
    create_test_job(db_session, novel.id, "completed")

    # Create 3 characters and 2 relationships in the same novel
    c1 = Character(novel_id=novel.id, name="A")
    c2 = Character(novel_id=novel.id, name="B")
    c3 = Character(novel_id=novel.id, name="C")
    db_session.add_all([c1, c2, c3])
    db_session.commit()
    db_session.refresh(c1)
    db_session.refresh(c2)
    db_session.refresh(c3)

    rel1 = CharacterRelationship(
        novel_id=novel.id,
        character_a_id=c1.id,
        character_b_id=c2.id,
        relationship_type="ally",
    )
    rel2 = CharacterRelationship(
        novel_id=novel.id,
        character_a_id=c2.id,
        character_b_id=c3.id,
        relationship_type="enemy",
    )
    db_session.add_all([rel1, rel2])
    db_session.commit()
    db_session.refresh(rel1)

    response = await client.get(
        f"/api/v1/materials/{novel.id}/relationships/{rel1.id}/preview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "A ↔ B"
    assert "A ↔ B" in data["markdown"]
    assert "B ↔ C" not in data["markdown"]


@pytest.mark.integration
async def test_worldview_preview_rejects_mismatched_entity_id(client: AsyncClient, db_session):
    """Worldview preview should enforce entity_id ownership and match."""
    user, token = await create_test_user(client, db_session, "previewuser2")
    novel = create_test_novel(db_session, user.id, "Preview Worldview Novel")
    create_test_job(db_session, novel.id, "completed")

    worldview = WorldView(novel_id=novel.id, power_system="Qi")
    db_session.add(worldview)
    db_session.commit()
    db_session.refresh(worldview)

    response = await client.get(
        f"/api/v1/materials/{novel.id}/worldview/{worldview.id + 1}/preview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_import_material_invalid_target_folder_rejected(client: AsyncClient, db_session):
    """Import should reject invalid target_folder_id."""
    user, token = await create_test_user(client, db_session, "importuser1")
    novel = create_test_novel(db_session, user.id, "Import Novel")
    create_test_job(db_session, novel.id, "completed")

    character = Character(novel_id=novel.id, name="Importer")
    db_session.add(character)

    project = Project(name="Import Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(character)
    db_session.refresh(project)

    response = await client.post(
        "/api/v1/materials/import",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "project_id": project.id,
            "novel_id": novel.id,
            "entity_type": "characters",
            "entity_id": character.id,
            "target_folder_id": "non-existent-folder-id",
        },
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_import_material_returns_selected_target_folder_name(client: AsyncClient, db_session):
    """Import response should report the actual selected folder, not the suggestion."""
    from models.file_model import File as ProjectFile

    user, token = await create_test_user(client, db_session, "importuser_selected_folder")
    novel = create_test_novel(db_session, user.id, "Import Selected Folder Novel")
    create_test_job(db_session, novel.id, "completed")

    character = Character(novel_id=novel.id, name="Folder Hero")
    db_session.add(character)

    project = Project(name="Import Selected Folder Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(character)
    db_session.refresh(project)

    target_folder = ProjectFile(
        project_id=project.id,
        title="Custom References",
        file_type="folder",
        content="",
    )
    db_session.add(target_folder)
    db_session.commit()
    db_session.refresh(target_folder)

    response = await client.post(
        "/api/v1/materials/import",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "project_id": project.id,
            "novel_id": novel.id,
            "entity_type": "characters",
            "entity_id": character.id,
            "target_folder_id": target_folder.id,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["folder_name"] == "Custom References"

    imported_file = db_session.get(ProjectFile, payload["file_id"])
    assert imported_file is not None
    assert imported_file.parent_id == target_folder.id


@pytest.mark.integration
async def test_import_material_skips_soft_deleted_auto_folder(client: AsyncClient, db_session):
    """Auto-folder lookup should ignore soft-deleted folders with same title."""
    from models.file_model import File as ProjectFile

    user, token = await create_test_user(client, db_session, "importuser2")
    novel = create_test_novel(db_session, user.id, "Import Novel 2")
    create_test_job(db_session, novel.id, "completed")

    character = Character(novel_id=novel.id, name="Importer2")
    db_session.add(character)

    project = Project(name="Import Project 2", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(character)
    db_session.refresh(project)

    deleted_folder = ProjectFile(
        project_id=project.id,
        title="角色",
        file_type="folder",
        content="",
        is_deleted=True,
    )
    db_session.add(deleted_folder)
    db_session.commit()
    db_session.refresh(deleted_folder)

    response = await client.post(
        "/api/v1/materials/import",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "project_id": project.id,
            "novel_id": novel.id,
            "entity_type": "characters",
            "entity_id": character.id,
        },
    )

    assert response.status_code == 200
    file_id = response.json()["file_id"]
    imported_file = db_session.get(ProjectFile, file_id)
    assert imported_file is not None
    assert imported_file.parent_id != deleted_folder.id

    new_folder = db_session.get(ProjectFile, imported_file.parent_id)
    assert new_folder is not None
    assert new_folder.title == "角色"
    assert new_folder.is_deleted is False


@pytest.mark.integration
async def test_import_material_uses_accept_language_for_auto_folder(client: AsyncClient, db_session):
    """Import should create the same localized auto-folder that preview recommended."""
    from models.file_model import File as ProjectFile

    user, token = await create_test_user(client, db_session, "importuser_locale_single")
    novel = create_test_novel(db_session, user.id, "Localized Import Novel")
    create_test_job(db_session, novel.id, "completed")

    character = Character(novel_id=novel.id, name="Locale Hero")
    db_session.add(character)

    project = Project(name="Localized Import Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(character)
    db_session.refresh(project)

    preview_response = await client.get(
        f"/api/v1/materials/{novel.id}/characters/{character.id}/preview",
        headers={"Authorization": f"Bearer {token}", "Accept-Language": "en"},
    )
    assert preview_response.status_code == 200
    assert preview_response.json()["suggested_folder_name"] == "Characters"

    response = await client.post(
        "/api/v1/materials/import",
        headers={"Authorization": f"Bearer {token}", "Accept-Language": "en"},
        json={
            "project_id": project.id,
            "novel_id": novel.id,
            "entity_type": "characters",
            "entity_id": character.id,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["folder_name"] == "Characters"

    imported_file = db_session.get(ProjectFile, payload["file_id"])
    assert imported_file is not None
    folder = db_session.get(ProjectFile, imported_file.parent_id)
    assert folder is not None
    assert folder.title == "Characters"


@pytest.mark.integration
async def test_batch_import_uses_accept_language_for_auto_folder(client: AsyncClient, db_session):
    """Batch import should preserve localized auto-folder hints for each imported item."""
    from models.file_model import File as ProjectFile

    user, token = await create_test_user(client, db_session, "importuser_locale_batch")
    novel = create_test_novel(db_session, user.id, "Localized Batch Novel")
    create_test_job(db_session, novel.id, "completed")

    character = Character(novel_id=novel.id, name="Batch Locale Hero")
    db_session.add(character)

    project = Project(name="Localized Batch Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(character)
    db_session.refresh(project)

    response = await client.post(
        "/api/v1/materials/batch-import",
        headers={"Authorization": f"Bearer {token}", "Accept-Language": "en"},
        json={
            "project_id": project.id,
            "items": [
                {
                    "novel_id": novel.id,
                    "entity_type": "characters",
                    "entity_id": character.id,
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["failed_count"] == 0
    assert payload["results"][0]["folder_name"] == "Characters"

    imported_file = db_session.get(ProjectFile, payload["results"][0]["file_id"])
    assert imported_file is not None
    folder = db_session.get(ProjectFile, imported_file.parent_id)
    assert folder is not None
    assert folder.title == "Characters"


# ==================== Delete Tests ====================


@pytest.mark.integration
async def test_delete_material_success(client: AsyncClient, db_session):
    """Test successful soft delete of material."""
    user, token = await create_test_user(client, db_session, "deleteuser1")

    novel = create_test_novel(db_session, user.id, "To Delete")
    create_test_job(db_session, novel.id, "completed")

    response = await client.delete(
        f"/api/v1/materials/{novel.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200

    # Verify soft delete
    db_session.refresh(novel)
    assert novel.deleted_at is not None


@pytest.mark.integration
async def test_delete_material_not_found(client: AsyncClient, db_session):
    """Test deleting non-existent material returns 403."""
    user, token = await create_test_user(client, db_session, "deleteuser2")

    response = await client.delete(
        "/api/v1/materials/99999",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_delete_material_unauthorized(client: AsyncClient, db_session):
    """Test deleting another user's material returns 403."""
    user1, token1 = await create_test_user(client, db_session, "deleteuser3")
    user2, _ = await create_test_user(client, db_session, "deleteuser4")

    novel = create_test_novel(db_session, user2.id, "User2 Novel")
    create_test_job(db_session, novel.id, "completed")

    response = await client.delete(
        f"/api/v1/materials/{novel.id}",
        headers={"Authorization": f"Bearer {token1}"},
    )

    assert response.status_code == 403


# ==================== Import Tests ====================


@pytest.mark.integration
async def test_import_material_rejects_invalid_target_folder_id(client: AsyncClient, db_session):
    """Test import endpoint rejects non-existent target folder IDs."""
    user, token = await create_test_user(client, db_session, "importuser11")
    novel = create_test_novel(db_session, user.id, "Import Novel")

    character = Character(novel_id=novel.id, name="Hero")
    db_session.add(character)
    db_session.commit()

    project = Project(name="Import Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    response = await client.post(
        "/api/v1/materials/import",
        json={
            "project_id": project.id,
            "novel_id": novel.id,
            "entity_type": "characters",
            "entity_id": character.id,
            "target_folder_id": "00000000-0000-0000-0000-000000000000",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_import_material_rejects_target_folder_from_other_project(client: AsyncClient, db_session):
    """Test import endpoint rejects target folders that belong to another project."""
    user, token = await create_test_user(client, db_session, "importuser12")
    novel = create_test_novel(db_session, user.id, "Import Novel")

    character = Character(novel_id=novel.id, name="Hero")
    db_session.add(character)
    db_session.commit()

    target_project = Project(name="Target Project", owner_id=user.id)
    other_project = Project(name="Other Project", owner_id=user.id)
    db_session.add_all([target_project, other_project])
    db_session.commit()

    other_project_folder = File(
        project_id=other_project.id,
        title="Characters",
        file_type="folder",
    )
    db_session.add(other_project_folder)
    db_session.commit()

    response = await client.post(
        "/api/v1/materials/import",
        json={
            "project_id": target_project.id,
            "novel_id": novel.id,
            "entity_type": "characters",
            "entity_id": character.id,
            "target_folder_id": other_project_folder.id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400


@pytest.mark.integration
async def test_import_material_rejects_non_folder_target(client: AsyncClient, db_session):
    """Test import endpoint rejects target IDs that are files instead of folders."""
    user, token = await create_test_user(client, db_session, "importuser13")
    novel = create_test_novel(db_session, user.id, "Import Novel")

    character = Character(novel_id=novel.id, name="Hero")
    db_session.add(character)
    db_session.commit()

    project = Project(name="Import Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    non_folder = File(
        project_id=project.id,
        title="Draft File",
        file_type="draft",
        content="content",
    )
    db_session.add(non_folder)
    db_session.commit()

    response = await client.post(
        "/api/v1/materials/import",
        json={
            "project_id": project.id,
            "novel_id": novel.id,
            "entity_type": "characters",
            "entity_id": character.id,
            "target_folder_id": non_folder.id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400


# ==================== Authentication Tests ====================


@pytest.mark.integration
async def test_materials_endpoints_require_auth(client: AsyncClient, db_session):
    """Test that all material endpoints require authentication."""
    endpoints = [
        ("GET", "/api/v1/materials"),
        ("GET", "/api/v1/materials/library-summary"),
        ("GET", "/api/v1/materials/search?q=test"),
        ("GET", "/api/v1/materials/1"),
        ("DELETE", "/api/v1/materials/1"),
    ]

    for method, endpoint in endpoints:
        if method == "GET":
            response = await client.get(endpoint)
        elif method == "DELETE":
            response = await client.delete(endpoint)

        assert response.status_code == 401, f"{method} {endpoint} should return 401"


@pytest.mark.integration
async def test_timeline_endpoint_removed(client: AsyncClient, db_session):
    """No decomposition stage populates event timelines; the unused endpoint is gone."""
    user, token = await create_test_user(client, db_session, "timelinegone")
    novel = create_test_novel(db_session, user.id, "No Timeline")

    response = await client.get(
        f"/api/v1/materials/{novel.id}/timeline",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code in (404, 405)
