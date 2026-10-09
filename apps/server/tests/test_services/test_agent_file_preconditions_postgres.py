from __future__ import annotations

import asyncio
import inspect
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlmodel import Session, select

from agent.tools.file_ops import crud as file_crud_module
from agent.tools.file_ops import edit as file_edit_module
from agent.tools.file_ops.crud import FileCRUD
from agent.tools.file_ops.edit import FileEditor
from api import agent_api
from api import files as files_api
from core.error_handler import APIException
from models.agent_api_key import AgentApiKey
from models.entities import Project, Snapshot, User
from models.file_model import File
from models.file_version import FileVersion
from services.features.file_version_service import get_file_version_service

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
    reason="isolated PostgreSQL URL not configured",
)

TABLES = [
    User.__table__,
    Project.__table__,
    File.__table__,
    Snapshot.__table__,
    FileVersion.__table__,
    AgentApiKey.__table__,
]
STAMP = datetime(2026, 10, 6, 8, 0, 0, 123456, tzinfo=UTC)


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


def _seed_file(session: Session, suffix: str) -> tuple[str, str, str]:
    user = User(
        username=f"agent-precondition-{suffix}",
        email=f"agent-precondition-{suffix}@example.test",
        hashed_password="hashed",
        email_verified=True,
    )
    session.add(user)
    session.commit()
    project = Project(name=f"Agent precondition {suffix}", owner_id=user.id)
    session.add(project)
    session.commit()
    file = File(
        project_id=project.id,
        title="Draft",
        file_type="draft",
        content="Original",
        updated_at=STAMP,
    )
    key = AgentApiKey(
        user_id=user.id,
        key_prefix=f"{suffix[:8]:0<8}",
        key_hash=f"hash-{suffix}",
        name="Concurrent writer",
        scopes=["read", "write"],
    )
    session.add_all([file, key])
    session.commit()
    return user.id, file.id, key.id


def _invoke_agent_update(
    session: Session,
    user_id: str,
    key_id: str,
    file_id: str,
    content: str,
):
    key = session.get(AgentApiKey, key_id)
    assert key is not None
    call = agent_api.update_file(
        file_id,
        agent_api.FileUpdate(content=content, base_updated_at=STAMP),
        BackgroundTasks(),
        _rate_limit=0,
        context=(session, user_id, key),
    )
    if inspect.isawaitable(call):
        return asyncio.run(call)
    return call


@pytest.fixture(autouse=True)
def stable_dependencies(monkeypatch: pytest.MonkeyPatch):
    import database

    # A dedicated PostgreSQL engine must exercise production row-lock branches,
    # even when the test process's unrelated default engine is SQLite.
    monkeypatch.setattr(database, "is_postgres", True)
    version_service = get_file_version_service()
    monkeypatch.setattr(
        type(version_service),
        "check_user_version_quota",
        lambda *_args, **_kwargs: (True, 0, 10),
    )
    monkeypatch.setattr(agent_api, "utcnow", lambda: STAMP)
    monkeypatch.setattr(
        files_api.activation_event_service,
        "record_once",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_args, **_kwargs: None)


def test_concurrent_agent_updates_with_same_token_commit_one_version(
    pg_engine, monkeypatch: pytest.MonkeyPatch
):
    with Session(pg_engine) as setup:
        user_id, file_id, key_id = _seed_file(setup, "agent-race")

    both_preloaded = Barrier(2)
    indexed_contents: list[str] = []
    monkeypatch.setattr(
        agent_api,
        "_schedule_file_index_upsert",
        lambda _tasks, file, _user_id: indexed_contents.append(file.content),
    )

    def update(content: str):
        with Session(pg_engine) as session:
            cached_file = session.get(File, file_id)
            assert cached_file is not None
            both_preloaded.wait(timeout=5)
            try:
                _invoke_agent_update(session, user_id, key_id, file_id, content)
                return 200
            except APIException as exc:
                return exc.status_code
            except Exception as exc:  # RED should report unexpected database races clearly.
                return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, ["Agent A", "Agent B"]))

    assert sorted(outcomes, key=str) == [200, 409]
    with Session(pg_engine) as verify:
        file = verify.get(File, file_id)
        versions = verify.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()
        assert file is not None
        assert file.content in {"Agent A", "Agent B"}
        assert file.updated_at.replace(tzinfo=UTC) > STAMP
        assert len(versions) == 1
        assert versions[0].content == file.content
    assert indexed_contents == [file.content]


