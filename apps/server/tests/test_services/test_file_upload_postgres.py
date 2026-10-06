"""Real PostgreSQL storage boundaries for multipart uploads and partial success."""

import os
from io import BytesIO

import pytest
from fastapi import BackgroundTasks, UploadFile
from sqlalchemy import event
from sqlmodel import Session, select

from api import files as files_api
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import File, Project, User
from models.file_version import FileVersion
from tests.test_services.test_snapshot_concurrency_postgres import pg_engine as pg_engine

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="isolated PostgreSQL URL not configured",
)
MAX_ORDER = 2_147_483_647


def _upload(filename, content):
    return UploadFile(filename=filename, file=BytesIO(content.encode("utf-8")))


def _seed(engine, suffix):
    with Session(engine) as session:
        user = User(username=f"upload-pg-{suffix}", email=f"upload-pg-{suffix}@example.test", hashed_password="unused")
        session.add(user)
        session.flush()
        project = Project(name="Upload boundaries", owner_id=user.id)
        session.add(project)
        session.commit()
        return user.id, project.id


@pytest.fixture(autouse=True)
def isolate_dependencies(monkeypatch):
    import database

    monkeypatch.setattr(database, "is_postgres", True)


def _assert_persisted(engine, project_id, result, expected):
    assert result.total == len(expected)
    assert [row.content for row in result.files] == expected
    with Session(engine) as reader:
        rows = reader.exec(select(File).where(File.project_id == project_id, File.file_type == "draft").order_by(File.order)).all()
        assert len(rows) == len(expected)
        assert sorted(row.content for row in rows) == sorted(expected)
        versions = reader.exec(select(FileVersion).where(FileVersion.file_id.in_([row.id for row in rows]))).all()
        assert len(versions) == len(expected)
        assert all(version.is_base_version and version.version_number == 1 for version in versions)
        assert sorted(version.content for version in versions) == sorted(expected)


@pytest.mark.parametrize("field", ["body", "filename"])
@pytest.mark.parametrize("invalid_first", [False, True])
async def test_draft_nul_is_per_input_error_and_keeps_valid_neighbour(pg_engine, field, invalid_first):
    user_id, project_id = _seed(pg_engine, f"nul-{field}-{invalid_first}")
    invalid = _upload("bad\x00.txt" if field == "filename" else "bad.txt", "bad\x00body" if field == "body" else "bad body")
    valid = _upload("valid.txt", "valid body")
    tasks = BackgroundTasks()
    with Session(pg_engine) as session:
        result = files_api.upload_drafts(
            project_id, [invalid, valid] if invalid_first else [valid, invalid],
            parent_id=None, current_user=session.get(User, user_id), session=session, background_tasks=tasks,
        )
    assert len(result.errors) == 1 and str(ErrorCode.VALIDATION_ERROR) in result.errors[0]
    _assert_persisted(pg_engine, project_id, result, ["valid body"])
    assert [task.kwargs["entity_id"] for task in tasks.tasks] == [result.files[0].id]


@pytest.mark.parametrize("field", ["body", "filename"])
async def test_material_nul_is_validation_error_before_folder_or_baseline(pg_engine, field):
    user_id, project_id = _seed(pg_engine, f"material-nul-{field}")
    upload = _upload("bad\x00.txt" if field == "filename" else "bad.txt", "bad\x00body" if field == "body" else "bad body")
    with Session(pg_engine) as session:
        with pytest.raises(APIException) as error:
            files_api.upload_material(project_id, upload, current_user=session.get(User, user_id), session=session)
        assert error.value.status_code == 400
        assert error.value.error_code == ErrorCode.VALIDATION_ERROR
        assert not session.new
        assert session.exec(select(File).where(File.project_id == project_id)).all() == []
    with Session(pg_engine) as reader:
        assert reader.exec(select(File).where(File.project_id == project_id)).all() == []


@pytest.mark.parametrize("overflow_source", ["filename", "second-chapter"])
async def test_draft_oversized_inferred_order_rejects_entire_input_only(pg_engine, overflow_source):
    user_id, project_id = _seed(pg_engine, f"order-{overflow_source}")
    invalid = _upload("Chapter 2147483648.txt", "invalid body") if overflow_source == "filename" else _upload(
        "novel.txt", "Chapter 1 Valid prefix\ninvalid first chapter\n\nChapter 2147483648 Invalid suffix\ninvalid second chapter",
    )
    tasks = BackgroundTasks()
    with Session(pg_engine) as session:
        result = files_api.upload_drafts(
            project_id, [_upload("before.txt", "before"), invalid, _upload("after.txt", "after")],
            parent_id=None, current_user=session.get(User, user_id), session=session, background_tasks=tasks,
        )
    assert len(result.errors) == 1 and str(ErrorCode.VALIDATION_ERROR) in result.errors[0]
    assert [row.order for row in result.files] == [0, 1], "rejected input must not consume append slots"
    _assert_persisted(pg_engine, project_id, result, ["before", "after"])
    assert len(tasks.tasks) == 2


