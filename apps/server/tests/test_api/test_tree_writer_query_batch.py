"""Bounded reorder query budget; keeps partial sibling semantics."""

from sqlalchemy import event

from api import files as files_api
from models import File, Project, User


def test_partial_reorder_loads_requested_rows_in_one_query(db_session):
    owner = User(
        username="reorder-query-owner", email="reorder-query-owner@example.test",
        hashed_password="unused", email_verified=True,
    )
    project = Project(name="Reorder query", owner_id=owner.id)
    rows = [File(project_id=project.id, title=f"Item {index}", order=100 + index) for index in range(50)]
    unlisted = File(project_id=project.id, title="Unlisted", order=999)
    db_session.add_all([owner, project, *rows, unlisted])
    db_session.commit()
    user_id, project_id = owner.id, project.id
    ids = [row.id for row in reversed(rows)]
    unlisted_id = unlisted.id
    db_session.expire_all()
    owner = db_session.get(User, user_id)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        sql = " ".join(statement.lower().split())
        if sql.startswith("select") and f" from {File.__tablename__} " in f" {sql} ":
            statements.append(sql)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = files_api.reorder_files(
            project_id, files_api.ReorderFilesRequest(ordered_ids=ids),
            current_user=owner, session=db_session,
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert result["count"] == 50
    assert len(statements) == 1, f"per-file SELECT count: {len(statements)}"
    assert db_session.get(File, unlisted_id).order == 999
