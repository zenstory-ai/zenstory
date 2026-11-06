"""The worker re-checks a trial's limits before any model call and refuses what it cannot trust."""

import pytest

from core.error_codes import ErrorCode
from flows.pipelines.novel_ingestion_v3_flow import _enforce_trial_scope
from models import User
from models.material_models import Chapter, IngestionJob, Novel
from services.material.ingestion_jobs_service import IngestionJobsService
from services.material.job_errors import MaterialPipelineError


def _job(session, *, chapters, billing):
    user = User(username="worker", email="worker@example.com", hashed_password="x")
    session.add(user)
    session.flush()
    novel = Novel(user_id=user.id, title="t", source_meta="{}")
    session.add(novel)
    session.flush()
    ids = []
    for number, length in enumerate(chapters, start=1):
        chapter = Chapter(novel_id=novel.id, chapter_number=number, title=f"c{number}", original_content="字" * length)
        session.add(chapter)
        session.flush()
        ids.append(chapter.id)
    job = IngestionJob(novel_id=novel.id, source_path="x", status="processing", total_chapters=len(chapters), processed_chapters=0)
    IngestionJobsService.set_billing(job, **billing)
    session.add(job)
    session.commit()
    return job, ids


@pytest.fixture(autouse=True)
def worker_session(monkeypatch, db_session):
    from contextlib import contextmanager

    import flows.pipelines.novel_ingestion_v3_flow as flow_mod

    @contextmanager
    def session_ctx():
        yield db_session

    monkeypatch.setattr(flow_mod, "get_prefect_db_session", session_ctx)


TRIAL = {"quota_mode": "trial", "chapter_limit": 3, "character_limit": 15_000}


def test_a_trial_within_its_limits_passes(db_session):
    job, ids = _job(db_session, chapters=[5_000, 5_000, 5_000], billing=TRIAL)

    _enforce_trial_scope(job.id, ids)


@pytest.mark.parametrize(
    ("chapters", "billing"),
    [
        pytest.param([100] * 4, TRIAL, id="too_many_chapters"),
        pytest.param([15_001], TRIAL, id="too_many_characters"),
        pytest.param([100], {"quota_mode": "trial", "character_limit": 15_000}, id="missing_chapter_limit"),
        pytest.param([100], {"quota_mode": "trial", "chapter_limit": 3}, id="missing_character_limit"),
        pytest.param([100], {**TRIAL, "chapter_limit": 0}, id="zero_chapter_limit"),
        pytest.param([100], {**TRIAL, "chapter_limit": -1}, id="negative_chapter_limit"),
        pytest.param([100], {**TRIAL, "chapter_limit": 20}, id="oversized_chapter_limit"),
        pytest.param([100], {**TRIAL, "character_limit": 300_000}, id="oversized_character_limit"),
        pytest.param([100], {**TRIAL, "character_limit": True}, id="boolean_character_limit"),
    ],
)
def test_a_trial_that_cannot_be_trusted_stops_before_any_model_call(db_session, chapters, billing):
    job, ids = _job(db_session, chapters=chapters, billing=billing)

    with pytest.raises(MaterialPipelineError) as stopped:
        _enforce_trial_scope(job.id, ids)

    assert stopped.value.error_code == ErrorCode.MATERIAL_TRIAL_LIMIT_INVALID


def test_a_paid_job_is_not_checked(db_session):
    job, ids = _job(db_session, chapters=[60_000] * 5, billing={"quota_charged": True, "chapter_limit": 3})

    _enforce_trial_scope(job.id, ids)