async def test_draft_appended_order_overflow_is_error_but_explicit_boundary_still_valid(pg_engine):
    user_id, project_id = _seed(pg_engine, "append-boundary")
    with Session(pg_engine) as session:
        folder = File(id=f"{project_id}-draft-folder", project_id=project_id, title="Drafts", file_type="folder")
        session.add(folder)
        session.flush()
        existing = File(project_id=project_id, parent_id=folder.id, title="Existing", file_type="draft", content="existing", order=MAX_ORDER)
        session.add(existing)
        session.commit()
        result = files_api.upload_drafts(
            project_id, [_upload("unnumbered.txt", "rejected"), _upload("Chapter 2147483647.txt", "boundary")],
            parent_id=None, current_user=session.get(User, user_id), session=session, background_tasks=BackgroundTasks(),
        )
    assert result.total == 1 and len(result.errors) == 1
    assert result.files[0].order == MAX_ORDER and result.files[0].content == "boundary"
    with Session(pg_engine) as reader:
        rows = reader.exec(select(File).where(File.project_id == project_id, File.file_type == "draft")).all()
        assert sorted(row.content for row in rows) == ["boundary", "existing"]


async def test_draft_append_order_ignores_foreign_project_dirty_parent_edge(pg_engine):
    user_id, project_id = _seed(pg_engine, "foreign-order")
    _, other_project_id = _seed(pg_engine, "foreign-owner")
    with Session(pg_engine) as session:
        folder = File(id=f"{project_id}-draft-folder", project_id=project_id, title="Drafts", file_type="folder")
        session.add(folder)
        session.flush()
        session.add(File(project_id=other_project_id, parent_id=folder.id, title="Foreign", file_type="draft", order=MAX_ORDER))
        session.commit()
        result = files_api.upload_drafts(
            project_id, [_upload("ordinary.txt", "local")], parent_id=None,
            current_user=session.get(User, user_id), session=session, background_tasks=BackgroundTasks(),
        )
    assert result.total == 1 and result.errors == [] and result.files[0].order == 0
    _assert_persisted(pg_engine, project_id, result, ["local"])


async def test_draft_append_reads_one_aggregate_row_instead_of_all_sibling_orders(pg_engine):
    user_id, project_id = _seed(pg_engine, "aggregate-order")
    with Session(pg_engine) as session:
        folder = File(id=f"{project_id}-draft-folder", project_id=project_id, title="Drafts", file_type="folder")
        session.add(folder)
        session.flush()
        session.add_all([
            File(project_id=project_id, parent_id=folder.id, title=f"Sibling {index}", file_type="draft", order=index)
            for index in range(500)
        ])
        session.add(File(project_id=project_id, parent_id=folder.id, title="Deleted", file_type="draft", order=MAX_ORDER, is_deleted=True))
        session.commit()
        observed = []

        def record_order_query(_connection, cursor, statement, _parameters, _context, _many):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith('select file."order"') or normalized.startswith('select max(file."order")'):
                observed.append((normalized, cursor.rowcount))

        event.listen(pg_engine, "after_cursor_execute", record_order_query)
        try:
            result = files_api.upload_drafts(
                project_id, [_upload("ordinary.txt", "local")], parent_id=None,
                current_user=session.get(User, user_id), session=session, background_tasks=BackgroundTasks(),
            )
        finally:
            event.remove(pg_engine, "after_cursor_execute", record_order_query)
    assert result.total == 1 and result.errors == [] and result.files[0].order == 500
    assert len(observed) == 1
    assert observed[0][1] == 1, f"sibling-order query returned {observed[0][1]} rows"
    assert "max(" in observed[0][0]


