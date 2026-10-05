"""SQLite round-trip coverage for the payment order migration."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

import models  # noqa: F401

SERVER_DIR = Path(__file__).resolve().parents[2]
REVISION = "20261005_120000"
DOWN_REVISION = "c3d5e7f9a1b2"


def _alembic_executable() -> str:
    candidate = Path(sys.executable).parent / "alembic"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("alembic")
    assert found, "alembic CLI not found"
    return found


def _alembic(db_url: str, *args: str) -> None:
    completed = subprocess.run(
        [_alembic_executable(), *args],
        cwd=SERVER_DIR,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def _version(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


@pytest.mark.integration
@pytest.mark.slow
def test_payment_order_migration_round_trip_preserves_other_tables(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'payment-migration.db'}"
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    _alembic(db_url, "stamp", REVISION)
    _alembic(db_url, "downgrade", "-1")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "payment_order" not in inspector.get_table_names()
    assert "user" in inspector.get_table_names()
    assert _version(engine) == DOWN_REVISION
    engine.dispose()

    _alembic(db_url, "upgrade", "head")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "payment_order" in inspector.get_table_names()
    indexes = {index["name"]: index for index in inspector.get_indexes("payment_order")}
    assert indexes["ix_payment_order_out_trade_no"]["unique"] == 1
    assert indexes["ix_payment_order_trade_no"]["unique"] == 1
    assert "ix_payment_order_status_created_at" in indexes
    assert "user" in inspector.get_table_names()
    assert _version(engine) == REVISION
    engine.dispose()
