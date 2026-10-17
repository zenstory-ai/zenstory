"""Regression coverage for the keyword-search ORM column projection."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import event
from sqlmodel import Session

from models import File, Project, User
from services.infra.file_search_service import FileSearchService


def _capture_search_selects(session: Session, search) -> tuple[list, list[str]]:
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select ") and " from file " in normalized:
            statements.append(normalized)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        results = search()
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    return results, statements


def test_keyword_search_projects_only_consumed_columns_without_n_plus_one(db_session: Session):
    owner = User(
        email="search-projection-owner@example.com",
        username="search_projection_owner",
        hashed_password="unused",
    )
    other = User(
        email="search-projection-other@example.com",
        username="search_projection_other",
        hashed_password="unused",
    )
    db_session.add_all([owner, other])
    db_session.flush()
    project = Project(name="Projected search", owner_id=owner.id)
    foreign_project = Project(name="Foreign search", owner_id=other.id)
    db_session.add_all([project, foreign_project])
    db_session.flush()

    base_time = datetime(2026, 1, 1)
    large_metadata = "x" * (64 * 1024)
    searchable: list[File] = []
    for index in range(24):
        content = f"needle body {index}\n" + ("content " * 100)
        file = File(
            project_id=project.id,
            title=f"needle draft {index}",
            content=content,
            file_type="draft",
            file_metadata=large_metadata,
            parent_id=None,
            order=index,
            updated_at=base_time + timedelta(minutes=index),
        )
        searchable.append(file)
        db_session.add(file)

    db_session.add_all(
        [
            File(
                project_id=project.id,
                title="needle deleted",
                content="needle private",
                file_type="draft",
                file_metadata=large_metadata,
                is_deleted=True,
                updated_at=base_time + timedelta(days=1),
            ),
            File(
                project_id=project.id,
                title="needle folder",
                content="needle private",
                file_type="folder",
                file_metadata=large_metadata,
                updated_at=base_time + timedelta(days=1),
            ),
            File(
                project_id=project.id,
                title="needle lore",
                content="needle private",
                file_type="lore",
                file_metadata=large_metadata,
                updated_at=base_time + timedelta(days=1),
            ),
            File(
                project_id=foreign_project.id,
                title="needle foreign",
                content="needle private",
                file_type="draft",
                file_metadata=large_metadata,
                updated_at=base_time + timedelta(days=1),
            ),
        ]
    )
    db_session.commit()

    project_id = project.id
    expected_ids = [file.id for file in reversed(searchable)]
    expected_full_content = {file.id: file.content for file in searchable}
    db_session.expunge_all()

    service = FileSearchService()
    preview_results, preview_statements = _capture_search_selects(
        db_session,
        lambda: service.search(
            project_id=project_id,
            query="needle",
            top_k=50,
            entity_types=["draft"],
            include_content=False,
            session=db_session,
        ),
    )

    assert [result.entity_id for result in preview_results] == expected_ids
    assert all(
        result.content == expected_full_content[result.entity_id][:500]
        for result in preview_results
    )
    assert len(preview_statements) == 1
    selected_clause = preview_statements[0].split(" from file ", maxsplit=1)[0]
    assert "file.id" in selected_clause
    assert "file.title" in selected_clause
    assert "file.content" in selected_clause
    assert "file.file_type" in selected_clause
    assert "file.updated_at" in selected_clause
    assert "file.file_metadata" not in selected_clause
    assert "file.parent_id" not in selected_clause
    assert "file.order" not in selected_clause
    assert "file.created_at" not in selected_clause
    assert "file.deleted_at" not in selected_clause

    db_session.expunge_all()
    full_results, full_statements = _capture_search_selects(
        db_session,
        lambda: service.search(
            project_id=project_id,
            query="needle",
            top_k=50,
            entity_types=["draft"],
            include_content=True,
            session=db_session,
        ),
    )

    assert [result.entity_id for result in full_results] == expected_ids
    assert all(
        result.content == expected_full_content[result.entity_id]
        for result in full_results
    )
    assert [
        (result.entity_id, result.title, result.score, result.snippet, result.line_start)
        for result in full_results
    ] == [
        (result.entity_id, result.title, result.score, result.snippet, result.line_start)
        for result in preview_results
    ]
    assert len(full_statements) == 1
