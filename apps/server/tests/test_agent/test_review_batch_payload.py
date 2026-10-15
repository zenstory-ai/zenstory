"""A batch review must not silently lose the chapters between its head and tail."""

from datetime import datetime

import pytest

from agent.graph.writing_graph import (
    REVIEW_PAYLOAD_MAX_CHARS,
    _collect_review_payload_text,
    _format_review_payload,
)
from agent.tools.mcp_tools import ToolContext
from models import File, Project, User


@pytest.fixture
def review_project(db_session):
    user = User(email="batch-review@example.com", username="batch-review", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    project = Project(name="Batch review", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    ToolContext.set_context(session=db_session, user_id=user.id, project_id=project.id, session_id="batch")
    try:
        yield project
    finally:
        ToolContext.clear_context()


@pytest.mark.unit
def test_large_batch_keeps_each_current_file_in_the_final_review_input(review_project, db_session):
    files = []
    version = datetime(2026, 10, 15, 1, 2, 3, 456789)
    for number in range(1, 6):
        body = f"本章开头标记{number}\n" + (f"第{number}章的雨一直没有停。" * 350) + f"\n本章结尾标记{number}"
        file = File(project_id=review_project.id, title=f"第{number}章", file_type="draft", content=body)
        file.updated_at = version
        db_session.add(file)
        files.append(file)
    db_session.commit()

    payload = _format_review_payload(_collect_review_payload_text([file.id for file in files]) or "")

    for number, file in enumerate(files, 1):
        assert f"本章开头标记{number}" in payload
        assert f"本章结尾标记{number}" in payload
        assert file.id in payload
        assert file.updated_at.isoformat() in payload
    assert len(payload) <= REVIEW_PAYLOAD_MAX_CHARS
    assert "query_files" in payload
    assert "省略" in payload


@pytest.mark.unit
def test_batch_budget_keeps_local_changes_instead_of_only_old_head_and_tail(review_project, db_session):
    files = []
    for number in range(1, 4):
        before = (f"旧开头{number}。" * 500) + "\n旧中段。\n" + (f"旧结尾{number}。" * 500)
        content = before.replace("旧中段。", f"本轮唯一修改{number}。")
        file = File(project_id=review_project.id, title=f"第{number}章", file_type="draft", content=content)
        db_session.add(file)
        files.append(file)
        ToolContext.remember_pre_write_content(file.id, before)
    db_session.commit()

    payload = _format_review_payload(_collect_review_payload_text([file.id for file in files]) or "")

    for number, file in enumerate(files, 1):
        assert f"本轮唯一修改{number}。" in payload
        assert file.id in payload
    assert len(payload) <= REVIEW_PAYLOAD_MAX_CHARS
    assert "没有附上" in payload


@pytest.mark.unit
def test_review_truncation_includes_its_omission_marker_in_the_budget():
    payload = _format_review_payload("甲" * 12000, max_chars=1000)
    assert len(payload) <= 1000
    assert "省略" in payload
