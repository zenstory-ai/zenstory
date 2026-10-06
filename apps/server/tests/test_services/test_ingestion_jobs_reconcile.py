"""Stale-job reconciliation: fail stuck jobs with a code and refund their quota once."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from core.error_codes import ErrorCode
from models import User
from models.material_models import IngestionJob, Novel
from models.subscription import UsageQuota
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
