"""ChatSession 的「每个 user+project 只有一个 active 会话」partial unique index 由模型声明，
与 20260307_230000 迁移建的索引一致（create_all 建的测试 / 新库与 prod 相同）。"""

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex
from sqlmodel import Session, SQLModel

from models import ChatSession, Project, User
from tests.test_models.test_payment_order_migration import _alembic

REVISION = "20260307_230000_add_unique_active_chat_session_index"
PREVIOUS_REVISIONS = ("20260306_001000_add_user_feedback_table", "20260306_120000_add_compaction_checkpoint_index")

INDEX_NAME = "uq_chat_session_user_project_active"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/20260307_230000_add_unique_active_chat_session_index.py"
)


def _normalize(sql: str) -> str:
    sql = re.sub(r"\s+", " ", sql).strip().lower()
    return sql.replace("if not exists ", "")


def _index_sql(engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :name"),
            {"name": INDEX_NAME},
        ).scalar()


@pytest.mark.integration
def test_create_all_builds_the_same_index_as_the_migration_on_sqlite(tmp_path):
    from_model = create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    SQLModel.metadata.create_all(from_model)
    model_sql = _index_sql(from_model)
    assert model_sql is not None

    migration_url = f"sqlite:///{tmp_path / 'migration.db'}"
    from_migration = create_engine(migration_url)
    SQLModel.metadata.create_all(from_migration)
    with from_migration.begin() as conn:
        conn.execute(text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))
    _alembic(migration_url, "stamp", *PREVIOUS_REVISIONS)
    _alembic(migration_url, "upgrade", REVISION)
    migration_sql = _index_sql(from_migration)

    assert _normalize(model_sql) == _normalize(migration_sql)


@pytest.mark.unit
def test_model_index_compiles_to_the_migration_statement_on_postgresql():
    index = next(index for index in ChatSession.__table__.indexes if index.name == INDEX_NAME)
    compiled = str(CreateIndex(index).compile(dialect=postgresql.dialect()))

    assert _normalize(compiled) == _normalize(
        "CREATE UNIQUE INDEX uq_chat_session_user_project_active "
        "ON chat_session (user_id, project_id) WHERE is_active = true"
    )
    assert _normalize(compiled) in _normalize(MIGRATION.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_second_active_session_for_the_same_project_is_rejected(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(username="index-writer", email="index-writer@example.com", hashed_password="synthetic")
        project = Project(name="雾港", owner_id=user.id)
        session.add_all([user, project])
        session.commit()
        session.add(ChatSession(user_id=user.id, project_id=project.id, is_active=False))
        session.add(ChatSession(user_id=user.id, project_id=project.id, is_active=True))
        session.commit()
        session.add(ChatSession(user_id=user.id, project_id=project.id, is_active=True))
        with pytest.raises(IntegrityError):
            session.commit()
