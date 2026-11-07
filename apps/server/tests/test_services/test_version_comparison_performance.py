"""Comparison payload fidelity, bounded duplicate work and version replay contracts."""

import difflib
import time

import pytest
from sqlalchemy import event
from sqlmodel import select

from models import File, FileVersion, Project, User
from services.features.file_version_service import FileVersionService, get_file_version_service


def _seed(session, contents, *, bases=True):
    user = User(username="comparison-work", email="comparison-work@example.test", hashed_password="unused")
    project = Project(name="Comparison work", owner_id=user.id)
    file = File(project_id=project.id, title="Draft", file_type="draft", content=contents[-1])
    session.add_all([user, project, file])
    session.flush()
    service = FileVersionService()
    versions = []
    for index, content in enumerate(contents):
        base = bases or index == 0
        version = FileVersion(file_id=file.id, project_id=project.id, version_number=index + 1,
                              is_base_version=base, content=content if base else service._create_diff(contents[index - 1], content),
                              word_count=index + 3, char_count=len(content))
        versions.append(version)
        session.add(version)
    session.commit()
    return file, versions, service


@pytest.mark.parametrize("bases,left,right,limit", [(True, 1, 2, 2), (False, 1, 2, 4), (False, 2, 3, 6), (False, 1, 1, 1), (False, 2, 2, 3), (True, 2, 1, 2), (False, 3, 2, 6)])
def test_comparison_reuses_loaded_targets_without_changing_payload(db_session, bases, left, right, limit):
    contents = ["Alpha\nRepeat\nRepeat\n", "Alpha\nBeta\nRepeat\n", "Alpha\nBeta\nLast\n"]
    file, versions, service = _seed(db_session, contents, bases=bases)
    statements = []

    def record(_conn, _cursor, sql, *_args):
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = service.compare_versions(db_session, file.id, left, right)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    print(f"comparison bases={bases} v{left}/v{right}: {len(statements)} SELECTs")
    a, b = contents[left - 1], contents[right - 1]
    assert result["unified_diff"] == "".join(difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True), fromfile=f"v{left}", tofile=f"v{right}", lineterm=""))
    assert result["html_diff"] == service._generate_html_diff(a, b)
    added, removed = service._calculate_diff_stats(a, b)
    assert result["stats"] == {"lines_added": added, "lines_removed": removed, "word_diff": versions[right - 1].word_count - versions[left - 1].word_count}
    assert result["version1"]["created_at"] == versions[left - 1].created_at.isoformat()
    assert result["version2"]["created_at"] == versions[right - 1].created_at.isoformat()
    assert len(statements) <= limit


@pytest.mark.parametrize("before,after", [("", ""), ("", "Added\n"), ("Removed\n", ""), ("Same\n", "Same"), ("甲\n乙\n", "甲\n改\n"), ("A\nB\nA\n", "B\nA\nB\n")])
def test_comparison_reuses_structured_diff_for_stats(db_session, monkeypatch, before, after):
    file, _, service = _seed(db_session, [before, after])
    original = difflib.SequenceMatcher
    calls = []

    def count(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(difflib, "SequenceMatcher", count)
    result = service.compare_versions(db_session, file.id, 1, 2)
    assert len(calls) <= 2, "comparison recomputes the structured diff solely for stats"
    assert result["stats"]["lines_added"] == sum(line["type"] == "added" for line in result["html_diff"])
    assert result["stats"]["lines_removed"] == sum(line["type"] == "removed" for line in result["html_diff"])


@pytest.mark.parametrize("left,right,missing", [(9, 1, 9), (1, 9, 9), (9, 9, 9)])
def test_comparison_preserves_missing_target_value_error(db_session, left, right, missing):
    file, _, service = _seed(db_session, ["Original\n"])
    with pytest.raises(ValueError, match=f"Version {missing} not found for file {file.id}"):
        service.compare_versions(db_session, file.id, left, right)


@pytest.mark.parametrize("lines", [200, 5000])
def test_representative_comparison_measurement_without_latency_gate(db_session, lines):
    before = "".join(f"段落{i:05d} 这是稳定叙事正文，每一行具有唯一编号。\n" for i in range(lines))
    after = before.replace(f"段落{lines // 2:05d}", "修改后的段落", 1)
    file, _, service = _seed(db_session, [before, after])
    durations = []
    for _ in range(5):
        start = time.perf_counter()
        result = service.compare_versions(db_session, file.id, 1, 2)
        durations.append((time.perf_counter() - start) * 1000)
    print(f"comparison lines={lines} chars={len(before)} median_ms={sorted(durations)[2]:.3f} structured_rows={len(result['html_diff'])}")
    assert result["stats"]["lines_added"] == result["stats"]["lines_removed"] == 1


def _seed_replay_files(session, count, delta):
    user = User(username="version-replay", email="version-replay@example.test", hashed_password="unused")
    project = Project(name="Version replay", owner_id=user.id)
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


def test_reconstruction_respects_target_and_nearest_base(db_session):
    _, files = _seed_replay_files(db_session, 1, False)
    file = files[0]
    service = get_file_version_service()
    contents = [file.content, "Earlier delta\nSame\n", "New base\nSame\n", "Target delta\nSame\n", "Later delta\nSame\n"]
    for number, content in enumerate(contents[1:], start=2):
        service.create_version(db_session, file.id, content, change_source="system", force_base=number == 3)
    assert service.get_content_at_version(db_session, file.id, 2) == contents[1]
    assert service.get_content_at_version(db_session, file.id, 4) == contents[3]
    assert service.get_content_at_version(db_session, file.id, 5) == contents[4]


def test_reconstruction_without_base_keeps_legacy_empty_start(db_session):
    _, files = _seed_replay_files(db_session, 1, False)
    file = files[0]
    service = get_file_version_service()
    version = db_session.exec(select(FileVersion).where(FileVersion.file_id == file.id)).one()
    version.is_base_version = False
    version.content = service._create_diff("", "Legacy delta\n")
    db_session.add(version)
    db_session.commit()
    assert service.get_content_at_version(db_session, file.id, 1) == "Legacy delta\n"


def test_mixed_selected_targets_keep_individual_base_and_upper_bounds(db_session):
    _, files = _seed_replay_files(db_session, 4, False)
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
    _, files = _seed_replay_files(db_session, 1, False)
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
