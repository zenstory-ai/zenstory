"""Snapshot query-count baselines and existing reconstruction contracts."""

import json

import pytest
from sqlalchemy import event
from sqlmodel import select

from models import File, FileVersion, Project, User
from services.features.file_version_service import get_file_version_service
from services.features.snapshot_service import VersionService


def _seed(session, count, delta):
    user = User(username="snapshot-query", email="snapshot-query@example.test", hashed_password="unused")
    project = Project(name="Snapshot queries", owner_id=user.id)
    session.add_all([user, project])
    session.flush()
    service = get_file_version_service()
    files = []
    for index in range(count):
        file = File(project_id=project.id, title=f"File {index}", file_type="draft", content=f"Base {index}\nSame\n")
        session.add(file)
        session.flush()
        service.create_initial_version(session, file)
        if delta:
            file.content = f"Changed {index}\nSame\n"
            session.add(file)
            session.flush()
            service.create_version(session, file.id, file.content, change_source="system", commit=False)
        files.append(file)
    session.commit()
    return project, files


@pytest.mark.parametrize("delta", [False, True])
def test_snapshot_queries_do_not_grow_per_file_for_existing_history(db_session, delta):
    project, files = _seed(db_session, 20, delta)
    statements = []
    engine = db_session.get_bind()

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        snapshot = VersionService().create_snapshot(db_session, project.id)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    print(f"20-file snapshot delta={delta}: {len(statements)} SQL statements")
    data = json.loads(snapshot.data)
    assert len(data["file_versions"]) == 20
    for file in files:
        ref = next(item for item in data["file_versions"] if item["file_id"] == file.id)
        assert get_file_version_service().get_content_at_version(db_session, file.id, ref["version_number"]) == file.content
        assert len(db_session.exec(select(FileVersion).where(FileVersion.file_id == file.id)).all()) == (2 if delta else 1)
    assert len(statements) <= (8 if delta else 6), "snapshot reconstructs each already-loaded version with separate SQL"


def test_reconstruction_respects_target_and_nearest_base(db_session):
    _, files = _seed(db_session, 1, False)
    file = files[0]
    service = get_file_version_service()
    contents = [file.content, "Earlier delta\nSame\n", "New base\nSame\n", "Target delta\nSame\n", "Later delta\nSame\n"]
    for number, content in enumerate(contents[1:], start=2):
        service.create_version(db_session, file.id, content, change_source="system", force_base=number == 3)
    assert service.get_content_at_version(db_session, file.id, 2) == contents[1]
    assert service.get_content_at_version(db_session, file.id, 4) == contents[3]
    assert service.get_content_at_version(db_session, file.id, 5) == contents[4]


def test_reconstruction_without_base_keeps_legacy_empty_start(db_session):
    _, files = _seed(db_session, 1, False)
    file = files[0]
    service = get_file_version_service()
    version = db_session.exec(select(FileVersion).where(FileVersion.file_id == file.id)).one()
    version.is_base_version = False
    version.content = service._create_diff("", "Legacy delta\n")
    db_session.add(version)
    db_session.commit()
    assert service.get_content_at_version(db_session, file.id, 1) == "Legacy delta\n"


def test_mixed_selected_targets_keep_individual_base_and_upper_bounds(db_session):
    _, files = _seed(db_session, 4, False)
    service = get_file_version_service()
    targets = {}
    expected = {}
    for index, file in enumerate(files[:2]):
        for number in range(2, 6):
            content = f"File {index}, version {number}\n"
            version = service.create_version(db_session, file.id, content, change_source="system", force_base=number == (3 if index == 0 else 4))
            if number == (2 if index == 0 else 5):
                targets[file.id] = version
                expected[file.id] = content
    base = files[2]
    targets[base.id] = service.get_latest_version(db_session, base.id)
    expected[base.id] = base.content
    legacy = files[3]
    version = service.get_latest_version(db_session, legacy.id)
    version.is_base_version = False
    version.content = service._create_diff("", "Legacy 无基线\n")
    db_session.add(version)
    db_session.commit()
    targets[legacy.id] = version
    expected[legacy.id] = "Legacy 无基线\n"
    assert {file_id: service.get_content_at_version(db_session, file_id, target.version_number) for file_id, target in targets.items()} == expected
    assert service._get_contents_for_versions(db_session, targets) == expected


def test_loaded_base_and_empty_mapping_reconstruction_need_no_query(db_session):
    _, files = _seed(db_session, 1, False)
    service = get_file_version_service()
    file = files[0]
    target = service.get_latest_version(db_session, file.id)
    statements = []
    engine = db_session.get_bind()

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert service._get_contents_for_versions(db_session, {}) == {}
        assert service._get_contents_for_versions(db_session, {file.id: target}) == {file.id: file.content}
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert statements == []


def test_snapshot_reconstructs_all_files_across_multiple_query_chunks(db_session):
    project, files = _seed(db_session, 401, True)
    statements = []
    engine = db_session.get_bind()

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        snapshot = VersionService().create_snapshot(db_session, project.id)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    data = json.loads(snapshot.data)
    assert {item["file_id"] for item in data["file_versions"]} == {file.id for file in files}
    assert all(item["version_number"] == 2 for item in data["file_versions"])
    print(f"401-file delta snapshot: {len(statements)} SQL statements")
    assert len(statements) <= 12, "200-file reconstruction chunking must not become a file cap or per-file query"


def test_snapshot_paging_has_deterministic_id_tiebreak(db_session):
    from datetime import datetime

    from models import Snapshot

    project, _ = _seed(db_session, 1, False)
    ids = ["tie-a", "tie-c", "tie-b", "tie-d"]
    for snapshot_id in ids:
        db_session.add(Snapshot(id=snapshot_id, project_id=project.id, created_at=datetime(2026, 10, 6), data="{}"))
    db_session.commit()
    service = VersionService()
    page_one = service.get_snapshots(db_session, project.id, limit=2)
    page_two = service.get_snapshots(db_session, project.id, limit=2, offset=2)
    assert [snapshot.id for snapshot in page_one + page_two] == sorted(ids, reverse=True)
