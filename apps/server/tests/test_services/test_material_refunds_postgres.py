from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import create_engine, update
from sqlmodel import Session, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from models.entities import User
from models.material_models import IngestionJob, Novel
from models.subscription import SubscriptionPlan, UsageQuota
from services.material.ingestion_jobs_service import IngestionJobsService

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
    reason="isolated PostgreSQL URL not configured",
)

TABLES = [
    User.__table__,
    Novel.__table__,
    IngestionJob.__table__,
    UsageQuota.__table__,
]
MONTH_START = datetime(2026, 9, 30, 16, tzinfo=UTC)
MONTH_END = datetime(2026, 10, 31, 16, tzinfo=UTC)


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(
        os.environ["ZENSTORY_TEST_POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC"},
    )
    for table in reversed(TABLES):
        table.drop(engine, checkfirst=True)
    for table in TABLES:
        table.create(engine, checkfirst=True)
    try:
        yield engine
    finally:
        for table in reversed(TABLES):
            table.drop(engine, checkfirst=True)
        engine.dispose()


def _charged_job(session: Session, suffix: str, *, used: int = 2) -> tuple[str, int]:
    user = User(
        email=f"refund-{suffix}@example.com",
        username=f"refund-{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    session.add(
        UsageQuota(
            user_id=user.id,
            period_start=MONTH_START,
            period_end=MONTH_START + timedelta(days=1),
            last_reset_at=MONTH_START,
            monthly_period_start=MONTH_START,
            monthly_period_end=MONTH_END,
            material_decompositions_used=used,
        )
    )
    novel = Novel(user_id=user.id, title=f"Refund {suffix}")
    session.add(novel)
    session.commit()
    session.refresh(novel)

    job = IngestionJob(
        novel_id=novel.id,
        source_path=f"/tmp/{suffix}.txt",
        status="processing",
    )
    IngestionJobsService.set_billing(
        job,
        quota_charged=True,
        quota_refunded=False,
        quota_period_start=MONTH_START.isoformat(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    assert job.id is not None
    return user.id, job.id


def _quota_used(session: Session, user_id: str) -> int:
    quota = session.exec(select(UsageQuota).where(UsageQuota.user_id == user_id)).one()
    return quota.material_decompositions_used


def test_refund_rolls_back_quota_decrement_when_job_billing_cas_is_stale(
    pg_engine, monkeypatch: pytest.MonkeyPatch
):
    from services.quota_service import quota_service

    with Session(pg_engine) as setup:
        user_id, job_id = _charged_job(setup, "stale-cas")
        job = setup.get(IngestionJob, job_id)
        assert job is not None
        job.status = "failed"
        job.error_message = ErrorCode.MATERIAL_PROCESSING_TIMEOUT
        setup.add(job)
        setup.commit()

    service = IngestionJobsService()
    real_release = quota_service.release_feature_quota

    def stale_after_decrement(*args, **kwargs):
        released = real_release(*args, **kwargs)
        with Session(pg_engine) as concurrent:
            concurrent.exec(
                update(IngestionJob)
                .where(IngestionJob.id == job_id)
                .values(updated_at=datetime(2026, 10, 6, 1, tzinfo=UTC))
            )
            concurrent.commit()
        return released

    with Session(pg_engine) as session, monkeypatch.context() as fault:
        job = session.get(IngestionJob, job_id)
        assert job is not None
        fault.setattr(quota_service, "release_feature_quota", stale_after_decrement)
        service.reconcile_stale_job(session, job)

    with Session(pg_engine) as verify:
        job = verify.get(IngestionJob, job_id)
        assert job is not None
        assert _quota_used(verify, user_id) == 2
        assert service.get_billing(job)["quota_charged"] is True

    with Session(pg_engine) as retry:
        job = retry.get(IngestionJob, job_id)
        assert job is not None
        service.reconcile_stale_job(retry, job)

    with Session(pg_engine) as verify:
        job = verify.get(IngestionJob, job_id)
        assert job is not None
        assert _quota_used(verify, user_id) == 1
        assert service.get_billing(job)["quota_refunded"] is True


def test_concurrent_duplicate_failures_refund_one_quota_unit(pg_engine):
    with Session(pg_engine) as setup:
        user_id, job_id = _charged_job(setup, "duplicate-failure")

    barrier = Barrier(2)

    def fail_loaded_snapshot() -> bool:
        with Session(pg_engine) as session:
            job = session.get(IngestionJob, job_id)
            assert job is not None
            barrier.wait(timeout=5)
            return IngestionJobsService().fail_job(
                session,
                job,
                error_code=ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
                stage="flow",
                reason="duplicate flow failure",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: fail_loaded_snapshot(), range(2)))

    assert sorted(outcomes) == [False, True]
    with Session(pg_engine) as verify:
        job = verify.get(IngestionJob, job_id)
        assert job is not None
        billing = IngestionJobsService.get_billing(job)
        assert job.status == "failed"
        assert billing["quota_charged"] is False
        assert billing["quota_refunded"] is True
        assert _quota_used(verify, user_id) == 1


def test_concurrent_retries_charge_and_create_one_job_when_clock_does_not_advance(
    pg_engine, monkeypatch: pytest.MonkeyPatch
):
    from api.materials import upload as upload_api
    from services.quota_service import quota_service

    frozen_now = datetime(2026, 10, 6, 8, tzinfo=UTC)
    with Session(pg_engine) as setup:
        user_id, source_job_id = _charged_job(setup, "duplicate-retry", used=0)
        source_job = setup.get(IngestionJob, source_job_id)
        assert source_job is not None
        source_job.status = "failed"
        source_job.error_message = ErrorCode.MATERIAL_PROCESSING_TIMEOUT
        source_job.updated_at = frozen_now
        IngestionJobsService.set_billing(
            source_job,
            quota_charged=False,
            quota_refunded=True,
        )
        setup.add(source_job)
        setup.commit()
        novel_id = source_job.novel_id

    current_user = User(
        id=user_id,
        email="refund-duplicate-retry@example.com",
        username="refund-duplicate-retry",
        hashed_password="hashed",
    )

    plan = SubscriptionPlan(
        name="postgres-retry-plan",
        display_name="PostgreSQL retry plan",
        features={"material_decompositions": 5},
    )
    both_loaded_source = Barrier(2)

    async def accepted_dispatch(**_kwargs):
        return "flow-run"

    def synchronize_after_source_read(*_args, **_kwargs):
        both_loaded_source.wait(timeout=5)

    monkeypatch.setattr(upload_api, "utcnow", lambda: frozen_now)
    monkeypatch.setattr(upload_api, "_start_flow_deployment", accepted_dispatch)
    monkeypatch.setattr(upload_api, "check_quota", synchronize_after_source_read)
    monkeypatch.setattr(quota_service, "has_feature_access", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(quota_service, "get_user_plan", lambda *_args, **_kwargs: plan)

    def retry_loaded_source():
        with Session(pg_engine) as session:
            try:
                return asyncio.run(
                    upload_api.retry_material_job(
                        novel_id,
                        current_user=current_user,
                        _rate_limit=0,
                        session=session,
                    )
                )
            except APIException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: retry_loaded_source(), range(2)))

    assert sorted(409 if outcome == 409 else 200 for outcome in outcomes) == [200, 409]
    with Session(pg_engine) as verify:
        jobs = verify.exec(
            select(IngestionJob).where(IngestionJob.novel_id == novel_id)
        ).all()
        assert len(jobs) == 2
        assert _quota_used(verify, user_id) == 1
