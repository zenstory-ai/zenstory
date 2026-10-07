"""Comparison payload fidelity and bounded duplicate work."""

import difflib
import time

import pytest
from sqlalchemy import event

from models import File, FileVersion, Project, User
from services.features.file_version_service import FileVersionService


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
