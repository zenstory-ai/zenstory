"""Real Prefect engine: the job identity reaches every model-call site, and the 5-character cap follows it."""

import threading
from contextlib import contextmanager

import pytest
from prefect import flow, task
from prefect.task_runners import ConcurrentTaskRunner
from prefect.testing.utilities import prefect_test_harness

from flows.pipelines.subflows import character_entity_build_flow as character_module
from models import User
from models.material_models import Chapter, CharacterMention, IngestionJob, Novel
from services.material.ingestion_jobs_service import (
    IngestionJobsService,
    _executing_job_id,
    material_job_scope,
)


@pytest.fixture(scope="module")
def prefect_engine():
    with prefect_test_harness():
        yield


@pytest.fixture(autouse=True)
def flow_db_session(monkeypatch, db_session):
    """The character flow reads through its own session helper."""
    import flows.database_session as flow_sessions

    @contextmanager
    def session_ctx():
        yield db_session

    monkeypatch.setattr(flow_sessions, "get_db_session", session_ctx)


@task
def _leaf_task():
    return _executing_job_id.get(), threading.get_ident()


@flow(task_runner=ConcurrentTaskRunner(max_workers=2))
def _inner_flow():
    futures = [_leaf_task.submit() for _ in range(4)]
    return [future.result() for future in futures]


@task
def _task_running_a_subflow():
    return _inner_flow()


@flow(task_runner=ConcurrentTaskRunner(max_workers=2))
def _outer_flow(job_id):
    with material_job_scope(job_id):
        direct = _leaf_task.submit().result()
        nested = _task_running_a_subflow.submit().result()
        return direct, nested


def test_job_identity_reaches_tasks_threads_and_subflows_inside_tasks(prefect_engine):
    direct, nested = _outer_flow(4242)

    assert direct[0] == 4242
    assert {job_id for job_id, _thread in nested} == {4242}
    assert {thread for _job_id, thread in nested} != {threading.get_ident()}
    assert _executing_job_id.get() is None


def _book_with_characters(session, count: int):
    user = User(username="scope_user", email="scope_user@example.com", hashed_password="x")
    session.add(user)
    session.flush()
    novel = Novel(user_id=user.id, title="t", source_meta="{}")
    session.add(novel)
    session.flush()
    chapter = Chapter(novel_id=novel.id, chapter_number=1, title="c", original_content="字")
    session.add(chapter)
    session.flush()
    for index in range(count):
        for _ in range(count - index):  # Earlier names appear more: the existing weight order.
            session.add(CharacterMention(
                novel_id=novel.id, chapter_id=chapter.id, character_name=f"角色{index}", importance="major",
            ))
    trial = IngestionJob(novel_id=novel.id, source_path="x", status="failed", total_chapters=1, processed_chapters=0)
    IngestionJobsService.set_billing(trial, quota_mode="trial", chapter_limit=3, character_limit=15_000)
    paid = IngestionJob(novel_id=novel.id, source_path="x", status="processing", total_chapters=1, processed_chapters=0)
    IngestionJobsService.set_billing(paid, quota_charged=True, chapter_limit=3)
    session.add_all([trial, paid])
    session.commit()
    return novel.id, trial.id, paid.id


@task
def _record_character(novel_id, character_name):
    return {"status": "created", "character_name": character_name, "job": _executing_job_id.get()}


def test_character_cap_follows_the_executing_job_not_the_latest_one(prefect_engine, db_session, monkeypatch):
    novel_id, trial_job, paid_job = _book_with_characters(db_session, 8)
    monkeypatch.setattr(character_module, "build_character_entity_task", _record_character)

    @flow
    def run(job_id):
        with material_job_scope(job_id):
            return character_module.character_entity_build_flow(novel_id=novel_id)

    assert run(trial_job)["selected_characters"] == 5
    assert run(paid_job)["selected_characters"] == 8


def test_character_build_without_a_job_on_a_trial_book_is_refused(prefect_engine, db_session, monkeypatch):
    from services.material.job_errors import MaterialPipelineError

    novel_id, _trial_job, _paid_job = _book_with_characters(db_session, 2)
    monkeypatch.setattr(character_module, "build_character_entity_task", _record_character)

    with pytest.raises(MaterialPipelineError):
        character_module.character_entity_build_flow(novel_id=novel_id)
