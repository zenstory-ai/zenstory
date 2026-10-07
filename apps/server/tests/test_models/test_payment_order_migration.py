"""SQLite round-trip coverage for the payment order migration."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, SQLModel, select

import models  # noqa: F401
from models.subscription import SubscriptionPlan

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

    _alembic(db_url, "stamp", "head")
    _alembic(db_url, "downgrade", DOWN_REVISION)

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "payment_order" not in inspector.get_table_names()
    assert "user" in inspector.get_table_names()
    assert _version(engine) == DOWN_REVISION
    engine.dispose()

    _alembic(db_url, "upgrade", REVISION)

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


UPGRADE_SOURCE_REVISION = "20261005_180000"
PRO_BACKFILL_REVISION = "20261005_180100"
# Production pro features before the backfill (no custom_skills / inspiration keys).
PRODUCTION_PRO_FEATURES = {
    "max_projects": -1,
    "custom_prompts": True,
    "export_formats": ["txt"],
    "material_uploads": 5,
    "context_window_tokens": 16384,
    "file_versions_per_file": 100,
    "material_decompositions": 5,
    "ai_conversations_per_day": -1,
    "materials_library_access": True,
}


def _pro_features(engine) -> dict:
    with Session(engine) as session:
        plan = session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == "pro")).one()
        return dict(plan.features)


@pytest.mark.integration
@pytest.mark.slow
def test_upgrade_source_and_pro_backfill_round_trip(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'payment-hardening.db'}"
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()
    _alembic(db_url, "stamp", "head")
    _alembic(db_url, "downgrade", REVISION)

    engine = create_engine(db_url)
    columns = {column["name"] for column in inspect(engine).get_columns("payment_order")}
    assert "upgrade_source" not in columns
    with Session(engine) as session:
        session.add(
            SubscriptionPlan(
                name="pro",
                display_name="专业版",
                price_monthly_cents=4900,
                price_yearly_cents=39900,
                # An operator-set value must survive the backfill.
                features={**PRODUCTION_PRO_FEATURES, "inspiration_copies_monthly": 250},
            )
        )
        session.add(
            SubscriptionPlan(
                name="free",
                display_name="免费试用",
                price_monthly_cents=0,
                price_yearly_cents=0,
                features={"max_projects": 3},
            )
        )
        session.commit()
    engine.dispose()

    _alembic(db_url, "upgrade", PRO_BACKFILL_REVISION)
    engine = create_engine(db_url)
    columns = {column["name"] for column in inspect(engine).get_columns("payment_order")}
    assert "upgrade_source" in columns
    assert _version(engine) == PRO_BACKFILL_REVISION
    upgraded = _pro_features(engine)
    assert upgraded == {
        **PRODUCTION_PRO_FEATURES,
        "custom_skills": 20,
        "inspiration_copies_monthly": 250,
    }
    with Session(engine) as session:
        free = session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == "free")).one()
        assert free.features == {"max_projects": 3}
    engine.dispose()

    # Downgrade keeps the data (see migration docstring); re-upgrading is a no-op.
    _alembic(db_url, "downgrade", UPGRADE_SOURCE_REVISION)
    _alembic(db_url, "downgrade", REVISION)
    _alembic(db_url, "upgrade", "head")
    engine = create_engine(db_url)
    assert _pro_features(engine) == upgraded
    engine.dispose()
