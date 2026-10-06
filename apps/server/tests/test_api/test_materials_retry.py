from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import select

import api.materials.upload as materials_upload_api
from core.error_handler import APIException
from models import User
from models.material_models import IngestionJob, Novel
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.core.auth_service import hash_password
from services.material.ingestion_jobs_service import IngestionJobsService


@pytest.fixture(autouse=True)
def stub_retry_flow_dispatch(monkeypatch):
    async def _fake_start_flow_deployment(*args, **kwargs):
        return "flow-run-test"

    monkeypatch.setattr(
        materials_upload_api,
        "_start_flow_deployment",
        _fake_start_flow_deployment,
    )


async def _create_test_user_and_token(
    client: AsyncClient,
    db_session,
    username: str,
) -> tuple[User, str]:
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
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return user, token


@pytest.mark.integration
async def test_retry_material_job_rejects_running_job(client: AsyncClient, db_session):
    user, token = await _create_test_user_and_token(client, db_session, "retrybusy1")

    novel = Novel(user_id=user.id, title="Retry Busy Novel", author="Tester")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    running_job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/test.txt",
        status="processing",
        total_chapters=10,
        processed_chapters=1,
    )
    db_session.add(running_job)
    db_session.commit()

    response = await client.post(
        f"/api/v1/materials/{novel.id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409


@pytest.mark.integration
async def test_retry_material_job_requires_paid_materials_access(client: AsyncClient, db_session):
    free_user = User(
        username="retryfree1",
        email="retryfree1@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(free_user)
    db_session.commit()
    db_session.refresh(free_user)

    novel = Novel(user_id=free_user.id, title="Retry Free Novel", author="Tester")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    failed_job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/test.txt",
        status="failed",
        total_chapters=10,
        processed_chapters=0,
        error_message="boom",
    )
    db_session.add(failed_job)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": free_user.username, "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    response = await client.post(
        f"/api/v1/materials/{novel.id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 402
    assert response.json()["error_code"] == "ERR_FEATURE_NOT_INCLUDED"


@pytest.mark.integration
async def test_retry_material_job_consumes_quota_for_regular_failed_jobs(client: AsyncClient, db_session):
    user, token = await _create_test_user_and_token(client, db_session, "retryquota1")

    now = datetime.utcnow()
    quota = UsageQuota(
        user_id=user.id,
        period_start=now,
        period_end=now + timedelta(days=30),
        ai_conversations_used=0,
        material_decompositions_used=4,
        monthly_period_start=now - timedelta(days=1),
        monthly_period_end=now + timedelta(days=30),
        last_reset_at=now,
    )
    db_session.add(quota)
    db_session.commit()

    novel = Novel(user_id=user.id, title="Retry Charged Novel", author="Tester")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    failed_job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/test.txt",
        status="failed",
        total_chapters=10,
        processed_chapters=0,
        error_message="flow failed",
        error_details='{"stage":"flow","message":"flow failed"}',
    )
    db_session.add(failed_job)
    db_session.commit()

    response = await client.post(
        f"/api/v1/materials/{novel.id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    db_session.refresh(quota)
    assert quota.material_decompositions_used == 5


@pytest.mark.integration
async def test_retry_material_job_allows_completed_with_errors_and_dispatches_exact_job(
    client: AsyncClient,
    db_session,
    monkeypatch,
):
    user, token = await _create_test_user_and_token(client, db_session, "retrypartial1")
    novel = Novel(user_id=user.id, title="Retry Partial Novel", author="Tester")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)
    db_session.add(
        IngestionJob(
            novel_id=novel.id,
            source_path="/tmp/test.txt",
            status="completed_with_errors",
            total_chapters=10,
            processed_chapters=8,
            error_message="two chapters failed",
        )
    )
    db_session.commit()
    dispatched_job_ids: list[int] = []

    async def _capture_dispatch(*args, **kwargs):
        dispatched_job_ids.append(kwargs["job_id"])
        return "flow-run-test"

    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _capture_dispatch)
    response = await client.post(
        f"/api/v1/materials/{novel.id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert dispatched_job_ids == [response.json()["job_id"]]


@pytest.mark.integration
async def test_retry_material_job_does_not_consume_quota_when_dispatch_fails(
    client: AsyncClient, db_session, monkeypatch
):
    async def _failed_start_flow_deployment(*args, **kwargs):
        return None

    monkeypatch.setattr(
        materials_upload_api,
        "_start_flow_deployment",
        _failed_start_flow_deployment,
    )

    user, token = await _create_test_user_and_token(client, db_session, "retrydispatchfail")

    now = datetime.utcnow()
    quota = UsageQuota(
        user_id=user.id,
        period_start=now,
        period_end=now + timedelta(days=30),
        ai_conversations_used=0,
        material_decompositions_used=4,
        monthly_period_start=now - timedelta(days=1),
        monthly_period_end=now + timedelta(days=30),
        last_reset_at=now,
    )
    db_session.add(quota)
    db_session.commit()

    novel = Novel(user_id=user.id, title="Retry Dispatch Failure Novel", author="Tester")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    failed_job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/test.txt",
        status="failed",
        total_chapters=10,
        processed_chapters=0,
        error_message="flow failed",
        error_details='{"stage":"flow","message":"flow failed"}',
    )
    db_session.add(failed_job)
    db_session.commit()

    response = await client.post(
        f"/api/v1/materials/{novel.id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 503
    db_session.refresh(quota)
    assert quota.material_decompositions_used == 4

    latest_job = db_session.exec(
        select(IngestionJob)
        .where(IngestionJob.novel_id == novel.id)
        .order_by(IngestionJob.created_at.desc())
    ).first()
    assert latest_job is not None
    assert latest_job.status == "failed"


@pytest.mark.integration
async def test_retry_dispatch_failure_does_not_refund_a_new_month_charge(
    client: AsyncClient, db_session, monkeypatch
):
    user, token = await _create_test_user_and_token(client, db_session, "retry_cross_month")
    now = datetime(2026, 10, 1, 1, tzinfo=UTC)
    current_period = datetime(2026, 9, 30, 16)
    old_period = datetime(2026, 8, 31, 16, tzinfo=UTC)
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
    novel = Novel(user_id=user.id, title="Retry crossed month")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)
    db_session.add(IngestionJob(novel_id=novel.id, source_path="/tmp/test.txt", status="failed"))
    db_session.commit()
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
        f"/api/v1/materials/{novel.id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 503
    db_session.refresh(quota)
    assert quota.material_decompositions_used == 1
    latest_job = db_session.exec(
        select(IngestionJob)
        .where(IngestionJob.novel_id == novel.id)
        .order_by(IngestionJob.created_at.desc())
    ).first()
    billing = json.loads(latest_job.stage_progress)["billing"]
    assert datetime.fromisoformat(billing["quota_period_start"]) == old_period


