"""Exercise the user_preference migration on a disposable, previous-head database."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

import models  # noqa: F401

SERVER_DIR = Path(__file__).resolve().parents[2]
REVISION = "20261021_100000"
DOWN_REVISION = "20261017_100000"


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
def test_user_preference_migration_is_additive_and_reversible(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'user-preference-migration.db'}"
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    models.UserPreference.__table__.drop(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE migration_sentinel (value TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO migration_sentinel VALUES ('preserve-me')"))
    engine.dispose()
    _alembic(db_url, "stamp", DOWN_REVISION)
    _alembic(db_url, "upgrade", REVISION)

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("user_preference")} == {
        "user_id", "ui_language", "updated_at",
    }
    assert inspector.get_foreign_keys("user_preference")[0]["referred_table"] == "user"
    engine.dispose()

    _alembic(db_url, "downgrade", DOWN_REVISION)
    engine = create_engine(db_url)
    assert "user_preference" not in inspect(engine).get_table_names()
    with engine.begin() as connection:
        assert connection.execute(text("SELECT value FROM migration_sentinel")).scalar_one() == "preserve-me"
    engine.dispose()
