"""Recursive-delete depth and bounded row-lock proofs on owned PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from agent.tools.file_ops import crud as crud_module
from api import files as files_api
from models import File
from tests.test_api.test_tree_delete_traversal import (
    _bulk_seed_chain,
    _delete,
    _make_user_and_project,
)
from tests.test_api.test_tree_delete_traversal import (
    test_recursive_delete_does_not_cross_project_boundary_from_legacy_parent_link as _scope_contract,
)
from tests.test_api.test_tree_delete_traversal import (
    test_tool_recursive_delete_keeps_legacy_cycle_visited_behavior as _tool_cycle_contract,
)
from tests.test_api.test_tree_delete_traversal import (
    test_web_recursive_delete_handles_legacy_cycles_once as _web_cycle_contract,
)
from tests.test_services.test_snapshot_concurrency_postgres import pg_engine as pg_engine

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="isolated PostgreSQL URL not configured",
)


@pytest.fixture(autouse=True)
def postgres_runtime(monkeypatch):
    import database

    monkeypatch.setattr(database, "is_postgres", True)


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_postgres_deletes_fk_valid_1200_depth_tree(pg_engine, monkeypatch, entrypoint):
    with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
        user, project = _make_user_and_project(session, f"pg-depth-{entrypoint}")
        ids = _bulk_seed_chain(session, project.id, count=1200, prefix=f"pg-depth-{entrypoint}")
        # Retain ORM references so refresh behavior cannot be hidden by weak refs.
        cached_leaf = session.get(File, ids[-1])
        assert not cached_leaf.is_deleted
        indexed = _delete(
            session, monkeypatch, entrypoint=entrypoint, user=user, file_id=ids[0], recursive=True,
        )
        assert indexed == list(reversed(ids))
        assert cached_leaf.is_deleted
        rows = session.exec(select(File).where(File.project_id == project.id).order_by(File.id)).all()
        assert len(rows) == 1200 and all(row.is_deleted for row in rows)
        assert [row.content for row in rows] == [f"body-{index}" for index in range(1200)]


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_postgres_cycle_and_foreign_scope_contract(pg_engine, monkeypatch, entrypoint):
    with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
        if entrypoint == "web":
            _web_cycle_contract(session, monkeypatch, cycle_size=3)
        else:
            _tool_cycle_contract(session, monkeypatch, cycle_size=3)
        _scope_contract(session, monkeypatch, entrypoint=entrypoint)


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_delete_loader_locks_descendant_only_before_mutation(pg_engine, monkeypatch, entrypoint):
    with Session(pg_engine, expire_on_commit=False) as setup:
        user, project = _make_user_and_project(setup, f"pg-locks-{entrypoint}")
        user_id = user.id
        ids = _bulk_seed_chain(setup, project.id, count=3, prefix=f"pg-locks-{entrypoint}")
        unrelated = File(project_id=project.id, title="Independent draft", content="Before", file_type="draft")
        setup.add(unrelated)
        setup.commit()
        unrelated_id = unrelated.id

    loaded, release = Event(), Event()
    module = files_api if entrypoint == "web" else crud_module
    original = module.load_live_subtree_postorder

    def pause_loaded(session, root):
        rows = original(session, root)
        assert [row.id for row in rows] == list(reversed(ids))
        assert all(not row.is_deleted for row in rows)
        assert not session.dirty
        loaded.set()
        assert release.wait(8), "loader lock proof was not released"
        return rows

    monkeypatch.setattr(module, "load_live_subtree_postorder", pause_loaded)

    def delete():
        from models import User

        with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
            return _delete(
                session, monkeypatch, entrypoint=entrypoint,
                user=session.get(User, user_id), file_id=ids[0], recursive=True,
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(delete)
        try:
            assert loaded.wait(5), "delete never finished loading its subtree"
            with Session(pg_engine) as contender:
                with pytest.raises(OperationalError) as rejected:
                    contender.exec(select(File).where(File.id == ids[-1]).with_for_update(
                        key_share=True, nowait=True,
                    )).one()
                assert rejected.value.orig.pgcode == "55P03"  # lock_not_available
                contender.rollback()
            with Session(pg_engine) as independent:
                row = independent.exec(select(File).where(File.id == unrelated_id).with_for_update(
                    key_share=True, nowait=True,
                )).one()
                row.content = "Independent committed body"
                independent.add(row)
                independent.commit()  # Completes while the exclusive Project gate is held.
            # Versions' FK KEY SHARE remains compatible with NO KEY UPDATE.
            with Session(pg_engine) as version_reader:
                assert version_reader.exec(select(File).where(File.id == ids[-1]).with_for_update(
                    read=True, key_share=True, nowait=True,
                )).one().id == ids[-1]
            assert not future.done()
        finally:
            release.set()
        assert future.result(10) == list(reversed(ids))

    with Session(pg_engine) as verify:
        assert all(verify.get(File, file_id).is_deleted for file_id in ids)
        assert verify.get(File, unrelated_id).content == "Independent committed body"
        assert not verify.get(File, unrelated_id).is_deleted


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_shared_snapshot_gate_waits_for_recursive_delete_commit(pg_engine, monkeypatch, entrypoint):
    import json

    from sqlalchemy import text

    from models import User
    from services.features.snapshot_service import VersionService
    from tests.test_services.test_tree_writers_postgres import _observe_wait

    with Session(pg_engine, expire_on_commit=False) as setup:
        user, project = _make_user_and_project(setup, f"pg-snapshot-delete-{entrypoint}")
        user_id, project_id = user.id, project.id
        ids = _bulk_seed_chain(setup, project.id, count=3, prefix=f"pg-snapshot-delete-{entrypoint}")

    loaded, release, snapshot_started = Event(), Event(), Event()
    snapshot_pid = []
    module = files_api if entrypoint == "web" else crud_module
    original = module.load_live_subtree_postorder

    def pause_loaded(session, root):
        rows = original(session, root)
        loaded.set()
        assert release.wait(8), "snapshot/delete gate proof was not released"
        return rows

    monkeypatch.setattr(module, "load_live_subtree_postorder", pause_loaded)

    def delete():
        with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
            return _delete(
                session, monkeypatch, entrypoint=entrypoint,
                user=session.get(User, user_id), file_id=ids[0], recursive=True,
            )

    def snapshot():
        with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
            cached_leaf = session.get(File, ids[-1])
            assert not cached_leaf.is_deleted
            snapshot_pid.append(session.exec(text("SELECT pg_backend_pid()")).scalar())
            snapshot_started.set()
            result = VersionService().create_snapshot(session, project_id)
            assert cached_leaf is not None  # Keep the stale reference through the gate wait.
            return json.loads(result.data)

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_future = pool.submit(delete)
        try:
            assert loaded.wait(5)
            snapshot_future = pool.submit(snapshot)
            assert snapshot_started.wait(5)
            assert _observe_wait(pg_engine, snapshot_pid[0], snapshot_future)
        finally:
            release.set()
        assert delete_future.result(10) == list(reversed(ids))
        assert snapshot_future.result(10)["files_metadata"] == []


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_postgres_default_expiration_index_delete_budget(pg_engine, monkeypatch, entrypoint):
    import services.llama_index as indexing
    from tests.test_api.test_tree_delete_traversal import (
        test_default_expiration_delete_uses_three_selects_and_schedules_postorder_once,
    )

    calls = []
    monkeypatch.setattr(indexing, "schedule_index_delete", lambda **kwargs: calls.append(kwargs))
    with Session(pg_engine, autoflush=False, expire_on_commit=False) as seed_session:
        test_default_expiration_delete_uses_three_selects_and_schedules_postorder_once(
            seed_session, calls, entrypoint, count=50,
        )
