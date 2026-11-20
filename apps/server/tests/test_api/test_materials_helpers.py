from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from sqlmodel import Session

import api.materials.helpers as materials_helpers
from api.materials.helpers import _get_novel_or_404
from core.error_handler import APIException
from models.material_models import IngestionJob, Novel


def _create_novel(db_session: Session, *, user_id: str, deleted: bool = False) -> Novel:
    novel = Novel(user_id=user_id, title="Materials Novel", author="Author")
    if deleted:
        from config.datetime_utils import utcnow

        novel.deleted_at = utcnow()
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)
    return novel


def test_get_novel_or_404_returns_owned_novel(db_session: Session):
    novel = _create_novel(db_session, user_id="user-1")

    result = _get_novel_or_404(db_session, novel.id, "user-1")

    assert result.id == novel.id


@pytest.mark.parametrize(
    "owner_id, request_user_id, deleted",
    [
        ("user-1", "user-2", False),
        ("user-1", "user-1", True),
    ],
)
def test_get_novel_or_404_rejects_unauthorized_or_deleted(
    db_session: Session,
    owner_id: str,
    request_user_id: str,
    deleted: bool,
):
    novel = _create_novel(db_session, user_id=owner_id, deleted=deleted)

    with pytest.raises(APIException):
        _get_novel_or_404(db_session, novel.id, request_user_id)


@pytest.mark.asyncio
async def test_start_flow_deployment_dispatches_exact_job_without_waiting(
    db_session: Session,
    monkeypatch,
):
    novel = _create_novel(db_session, user_id="user-1")
    target_job = IngestionJob(novel_id=novel.id, source_path="/tmp/target.txt")
    newer_job = IngestionJob(novel_id=novel.id, source_path="/tmp/newer.txt")
    db_session.add(target_job)
    db_session.add(newer_job)
    db_session.commit()
    db_session.refresh(target_job)
    db_session.refresh(newer_job)

    calls: list[dict] = []

    async def _run_deployment(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id="accepted-run-id")

    def session_factory():
        return Session(db_session.get_bind())

    monkeypatch.setattr(materials_helpers, "create_session", session_factory)
    monkeypatch.setitem(
        sys.modules,
        "prefect.deployments",
        SimpleNamespace(run_deployment=_run_deployment),
    )

    result = await materials_helpers._start_flow_deployment(
        file_path="/tmp/target.txt",
        novel_title=novel.title,
        author=novel.author,
        user_id="user-1",
        novel_id=novel.id,
        job_id=target_job.id,
    )

    assert str(result) == "accepted-run-id"
    assert calls == [
        {
            "name": "novel_ingestion_v3/novel_ingestion_v3",
            "parameters": {
                "file_path": "/tmp/target.txt",
                "user_id": "user-1",
                "novel_title": novel.title,
                "author": novel.author,
                "resume_from_checkpoint": True,
                "novel_id": novel.id,
                "job_id": target_job.id,
            },
            "timeout": 0,
            "as_subflow": False,
        }
    ]

    db_session.expire_all()
    assert db_session.get(IngestionJob, target_job.id).correlation_id == "accepted-run-id"
    assert db_session.get(IngestionJob, newer_job.id).correlation_id is None


@pytest.mark.asyncio
async def test_start_flow_deployment_rejects_job_novel_mismatch_before_sdk(
    db_session: Session,
    monkeypatch,
):
    first_novel = _create_novel(db_session, user_id="user-1")
    other_novel = _create_novel(db_session, user_id="user-1")
    mismatched_job = IngestionJob(novel_id=other_novel.id, source_path="/tmp/other.txt")
    db_session.add(mismatched_job)
    db_session.commit()
    db_session.refresh(mismatched_job)

    dispatch_calls: list[int] = []

    async def _run_deployment(**kwargs):
        dispatch_calls.append(1)
        return SimpleNamespace(id="must-not-exist")

    monkeypatch.setattr(
        materials_helpers,
        "create_session",
        lambda: Session(db_session.get_bind()),
    )
    monkeypatch.setitem(
        sys.modules,
        "prefect.deployments",
        SimpleNamespace(run_deployment=_run_deployment),
    )

    result = await materials_helpers._start_flow_deployment(
        file_path="/tmp/other.txt",
        novel_title=first_novel.title,
        author=first_novel.author,
        user_id="user-1",
        novel_id=first_novel.id,
        job_id=mismatched_job.id,
    )

    assert result is None
    assert dispatch_calls == []