@pytest.mark.parametrize(("kind", "count"), [("material", 1), ("material", 3), ("draft", 1), ("draft", 50)])
async def test_upload_response_and_index_use_no_postcommit_expired_rows(pg_engine, monkeypatch, kind, count):
    from services.infra.dashboard_cache import dashboard_cache

    cache_calls = []
    monkeypatch.setattr(dashboard_cache, "bump_project_version", lambda **kwargs: cache_calls.append(kwargs))
    user_id, project_id = _seed(pg_engine, f"postcommit-{kind}-{count}")
    tasks = BackgroundTasks()
    with Session(pg_engine) as session:  # Production default: expire_on_commit=True.
        assert session.expire_on_commit
        committed = []
        reads = []

        def after_commit(_session):
            # SQLAlchemy also emits this event for the canonical insertion
            # SAVEPOINT release; only the outer commit expires these objects.
            if not _session.in_nested_transaction():
                committed.append(True)

        def after_query(_connection, _cursor, statement, _parameters, _context, _many):
            normalized = " ".join(statement.lower().split())
            if committed and normalized.startswith("select") and (" from file " in normalized or " from \"user\" " in normalized):
                reads.append(normalized)

        event.listen(session, "after_commit", after_commit)
        event.listen(pg_engine, "after_cursor_execute", after_query)
        try:
            user = session.get(User, user_id)
            if kind == "draft":
                content = "\n\n".join(f"Chapter {index + 1} Title\nBody {index + 1}" for index in range(count))
                response = files_api.upload_drafts(
                    project_id, [_upload("novel.txt", content)], parent_id=None,
                    current_user=user, session=session, background_tasks=tasks,
                )
                assert response.total == count and response.errors == []
                returned = response.files
                assert len(tasks.tasks) == count
                for row, task in zip(returned, tasks.tasks, strict=True):
                    assert task.kwargs == {
                        "project_id": project_id, "user_id": user_id, "entity_type": "draft",
                        "entity_id": row.id, "content": row.content, "title": row.title,
                        "extra_metadata": {"parent_id": row.parent_id},
                    }
                assert cache_calls == [{"user_id": user_id, "project_id": project_id}]
            else:
                content = "Short material" if count == 1 else "x" * (files_api.MATERIAL_AUTO_SPLIT_MAX_CHARS * 2 + 1)
                response = files_api.upload_material(
                    project_id, _upload("material.txt", content), current_user=user, session=session,
                )
                returned = [files_api.FileResponse.model_validate(response)]
                assert tasks.tasks == [] and cache_calls == []
            assert len(committed) == 1
            assert reads == [], f"postcommit expired File/User SELECTs: {len(reads)}"
        finally:
            event.remove(pg_engine, "after_cursor_execute", after_query)
            event.remove(session, "after_commit", after_commit)
    with Session(pg_engine) as reader:
        rows = reader.exec(select(File).where(File.project_id == project_id, File.file_type != "folder")).all()
        assert len(rows) == count
        by_id = {row.id: row for row in rows}
        for row in returned:
            assert row.model_dump() == files_api.FileResponse.model_validate(by_id[row.id]).model_dump()
        versions = reader.exec(select(FileVersion).where(FileVersion.file_id.in_(list(by_id)))).all()
        assert len(versions) == count and all(version.is_base_version for version in versions)


@pytest.mark.parametrize("kind", ["material", "draft"])
async def test_failed_upload_commit_emits_no_index_or_cache_and_rolls_back_folder(pg_engine, monkeypatch, kind):
    from services.infra.dashboard_cache import dashboard_cache

    cache_calls = []
    monkeypatch.setattr(dashboard_cache, "bump_project_version", lambda **kwargs: cache_calls.append(kwargs))
    user_id, project_id = _seed(pg_engine, f"failed-commit-{kind}")
    tasks = BackgroundTasks()
    with Session(pg_engine) as session:
        user = session.get(User, user_id)

        def fail_commit():
            raise RuntimeError("commit fault")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit fault"):
            if kind == "material":
                files_api.upload_material(project_id, _upload("material.txt", "Body"), current_user=user, session=session)
            else:
                files_api.upload_drafts(
                    project_id, [_upload("draft.txt", "Body")], parent_id=None,
                    current_user=user, session=session, background_tasks=tasks,
                )
    assert tasks.tasks == [] and cache_calls == []
    with Session(pg_engine) as reader:
        assert reader.exec(select(File).where(File.project_id == project_id)).all() == []


async def test_all_invalid_draft_upload_returns_empty_response_without_postcommit_reads(pg_engine, monkeypatch):
    from services.infra.dashboard_cache import dashboard_cache

    cache_calls = []
    monkeypatch.setattr(dashboard_cache, "bump_project_version", lambda **kwargs: cache_calls.append(kwargs))
    user_id, project_id = _seed(pg_engine, "empty-result")
    tasks = BackgroundTasks()
    with Session(pg_engine) as session:
        result = files_api.upload_drafts(
            project_id, [_upload("invalid.exe", "Rejected")], parent_id=None,
            current_user=session.get(User, user_id), session=session, background_tasks=tasks,
        )
    assert result.total == 0 and result.files == [] and len(result.errors) == 1
    assert tasks.tasks == [] and cache_calls == []
