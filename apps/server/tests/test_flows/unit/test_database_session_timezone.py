"""Flow engine UTC parity, libpq options, and existing bounded connection policy."""

import os
from urllib.parse import urlencode

import pytest
from sqlalchemy import text

from flows import database_session as dbs


@pytest.mark.parametrize("options", [[], ["-c application_name=flow-test"], ["-c application_name=flow-test", "-c statement_timeout=1234"]])
def test_postgres_constructor_retains_options_and_forces_utc(monkeypatch, options):
    observed = {}

    def capture(url, **kwargs):
        observed.update(url=url, **kwargs)
        return object()

    monkeypatch.setattr(dbs, "create_engine", capture)
    url = "postgresql://test:test@localhost/owned"
    if options:
        url += "?" + urlencode([("options", value) for value in options])
    dbs._build_engine(url)
    assert observed["url"].startswith("postgresql+psycopg://")
    assert observed["pool_size"] == 5 and observed["max_overflow"] == 0
    assert observed["pool_pre_ping"] is True and observed["pool_recycle"] == 1800
    assert observed["connect_args"]["options"] == " ".join([*options, "-c timezone=UTC"])


def test_sqlite_wal_and_timeout_remain_unchanged(tmp_path):
    path = tmp_path / "flow-engine.sqlite"
    engine = dbs._build_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30000
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("with_options", [False, True])
def test_real_postgres_flow_engine_overrides_database_timezone_and_keeps_options(with_options, record_property):
    url = os.environ.get("ZENSTORY_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("explicit owned PostgreSQL URL required")
    # The evidence runner creates this owned database with Pacific/Honolulu default.
    if with_options:
        url += ("&" if "?" in url else "?") + urlencode({"options": "-c application_name=flow-parity -c statement_timeout=1234 -c timezone=Asia/Tokyo"})
    engine = dbs._build_engine(url)
    try:
        with engine.connect() as conn:
            settings = {key: conn.execute(text("SHOW " + key)).scalar_one() for key in ["timezone", "application_name", "statement_timeout"]}
            record_property("actual_postgres_settings", str(settings))
            assert settings["timezone"] == "UTC"
            if with_options:
                assert settings["application_name"] == "flow-parity"
                assert settings["statement_timeout"] == "1234ms"
    finally:
        engine.dispose()