@pytest.mark.asyncio
async def test_start_flow_deployment_failure_marks_only_exact_job(
    db_session: Session,
    monkeypatch,
):
    novel = _create_novel(db_session, user_id="user-1")
    target_job = IngestionJob(novel_id=novel.id, source_path="/tmp/target.txt")
    other_job = IngestionJob(novel_id=novel.id, source_path="/tmp/other.txt")
    db_session.add(target_job)
    db_session.add(other_job)
    db_session.commit()
    db_session.refresh(target_job)
    db_session.refresh(other_job)

    async def _run_deployment(**kwargs):
        raise RuntimeError("prefect unavailable")

    monkeypatch.setattr(
        materials_helpers,
        "create_session",
        lambda: Session(db_session.get_bind()),
    )
    monkeypatch.setitem(
        sys.modules,
        "prefect.deployments",
        SimpleNamespace(run_deployment=_run_deployment),
    )

    result = await materials_helpers._start_flow_deployment(
        file_path="/tmp/target.txt",
        novel_title=novel.title,
        author=novel.author,
        user_id="user-1",
        novel_id=novel.id,
        job_id=target_job.id,
    )

    assert result is None
    db_session.expire_all()
    assert db_session.get(IngestionJob, target_job.id).status == "failed"
    assert db_session.get(IngestionJob, other_job.id).status == "pending"


@pytest.mark.asyncio
async def test_start_flow_deployment_returns_accepted_id_when_correlation_write_fails(
    monkeypatch,
):
    job = SimpleNamespace(
        id=9,
        novel_id=4,
        correlation_id=None,
        updated_at=None,
        update_stage_progress=lambda *args, **kwargs: None,
    )

    class _Session:
        def __init__(self, *, fail_commit: bool = False):
            self.fail_commit = fail_commit

        def get(self, model, object_id):
            assert model is IngestionJob
            return job if object_id == job.id else None

        def add(self, value):
            assert value is job

        def commit(self):
            if self.fail_commit:
                raise RuntimeError("correlation database unavailable")

        def close(self):
            pass

    sessions = iter([_Session(), _Session(fail_commit=True)])
    monkeypatch.setattr(materials_helpers, "create_session", lambda: next(sessions))

    async def _run_deployment(**kwargs):
        return SimpleNamespace(id="accepted-even-with-db-failure")

    monkeypatch.setitem(
        sys.modules,
        "prefect.deployments",
        SimpleNamespace(run_deployment=_run_deployment),
    )

    result = await materials_helpers._start_flow_deployment(
        file_path="/tmp/novel.txt",
        novel_title="Novel",
        author=None,
        user_id="user-1",
        novel_id=4,
        job_id=9,
    )

    assert str(result) == "accepted-even-with-db-failure"


def test_parse_json_list_returns_list_and_warns_on_malformed_or_non_list(caplog):
    from api.materials.entities import _parse_json_list

    assert _parse_json_list(None, field="character.aliases", entity_id=1) is None
    assert _parse_json_list("", field="character.aliases", entity_id=1) is None
    assert _parse_json_list('["a", "b"]', field="character.aliases", entity_id=1) == ["a", "b"]

    with caplog.at_level("WARNING", logger="api.materials.entities"):
        assert _parse_json_list("{not-json", field="character.aliases", entity_id=42) is None
        assert _parse_json_list('{"k": "v"}', field="plot.characters", entity_id=99) is None

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 2
    assert "Malformed JSON list" in warnings[0].getMessage()
    assert "Expected JSON list" in warnings[1].getMessage()
