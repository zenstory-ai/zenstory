"""The admin total reuses wallet replay in one read-only projected scan."""

from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel

from models import User
from models.points import PointsTransaction
from services.features.points_service import points_service


def test_total_uses_one_read_only_projected_scan(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'circulation.db'}")
    now = datetime(2026, 4, 8, 9, tzinfo=UTC)
    calls = []

    def record_sql(_connection, _cursor, statement, _parameters, _context, _many):
        calls.append(statement)

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            for user_id in ("a", "b", "c"):
                seed.add(
                    User(
                        id=user_id,
                        username=user_id,
                        email=user_id + "@example.test",
                        hashed_password="unused",
                        is_active=True,
                    )
                )
            seed.flush()

            def tx(user, amount, days, *, expires=None, expired=False):
                return PointsTransaction(
                    user_id=user,
                    amount=amount,
                    balance_after=0,
                    transaction_type="test",
                    created_at=(now - timedelta(days=days)).replace(tzinfo=None),
                    expires_at=expires.replace(tzinfo=None) if expires else None,
                    is_expired=expired,
                    description="not needed by replay",
                )

            seed.add_all(
                [
                    tx("a", 100, 4, expires=now - timedelta(days=1), expired=True),
                    tx("a", -40, 3),
                    tx("a", 30, 0.5, expires=now + timedelta(days=10)),
                    tx("b", -500, 2),  # Legacy overspend must not consume a's or c's balance.
                    tx("b", 10, 1),
                    tx("c", 50, 1),
                    tx("c", 0, 0.5),
                    tx("c", 100, 4, expires=now - timedelta(days=1)),  # Unmarked expiry.
                ]
            )
            seed.commit()
        clock = []
        monkeypatch.setattr(
            import_module("services.features.points_service"), "utcnow", lambda: (clock.append(now), now)[1]
        )
        event.listen(engine, "before_cursor_execute", record_sql)
        with Session(engine, autoflush=False) as session:
            monkeypatch.setattr(session, "commit", lambda: pytest.fail("read must not commit"))
            monkeypatch.setattr(session, "flush", lambda: pytest.fail("read must not flush"))
            assert points_service.get_total_available_points(session) == 80
            assert not session.dirty and not session.new and not session.deleted
        assert clock == [now]
        assert len(calls) == 1
        statement = calls[0].lower()
        columns = statement.split(" from ")[0].split("\nfrom ")[0]
        for name in ("id", "user_id", "amount", "created_at", "expires_at", "is_expired"):
            assert "points_transaction." + name in columns
        for name in ("description", "balance_after", "transaction_type", "source_id", "expired_at"):
            assert "points_transaction." + name not in columns
        assert "order by points_transaction.user_id asc, points_transaction.created_at asc" in statement
        # A cold read of flags is outside the measured scan, and detects accidental expiry mutation.
        event.remove(engine, "before_cursor_execute", record_sql)
        with Session(engine) as session:
            from sqlmodel import select

            rows = session.exec(select(PointsTransaction)).all()
            assert sum(row.is_expired for row in rows) == 1
    finally:
        engine.dispose()
        (tmp_path / "circulation.db").unlink(missing_ok=True)


def test_total_empty_ledger(db_session):
    assert points_service.get_total_available_points(db_session) == 0


@pytest.mark.parametrize("failure", [None, "iteration", "calculation"])
def test_total_closes_cursor_on_success_or_failure(monkeypatch, failure):
    now = datetime(2026, 4, 8, tzinfo=UTC)
    monkeypatch.setattr(import_module("services.features.points_service"), "utcnow", lambda: now)

    class Result:
        closed = False

        def __iter__(self):
            if failure == "iteration":
                raise RuntimeError("cursor failed")
            yield SimpleNamespace(
                user_id="a",
                amount="invalid" if failure == "calculation" else 5,
                created_at=now,
                expires_at=None,
                is_expired=False,
            )

        def close(self):
            self.closed = True

    result = Result()
    options = []

    def execute(statement, *, execution_options):
        assert "points_transaction" in str(statement)
        options.append(execution_options)
        return result

    session = SimpleNamespace(exec=execute)
    if failure:
        with pytest.raises(RuntimeError if failure == "iteration" else TypeError):
            points_service.get_total_available_points(session)
    else:
        assert points_service.get_total_available_points(session) == 5
    assert result.closed
    assert options == [{"yield_per": 256}]


@pytest.mark.parametrize("scenario", ["empty", "healthy", "spent_all", "unmarked_expiry", "overspent"])
def test_available_stats_and_total_wrapper_agree(monkeypatch, scenario):
    now = datetime(2026, 4, 8, 9, tzinfo=UTC)
    clock = []
    monkeypatch.setattr(
        import_module("services.features.points_service"), "utcnow", lambda: (clock.append(now), now)[1]
    )

    def tx(user, amount, days, expires=None):
        return SimpleNamespace(
            user_id=user, amount=amount, created_at=now - timedelta(days=days), expires_at=expires, is_expired=False
        )

    if scenario == "empty":
        rows, expected = [], (0, 0)
    else:
        rows = [
            tx("b", 50, 1),
            tx("a", 100, 3, expires=now - timedelta(days=1) if scenario == "unmarked_expiry" else None),
        ]
        if scenario == "spent_all":
            rows.append(tx("a", -100, 2))
        if scenario == "overspent":
            rows = [tx("a", -200, 4), tx("a", 100, 3), tx("b", 50, 1)]
        rows.sort(key=lambda row: (row.user_id, row.created_at))
        expected = (150, 2) if scenario == "healthy" else (50, 1)
    results = []
    options = []

    class Result:
        closed = False

        def __iter__(self):
            return iter(rows)

        def close(self):
            self.closed = True

    def execute(_statement, *, execution_options):
        result = Result()
        results.append(result)
        options.append(execution_options)
        return result

    session = SimpleNamespace(exec=execute)
    assert points_service.get_available_points_stats(session) == expected
    assert points_service.get_total_available_points(session) == expected[0]
    assert clock == [now, now]  # One shared instant per independent call.
    assert options == [{"yield_per": 256}, {"yield_per": 256}]
    assert len(results) == 2 and all(result.closed for result in results)
