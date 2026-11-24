"""SQLite round-trip coverage for the payment order migration."""

import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
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
    with engine.connect() as conn:
        raw = conn.execute(
            text("SELECT features FROM subscription_plan WHERE name = 'pro'")
        ).scalar_one()
        return json.loads(raw) if isinstance(raw, str) else dict(raw)


def _insert_historical_plan(engine, *, name: str, display_name: str, monthly: int, yearly: int, features: dict) -> None:
    now = datetime.utcnow().isoformat(sep=" ")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO subscription_plan "
                "(id, name, display_name, price_monthly_cents, price_yearly_cents, features, is_active, created_at, updated_at) "
                "VALUES (:id, :name, :display_name, :monthly, :yearly, :features, 1, :created_at, :updated_at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "display_name": display_name,
                "monthly": monthly,
                "yearly": yearly,
                "features": json.dumps(features),
                "created_at": now,
                "updated_at": now,
            },
        )


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
    _insert_historical_plan(
        engine,
        name="pro",
        display_name="专业版",
        monthly=4900,
        yearly=39900,
        features={**PRODUCTION_PRO_FEATURES, "inspiration_copies_monthly": 250},
    )
    _insert_historical_plan(
        engine,
        name="free",
        display_name="免费试用",
        monthly=0,
        yearly=0,
        features={"max_projects": 3},
    )
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
    with engine.connect() as conn:
        raw_free = conn.execute(
            text("SELECT features FROM subscription_plan WHERE name = 'free'")
        ).scalar_one()
        assert (json.loads(raw_free) if isinstance(raw_free, str) else raw_free) == {"max_projects": 3}
    engine.dispose()

    # Freeze this round trip at the historical backfill. Later revisions may
    # intentionally normalize the plan JSON and have their own migration tests.
    _alembic(db_url, "downgrade", UPGRADE_SOURCE_REVISION)
    _alembic(db_url, "downgrade", REVISION)
    _alembic(db_url, "upgrade", PRO_BACKFILL_REVISION)
    engine = create_engine(db_url)
    assert _version(engine) == PRO_BACKFILL_REVISION
    columns = {column["name"] for column in inspect(engine).get_columns("payment_order")}
    assert "upgrade_source" in columns
    assert _pro_features(engine) == upgraded
    engine.dispose()


@pytest.mark.integration
@pytest.mark.slow
def test_multicurrency_creem_migration_round_trip(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'creem-migration.db'}"
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    _alembic(db_url, "stamp", "head")
    _alembic(db_url, "downgrade", "20261122_000000")

    engine = create_engine(db_url)
    po_cols = {c["name"] for c in inspect(engine).get_columns("payment_order")}
    plan_cols = {c["name"] for c in inspect(engine).get_columns("subscription_plan")}
    assert "provider" not in po_cols
    assert "currency" not in po_cols
    assert "provider_checkout_id" not in po_cols
    assert "price_monthly_usd_cents" not in plan_cols
    _insert_historical_plan(
        engine, name="pro", display_name="专业版", monthly=4900, yearly=39900, features=PRODUCTION_PRO_FEATURES
    )
    engine.dispose()

    _alembic(db_url, "upgrade", "20261124_000000")
    engine = create_engine(db_url)
    po_cols = {c["name"] for c in inspect(engine).get_columns("payment_order")}
    plan_cols = {c["name"] for c in inspect(engine).get_columns("subscription_plan")}
    assert {"provider", "currency", "provider_checkout_id"} <= po_cols
    assert {"price_monthly_usd_cents", "price_yearly_usd_cents"} <= plan_cols
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT price_monthly_usd_cents, price_yearly_usd_cents FROM subscription_plan WHERE name = 'pro'")
        ).one()
        assert tuple(row) == (999, 7900)
    engine.dispose()
