"""The additive recovery migration preserves manuscripts and matches model metadata."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from models import FileDeletionBatch, FileEditOperation

SERVER_DIR = Path(__file__).resolve().parents[2]
PREVIOUS = "20261106_120000"
REVISION = "20261119_040000"
MODELS = (FileEditOperation, FileDeletionBatch)


def _alembic(url, *args):
    result = subprocess.run(
        [str(Path(sys.executable).parent / "alembic"), *args],
        cwd=SERVER_DIR,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_recovery_migration_round_trip_matches_models_and_preserves_data(tmp_path):
    url = f"sqlite:///{tmp_path / 'recovery-migration.db'}"
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    for model in MODELS:
        model.__table__.drop(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE migration_sentinel (body TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO migration_sentinel VALUES ('作者原稿。')"))
    engine.dispose()
    _alembic(url, "stamp", PREVIOUS)
    _alembic(url, "upgrade", REVISION)
    engine = create_engine(url)
    inspector = inspect(engine)
    for model in MODELS:
        table = model.__table__
        assert {column["name"] for column in inspector.get_columns(table.name)} == set(table.columns.keys())
        assert {index["name"] for index in inspector.get_indexes(table.name)} == {index.name for index in table.indexes}
        assert {
            (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
            for fk in inspector.get_foreign_keys(table.name)
        } == {
            ((fk.parent.name,), fk.column.table.name, (fk.column.name,))
            for fk in table.foreign_keys
        }
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT body FROM migration_sentinel")) == "作者原稿。"
    engine.dispose()
    _alembic(url, "downgrade", PREVIOUS)
    engine = create_engine(url)
    assert all(model.__table__.name not in inspect(engine).get_table_names() for model in MODELS)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT body FROM migration_sentinel")) == "作者原稿。"
    engine.dispose()
    _alembic(url, "upgrade", REVISION)
