"""Only an explicitly matching serial-PG test database receives schema cleanup."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import tests.conftest as fixtures


@pytest.mark.parametrize("opt_in,primary", [
    (None, "postgresql://test:test@localhost/owned"),
    ("", "postgresql://test:test@localhost/owned"),
    ("postgresql://test:test@localhost/owned", "postgresql://test:test@localhost/other"),
    ("sqlite:///owned.db", "sqlite:///owned.db"),
])
def test_nonmatching_or_nonpostgres_environment_never_constructs_engine(monkeypatch, opt_in, primary):
    if opt_in is None:
        monkeypatch.delenv("ZENSTORY_TEST_POSTGRES_URL", raising=False)
    else:
        monkeypatch.setenv("ZENSTORY_TEST_POSTGRES_URL", opt_in)
    monkeypatch.setenv("DATABASE_URL", primary)
    factory = Mock(side_effect=AssertionError("unowned DB access"))
    monkeypatch.setattr(fixtures, "create_engine", factory)
    lifecycle = fixtures.isolated_serial_postgres_schema.__wrapped__()
    assert next(lifecycle) is None
    with pytest.raises(StopIteration):
        next(lifecycle)
    factory.assert_not_called()


@pytest.mark.parametrize("fail_at", [None, 1, 2])
def test_owned_postgres_boundaries_use_full_metadata_and_always_dispose(monkeypatch, fail_at):
    url = "postgresql://test:test@localhost/owned"
    monkeypatch.setenv("ZENSTORY_TEST_POSTGRES_URL", url)
    monkeypatch.setenv("DATABASE_URL", url)
    engine = Mock()
    factory = Mock(return_value=engine)
    monkeypatch.setattr(fixtures, "create_engine", factory)
    calls = []

    def drop(current):
        assert current is engine
        calls.append(current)
        if len(calls) == fail_at:
            raise RuntimeError("cleanup failure")

    monkeypatch.setattr(fixtures.SQLModel, "metadata", SimpleNamespace(drop_all=drop))
    lifecycle = fixtures.isolated_serial_postgres_schema.__wrapped__()
    if fail_at == 1:
        with pytest.raises(RuntimeError, match="cleanup failure"):
            next(lifecycle)
        assert len(calls) == 1
    else:
        assert next(lifecycle) is None
        assert len(calls) == 1 and not engine.dispose.called
        with pytest.raises(RuntimeError if fail_at == 2 else StopIteration):
            next(lifecycle)
        assert len(calls) == 2
    factory.assert_called_once_with(url, poolclass=fixtures.NullPool)
    engine.dispose.assert_called_once_with()