def test_agent_rejects_old_token_after_preloaded_web_update(
    pg_engine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(files_api, "utcnow", lambda: STAMP)
    with Session(pg_engine) as setup:
        user_id, file_id, key_id = _seed_file(setup, "web-agent")

    indexed_contents: list[str] = []
    monkeypatch.setattr(
        agent_api,
        "_schedule_file_index_upsert",
        lambda _tasks, file, _user_id: indexed_contents.append(file.content),
    )
    current_user = User(
        id=user_id,
        username="agent-precondition-web-agent",
        email="agent-precondition-web-agent@example.test",
        hashed_password="hashed",
    )

    with Session(pg_engine) as agent_session, Session(pg_engine) as web_session:
        cached_agent_file = agent_session.get(File, file_id)
        cached_web_file = web_session.get(File, file_id)
        assert cached_agent_file is not None
        assert cached_web_file is not None
        files_api.update_file(
            file_id,
            files_api.FileUpdate(content="Web winner", base_updated_at=STAMP),
            BackgroundTasks(),
            current_user=current_user,
            session=web_session,
        )
        with pytest.raises(APIException) as conflict:
            _invoke_agent_update(
                agent_session,
                user_id,
                key_id,
                file_id,
                "Stale agent overwrite",
            )

    assert conflict.value.status_code == 409
    with Session(pg_engine) as verify:
        file = verify.get(File, file_id)
        versions = verify.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()
        assert file is not None
        assert file.content == "Web winner"
        assert len(versions) == 1
        assert versions[0].content == "Web winner"
    assert indexed_contents == []


def test_preloaded_web_writer_rejects_token_invalidated_by_agent(
    pg_engine, monkeypatch: pytest.MonkeyPatch,
):
    with Session(pg_engine) as setup:
        user_id, file_id, key_id = _seed_file(setup, "web-cached")
    monkeypatch.setattr(agent_api, "_schedule_file_index_upsert", lambda *_args: None)
    monkeypatch.setattr(files_api, "utcnow", lambda: STAMP)
    with Session(pg_engine) as web_session, Session(pg_engine) as agent_session:
        cached = web_session.get(File, file_id)
        owner = web_session.get(User, user_id)
        assert cached is not None and owner is not None
        _invoke_agent_update(agent_session, user_id, key_id, file_id, "Agent winner")
        with pytest.raises(APIException) as conflict:
            files_api.update_file(
                file_id,
                files_api.FileUpdate(content="Stale web overwrite", base_updated_at=STAMP),
                BackgroundTasks(), owner, web_session,
            )
        assert conflict.value.status_code == 409
    with Session(pg_engine) as verify:
        file = verify.get(File, file_id)
        assert file.content == "Agent winner"
        versions = verify.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()
        assert len(versions) == 1


def test_preloaded_file_editor_appends_to_latest_web_content(
    pg_engine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(files_api, "utcnow", lambda: STAMP)
    monkeypatch.setattr(file_edit_module, "utcnow", lambda: STAMP)
    with Session(pg_engine) as setup:
        user_id, file_id, _key_id = _seed_file(setup, "web-editor")

    current_user = User(
        id=user_id,
        username="agent-precondition-web-editor",
        email="agent-precondition-web-editor@example.test",
        hashed_password="hashed",
    )
    with Session(pg_engine) as editor_session, Session(pg_engine) as web_session:
        cached_editor_file = editor_session.get(File, file_id)
        cached_web_file = web_session.get(File, file_id)
        assert cached_editor_file is not None
        assert cached_web_file is not None
        web_result = files_api.update_file(
            file_id,
            files_api.FileUpdate(content="Web winner", base_updated_at=STAMP),
            BackgroundTasks(),
            current_user=current_user,
            session=web_session,
        )
        web_token = web_result.updated_at.replace(tzinfo=UTC)

        FileEditor(editor_session, user_id=user_id).edit_file(
            file_id,
            [{"op": "append", "text": " + Agent append"}],
        )

    with Session(pg_engine) as verify:
        file = verify.get(File, file_id)
        versions = verify.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()
        assert file is not None
        assert file.content == "Web winner + Agent append"
        assert file.updated_at.replace(tzinfo=UTC) > web_token
        assert len(versions) == 2


def test_preloaded_file_crud_title_update_returns_latest_web_content(
    pg_engine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(files_api, "utcnow", lambda: STAMP)
    monkeypatch.setattr(file_crud_module, "utcnow", lambda: STAMP)
    with Session(pg_engine) as setup:
        user_id, file_id, _key_id = _seed_file(setup, "web-crud")

    current_user = User(
        id=user_id,
        username="agent-precondition-web-crud",
        email="agent-precondition-web-crud@example.test",
        hashed_password="hashed",
    )
    with Session(pg_engine) as crud_session, Session(pg_engine) as web_session:
        cached_crud_file = crud_session.get(File, file_id)
        cached_web_file = web_session.get(File, file_id)
        assert cached_crud_file is not None
        assert cached_web_file is not None
        web_result = files_api.update_file(
            file_id,
            files_api.FileUpdate(content="Web winner", base_updated_at=STAMP),
            BackgroundTasks(),
            current_user=current_user,
            session=web_session,
        )
        web_token = web_result.updated_at.replace(tzinfo=UTC)

        result = FileCRUD(crud_session, user_id=user_id).update_file(
            file_id,
            title="Renamed after web write",
        )

    assert result["content"] == "Web winner"
    with Session(pg_engine) as verify:
        file = verify.get(File, file_id)
        versions = verify.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()
        assert file is not None
        assert file.title == "Renamed after web write"
        assert file.content == "Web winner"
        assert file.updated_at.replace(tzinfo=UTC) > web_token
        assert len(versions) == 1


def test_preloaded_rollback_advances_past_latest_web_token(
    pg_engine, monkeypatch: pytest.MonkeyPatch
):
    from services.features import file_version_service as version_service_module

    monkeypatch.setattr(files_api, "utcnow", lambda: STAMP)
    monkeypatch.setattr(version_service_module, "utcnow", lambda: STAMP)
    service = get_file_version_service()
    with Session(pg_engine) as setup:
        user_id, file_id, _key_id = _seed_file(setup, "web-rollback")
        # 种子版本用 system 来源（等同创建文件时的基线）：用户来源的 v1 会让下面
        # 窗口内的网页保存直接合并改写 v1，测的就不再是「恢复到原稿」。
        service.create_version(
            setup,
            file_id,
            "Original",
            change_source="system",
            force_base=True,
            skip_quota=True,
        )

    current_user = User(
        id=user_id,
        username="agent-precondition-web-rollback",
        email="agent-precondition-web-rollback@example.test",
        hashed_password="hashed",
    )
    with Session(pg_engine) as rollback_session, Session(pg_engine) as web_session:
        cached_rollback_file = rollback_session.get(File, file_id)
        cached_web_file = web_session.get(File, file_id)
        assert cached_rollback_file is not None
        assert cached_web_file is not None
        web_result = files_api.update_file(
            file_id,
            files_api.FileUpdate(content="Web winner", base_updated_at=STAMP),
            BackgroundTasks(),
            current_user=current_user,
            session=web_session,
        )
        web_token = web_result.updated_at.replace(tzinfo=UTC)

        restored, new_version, exceeded = service.rollback_to_version(
            rollback_session,
            file_id,
            1,
            user_id=user_id,
        )

    assert exceeded is False
    assert new_version is not None
    assert restored.content == "Original"
    assert restored.updated_at.replace(tzinfo=UTC) > web_token
    with Session(pg_engine) as verify:
        file = verify.get(File, file_id)
        versions = verify.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()
        assert file is not None
        assert file.content == "Original"
        assert file.updated_at.replace(tzinfo=UTC) > web_token
        assert len(versions) == 3


@pytest.mark.asyncio
async def test_conditional_undo_waits_for_writer_then_rejects_its_old_token(pg_engine, monkeypatch):
    """Actual HTTP body + retained ORM identity + observed PostgreSQL lock wait."""
    import time
    from threading import Event

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from api import versions as versions_api
    from config.datetime_utils import advance_timestamp
    from database import get_session
    from main import app

    service = get_file_version_service()
    with Session(pg_engine) as setup:
        user_id, file_id, _ = _seed_file(setup, "undo-writer-first")
        service.create_initial_version(setup, setup.get(File, file_id))
        setup.commit()
        user = setup.get(User, user_id)
        setup.expunge(user)

    locked, release = Event(), Event()
    reader_pid = []
    indexed, cache_bumps = [], []
    monkeypatch.setattr("services.llama_index.schedule_index_upsert", lambda **kw: indexed.append(kw))
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **kw: cache_bumps.append(kw))

    def request_session():
        with Session(pg_engine) as session:
            # Keep this strong reference through yield, so a missing refresh
            # after FOR NO KEY UPDATE really would use the stale ORM object.
            cached = session.get(File, file_id)
            assert cached.content == "Original"
            reader_pid.append(session.exec(text("select pg_backend_pid()")).one()[0])
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, request_session)
    monkeypatch.setitem(app.dependency_overrides, versions_api.get_current_active_user, lambda: user)

    def write():
        with Session(pg_engine) as session:
            file = session.exec(select(File).where(File.id == file_id).with_for_update(key_share=True)).one()
            file.content = "Writer committed after undo card"
            file.updated_at = advance_timestamp(file.updated_at, now=STAMP)
            session.add(file)
            session.flush()
            locked.set()
            assert release.wait(timeout=8)
            service.create_version(session, file_id, file.content, change_source="system", commit=False)
            session.commit()

    async def observed_lock_wait():
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if reader_pid:
                with pg_engine.connect() as connection:
                    if connection.execute(text("select wait_event_type from pg_stat_activity where pid=:pid"), {"pid": reader_pid[0]}).scalar() == "Lock":
                        return
            await asyncio.sleep(0.01)
        pytest.fail("Conditional rollback never waited on the writer's real PG File lock")

    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(write)
        request = None
        try:
            assert await asyncio.to_thread(locked.wait, 5)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                request = asyncio.create_task(client.post(f"/api/v1/files/{file_id}/versions/1/rollback", json={"expected_updated_at": STAMP.isoformat()}))
                try:
                    await observed_lock_wait()
                finally:
                    release.set()
                response = await request
        finally:
            release.set()
            await asyncio.to_thread(writer.result, 8)
            if request is not None and not request.done():
                await request

    assert response.status_code == 409
    assert response.json()["error_code"] == "ERR_RESOURCE_CONFLICT"
    assert indexed == cache_bumps == []
    with Session(pg_engine) as verify:
        file = verify.get(File, file_id)
        assert file.content == "Writer committed after undo card"
        assert file.updated_at.replace(tzinfo=UTC) > STAMP
        assert len(verify.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()) == 2


def test_undo_provenance_query_failure_does_not_poison_postgres_edit_transaction(pg_engine, monkeypatch):
    """Failed optional history SELECTs must roll back only their savepoints.

    The first read is the pre-AI-write backup comparison, the second the undo
    provenance check; both fail here and the content edit still commits.
    """
    from sqlalchemy import text

    from services.features.file_version_service import FileVersionService

    service = get_file_version_service()
    with Session(pg_engine) as setup:
        user_id, file_id, _ = _seed_file(setup, "undo-read-error")
        service.create_initial_version(setup, setup.get(File, file_id))
        setup.commit()
    original = FileVersionService.get_latest_version
    calls = []

    def fail_history_reads(self, session, target_id):
        calls.append(target_id)
        if len(calls) <= 2:
            session.exec(text("select 1 / 0"))
        return original(self, session, target_id)

    monkeypatch.setattr(FileVersionService, "get_latest_version", fail_history_reads)
    monkeypatch.setattr(files_api.activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    with Session(pg_engine) as session:
        result = FileEditor(session, user_id).edit_file(file_id, [{"op": "append", "text": " + edit"}])
    assert result.get("undo") is None
    with Session(pg_engine) as verify:
        file = verify.get(File, file_id)
        assert file.content == "Original + edit"
        assert len(verify.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()) == 2


@pytest.mark.asyncio
async def test_undo_token_belongs_to_edit_even_when_web_write_commits_before_refresh(pg_engine, monkeypatch):
    """A post-commit refresh must not give an old card a later writer's token."""
    from httpx import ASGITransport, AsyncClient

    from api import versions as versions_api
    from database import get_session
    from main import app

    service = get_file_version_service()
    with Session(pg_engine) as setup:
        user_id, file_id, _ = _seed_file(setup, "undo-postcommit")
        service.create_initial_version(setup, setup.get(File, file_id))
        setup.commit()
        owner = setup.get(User, user_id)
        setup.expunge(owner)

    monkeypatch.setattr(file_edit_module, "utcnow", lambda: STAMP)
    monkeypatch.setattr(files_api, "utcnow", lambda: STAMP)
    monkeypatch.setattr(files_api.activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    cache_bumps, indexed = [], []
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **kw: cache_bumps.append(kw))
    monkeypatch.setattr("services.llama_index.schedule_index_upsert", lambda **kw: indexed.append(kw))

    own_token = STAMP + timedelta(microseconds=1)
    later_token = STAMP + timedelta(microseconds=2)
    interposed = []
    with Session(pg_engine) as editor_session:
        refresh = editor_session.refresh

        def refresh_after_later_write(instance, *args, **kwargs):
            if isinstance(instance, File) and not interposed:
                # A separate transaction can observe the editor's commit and
                # acquire the real File lock before this refreshed SELECT.
                with Session(pg_engine) as web_session:
                    committed = web_session.get(File, file_id)
                    assert committed.content == "Original + AI edit"
                    assert committed.updated_at.replace(tzinfo=UTC) == own_token
                    updated = files_api.update_file(
                        file_id,
                        files_api.FileUpdate(content="Later Web work", base_updated_at=own_token),
                        BackgroundTasks(),
                        current_user=owner,
                        session=web_session,
                    )
                    interposed.append(updated.updated_at.replace(tzinfo=UTC))
            return refresh(instance, *args, **kwargs)

        monkeypatch.setattr(editor_session, "refresh", refresh_after_later_write)
        result = FileEditor(editor_session, user_id).edit_file(file_id, [{"op": "append", "text": " + AI edit"}])

    assert interposed == [later_token]
    before_rollback_cache_bumps = list(cache_bumps)
    before_rollback_indexed = list(indexed)
    # Invoke actual HTTP rollback/body parsing before asserting the token, so RED proves
    # the unsafe overwrite rather than merely a wrong response field.
    def request_session():
        with Session(pg_engine) as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, request_session)
    monkeypatch.setitem(app.dependency_overrides, versions_api.get_current_active_user, lambda: owner)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/files/{file_id}/versions/{result['undo']['before_version_number']}/rollback",
            json={"expected_updated_at": result["undo"]["expected_after_updated_at"]},
        )

    with Session(pg_engine) as verify:
        file = verify.get(File, file_id)
        versions = verify.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()
        observed = (result["undo"]["expected_after_updated_at"], response.status_code, file.content, len(versions))
        assert observed == (own_token.isoformat(), 409, "Later Web work", 3)
        assert file.updated_at.replace(tzinfo=UTC) == later_token
    assert response.json()["error_code"] == "ERR_RESOURCE_CONFLICT"
    assert cache_bumps == before_rollback_cache_bumps
    assert indexed == before_rollback_indexed


