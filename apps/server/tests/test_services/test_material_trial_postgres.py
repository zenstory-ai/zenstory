"""Owned PostgreSQL proof that the trial claim, network seat and cost total hold under real locks."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import User
from models.ai_cost_budget import AICostReservation
from models.material_models import IngestionJob, Novel
from models.material_trial import MaterialTrialNetworkSeat
from models.subscription import UsageQuota
from services.material.ingestion_jobs_service import IngestionJobsService
from services.material.trial_network import claim_network_seat
from services.quota_service import quota_service
from services.usage import cost_budget as budget

pytestmark = pytest.mark.skipif(not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="owned PostgreSQL URL required")

NOW = datetime(2026, 11, 6, 2, tzinfo=UTC)
PUBLIC_IP = "93.184.216.34"


@pytest.fixture(scope="module")
def owned_engine():
    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert url == os.environ["DATABASE_URL"]
    engine = create_engine(
        url, pool_size=20, connect_args={"options": "-c lock_timeout=5000 -c statement_timeout=15000"}
    )
    assert engine.dialect.name == "postgresql"
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


def _account(engine, *, used=False) -> str:
    with Session(engine) as session:
        suffix = uuid4().hex
        user = User(username=suffix, email=f"{suffix}@example.test", hashed_password="unused")
        session.add(user)
        session.flush()
        session.add(UsageQuota(
            user_id=user.id,
            period_start=NOW.replace(tzinfo=None),
            period_end=(NOW + timedelta(days=30)).replace(tzinfo=None),
            material_trial_used_at=NOW.replace(tzinfo=None) if used else None,
        ))
        session.commit()
        return user.id


def test_same_account_concurrent_claims_succeed_once(owned_engine):
    user_id = _account(owned_engine)

    def claim(_):
        with Session(owned_engine) as session:
            taken = quota_service.reserve_material_trial(session, user_id, commit=False)
            session.commit()
            return taken

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))

    assert sum(results) == 1


def test_accounts_racing_for_one_network_get_one_seat_and_a_rollback_frees_it(owned_engine, monkeypatch):
    ip = "93.184.216.40"
    with Session(owned_engine) as session:
        assert claim_network_seat(session, user_id=_account(owned_engine), client_ip=ip)
        session.rollback()  # An uncommitted claim is not a used seat.

    accounts = [_account(owned_engine) for _ in range(8)]

    def claim(user_id):
        with Session(owned_engine) as session:
            won = claim_network_seat(session, user_id=user_id, client_ip=ip)
            session.commit()
            return won

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, accounts))

    assert sum(results) == 1
    with Session(owned_engine) as session:
        assert len(session.exec(select(MaterialTrialNetworkSeat)).all()) >= 1


def test_concurrent_reservations_never_pass_the_lifetime_budget_across_sessions(owned_engine, monkeypatch):
    """Independent sessions stand in for separate workers and restarts: nothing lives in memory."""
    monkeypatch.setattr(budget, "_session_factory", lambda: Session(owned_engine))
    user_id = _account(owned_engine, used=True)

    def reserve(_):
        request = {
            "model": "deepseek-flash",
            "messages": [{"role": "user", "content": "字" * 40_000}],
            "max_tokens": 64_000,
        }
        try:
            with budget.material_trial_call(output_limit=4096):
                return budget.reserve_model_call(request, user_id=user_id, now=NOW)
        except APIException as error:
            assert error.error_code == ErrorCode.MATERIAL_TRIAL_COST_LIMIT
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        calls = list(pool.map(reserve, range(40)))

    granted = [call for call in calls if call]
    assert 0 < len(granted) < 40
    with Session(owned_engine) as session:
        quota = session.exec(select(UsageQuota).where(UsageQuota.user_id == user_id)).one()
        rows = session.exec(select(AICostReservation).where(AICostReservation.user_id == user_id)).all()
    assert quota.material_trial_charged_units <= budget.MATERIAL_TRIAL_BUDGET_UNITS
    assert quota.material_trial_charged_units == sum(row.reserved_units for row in rows)
    assert len(rows) == len(granted)

    # Settling frees only what actual usage proves; concurrent settles apply once each.
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(
            lambda call: budget.settle_model_call(call, {"prompt_tokens": 10, "completion_tokens": 10}),
            granted + granted,
        ))
    with Session(owned_engine) as session:
        quota = session.exec(select(UsageQuota).where(UsageQuota.user_id == user_id)).one()
    assert quota.material_trial_charged_units == len(granted) * (10 * 200 + 10 * 800)


def test_a_late_progress_write_cannot_overwrite_a_concurrent_failure(owned_engine):
    user_id = _account(owned_engine)
    with Session(owned_engine) as session:
        novel = Novel(user_id=user_id, title="t", source_meta="{}")
        session.add(novel)
        session.flush()
        job = IngestionJob(novel_id=novel.id, source_path="x", status="processing", total_chapters=3, processed_chapters=0)
        session.add(job)
        session.commit()
        job_id = job.id

    service = IngestionJobsService()

    def late_progress(_):
        with Session(owned_engine) as session:
            service.update_processed(session, job_id, processed_chapters=1, status="processing", stage="stage1")
            session.commit()

    def fail(_):
        # A lost compare-and-set is retried by the watchdog's next read; here until it lands.
        for _attempt in range(50):
            with Session(owned_engine) as session:
                if service.fail_job(
                    session, session.get(IngestionJob, job_id),
                    error_code="ERR_MATERIAL_PROCESSING_TIMEOUT", stage="processing_timeout", reason="x",
                ):
                    return
        raise AssertionError("the failure never landed")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: (fail if index == 5 else late_progress)(index), range(40)))

    # Everything that ran after the failure landed changed nothing, including a final late write.
    late_progress(None)
    with Session(owned_engine) as other:
        service.update_processed(other, job_id, status="completed")
        other.commit()
    with Session(owned_engine) as session:
        assert session.get(IngestionJob, job_id).status == "failed"
