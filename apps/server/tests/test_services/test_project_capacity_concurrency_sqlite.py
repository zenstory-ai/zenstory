"""File-backed SQLite capacity proof; writer serialization is database-wide."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel

from models import Project, User
from services.project_service import create_project_with_default_folders
from services.quota_service import quota_service
from tests.test_services.test_project_capacity_concurrency_postgres import (
    TABLES,
    ErrorCode,
    durable_http,
    http_sessions,
    post_http,
    seed_http,
)


@pytest.fixture
def sqlite_capacity_engine(tmp_path):
    path = tmp_path / 'owned-capacity.db'
    engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False, 'timeout': 45})

    def foreign_keys(connection, _record):
        connection.execute('PRAGMA foreign_keys=ON')

    event.listen(engine, 'connect', foreign_keys)
    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        yield engine
    finally:
        engine.dispose()
        event.remove(engine, 'connect', foreign_keys)
        path.unlink()
        assert not path.exists()


@pytest.mark.parametrize('different', [False, True])
def test_sqlite_actual_http_capacity_serializes_writers(sqlite_capacity_engine, different, record_property):
    engine = sqlite_capacity_engine
    ids = seed_http(engine, different=different)
    held, release, b_attempted = threading.Event(), threading.Event(), threading.Event()

    def before(connection, cursor, statement, parameters, context, executemany):
        sql = ' '.join(statement.lower().split())
        if connection.info.get('capacity_worker') == 'B' and sql.startswith('update user set id='):
            b_attempted.set()

    def after(connection, cursor, statement, parameters, context, executemany):
        sql = ' '.join(statement.lower().split())
        if (connection.info.get('capacity_worker') == 'A' and 'select count(' in sql
                and 'from project' in sql and not held.is_set()):
            held.set()
            assert release.wait(45), 'finite SQLite writer hold not released'

    event.listen(engine, 'before_cursor_execute', before)
    event.listen(engine, 'after_cursor_execute', after)
    try:
        with http_sessions(engine) as closed, ThreadPoolExecutor(max_workers=2) as pool:
            try:
                a = pool.submit(post_http, ids, 'web', 'A', different=different)
                assert held.wait(30)
                b = pool.submit(post_http, ids, 'web', 'B', different=different)
                # New explicit target proves the actual no-op UPDATE is attempted.
                # It does not assert per-owner nonblocking SQLite.
                assert b_attempted.wait(30), 'B never attempted the actual SQLite creation gate'
                assert not b.done(), 'A holds the database writer transaction'
            finally:
                release.set()
            outcomes = [a.result(timeout=45), b.result(timeout=45)]
        assert sorted(closed) == ['A', 'B']
        durable = durable_http(engine, ids)
        assert sorted(r['status'] for r in outcomes) == ([200, 200] if different else [200, 402])
        assert set(durable['active'].values()) == {3}
        if not different:
            rejected = next(r for r in outcomes if r['status'] == 402)
            assert rejected['body']['error_code'] == ErrorCode.QUOTA_PROJECTS_EXCEEDED
            assert 'Candidate B' not in durable['candidates']
        record_property('sqlite_capacity', str({'different_owner': different, 'outcomes': outcomes,
                                              'durable': durable, 'writer_update_observed': True}))
    finally:
        release.set()
        event.remove(engine, 'before_cursor_execute', before)
        event.remove(engine, 'after_cursor_execute', after)


def test_creation_gate_does_not_overwrite_attached_user_or_timestamp(sqlite_capacity_engine):
    engine = sqlite_capacity_engine
    ids = seed_http(engine)
    with Session(engine) as session:
        user = session.get(User, ids['owners'][0])
        original_timestamp = user.updated_at
        user.username = 'Prospective name remains attached'
        allowed, count, limit = quota_service.check_project_limit(session, user.id, for_creation=True)
        assert (allowed, count, limit) == (True, 2, 3)
        assert user.username == 'Prospective name remains attached'
        assert user.updated_at == original_timestamp
        session.rollback()
    with Session(engine) as reader:
        assert reader.get(User, ids['owners'][0]).updated_at == original_timestamp


def test_gate_reject_and_folder_failure_roll_back(sqlite_capacity_engine, monkeypatch):
    ids = seed_http(sqlite_capacity_engine)
    uid = ids['owners'][0]

    def broken_folders(_type, _lang):
        raise RuntimeError('Owned diagnostic folder failure')

    monkeypatch.setattr('services.project_service.get_folders_for_type', broken_folders)
    with Session(sqlite_capacity_engine) as session:
        with pytest.raises(RuntimeError, match='Owned diagnostic folder failure'):
            create_project_with_default_folders(session, Project(name='Candidate failed', owner_id=uid))
        assert not session.in_transaction()
    durable = durable_http(sqlite_capacity_engine, ids)
    assert list(durable['active'].values()) == [2]
    assert not durable['candidates']
    with Session(sqlite_capacity_engine) as session:
        assert quota_service.check_project_limit(session, uid) == (True, 2, 3)
        session.rollback()


def test_missing_owner_is_not_a_quota_rejection(sqlite_capacity_engine):
    with Session(sqlite_capacity_engine) as session:
        with pytest.raises(RuntimeError, match='owner'):
            quota_service.check_project_limit(session, 'missing-owned-fixture', for_creation=True)
        session.rollback()


def test_http_unexpected_copy_failure_rolls_back_staged_project(sqlite_capacity_engine, monkeypatch):
    import asyncio
    import json

    from httpx import ASGITransport, AsyncClient

    from main import app
    from models import Inspiration

    monkeypatch.setenv('INSPIRATIONS_ENABLED', 'true')
    ids = seed_http(sqlite_capacity_engine)
    with Session(sqlite_capacity_engine) as session:
        template = session.get(Inspiration, ids['inspiration'])
        template.snapshot_data = json.dumps({'files': None})
        session.commit()
    staged = []

    def observe(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lower().startswith('insert into project'):
            staged.append(statement)

    async def request():
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url='http://owned.test') as client:
            return await client.post(f"/api/v1/inspirations/{ids['inspiration']}/copy",
                                     json={'project_name': 'Candidate failed'},
                                     headers={**ids['headers'][0], 'X-Capacity-Worker': 'A'})

    event.listen(sqlite_capacity_engine, 'after_cursor_execute', observe)
    try:
        with http_sessions(sqlite_capacity_engine) as closed:
            result = asyncio.run(request())
        assert result.status_code == 500
        assert closed == ['A'] and staged
        durable = durable_http(sqlite_capacity_engine, ids)
        assert list(durable['active'].values()) == [2]
        assert durable['candidates'] == {}
        assert durable['copy_count'] == 0
        assert list(durable['usage'].values()) == [0]
    finally:
        event.remove(sqlite_capacity_engine, 'after_cursor_execute', observe)
