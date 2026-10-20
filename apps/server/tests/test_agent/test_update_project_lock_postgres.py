"""update_project must not keep its project row lock while its own artifact ledger is written.

2026-10-20 production incident: a title-only update_project on a work the author had named
changed nothing, so the call returned without ending the transaction that took
``SELECT ... FOR UPDATE`` on the project row. The tool then recorded its artifact ledger in a
separate session; that insert's foreign key waits on the same row, so the request deadlocked
itself and held the lock for over an hour.
"""

import json
import os

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from agent.tools import mcp_tools
from models import AgentArtifactLedger, ChatSession, Project, User

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="isolated PostgreSQL URL not configured",
)

TABLES = [User.__table__, Project.__table__, ChatSession.__table__, AgentArtifactLedger.__table__]


@pytest.fixture
def pg_engine():
    # A short lock_timeout turns the old self-deadlock into a failed ledger insert instead of a hang.
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title",
    ["九十三天", "作者自己起的名字", "《》"],
    ids=["author-named", "unchanged", "invalid"],
)
async def test_update_project_that_changes_nothing_still_records_its_ledger(pg_engine, title):
    with Session(pg_engine) as session:
        user = User(username="lock-owner", email="lock-owner@example.test", hashed_password="unused")
        session.add(user)
        session.flush()
        project = Project(name="作者自己起的名字", owner_id=user.id, project_type="novel")
        session.add(project)
        session.commit()
        user_id, project_id = user.id, project.id

    mcp_tools.ToolContext.set_context(
        None, user_id, project_id, None, create_session_func=lambda: Session(pg_engine),
    )
    try:
        raw = await mcp_tools.update_project({"title": title})
    finally:
        mcp_tools.ToolContext.clear_context()

    payload = json.loads(raw["content"][0]["text"])
    assert payload["status"] == "success"
    with Session(pg_engine) as verify:
        recorded = verify.exec(
            select(AgentArtifactLedger).where(
                AgentArtifactLedger.project_id == project_id,
                AgentArtifactLedger.tool_name == "update_project",
            )
        ).all()
        assert len(recorded) == 1
        assert verify.get(Project, project_id).name == "作者自己起的名字"
