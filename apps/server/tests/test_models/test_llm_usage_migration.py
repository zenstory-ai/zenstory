"""SQLite round trip for the llm_usage_event migration."""

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
REVISION = "20261006_090000"
DOWN_REVISION = "20261005_180100"


def _alembic(db_url: str, *args: str) -> None:
    candidate = Path(sys.executable).parent / "alembic"
    executable = str(candidate) if candidate.exists() else shutil.which("alembic")
    assert executable, "alembic CLI not found"
    completed = subprocess.run(
        [executable, *args],
        cwd=SERVER_DIR,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.integration
@pytest.mark.slow
def test_llm_usage_event_migration_round_trip(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'llm-usage-migration.db'}"
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    _alembic(db_url, "stamp", "head")
    _alembic(db_url, "downgrade", DOWN_REVISION)
    engine = create_engine(db_url)
    assert "llm_usage_event" not in inspect(engine).get_table_names()
    engine.dispose()

    _alembic(db_url, "upgrade", REVISION)
    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "llm_usage_event" in inspector.get_table_names()
    indexes = {index["name"]: index["column_names"] for index in inspector.get_indexes("llm_usage_event")}
    assert indexes["ix_llm_usage_event_occurred_at"] == ["occurred_at"]
    assert indexes["ix_llm_usage_event_user_id_occurred_at"] == ["user_id", "occurred_at"]
    assert indexes["ix_llm_usage_event_user_id"] == ["user_id"]
    columns = {column["name"]: column for column in inspector.get_columns("llm_usage_event")}
    assert columns["user_id"]["nullable"] is False
    assert columns["project_id"]["nullable"] is True
    assert columns["correlation_id"]["nullable"] is True
    for name in ("cache_hit_tokens", "cache_miss_tokens", "output_tokens", "is_backfilled"):
        assert columns[name]["default"] is not None, name
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
        # A raw insert that names none of the defaulted columns gets zeros and false
        # (SQLite leaves foreign keys unenforced here, so no user row is needed).
        connection.execute(
            text(
                "INSERT INTO llm_usage_event (id, user_id, source, model, price_band, pricing_version, occurred_at) "
                "VALUES ('e1', 'u1', 'agent', 'deepseek-flash', 'peak', 'v', '2026-10-06 02:00:00')"
            )
        )
        row = connection.execute(
            text("SELECT cache_hit_tokens, cache_miss_tokens, output_tokens, is_backfilled FROM llm_usage_event")
        ).one()
        assert tuple(row) == (0, 0, 0, 0)
    engine.dispose()
