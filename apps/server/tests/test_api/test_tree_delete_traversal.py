"""Regression coverage for iterative, project-scoped file-tree deletion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import event, insert, update
from sqlmodel import select

from agent.tools.file_ops.crud import FileCRUD
from api import files as files_api
from config.datetime_utils import normalize_datetime_to_utc
from models import File, Project, User

EntryPoint = Literal["web", "tool"]
BASE_TIME = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)


def _make_user_and_project(db_session, suffix: str) -> tuple[User, Project]:
    user = User(
        username=f"delete-traversal-{suffix}",
        email=f"delete-traversal-{suffix}@example.test",
        hashed_password="unused",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(name=f"Delete traversal {suffix}", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    return user, project


def _file_row(
    *,
    file_id: str,
    project_id: str,
    parent_id: str | None,
    title: str,
    content: str,
    updated_at: datetime = BASE_TIME,
) -> dict[str, object]:
    return {
        "id": file_id,
        "project_id": project_id,
        "title": title,
        "content": content,
        "file_type": "folder",
        "parent_id": parent_id,
        "order": 0,
        "file_metadata": None,
        "created_at": BASE_TIME,
        "updated_at": updated_at,
        "is_deleted": False,
        "deleted_at": None,
    }


def _bulk_seed_chain(db_session, project_id: str, *, count: int, prefix: str) -> list[str]:
    """Insert parents before children so the fixture remains FK-compatible."""
    ids = [f"{prefix}-{index:04d}" for index in range(count)]
    rows = [
        _file_row(
            file_id=file_id,
            project_id=project_id,
            parent_id=ids[index - 1] if index else None,
            title=f"Node {index}",
            content=f"body-{index}",
        )
        for index, file_id in enumerate(ids)
    ]
    db_session.execute(insert(File), rows)
    db_session.commit()
    return ids


def _delete(
    db_session,
    monkeypatch,
    *,
    entrypoint: EntryPoint,
    user: User,
    file_id: str,
    recursive: bool,
) -> list[str]:
    indexed_ids: list[str] = []
    if entrypoint == "web":
        background_tasks = BackgroundTasks()
        result = files_api.delete_file(
            file_id,
            background_tasks,
            recursive=recursive,
            current_user=user,
            session=db_session,
        )
        assert result == {"message": "File deleted successfully"}
        return [task.kwargs["entity_id"] for task in background_tasks.tasks]

    import services.llama_index as indexing

    monkeypatch.setattr(
        indexing, "schedule_index_delete", lambda **kwargs: indexed_ids.append(kwargs["entity_id"]),
    )
    assert FileCRUD(db_session, user.id).delete_file(file_id, recursive=recursive) is True
    return indexed_ids


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_recursive_delete_handles_a_1200_level_subtree_without_recursion_error(
    db_session, monkeypatch, entrypoint: EntryPoint,
):
    user, project = _make_user_and_project(db_session, f"deep-{entrypoint}")
    ids = _bulk_seed_chain(
        db_session,
        project.id,
        count=1200,
        prefix=f"deep-{entrypoint}",
    )

    indexed_ids = _delete(
        db_session,
        monkeypatch,
        entrypoint=entrypoint,
        user=user,
        file_id=ids[0],
        recursive=True,
    )

    rows = db_session.exec(select(File).where(File.id.in_(ids)).order_by(File.id)).all()
    assert len(rows) == 1200
    assert all(row.is_deleted for row in rows)
    assert [row.content for row in rows] == [f"body-{index}" for index in range(1200)]
    assert indexed_ids == list(reversed(ids))
    assert len(indexed_ids) == len(set(indexed_ids)) == 1200


@pytest.mark.parametrize("cycle_size", [1, 2, 3])
def test_web_recursive_delete_handles_legacy_cycles_once(
    db_session, monkeypatch, cycle_size: int,
):
    user, project = _make_user_and_project(db_session, f"web-cycle-{cycle_size}")
    ids = _bulk_seed_chain(
        db_session,
        project.id,
        count=cycle_size,
        prefix=f"web-cycle-{cycle_size}",
    )
    db_session.execute(update(File).where(File.id == ids[0]).values(parent_id=ids[-1]))
    db_session.commit()
    monkeypatch.setattr(files_api, "utcnow", lambda: BASE_TIME)

    indexed_ids = _delete(
        db_session,
        monkeypatch,
        entrypoint="web",
        user=user,
        file_id=ids[0],
        recursive=True,
    )

    rows = db_session.exec(select(File).where(File.id.in_(ids))).all()
    assert {row.id for row in rows if row.is_deleted} == set(ids)
    assert all(row.content == f"body-{ids.index(row.id)}" for row in rows)
    assert all(
        normalize_datetime_to_utc(row.updated_at) == BASE_TIME + timedelta(microseconds=1)
        for row in rows
    )
    assert indexed_ids == list(reversed(ids))
    assert len(indexed_ids) == len(set(indexed_ids)) == cycle_size


@pytest.mark.parametrize("cycle_size", [1, 2, 3])
def test_tool_recursive_delete_keeps_legacy_cycle_visited_behavior(
    db_session, monkeypatch, cycle_size: int,
):
    from agent.tools.file_ops import crud as crud_module

    user, project = _make_user_and_project(db_session, f"tool-cycle-{cycle_size}")
    ids = _bulk_seed_chain(
        db_session,
        project.id,
        count=cycle_size,
        prefix=f"tool-cycle-{cycle_size}",
    )
    db_session.execute(update(File).where(File.id == ids[0]).values(parent_id=ids[-1]))
    db_session.commit()
    monkeypatch.setattr(crud_module, "utcnow", lambda: BASE_TIME)

    indexed_ids = _delete(
        db_session,
        monkeypatch,
        entrypoint="tool",
        user=user,
        file_id=ids[0],
        recursive=True,
    )

    rows = db_session.exec(select(File).where(File.id.in_(ids))).all()
    assert {row.id for row in rows if row.is_deleted} == set(ids)
    assert all(
        normalize_datetime_to_utc(row.updated_at) == BASE_TIME + timedelta(microseconds=1)
        for row in rows
    )
    assert indexed_ids == list(reversed(ids))
    assert len(indexed_ids) == len(set(indexed_ids)) == cycle_size


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_recursive_delete_does_not_cross_project_boundary_from_legacy_parent_link(
    db_session, monkeypatch, entrypoint: EntryPoint,
):
    user, project = _make_user_and_project(db_session, f"scope-owner-{entrypoint}")
    _other_user, other_project = _make_user_and_project(db_session, f"scope-other-{entrypoint}")
    root_id = f"scope-{entrypoint}-root"
    child_id = f"scope-{entrypoint}-child"
    foreign_id = f"scope-{entrypoint}-foreign"
    db_session.execute(
        insert(File),
        [
            _file_row(
                file_id=root_id,
                project_id=project.id,
                parent_id=None,
                title="Owned root",
                content="owned-root-body",
            ),
            _file_row(
                file_id=child_id,
                project_id=project.id,
                parent_id=root_id,
                title="Owned child",
                content="owned-child-body",
            ),
            _file_row(
                file_id=foreign_id,
                project_id=other_project.id,
                parent_id=None,
                title="Foreign legacy row",
                content="foreign-body-must-survive",
            ),
        ],
    )
    db_session.commit()
    # Explicit historical dirty-data fixture: public creation rejects this link.
    db_session.execute(update(File).where(File.id == foreign_id).values(parent_id=root_id))
    db_session.commit()

    indexed_ids = _delete(
        db_session,
        monkeypatch,
        entrypoint=entrypoint,
        user=user,
        file_id=root_id,
        recursive=True,
    )

    owned_root = db_session.get(File, root_id, populate_existing=True)
    owned_child = db_session.get(File, child_id, populate_existing=True)
    foreign = db_session.get(File, foreign_id, populate_existing=True)
    assert owned_root is not None and owned_root.is_deleted
    assert owned_child is not None and owned_child.is_deleted
    assert foreign is not None and not foreign.is_deleted
    assert foreign.content == "foreign-body-must-survive"
    assert normalize_datetime_to_utc(foreign.updated_at) == BASE_TIME
    assert set(indexed_ids) == {root_id, child_id}


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_nonrecursive_delete_preserves_active_child_body_and_timestamp(
    db_session, monkeypatch, entrypoint: EntryPoint,
):
    user, project = _make_user_and_project(db_session, f"nonrecursive-{entrypoint}")
    root_id = f"nonrecursive-{entrypoint}-root"
    child_id = f"nonrecursive-{entrypoint}-child"
    db_session.execute(
        insert(File),
        [
            _file_row(
                file_id=root_id,
                project_id=project.id,
                parent_id=None,
                title="Root",
                content="root-body",
            ),
            _file_row(
                file_id=child_id,
                project_id=project.id,
                parent_id=root_id,
                title="Child",
                content="child-body-must-survive",
            ),
        ],
    )
    db_session.commit()

    indexed_ids = _delete(
        db_session,
        monkeypatch,
        entrypoint=entrypoint,
        user=user,
        file_id=root_id,
        recursive=False,
    )

    root = db_session.get(File, root_id, populate_existing=True)
    child = db_session.get(File, child_id, populate_existing=True)
    assert root is not None and root.is_deleted
    assert child is not None and not child.is_deleted
    assert child.content == "child-body-must-survive"
    assert normalize_datetime_to_utc(child.updated_at) == BASE_TIME
    assert indexed_ids == [root_id]


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_recursive_delete_obeys_three_file_select_budget_for_50_node_subtree(
    db_session, monkeypatch, entrypoint: EntryPoint,
):
    user, project = _make_user_and_project(db_session, f"select-count-{entrypoint}")
    ids = _bulk_seed_chain(
        db_session,
        project.id,
        count=50,
        prefix=f"select-count-{entrypoint}",
    )
    db_session.expire_all()
    file_selects = 0

    def count_file_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal file_selects
        normalized = " ".join(statement.lower().split())
        if normalized.startswith(("select", "with")) and f" from {File.__tablename__} " in f" {normalized} ":
            file_selects += 1

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count_file_selects)
    try:
        _delete(
            db_session,
            monkeypatch,
            entrypoint=entrypoint,
            user=user,
            file_id=ids[0],
            recursive=True,
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_file_selects)

    print(f"FILE_SELECT_COUNT[{entrypoint}]={file_selects}")
    assert file_selects == 3


def test_web_recursive_delete_of_already_deleted_root_still_deletes_active_children(
    db_session, monkeypatch,
):
    user, project = _make_user_and_project(db_session, "web-redelete")
    ids = _bulk_seed_chain(db_session, project.id, count=3, prefix="web-redelete")
    from agent.tools.file_ops import crud as crud_module

    monkeypatch.setattr(files_api, "utcnow", lambda: BASE_TIME)
    monkeypatch.setattr(crud_module, "utcnow", lambda: BASE_TIME)
    assert _delete(
        db_session, monkeypatch, entrypoint="web", user=user, file_id=ids[0], recursive=False,
    ) == [ids[0]]
    root = db_session.get(File, ids[0], populate_existing=True)
    previous_token = normalize_datetime_to_utc(root.updated_at)
    with pytest.raises(ValueError, match="文件不存在或已删除"):
        FileCRUD(db_session, user.id).delete_file(ids[0], recursive=True)

    indexed = _delete(
        db_session, monkeypatch, entrypoint="web", user=user, file_id=ids[0], recursive=True,
    )
    rows = db_session.exec(select(File).where(File.id.in_(ids)).order_by(File.id)).all()
    assert indexed == list(reversed(ids))
    assert all(row.is_deleted for row in rows)
    assert normalize_datetime_to_utc(rows[0].updated_at) > previous_token
    assert [row.content for row in rows] == [f"body-{index}" for index in range(3)]


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_recursive_delete_prunes_deleted_intermediate_and_keeps_live_grandchild(
    db_session, monkeypatch, entrypoint: EntryPoint,
):
    user, project = _make_user_and_project(db_session, f"pruned-{entrypoint}")
    ids = _bulk_seed_chain(db_session, project.id, count=3, prefix=f"pruned-{entrypoint}")
    db_session.execute(update(File).where(File.id == ids[1]).values(is_deleted=True, deleted_at=BASE_TIME))
    db_session.commit()
    indexed = _delete(
        db_session, monkeypatch, entrypoint=entrypoint, user=user, file_id=ids[0], recursive=True,
    )
    child = db_session.get(File, ids[1], populate_existing=True)
    grandchild = db_session.get(File, ids[2], populate_existing=True)
    assert indexed == [ids[0]]
    assert child.is_deleted and normalize_datetime_to_utc(child.updated_at) == BASE_TIME
    assert not grandchild.is_deleted
    assert grandchild.content == "body-2"
    assert normalize_datetime_to_utc(grandchild.updated_at) == BASE_TIME


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
@pytest.mark.parametrize("future_days", [0, 365])
def test_recursive_delete_has_stable_id_postorder_and_preserves_payload(
    db_session, monkeypatch, entrypoint: EntryPoint, future_days: int,
):
    from agent.tools.file_ops import crud as crud_module

    user, project = _make_user_and_project(db_session, f"postorder-{entrypoint}-{future_days}")
    prefix = f"postorder-{entrypoint}-{future_days}"
    root, a, b, a_child, b_child = [f"{prefix}-{name}" for name in ("root", "a", "b", "z", "c")]
    previous = BASE_TIME + timedelta(days=future_days)
    specs = [(root, None), (b, root), (b_child, b), (a, root), (a_child, a)]
    rows = [
        dict(_file_row(
            file_id=file_id, project_id=project.id, parent_id=parent_id,
            title=file_id, content=f"Body {file_id}", updated_at=previous,
        ), order=index, file_type="folder" if file_id in (root, a, b) else "draft")
        for index, (file_id, parent_id) in enumerate(specs)
    ]
    db_session.execute(insert(File), rows)
    db_session.commit()
    for module in (files_api, crud_module):
        monkeypatch.setattr(module, "utcnow", lambda: BASE_TIME)
    indexed = _delete(
        db_session, monkeypatch, entrypoint=entrypoint, user=user, file_id=root, recursive=True,
    )
    assert indexed == [a_child, a, b_child, b, root]
    assert len(set(indexed)) == 5
    for expected in rows:
        row = db_session.get(File, expected["id"], populate_existing=True)
        assert row.is_deleted
        assert normalize_datetime_to_utc(row.deleted_at) == BASE_TIME
        assert normalize_datetime_to_utc(row.updated_at) == previous + timedelta(microseconds=1)
        assert (row.content, row.order, row.file_type, row.title) == (
            expected["content"], expected["order"], expected["file_type"], expected["title"],
        )


@pytest.mark.parametrize("count", [1, 50, 1200])
def test_subtree_loader_uses_one_select_and_does_not_mutate_rows(db_session, count: int):
    from services.file_tree_rules import load_live_subtree_postorder, lock_project_for_files

    _user, project = _make_user_and_project(db_session, f"loader-{count}")
    ids = _bulk_seed_chain(db_session, project.id, count=count, prefix=f"loader-{count}")
    lock_project_for_files(db_session, project.id, exclusive=True)
    root = db_session.get(File, ids[0], populate_existing=True)
    file_selects: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith(("select", "with")) and f" from {File.__tablename__} " in f" {normalized} ":
            file_selects.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        loaded = load_live_subtree_postorder(db_session, root)
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    print(f"HELPER_FILE_SELECT_COUNT[{count}]={len(file_selects)}")
    assert len(file_selects) == 1
    assert [row.id for row in loaded] == list(reversed(ids))
    assert loaded[-1] is root
    assert not db_session.dirty
    assert all(not row.is_deleted and row.deleted_at is None for row in loaded)
    assert all(normalize_datetime_to_utc(row.updated_at) == BASE_TIME for row in loaded)


@pytest.fixture
def provider_delete_calls(monkeypatch):
    import services.llama_index as indexing

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(indexing, "schedule_index_delete", lambda **kwargs: calls.append(kwargs))
    return calls


def _invoke_real_delete(session, user, entrypoint, file_id, recursive, background_tasks):
    if entrypoint == "web":
        return files_api.delete_file(
            file_id, background_tasks, recursive=recursive, current_user=user, session=session,
        )
    return FileCRUD(session, user.id).delete_file(file_id, recursive=recursive)


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
@pytest.mark.parametrize("count", [1, 50, 1200])
def test_default_expiration_delete_uses_three_selects_and_schedules_postorder_once(
    db_session, provider_delete_calls, entrypoint: EntryPoint, count: int,
):
    import asyncio

    from sqlmodel import Session

    user, project = _make_user_and_project(db_session, f"expiration-{entrypoint}-{count}")
    user_id, project_id = user.id, project.id
    ids = _bulk_seed_chain(db_session, project_id, count=count, prefix=f"expiration-{entrypoint}-{count}")
    # The leaf has a different index entity type to check all descriptor fields.
    db_session.execute(update(File).where(File.id == ids[-1]).values(file_type="draft"))
    db_session.commit()
    selects: list[str] = []
    postcommit_selects: list[str] = []
    committed = False
    tasks = BackgroundTasks()

    def after_commit(_session):
        nonlocal committed
        committed = True
        assert not tasks.tasks and not provider_delete_calls

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith(("select", "with")) and f" from {File.__tablename__} " in f" {normalized} ":
            selects.append(statement)
            if committed:
                postcommit_selects.append(statement)

    engine = db_session.get_bind()
    with Session(engine) as production_session:
        assert production_session.expire_on_commit is True
        owner = production_session.get(User, user_id)
        event.listen(production_session, "after_commit", after_commit)
        event.listen(engine, "before_cursor_execute", observe)
        try:
            result = _invoke_real_delete(production_session, owner, entrypoint, ids[0], True, tasks)
            assert committed
            if entrypoint == "web":
                assert result == {"message": "File deleted successfully"}
                assert not provider_delete_calls  # Only enqueued until background execution.
                asyncio.run(tasks())
            else:
                assert result is True
        finally:
            event.remove(engine, "before_cursor_execute", observe)
            event.remove(production_session, "after_commit", after_commit)
    expected = [
        (project_id, "draft" if file_id == ids[-1] else "folder", file_id)
        for file_id in reversed(ids)
    ]
    assert [(call["project_id"], call["entity_type"], call["entity_id"]) for call in provider_delete_calls] == expected
    print(f"DEFAULT_EXPIRATION_FILE_SELECTS[{entrypoint},{count}]={len(selects)} POSTCOMMIT={len(postcommit_selects)}")
    assert len(selects) == 3
    assert postcommit_selects == []


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_default_expiration_nonrecursive_delete_has_two_selects_and_one_schedule(
    db_session, provider_delete_calls, entrypoint: EntryPoint,
):
    import asyncio

    from sqlmodel import Session

    user, project = _make_user_and_project(db_session, f"expiration-single-{entrypoint}")
    ids = _bulk_seed_chain(db_session, project.id, count=3, prefix=f"expiration-single-{entrypoint}")
    user_id, project_id = user.id, project.id
    tasks = BackgroundTasks()
    selects: list[str] = []

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith(("select", "with")) and f" from {File.__tablename__} " in f" {normalized} ":
            selects.append(statement)

    engine = db_session.get_bind()
    with Session(engine) as production_session:
        owner = production_session.get(User, user_id)
        event.listen(engine, "before_cursor_execute", observe)
        try:
            _invoke_real_delete(production_session, owner, entrypoint, ids[0], False, tasks)
            asyncio.run(tasks())
        finally:
            event.remove(engine, "before_cursor_execute", observe)
    assert [(call["project_id"], call["entity_type"], call["entity_id"]) for call in provider_delete_calls] == [
        (project_id, "folder", ids[0]),
    ]
    assert not db_session.get(File, ids[1], populate_existing=True).is_deleted
    print(f"DEFAULT_EXPIRATION_NONRECURSIVE_SELECTS[{entrypoint}]={len(selects)}")
    assert len(selects) == 2


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
def test_default_expiration_already_deleted_root_keeps_web_tool_contract(
    db_session, provider_delete_calls, entrypoint: EntryPoint,
):
    import asyncio

    from sqlmodel import Session

    user, project = _make_user_and_project(db_session, f"expiration-redelete-{entrypoint}")
    ids = _bulk_seed_chain(db_session, project.id, count=3, prefix=f"expiration-redelete-{entrypoint}")
    user_id, project_id = user.id, project.id
    db_session.execute(update(File).where(File.id == ids[0]).values(is_deleted=True, deleted_at=BASE_TIME))
    db_session.commit()
    tasks = BackgroundTasks()
    with Session(db_session.get_bind()) as production_session:
        owner = production_session.get(User, user_id)
        if entrypoint == "tool":
            with pytest.raises(ValueError, match="文件不存在或已删除"):
                _invoke_real_delete(production_session, owner, entrypoint, ids[0], True, tasks)
            assert not provider_delete_calls and not tasks.tasks
        else:
            _invoke_real_delete(production_session, owner, entrypoint, ids[0], True, tasks)
            asyncio.run(tasks())
            assert [(call["project_id"], call["entity_type"], call["entity_id"]) for call in provider_delete_calls] == [
                (project_id, "folder", file_id) for file_id in reversed(ids)
            ]


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
@pytest.mark.parametrize("recursive", [False, True])
def test_default_expiration_failed_commit_never_schedules_index_delete(
    db_session, provider_delete_calls, monkeypatch, entrypoint: EntryPoint, recursive: bool,
):
    from sqlmodel import Session

    user, project = _make_user_and_project(db_session, f"expiration-failure-{entrypoint}-{recursive}")
    ids = _bulk_seed_chain(db_session, project.id, count=3, prefix=f"expiration-failure-{entrypoint}-{recursive}")
    user_id = user.id
    tasks = BackgroundTasks()
    with Session(db_session.get_bind()) as production_session:
        owner = production_session.get(User, user_id)

        def fail_commit():
            raise RuntimeError("delete commit failed")

        monkeypatch.setattr(production_session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="delete commit failed"):
            _invoke_real_delete(production_session, owner, entrypoint, ids[0], recursive, tasks)
        assert not tasks.tasks and not provider_delete_calls
        production_session.rollback()
    assert all(not db_session.get(File, file_id, populate_existing=True).is_deleted for file_id in ids)


@pytest.mark.parametrize("entrypoint", ["web", "tool"])
@pytest.mark.parametrize("provider_fails", [False, True])
def test_default_expiration_direct_scheduling_keeps_best_effort_policy(
    db_session, monkeypatch, entrypoint: EntryPoint, provider_fails: bool,
):
    from sqlmodel import Session

    import services.llama_index as indexing

    user, project = _make_user_and_project(db_session, f"expiration-direct-{entrypoint}-{provider_fails}")
    ids = _bulk_seed_chain(db_session, project.id, count=3, prefix=f"expiration-direct-{entrypoint}-{provider_fails}")
    user_id, project_id = user.id, project.id
    calls: list[tuple[str, str, str]] = []
    committed = False

    def after_commit(_session):
        nonlocal committed
        assert not calls
        committed = True

    def schedule(**kwargs):
        assert committed
        calls.append((kwargs["project_id"], kwargs["entity_type"], kwargs["entity_id"]))
        if provider_fails:
            raise RuntimeError("index scheduling unavailable")

    monkeypatch.setattr(indexing, "schedule_index_delete", schedule)
    with Session(db_session.get_bind()) as production_session:
        event.listen(production_session, "after_commit", after_commit)
        try:
            result = _invoke_real_delete(
                production_session, production_session.get(User, user_id), entrypoint, ids[0], True, None,
            )
        finally:
            event.remove(production_session, "after_commit", after_commit)
    assert result == {"message": "File deleted successfully"} if entrypoint == "web" else result is True
    expected_ids = [ids[-1]] if provider_fails else list(reversed(ids))
    assert calls == [(project_id, "folder", file_id) for file_id in expected_ids]
    assert all(db_session.get(File, file_id, populate_existing=True).is_deleted for file_id in ids)
