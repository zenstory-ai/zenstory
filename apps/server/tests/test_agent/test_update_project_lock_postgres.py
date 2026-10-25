"""update_project_status must end the transaction that took the project row lock, on every path.

2026-10-20 production incident: a title-only update_project on a work the author had named
changed nothing, so the call returned without ending the transaction that took
``SELECT ... FOR UPDATE`` on the project row. A later write in the same request (then the tool's
artifact ledger insert, whose foreign key waits on that row) deadlocked the request, which held the
lock for over an hour. The tool-action ledger is gone, but the request session can stay open for
the whole agent run, so the lock must still not outlive the call: another connection must be able
to take the row lock with ``NOWAIT`` while the caller's session is still open.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

from agent.tools.file_ops.project import ProjectOperations
from models import AgentArtifactLedger, ChatSession, Project, User

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="isolated PostgreSQL URL not configured",
)

TABLES = [User.__table__, Project.__table__, ChatSession.__table__, AgentArtifactLedger.__table__]


@pytest.fixture
def pg_engine():
    engine = create_engine(
        os.environ["ZENSTORY_TEST_POSTGRES_URL"],
        connect_args={"options": "-c timezone=UTC -c statement_timeout=10000 -c lock_timeout=2000"},
    )
    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine, tables=TABLES)
        engine.dispose()


@pytest.mark.parametrize(
    ("name", "kwargs"),
    [
        ("作者自己起的名字", {"title": "九十三天"}),
        ("作者自己起的名字", {"title": "作者自己起的名字"}),
        ("作者自己起的名字", {"title": "《》"}),
        ("我的小说", {"title": "雾港来信"}),
        ("作者自己起的名字", {"summary": "新的简介"}),
    ],
    ids=["author-named", "unchanged", "invalid", "ai-renames-default", "status-field"],
)
def test_update_project_status_releases_the_row_lock_before_returning(pg_engine, name, kwargs):
    with Session(pg_engine) as setup:
        user = User(username="lock-owner", email="lock-owner@example.test", hashed_password="unused")
        setup.add(user)
        setup.flush()
        project = Project(name=name, owner_id=user.id, project_type="novel")
        setup.add(project)
        setup.commit()
        user_id, project_id = user.id, project.id

    # The caller's session stays open after the call, as a request-scoped session does.
    with Session(pg_engine) as request_session:
        ProjectOperations(request_session, user_id).update_project_status(
            project_id=project_id, **kwargs
        )

        # Reading attributes after the commit may autobegin a plain read; only the row lock matters.
        with pg_engine.connect() as other:
            locked = other.execute(
                text("SELECT id FROM project WHERE id = :id FOR UPDATE NOWAIT"), {"id": project_id}
            ).scalar_one()
            other.rollback()
        assert locked == project_id