@pytest.mark.parametrize("writer", ["update_file", "edit_file"])
def test_ai_write_backs_up_unversioned_body_inside_the_locked_postgres_transaction(pg_engine, monkeypatch, writer):
    """Unversioned live text is snapshotted (system source) before the AI overwrite commits."""
    service = get_file_version_service()
    with Session(pg_engine) as setup:
        user_id, file_id, _ = _seed_file(setup, f"backup-{writer}")
        service.create_initial_version(setup, setup.get(File, file_id))
        setup.commit()
        # 作者的小改动保存不生成版本：正文变了，历史头仍是 "Original"。
        live = setup.get(File, file_id)
        live.content = "Manual edit"
        setup.add(live)
        setup.commit()
    monkeypatch.setattr(files_api.activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_a, **_kw: None)

    with Session(pg_engine) as session:
        if writer == "update_file":
            FileCRUD(session, user_id).update_file(file_id, content='他说："改好了。"', normalize_quotes=True)
            expected = "他说：“改好了。”"
        else:
            FileEditor(session, user_id).edit_file(
                file_id, [{"op": "append", "text": '\n她说："嗯。"'}], normalize_quotes=True
            )
            expected = "Manual edit\n她说：“嗯。”"

    with Session(pg_engine) as verify:
        assert verify.get(File, file_id).content == expected
        versions = verify.exec(
            select(FileVersion).where(FileVersion.file_id == file_id).order_by(FileVersion.version_number)
        ).all()
        assert [v.change_source for v in versions] == ["system", "system", "ai"]
        assert versions[1].change_summary == "Before AI edit"
        assert service.get_content_at_version(verify, file_id, 2) == "Manual edit"
        assert service.get_content_at_version(verify, file_id, 3) == expected
