"""Exercise the announcement migration on a disposable, previous-head database."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

import models  # noqa: F401

SERVER_DIR = Path(__file__).resolve().parents[2]
REVISION = "20261017_100000"
DOWN_REVISION = "20261014_100000"


def _alembic(db_url: str, *args: str) -> None:
    result = subprocess.run(
        [str(Path(sys.executable).parent / "alembic"), *args],
        cwd=SERVER_DIR,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_announcement_migration_round_trip_preserves_existing_tables(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'announcement-migration.db'}"
    engine = create_engine(db_url)
    # Start with the current metadata minus this new table, like a previous-head DB.
    SQLModel.metadata.create_all(engine)
    models.AnnouncementConfig.__table__.drop(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE migration_sentinel (value TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO migration_sentinel VALUES ('preserve-me')"))
    engine.dispose()
    _alembic(db_url, "stamp", DOWN_REVISION)
    _alembic(db_url, "upgrade", REVISION)

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("announcement_config")} == {
        "id", "title", "description", "action_label", "action_url", "enabled",
        "version", "updated_at", "updated_by",
    }
    assert inspector.get_foreign_keys("announcement_config")[0]["referred_table"] == "user"
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO announcement_config "
            "(id, title, description, enabled, version, updated_at) "
            "VALUES ('current', 'Guide update', 'Keep writing.', 1, 1, '2026-10-17 00:00:00')"
        ))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO announcement_config "
            "(id, title, description, enabled, version, updated_at) "
            "VALUES ('second', 'Invalid', 'No second config.', 0, 0, '2026-10-17 00:00:00')"
        ))
    engine.dispose()

    _alembic(db_url, "downgrade", DOWN_REVISION)
    engine = create_engine(db_url)
    assert "announcement_config" not in inspect(engine).get_table_names()
    assert "user" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT value FROM migration_sentinel")) == "preserve-me"
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == DOWN_REVISION
    engine.dispose()

    _alembic(db_url, "upgrade", REVISION)
    engine = create_engine(db_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM announcement_config")) == 0
        assert connection.scalar(text("SELECT value FROM migration_sentinel")) == "preserve-me"
    engine.dispose()
