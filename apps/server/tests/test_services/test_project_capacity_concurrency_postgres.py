"""Owned PostgreSQL proof of the configured active-project capacity contract."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlmodel import Session, SQLModel, func, select

import database
from api.inspirations import CopyInspirationRequest, copy_inspiration
from config.project_templates import get_folders_for_type
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from main import app
from models import AgentApiKey, File, FileVersion, Project, Snapshot, User
from models.activation_event import ActivationEvent
from models.inspiration import Inspiration
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.agent_auth_service import hash_api_key
from services.core.auth_service import create_access_token
from services.project_service import create_project_with_default_folders
from services.quota_service import quota_service

pytestmark = pytest.mark.skipif(
    not os.getenv('ZENSTORY_TEST_POSTGRES_URL'), reason='explicitly owned PostgreSQL URL required',
)
TABLES = [User.__table__, Project.__table__, File.__table__, SubscriptionPlan.__table__,
          UserSubscription.__table__, UsageQuota.__table__, ActivationEvent.__table__, Inspiration.__table__,
          Snapshot.__table__, FileVersion.__table__, AgentApiKey.__table__]


@pytest.fixture(scope='module')
def capacity_engine():
    url = os.environ['ZENSTORY_TEST_POSTGRES_URL']
    assert url == os.environ['DATABASE_URL'] and database.is_postgres is True
    engine = create_engine(url, connect_args={
        'options': '-c timezone=UTC -c statement_timeout=60000 -c lock_timeout=45000',
    })
    assert engine.dialect.name == 'postgresql'
    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine, tables=TABLES)
        engine.dispose()


def seed(engine, *, expired=False, different_owner=False, copy=False):
    with Session(engine) as session:
        suffix = uuid4().hex
        users = [User(username=f'capacity-{suffix}-{i}', email=f'capacity-{suffix}-{i}@example.test',
                      hashed_password='unused-local-test') for i in range(2 if different_owner else 1)]
        session.add_all(users)
        session.flush()
        user_ids = [user.id for user in users]
        for uid in user_ids:
            session.add_all([Project(name=f'Existing {i}', owner_id=uid) for i in range(2)])
        if expired or copy:
            plan = session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == 'pro')).first()
            if plan is None:
                plan = SubscriptionPlan(name='pro', display_name='Local configured cap', features={'max_projects': 3})
                session.add(plan)
                session.flush()
            now = datetime.now(UTC)
            session.add(UserSubscription(user_id=user_ids[0], plan_id=plan.id, status='active',
                                         current_period_start=now - timedelta(days=30),
                                         current_period_end=now + timedelta(days=-1 if expired else 30)))
        inspiration_id = None
        if copy:
            inspiration = Inspiration(name=f'Local {suffix}', snapshot_data=json.dumps({'files': []}))
            session.add(inspiration)
            session.flush()
            inspiration_id = inspiration.id
        session.commit()
        return {'owners': user_ids, 'inspiration': inspiration_id}


def create(session, ids, worker, *, copy=False, different_owner=False):
    uid = ids['owners'][1 if different_owner and worker == 'B' else 0]
    try:
        if copy and worker == 'B':
            user = session.get(User, uid)
            result = copy_inspiration(ids['inspiration'], CopyInspirationRequest(project_name='Copied B'), user, session)
            return {'status': 'created', 'project': result.project_id, 'owner': uid, 'copy': True}
        project = Project(name=f'Created {worker}', owner_id=uid)
        folders = create_project_with_default_folders(session, project)
        assert len(folders) == len(get_folders_for_type('novel', 'zh'))
        return {'status': 'created', 'project': project.id, 'owner': uid, 'copy': False}
    except APIException as error:
        session.rollback()
        assert error.status_code == 402
        return {'status': 'quota', 'code': str(error.error_code), 'owner': uid}


def counts(engine, ids):
    with Session(engine) as session:
        return {uid: session.exec(select(func.count()).select_from(Project).where(
            Project.owner_id == uid, Project.is_deleted.is_(False),
        )).one() for uid in ids['owners']}


@pytest.mark.parametrize('scenario', ['same-owner', 'expired-plan', 'different-owner', 'shared-vs-copy'])
def test_last_slot_is_not_allocated_twice(capacity_engine, scenario, record_property):
    engine = capacity_engine
    different = scenario == 'different-owner'
    copy = scenario == 'shared-vs-copy'
    ids = seed(engine, expired=scenario == 'expired-plan', different_owner=different, copy=copy)
    held, release, b_started = threading.Event(), threading.Event(), threading.Event()
    pids = {}
    thread_owners = {}

    def observe(connection, cursor, statement, parameters, context, executemany):
        worker = thread_owners.get(threading.get_ident())
        if worker:
            pids[worker] = connection.connection.driver_connection.get_backend_pid()
        normalized = ' '.join(statement.lower().split())
        if worker == 'A' and 'select count(' in normalized and 'from project' in normalized:
            held.set()
            assert release.wait(45), 'A count hold was not released'

    event.listen(engine, 'after_cursor_execute', observe)

    def run(worker):
        with Session(engine) as session:
            connection = session.connection()
            thread_owners[threading.get_ident()] = worker
            pids[worker] = connection.execute(text('SELECT pg_backend_pid()')).scalar_one()
            if worker == 'B':
                b_started.set()
            try:
                return create(session, ids, worker, copy=copy, different_owner=different)
            finally:
                thread_owners.pop(threading.get_ident(), None)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(run, 'A')
            try:
                assert held.wait(30), 'A never executed its actual capacity count'
                b = pool.submit(run, 'B')
                assert b_started.wait(30)
                wait_fact = None
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if b.done():
                        wait_fact = {'b_finished_before_a_release': True}
                        break
                    with engine.connect() as observer:
                        row = observer.execute(text('SELECT wait_event_type, wait_event, pg_blocking_pids(pid) '
                                                    'FROM pg_stat_activity WHERE pid=:pid'), {'pid': pids['B']}).first()
                        if row is not None and row[0] == 'Lock' and pids['A'] in row[2]:
                            wait_fact = {'b_wait_event_type': row[0], 'b_wait_event': row[1], 'blockers': row[2]}
                            break
                    time.sleep(0.01)
                assert wait_fact is not None, 'B neither finished nor observably waited for A'
            finally:
                release.set()
            outcomes = [a.result(timeout=45), b.result(timeout=45)]
        durable = counts(engine, ids)
        facts = {'scenario': scenario, 'wait': wait_fact, 'outcomes': outcomes, 'durable_active_counts': durable}
        print('ACTUAL_PROJECT_CAPACITY ' + json.dumps(facts, sort_keys=True))
        record_property('project_capacity', json.dumps(facts, sort_keys=True))
        assert all(count == 3 for count in durable.values())
        if different:
            assert [r['status'] for r in outcomes] == ['created', 'created']
            assert wait_fact == {'b_finished_before_a_release': True}
        else:
            assert sorted(r['status'] for r in outcomes) == ['created', 'quota']
            assert wait_fact.get('b_wait_event_type') == 'Lock'
            assert wait_fact['blockers'] == [pids['A']]
        with Session(engine) as reader:
            for result in outcomes:
                if result['status'] == 'created' and not result['copy']:
                    assert reader.exec(select(func.count()).select_from(File).where(
                        File.project_id == result['project'],
                    )).one() == len(get_folders_for_type('novel', 'zh'))
    finally:
        release.set()
        event.remove(engine, 'after_cursor_execute', observe)


def test_sequential_last_slot_keeps_existing_402_contract(capacity_engine, record_property):
    ids = seed(capacity_engine)
    with Session(capacity_engine) as first:
        result_a = create(first, ids, 'A')
    with Session(capacity_engine) as second:
        result_b = create(second, ids, 'B')
    assert result_a['status'] == 'created'
    assert result_b['status'] == 'quota'
    assert counts(capacity_engine, ids) == {ids['owners'][0]: 3}
    record_property('project_capacity_control', 'sequential created then402; durable3')


def seed_http(engine, *, plan_name='free', limit=3, expired=False, different=False,
              quota_state='current', slots=2):
    suffix = uuid4().hex
    with Session(engine) as session:
        plan = session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == plan_name)).first()
        if plan is None:
            plan = SubscriptionPlan(name=plan_name, display_name='Owned local capacity', features={})
        plan.features = {'max_projects': limit, 'inspiration_copies_monthly': 10}
        session.add(plan)
        session.flush()
        if expired:
            free = session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == 'free')).first()
            if free is None:
                free = SubscriptionPlan(name='free', display_name='Local free', features={'max_projects': 3})
                session.add(free)
        owners, headers = [], []
        for i in range(2 if different else 1):
            user = User(username=f'http-{suffix}-{i}', email=f'http-{suffix}-{i}@example.test',
                        hashed_password='not-used-local-fixture')
            session.add(user)
            session.flush()
            uid = user.id
            owners.append(uid)
            token = create_access_token({'sub': uid})
            raw_key = 'eg_' + uuid4().hex
            session.add(AgentApiKey(user_id=uid, key_prefix=raw_key[:8], key_hash=hash_api_key(raw_key),
                                    name='Owned local HTTP', scopes=['read', 'write']))
            headers.append({'Authorization': 'Bearer ' + token, 'X-Agent-API-Key': raw_key})
            session.add_all([Project(name=f'Existing HTTP {j}', owner_id=uid) for j in range(slots)])
            now = datetime.now(UTC)
            session.add(UserSubscription(user_id=uid, plan_id=plan.id, status='active',
                                         current_period_start=now - timedelta(days=30),
                                         current_period_end=now + timedelta(days=-1 if expired else 30)))
            if quota_state != 'missing':
                quota = quota_service.create_default_quota(session, uid, commit=False)
                if quota_state == 'stale':
                    quota.monthly_period_start = now - timedelta(days=65)
                    quota.monthly_period_end = now - timedelta(days=35)
                    quota.inspiration_copies_used = 9
        snapshot = {'files': [{'id': 'old-body', 'title': 'Chapter 1', 'file_type': 'draft',
                               'content': 'Owned local body 中文', 'order': 1}]}
        inspiration = Inspiration(name=f'HTTP template {suffix}', snapshot_data=json.dumps(snapshot))
        session.add(inspiration)
        session.flush()
        iid = inspiration.id
        session.commit()
    return {'owners': owners, 'headers': headers, 'inspiration': iid}


@contextmanager
def http_sessions(engine):
    previous = app.dependency_overrides.copy()
    closed = []

    def owned_session(request: Request):
        worker = request.headers.get('X-Capacity-Worker', 'control')
        with Session(engine) as session:
            assert session.expire_on_commit is True

            def label(_session, _transaction, connection):
                connection.info['capacity_worker'] = worker

            event.listen(session, 'after_begin', label)
            try:
                yield session
            finally:
                event.remove(session, 'after_begin', label)
                closed.append(worker)

    def unlabel(_dbapi_connection, connection_record):
        connection_record.info.pop('capacity_worker', None)

    event.listen(engine, 'checkin', unlabel)
    app.dependency_overrides[get_session] = owned_session
    try:
        yield closed
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        event.remove(engine, 'checkin', unlabel)


def post_http(ids, kind, worker, *, different=False):
    index = 1 if different and worker == 'B' else 0
    headers = {**ids['headers'][index], 'X-Capacity-Worker': worker}
    if kind == 'copy':
        path = f"/api/v1/inspirations/{ids['inspiration']}/copy"
        payload = {'project_name': f'Candidate {worker}'}
    else:
        path = '/api/v1/agent/projects' if kind == 'agent' else '/api/v1/projects'
        payload = {'name': f'Candidate {worker}', 'project_type': 'novel'}

    async def request():
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://owned.test') as client:
            response = await client.post(path, json=payload, headers=headers)
            return {'status': response.status_code, 'body': response.json(), 'kind': kind, 'worker': worker}

    return asyncio.run(request())


def durable_http(engine, ids):
    with Session(engine) as session:
        projects = session.exec(select(Project).where(Project.owner_id.in_(ids['owners']))).all()
        candidates = [p for p in projects if p.name.startswith('Candidate ')]
        result = {'active': {uid: sum(p.owner_id == uid and not p.is_deleted for p in projects)
                             for uid in ids['owners']}, 'candidates': {p.name: p.id for p in candidates},
                  'files': {}, 'versions': {}, 'activation': {}, 'usage': {}}
        for project in candidates:
            for key, model in [('files', File), ('versions', FileVersion), ('activation', ActivationEvent)]:
                result[key][project.name] = session.exec(select(func.count()).select_from(model).where(
                    model.project_id == project.id)).one()
        for uid in ids['owners']:
            quotas = session.exec(select(UsageQuota).where(UsageQuota.user_id == uid)).all()
            assert len(quotas) <= 1, 'no duplicate monthly quota rows'
            result['usage'][uid] = quotas[0].inspiration_copies_used if quotas else None
        result['copy_count'] = session.get(Inspiration, ids['inspiration']).copy_count
        return result


def concurrent_http(engine, ids, pair, *, different=False, observer_action=None):
    held, release = threading.Event(), threading.Event()
    pids, sql_frames = {}, []

    def observe(connection, cursor, statement, parameters, context, executemany):
        worker = connection.info.get('capacity_worker')
        if worker not in ('A', 'B'):
            return
        if engine.dialect.name == 'postgresql':
            pids[worker] = connection.connection.driver_connection.get_backend_pid()
        normalized = ' '.join(statement.lower().split())
        if (('from project' in normalized and 'select count(' in normalized) or 'for no key update' in normalized
                or normalized.startswith(('insert into project', 'insert into file', 'update usage_quota'))):
            sql_frames.append({'worker': worker, 'sql': normalized})
        if worker == 'A' and 'select count(' in normalized and 'from project' in normalized and not held.is_set():
            held.set()
            assert release.wait(45), 'finite count hold not released'

    event.listen(engine, 'after_cursor_execute', observe)
    try:
        with http_sessions(engine) as closed, ThreadPoolExecutor(max_workers=2) as pool:
            try:
                a = pool.submit(post_http, ids, pair[0], 'A', different=different)
                assert held.wait(30), 'A did not reach real capacity count'
                if observer_action:
                    observer_action()
                b = pool.submit(post_http, ids, pair[1], 'B', different=different)
                fact = None
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if b.done():
                        fact = {'b_finished_before_a_release': True}
                        break
                    if 'B' in pids:
                        with engine.connect() as observer:
                            row = observer.execute(text('SELECT wait_event_type, wait_event, pg_blocking_pids(pid) '
                                                        'FROM pg_stat_activity WHERE pid=:pid'), {'pid': pids['B']}).first()
                            if row and row[0] == 'Lock' and pids['A'] in row[2]:
                                fact = {'type': row[0], 'event': row[1], 'blockers': row[2]}
                                break
                    time.sleep(.01)  # Poll actual DB state; elapsed sleep is not race evidence.
                assert fact is not None, 'B neither completed nor observably blocked'
            finally:
                release.set()
            outcomes = [a.result(timeout=45), b.result(timeout=45)]
        assert sorted(closed) == ['A', 'B']
        return outcomes, fact, sql_frames
    finally:
        release.set()
        event.remove(engine, 'after_cursor_execute', observe)


HTTP_CASES = [
    (('web', 'web'), 'free', 3, False, False, 'current'),
    (('web', 'agent'), 'free', 3, False, False, 'current'),
    (('web', 'copy'), 'pro', 3, False, False, 'current'),
    (('copy', 'copy'), 'pro', 3, False, False, 'current'),
    (('web', 'web'), 'pro', 3, True, False, 'current'),
    (('copy', 'copy'), 'free', 3, False, False, 'missing'),
    (('copy', 'copy'), 'free', 3, False, False, 'stale'),
    (('web', 'web'), 'pro', -1, False, False, 'current'),
    (('copy', 'copy'), 'pro', -1, False, False, 'current'),
    (('web', 'web'), 'free', 3, False, True, 'current'),
]


@pytest.mark.parametrize(('pair', 'plan', 'limit', 'expired', 'different', 'quota_state'), HTTP_CASES)
def test_http_capacity_transaction_matrix(capacity_engine, monkeypatch, record_property,
                                          pair, plan, limit, expired, different, quota_state):
    monkeypatch.setenv('INSPIRATIONS_ENABLED', 'true')
    ids = seed_http(capacity_engine, plan_name=plan, limit=limit, expired=expired,
                    different=different, quota_state=quota_state)
    outcomes, wait, frames = concurrent_http(capacity_engine, ids, pair, different=different)
    durable = durable_http(capacity_engine, ids)
    fact = {'pair': pair, 'plan': plan, 'limit': limit, 'expired': expired, 'quota_state': quota_state,
            'wait': wait, 'outcomes': outcomes, 'durable': durable, 'gate_count_frames': frames}
    print('ACTUAL_HTTP_CAPACITY ' + json.dumps(fact, sort_keys=True))
    record_property('http_capacity', json.dumps(fact, sort_keys=True))
    expected = [200, 200] if different or limit == -1 else [200, 402]
    assert sorted(result['status'] for result in outcomes) == expected
    expected_active = 3 if different or limit != -1 else 4
    assert set(durable['active'].values()) == {expected_active}
    if different:
        assert wait == {'b_finished_before_a_release': True}
    else:
        assert wait.get('type') == 'Lock'
    for result in outcomes:
        name = f"Candidate {result['worker']}"
        if result['status'] == 402:
            assert result['body']['error_code'] == ErrorCode.QUOTA_PROJECTS_EXCEEDED
            assert name not in durable['candidates'], 'reject leaves no project/files/versions/activation'
        else:
            assert name in durable['candidates']
            if result['kind'] == 'copy':
                assert durable['files'][name] == durable['versions'][name] == 1
                assert durable['activation'][name] == 0  # Existing copy contract.
            else:
                assert durable['files'][name] == len(get_folders_for_type('novel', 'zh'))
                assert durable['activation'][name] in (0, 1)  # Once per owner, not per project.
    expected_activations = len({ids['owners'][1 if different and result['worker'] == 'B' else 0]
                                for result in outcomes if result['status'] == 200 and result['kind'] != 'copy'})
    assert sum(durable['activation'].values()) == expected_activations
    copies = sum(result['kind'] == 'copy' and result['status'] == 200 for result in outcomes)
    assert durable['copy_count'] == copies
    if 'copy' in pair and plan != 'pro':
        assert list(durable['usage'].values()) == [copies]




def test_http_soft_deleted_slot_is_reused_once(capacity_engine, monkeypatch):
    monkeypatch.setenv('INSPIRATIONS_ENABLED', 'true')
    ids = seed_http(capacity_engine, slots=3)
    with Session(capacity_engine) as session:
        row = session.exec(select(Project).where(Project.owner_id == ids['owners'][0])).first()
        row.is_deleted = True
        session.commit()
    with http_sessions(capacity_engine) as closed:
        winner = post_http(ids, 'web', 'A')
        loser = post_http(ids, 'copy', 'B')
    assert winner['status'] == 200
    assert loser['status'] == 402
    assert loser['body']['error_code'] == ErrorCode.QUOTA_PROJECTS_EXCEEDED
    assert sorted(closed) == ['A', 'B']
    durable = durable_http(capacity_engine, ids)
    assert list(durable['active'].values()) == [3]
    assert list(durable['candidates']) == ['Candidate A']
    assert durable['copy_count'] == 0
    assert list(durable['usage'].values()) == [0]


def test_http_final_monthly_reservation_failure_rolls_back_copy(capacity_engine, monkeypatch, record_property):
    monkeypatch.setenv('INSPIRATIONS_ENABLED', 'true')
    ids = seed_http(capacity_engine)

    def exhaust_real_quota():
        # An independent monthly consumer can write this row without allocating
        # a project. Do not replace advisory checks or the actual reservation.
        with Session(capacity_engine) as consumer:
            quota = consumer.exec(select(UsageQuota).where(UsageQuota.user_id == ids['owners'][0])).one()
            quota.inspiration_copies_used = 10
            consumer.commit()

    outcomes, wait, frames = concurrent_http(capacity_engine, ids, ('copy', 'web'),
                                             observer_action=exhaust_real_quota)
    assert outcomes[0]['status'] == 402
    assert outcomes[1]['status'] == 200
    durable = durable_http(capacity_engine, ids)
    assert list(durable['candidates']) == ['Candidate B']
    assert list(durable['active'].values()) == [3]
    assert durable['copy_count'] == 0
    assert list(durable['usage'].values()) == [10]
    assert wait['type'] == 'Lock'
    assert any(frame['worker'] == 'A' and 'insert into file_version' in frame['sql'] for frame in frames)
    record_property('quota_reservation_rollback', json.dumps({'outcomes': outcomes, 'durable': durable,
                                                            'frames': frames}, sort_keys=True))


def test_pg_creation_gate_compatible_with_fk_key_share(capacity_engine):
    ids = seed_http(capacity_engine)
    uid = ids['owners'][0]
    with Session(capacity_engine) as owner:
        user = owner.get(User, uid)
        timestamp = user.updated_at
        user.avatar_url = 'local-prospective-avatar'
        assert quota_service.check_project_limit(owner, uid, for_creation=True) == (True, 2, 3)
        assert user.avatar_url == 'local-prospective-avatar' and user.updated_at == timestamp
        with Session(capacity_engine) as other:
            other.exec(text('SET LOCAL lock_timeout = \'2s\''))
            assert other.exec(select(User.id).where(User.id == uid).with_for_update(key_share=True, read=True)).one() == uid
            other.rollback()
        owner.rollback()


def test_pg_checking_only_does_not_lock_owner(capacity_engine):
    ids = seed_http(capacity_engine)
    uid = ids['owners'][0]
    with Session(capacity_engine) as checking:
        assert quota_service.check_project_limit(checking, uid) == (True, 2, 3)
        with Session(capacity_engine) as other:
            assert other.exec(select(User.id).where(User.id == uid).with_for_update(nowait=True)).one() == uid
            other.rollback()
        checking.rollback()
