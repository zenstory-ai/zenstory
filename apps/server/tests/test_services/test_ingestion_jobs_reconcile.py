"""Stale-job reconciliation: fail stuck jobs with a code and refund their quota once."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from core.error_codes import ErrorCode
from models import User
from models.material_models import IngestionJob, Novel
from models.subscription import SubscriptionPlan, UsageQuota
from services.material.ingestion_jobs_service import IngestionJobsService
from tests.conftest import TestSessionLocal


def _make_user_with_quota(db_session: Session, username: str, used: int = 1) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="x",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    now = datetime.utcnow()
    db_session.add(
        UsageQuota(
            user_id=user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            material_decompositions_used=used,
            monthly_period_start=now - timedelta(days=1),
            monthly_period_end=now + timedelta(days=30),
            last_reset_at=now,
        )
    )
    db_session.commit()
    return user


def _set_monthly_period(
    db_session: Session,
    user: User,
    *,
    start: datetime,
    end: datetime,
    used: int,
) -> None:
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).one()
    quota.monthly_period_start = start
    quota.monthly_period_end = end
    quota.material_decompositions_used = used
    db_session.add(quota)
    db_session.commit()


def _make_job(
    db_session: Session,
    user: User,
    *,
    status: str,
    age: timedelta,
    correlation_id: str | None = None,
    charged: bool | None = True,
    created_at: datetime | None = None,
) -> IngestionJob:
    novel = Novel(user_id=user.id, title=f"Novel {status}")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)
    stamp = created_at or datetime.utcnow() - age
    job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/x.txt",
        status=status,
        correlation_id=correlation_id,
        created_at=stamp,
        updated_at=stamp,
    )
    if charged is not None:
        IngestionJobsService.set_billing(job, quota_charged=charged, quota_refunded=False)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _used(db_session: Session, user: User) -> int:
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).one()
    db_session.refresh(quota)
    return quota.material_decompositions_used


def _record_quota_period(
    db_session: Session,
    job: IngestionJob,
    period_start: datetime,
) -> None:
    IngestionJobsService.set_billing(
        job,
        quota_period_start=period_start.replace(tzinfo=UTC).isoformat(),
    )
    db_session.add(job)
    db_session.commit()


@pytest.mark.unit
def test_dispatched_pending_job_past_30_minutes_fails_and_refunds(db_session: Session):
    user = _make_user_with_quota(db_session, "reconcile_pending_flow")
    job = _make_job(
        db_session, user, status="pending", age=timedelta(minutes=31), correlation_id="run-1"
    )

    IngestionJobsService().reconcile_stale_job(db_session, job)

    assert job.status == "failed"
    assert job.error_message == ErrorCode.MATERIAL_DISPATCH_TIMEOUT
    billing = IngestionJobsService.get_billing(job)
    assert billing["quota_charged"] is False
    assert billing["quota_refunded"] is True
    assert _used(db_session, user) == 0


@pytest.mark.unit
def test_dispatched_pending_job_within_threshold_stays_pending(db_session: Session):
    user = _make_user_with_quota(db_session, "reconcile_pending_fresh")
    job = _make_job(
        db_session, user, status="pending", age=timedelta(minutes=20), correlation_id="run-2"
    )

    IngestionJobsService().reconcile_stale_job(db_session, job)

    assert job.status == "pending"
    assert _used(db_session, user) == 1


@pytest.mark.unit
def test_stalled_processing_job_fails_with_code_and_refunds(db_session: Session):
    user = _make_user_with_quota(db_session, "reconcile_processing")
    job = _make_job(db_session, user, status="processing", age=timedelta(hours=3))

    IngestionJobsService().reconcile_stale_job(db_session, job)

    assert job.status == "failed"
    assert job.error_message == ErrorCode.MATERIAL_PROCESSING_TIMEOUT
    assert json.loads(job.error_details)["stage"] == "processing_timeout"
    assert _used(db_session, user) == 0


@pytest.mark.unit
def test_job_without_billing_state_is_failed_but_not_refunded(db_session: Session):
    user = _make_user_with_quota(db_session, "reconcile_legacy")
    job = _make_job(db_session, user, status="processing", age=timedelta(hours=3), charged=None)

    IngestionJobsService().reconcile_stale_job(db_session, job)

    assert job.status == "failed"
    assert _used(db_session, user) == 1


@pytest.mark.unit
def test_concurrent_reconciles_refund_only_once(db_session: Session):
    user = _make_user_with_quota(db_session, "reconcile_race", used=2)
    job = _make_job(db_session, user, status="processing", age=timedelta(hours=3))

    # Two requests (e.g. list + detail polling) loaded the same stale row.
    other_session = TestSessionLocal()
    try:
        stale_copy = other_session.get(IngestionJob, job.id)
        IngestionJobsService().reconcile_stale_job(db_session, job)
        IngestionJobsService().reconcile_stale_job(other_session, stale_copy)
        assert stale_copy.status == "failed"
    finally:
        other_session.close()

    assert _used(db_session, user) == 1


@pytest.mark.unit
def test_non_refundable_failure_keeps_quota_charged(db_session: Session):
    user = _make_user_with_quota(db_session, "fail_extraction")
    job = _make_job(db_session, user, status="processing", age=timedelta(minutes=1))

    IngestionJobsService().fail_job(
        db_session,
        job,
        error_code=ErrorCode.MATERIAL_EXTRACTION_FAILED,
        stage="flow",
        reason="flow",
    )

    assert job.status == "failed"
    assert IngestionJobsService.get_billing(job)["quota_charged"] is True
    assert _used(db_session, user) == 1


@pytest.mark.unit
def test_previous_beijing_month_failure_does_not_refund_current_month_usage(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    """A late September job must not subtract an unrelated October charge."""
    now = datetime(2026, 10, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("services.material.ingestion_jobs_service.utcnow", lambda: now)
    monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
    user = _make_user_with_quota(db_session, "refund_previous_beijing_month", used=4)
    _set_monthly_period(
        db_session,
        user,
        start=datetime(2026, 9, 30, 16),
        end=datetime(2026, 10, 31, 16),
        used=4,
    )
    job = _make_job(
        db_session,
        user,
        status="processing",
        age=timedelta(),
        created_at=datetime(2026, 9, 30, 15, 59),  # Sep 30, 23:59 Beijing
    )

    IngestionJobsService().fail_job(
        db_session,
        job,
        error_code=ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
        stage="processing_timeout",
        reason="processing_timeout",
    )

    assert _used(db_session, user) == 4


@pytest.mark.unit
def test_job_created_after_month_rollover_refunds_its_reserved_month_only(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    """The charged period, not a later job timestamp, owns the compensation."""
    now = datetime(2026, 10, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("services.material.ingestion_jobs_service.utcnow", lambda: now)
    monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
    user = _make_user_with_quota(db_session, "refund_reserved_period", used=4)
    _set_monthly_period(
        db_session,
        user,
        start=datetime(2026, 9, 30, 16),
        end=datetime(2026, 10, 31, 16),
        used=4,
    )
    job = _make_job(
        db_session,
        user,
        status="processing",
        age=timedelta(),
        created_at=datetime(2026, 9, 30, 16, 1),  # Oct 1, 00:01 Beijing
    )
    _record_quota_period(db_session, job, datetime(2026, 8, 31, 16))

    IngestionJobsService().fail_job(
        db_session,
        job,
        error_code=ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
        stage="processing_timeout",
        reason="processing_timeout",
    )

    assert _used(db_session, user) == 4


@pytest.mark.unit
def test_current_beijing_month_failure_refunds_current_month_usage(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    monkeypatch.setattr("services.material.ingestion_jobs_service.utcnow", lambda: now)
    monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
    user = _make_user_with_quota(db_session, "refund_current_beijing_month", used=4)
    _set_monthly_period(
        db_session,
        user,
        start=datetime(2026, 9, 30, 16),
        end=datetime(2026, 10, 31, 16),
        used=4,
    )
    job = _make_job(
        db_session,
        user,
        status="processing",
        age=timedelta(),
        created_at=datetime(2026, 10, 2, 12),
    )
    _record_quota_period(db_session, job, datetime(2026, 9, 30, 16))

    IngestionJobsService().fail_job(
        db_session,
        job,
        error_code=ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
        stage="processing_timeout",
        reason="processing_timeout",
    )

    assert _used(db_session, user) == 3


@pytest.mark.unit
def test_repeated_failure_refunds_current_month_usage_only_once(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    monkeypatch.setattr("services.material.ingestion_jobs_service.utcnow", lambda: now)
    monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
    user = _make_user_with_quota(db_session, "refund_current_month_once", used=4)
    _set_monthly_period(
        db_session,
        user,
        start=datetime(2026, 9, 30, 16),
        end=datetime(2026, 10, 31, 16),
        used=4,
    )
    job = _make_job(
        db_session,
        user,
        status="processing",
        age=timedelta(),
        created_at=datetime(2026, 10, 2, 12),
    )
    service = IngestionJobsService()

    for _ in range(2):
        service.fail_job(
            db_session,
            job,
            error_code=ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
            stage="processing_timeout",
            reason="processing_timeout",
        )

    assert _used(db_session, user) == 3


@pytest.mark.unit
def test_beijing_new_month_job_refunds_while_utc_is_still_previous_month(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    """Sep 30 16:30 UTC is already Oct 1 in Beijing and belongs to October."""
    now = datetime(2026, 9, 30, 18, tzinfo=UTC)
    monkeypatch.setattr("services.material.ingestion_jobs_service.utcnow", lambda: now)
    monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
    user = _make_user_with_quota(db_session, "refund_beijing_month_before_utc", used=2)
    _set_monthly_period(
        db_session,
        user,
        start=datetime(2026, 9, 30, 16),
        end=datetime(2026, 10, 31, 16),
        used=2,
    )
    job = _make_job(
        db_session,
        user,
        status="processing",
        age=timedelta(),
        created_at=datetime(2026, 9, 30, 16, 30),
    )

    IngestionJobsService().fail_job(
        db_session,
        job,
        error_code=ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
        stage="processing_timeout",
        reason="processing_timeout",
    )

    assert _used(db_session, user) == 1


@pytest.mark.unit
def test_transient_refund_failure_remains_charged_and_retries(db_session, monkeypatch):
    from services.quota_service import quota_service

    user = _make_user_with_quota(db_session, "refund_transient", used=2)
    job = _make_job(db_session, user, status="processing", age=timedelta(hours=3))
    service = IngestionJobsService()
    real_release = quota_service.release_feature_quota
    attempts = []

    def flaky_release(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary database failure")
        return real_release(*args, **kwargs)

    monkeypatch.setattr(quota_service, "release_feature_quota", flaky_release)
    service.reconcile_stale_job(db_session, job)
    assert job.status == "failed"
    assert _used(db_session, user) == 2
    assert service.get_billing(job)["quota_charged"] is True
    assert service.get_billing(job)["quota_refunded"] is False

    service.reconcile_stale_job(db_session, job)
    service.reconcile_stale_job(db_session, job)
    assert attempts == [1, 1]
    assert _used(db_session, user) == 1
    assert service.get_billing(job)["quota_charged"] is False
    assert service.get_billing(job)["quota_refunded"] is True


@pytest.mark.unit
def test_refund_settlement_rolls_back_decrement_on_late_failure(db_session, monkeypatch):
    from services.quota_service import quota_service

    user = _make_user_with_quota(db_session, "refund_atomic_rollback", used=2)
    job = _make_job(db_session, user, status="processing", age=timedelta(hours=3))
    service = IngestionJobsService()
    real_release = quota_service.release_feature_quota

    def fail_after_decrement(*args, **kwargs):
        real_release(*args, **kwargs)
        raise RuntimeError("failure after quota update, before settlement commit")

    with monkeypatch.context() as fault:
        fault.setattr(quota_service, "release_feature_quota", fail_after_decrement)
        service.reconcile_stale_job(db_session, job)
    assert _used(db_session, user) == 2
    assert service.get_billing(job)["quota_charged"] is True

    service.reconcile_stale_job(db_session, job)
    assert _used(db_session, user) == 1
    assert service.get_billing(job)["quota_refunded"] is True


@pytest.mark.unit
def test_stale_flow_failure_cannot_replay_refunded_billing(db_session):
    user = _make_user_with_quota(db_session, "refund_stale_flow", used=2)
    job = _make_job(db_session, user, status="processing", age=timedelta(hours=3))
    service = IngestionJobsService()
    with TestSessionLocal() as other_session:
        stale_copy = other_session.get(IngestionJob, job.id)
        service.fail_job(db_session, job, error_code=ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
                         stage="flow", reason="first failure")
        service.fail_job(other_session, stale_copy, error_code=ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
                         stage="flow", reason="stale duplicate failure")
        assert stale_copy.status == "failed"
        assert service.get_billing(stale_copy)["quota_refunded"] is True
    assert _used(db_session, user) == 1


@pytest.mark.unit
def test_stale_failure_cannot_overwrite_completed_job(db_session):
    user = _make_user_with_quota(db_session, "refund_completed_guard", used=2)
    job = _make_job(db_session, user, status="processing", age=timedelta(hours=3))
    with TestSessionLocal() as other_session:
        stale_copy = other_session.get(IngestionJob, job.id)
        job.status = "completed"
        db_session.add(job)
        db_session.commit()
        assert IngestionJobsService().fail_job(
            other_session, stale_copy, error_code=ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
            stage="flow", reason="late failure",
        ) is False
        assert stale_copy.status == "completed"
    assert _used(db_session, user) == 2


@pytest.mark.unit
def test_zero_counter_settles_without_claiming_refund_or_stealing_later_unit(db_session):
    user = _make_user_with_quota(db_session, "refund_noop_settled", used=0)
    job = _make_job(db_session, user, status="processing", age=timedelta(hours=3))
    service = IngestionJobsService()
    service.reconcile_stale_job(db_session, job)
    assert service.get_billing(job)["quota_charged"] is False
    assert service.get_billing(job)["quota_refunded"] is False
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).one()
    quota.material_decompositions_used = 1
    db_session.add(quota)
    db_session.commit()
    service.reconcile_stale_job(db_session, job)
    assert _used(db_session, user) == 1


@pytest.mark.unit
def test_monthly_reserve_and_release_join_caller_transaction(db_session, monkeypatch):
    from services.quota_service import quota_service

    user = _make_user_with_quota(db_session, "refund_transaction_owner", used=2)
    plan = SubscriptionPlan(name="pro", display_name="Pro", features={"material_decompositions": 5})
    monkeypatch.setattr(quota_service, "get_user_plan", lambda *args, **kwargs: plan)
    assert quota_service.reserve_feature_quota(
        db_session, user.id, "material_decompose", commit=False,
    ) is not None
    db_session.rollback()
    assert _used(db_session, user) == 2
    assert quota_service.release_feature_quota(
        db_session, user.id, "material_decompose", commit=False,
    ) is True
    db_session.rollback()
    assert _used(db_session, user) == 2


@pytest.mark.unit
def test_refund_does_not_create_a_missing_quota(db_session):
    user = _make_user_with_quota(db_session, "refund_missing_quota", used=2)
    job = _make_job(db_session, user, status="processing", age=timedelta(hours=3))
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).one()
    db_session.delete(quota)
    db_session.commit()
    service = IngestionJobsService()
    service.reconcile_stale_job(db_session, job)
    assert db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).first() is None
    assert service.get_billing(job)["quota_charged"] is False
    assert service.get_billing(job)["quota_refunded"] is False


@pytest.mark.unit
def test_list_settles_superseded_failed_job_but_returns_latest_status(db_session, monkeypatch):
    from api.materials.library import get_materials
    from api.materials.search import get_library_summary
    from services.quota_service import quota_service

    user = _make_user_with_quota(db_session, "refund_historical_job", used=2)
    job = _make_job(db_session, user, status="processing", age=timedelta(hours=3))

    def unavailable_release(*args, **kwargs):
        raise RuntimeError("temporary refund failure")

    with monkeypatch.context() as fault:
        fault.setattr(quota_service, "release_feature_quota", unavailable_release)
        IngestionJobsService().reconcile_stale_job(db_session, job)
    latest = IngestionJob(novel_id=job.novel_id, source_path="/tmp/new.txt", status="completed")
    db_session.add(latest)
    db_session.commit()

    result = get_materials(current_user=user, session=db_session)
    assert result[0].status == "completed"
    assert _used(db_session, user) == 1
    assert IngestionJobsService.get_billing(job)["quota_refunded"] is True
    get_library_summary(current_user=user, session=db_session)
    assert _used(db_session, user) == 1