@pytest.mark.integration
@pytest.mark.parametrize(
    "billing",
    [
        None,  # legacy job written before billing state existed
        {"quota_charged": False, "quota_refunded": True},  # refunded dispatch failure
    ],
)
async def test_retry_after_refunded_dispatch_failure_is_charged(
    client: AsyncClient, db_session, monkeypatch, billing
):
    """A dispatch failure already refunded its quota; retrying it must not be free."""
    user, token = await _create_test_user_and_token(
        client, db_session, f"retryquota2{'b' if billing else 'l'}"
    )

    now = datetime.utcnow()
    quota = UsageQuota(
        user_id=user.id,
        period_start=now,
        period_end=now + timedelta(days=30),
        ai_conversations_used=0,
        material_decompositions_used=5,
        monthly_period_start=now - timedelta(days=1),
        monthly_period_end=now + timedelta(days=30),
        last_reset_at=now,
    )
    db_session.add(quota)
    db_session.commit()

    novel = Novel(user_id=user.id, title="Retry Compensated Novel", author="Tester")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    failed_job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/test.txt",
        status="failed",
        total_chapters=10,
        processed_chapters=0,
        error_message="deployment startup failed",
        error_details='{"stage":"deployment_start","message":"deployment startup failed"}',
        stage_progress=json.dumps({"billing": billing}) if billing else None,
    )
    db_session.add(failed_job)
    db_session.commit()

    dispatch_calls: list[int] = []

    async def _capture_dispatch(*args, **kwargs):
        dispatch_calls.append(kwargs["job_id"])
        return "flow-run-test"

    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _capture_dispatch)

    exhausted = await client.post(
        f"/api/v1/materials/{novel.id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert exhausted.status_code == 402
    assert dispatch_calls == []

    quota.material_decompositions_used = 4
    db_session.add(quota)
    db_session.commit()

    charged = await client.post(
        f"/api/v1/materials/{novel.id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert charged.status_code == 200
    db_session.refresh(quota)
    assert quota.material_decompositions_used == 5
    new_job = db_session.get(IngestionJob, charged.json()["job_id"])
    assert json.loads(new_job.stage_progress)["billing"]["quota_charged"] is True


@pytest.mark.integration
async def test_retry_material_job_rejects_when_quota_cannot_be_consumed(
    client: AsyncClient, db_session, monkeypatch
):
    """If quota consumption fails, retry must reject (402) and must NOT proceed
    to dispatch or refund quota it never consumed.

    Regression: when quota reservation returned None but a concurrent decrement
    made check_feature_quota report allowed=True, the old code fell through to
    dispatch without charging quota, and a dispatch failure then refunded a unit
    that was never consumed (refund-leak / free decomposition).
    """
    user, token = await _create_test_user_and_token(client, db_session, "retryconsumefail")

    now = datetime.utcnow()
    quota = UsageQuota(
        user_id=user.id,
        period_start=now,
        period_end=now + timedelta(days=30),
        ai_conversations_used=0,
        material_decompositions_used=5,
        monthly_period_start=now - timedelta(days=1),
        monthly_period_end=now + timedelta(days=30),
        last_reset_at=now,
    )
    db_session.add(quota)
    db_session.commit()

    novel = Novel(user_id=user.id, title="Retry Consume Fail Novel", author="Tester")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    failed_job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/test.txt",
        status="failed",
        total_chapters=10,
        processed_chapters=0,
        error_message="flow failed",
        error_details='{"stage":"flow","message":"flow failed"}',
    )
    db_session.add(failed_job)
    db_session.commit()

    # Simulate the race: the pre-check passes, reservation fails, but a
    # concurrent decrement makes check_feature_quota report allowed=True.
    monkeypatch.setattr(materials_upload_api, "check_quota", lambda *a, **k: None)
    monkeypatch.setattr(
        materials_upload_api.quota_service,
        "reserve_feature_quota",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        materials_upload_api.quota_service,
        "check_feature_quota",
        lambda *a, **k: (True, 4, 5),
    )

    release_calls: list[int] = []
    monkeypatch.setattr(
        materials_upload_api.quota_service,
        "release_feature_quota",
        lambda *a, **k: release_calls.append(1),
    )

    dispatch_calls: list[int] = []

    async def _tracking_dispatch(*args, **kwargs):
        dispatch_calls.append(1)
        return "flow-run-test"

    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _tracking_dispatch)

    response = await client.post(
        f"/api/v1/materials/{novel.id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 402
    assert dispatch_calls == []  # never proceeded to dispatch
    assert release_calls == []  # never refunded quota it did not consume
    db_session.refresh(quota)
    assert quota.material_decompositions_used == 5  # unchanged


@pytest.mark.integration
@pytest.mark.parametrize("dispatch_raises", [False, True])
async def test_retry_reconciles_refund_after_failure_status_cannot_be_persisted(
    client: AsyncClient, db_session, monkeypatch, dispatch_raises,
):
    user, _ = await _create_test_user_and_token(
        client, db_session, f"retrystatusfail{int(dispatch_raises)}",
    )
    novel = Novel(user_id=user.id, title="Retry status persistence failure")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)
    db_session.add(IngestionJob(
        novel_id=novel.id, source_path="/tmp/test.txt", status="failed",
    ))
    db_session.commit()

    async def _dispatch(*args, **kwargs):
        if dispatch_raises:
            raise RuntimeError("SDK dispatch failed")
        return None

    def _mark_failed(*args, **kwargs):
        raise RuntimeError("Failure status persistence failed")

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
        await materials_upload_api.retry_material_job(
            novel_id=novel.id, current_user=user, session=db_session,
        )
    assert refund_calls == []
    if dispatch_raises:
        assert str(raised.value) == "SDK dispatch failed"
    else:
        assert raised.value.status_code == 503
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
async def test_retry_waits_for_refund_settlement_before_a_new_charge(client, db_session, monkeypatch):
    user, token = await _create_test_user_and_token(client, db_session, "retry_wait_refund")
    quota_service = materials_upload_api.quota_service
    period = quota_service.reserve_feature_quota(db_session, user.id, "material_decompose")
    assert period is not None
    novel = Novel(user_id=user.id, title="Pending settlement")
    db_session.add(novel)
    db_session.flush()
    job = IngestionJob(
        novel_id=novel.id, source_path="/tmp/test.txt", status="failed",
        error_message=materials_upload_api.ErrorCode.MATERIAL_DISPATCH_FAILED,
    )
    IngestionJobsService.set_billing(
        job, quota_charged=True, quota_refunded=False, quota_period_start=period.isoformat(),
    )
    db_session.add(job)
    db_session.commit()

    def unavailable_release(*args, **kwargs):
        raise RuntimeError("temporary settlement failure")

    with monkeypatch.context() as fault:
        fault.setattr(quota_service, "release_feature_quota", unavailable_release)
        response = await client.post(
            f"/api/v1/materials/{novel.id}/retry", headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 503
    db_session.refresh(job)
    assert IngestionJobsService.get_billing(job)["quota_charged"] is True
    assert len(db_session.exec(select(IngestionJob).where(IngestionJob.novel_id == novel.id)).all()) == 1
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).one()
    db_session.refresh(quota)
    assert quota.material_decompositions_used == 1

    response = await client.post(
        f"/api/v1/materials/{novel.id}/retry", headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    db_session.refresh(job)
    db_session.refresh(quota)
    assert IngestionJobsService.get_billing(job)["quota_refunded"] is True
    assert quota.material_decompositions_used == 1  # one new job, not two units
    assert len(db_session.exec(select(IngestionJob).where(IngestionJob.novel_id == novel.id)).all()) == 2
