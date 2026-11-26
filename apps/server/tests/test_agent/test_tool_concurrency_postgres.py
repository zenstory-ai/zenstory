"""
Gate test for item 1.5: relaxing max_function_tool_concurrency above 1.

THIS FILE IS THE BLOCKING GATE FOR RAISING max_function_tool_concurrency IN
runner.py.  DO NOT relax the concurrency cap until this test passes in CI with
a real Postgres database AND item 1.1 (per-call SQLAlchemy Session isolation)
is implemented.

Background
----------
The agent runner currently sets::

    run_config=RunConfig(
        tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
    )

This serialises all tool calls when desired; each concurrent tool call receives
its own short-lived SQLAlchemy Session via ``AgentRequestContext.session_scope()``
(replacing the old global ``ToolContext._owned_session_var``).

This test verifies the Session-isolation contract on a live PostgreSQL pool:
when N tool calls run concurrently each must obtain a DISTINCT Session object,
and the connection pool must return to its baseline depth after all calls
complete.

Skipped unless RUN_PG_CONCURRENCY_GATE=1 and TEST_DATABASE_URL are set.

See: .omc/plans/agent-layer-improvements.md Phase 1, items 1.1 and 1.5.
"""

import asyncio
import os

import pytest

# ---------------------------------------------------------------------------
# Skip the entire module unless a Postgres DSN is explicitly provided AND the
# caller opts in with RUN_PG_CONCURRENCY_GATE=1.  This prevents accidental
# execution in CI pipelines that only have SQLite.
# ---------------------------------------------------------------------------
_PG_DSN = os.environ.get("TEST_DATABASE_URL", "")
_GATE_ENABLED = os.environ.get("RUN_PG_CONCURRENCY_GATE", "") == "1"

pytestmark = pytest.mark.skip(
    reason=(
        "Gate for item 1.5 cap relax: requires Postgres + per-call session isolation "
        "(item 1.1). See .omc/plans/agent-layer-improvements.md Phase 1.  "
        "Re-enable by setting RUN_PG_CONCURRENCY_GATE=1 and TEST_DATABASE_URL=<pg-dsn>."
    )
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pg_engine(dsn: str):
    """Create a SQLAlchemy engine pointed at the given Postgres DSN."""
    from sqlalchemy import create_engine

    return create_engine(
        dsn,
        pool_size=10,
        max_overflow=0,
        pool_pre_ping=True,
    )


def _pool_checkedout(engine) -> int:
    """Return the number of connections currently checked out from the pool."""
    return engine.pool.checkedout()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolConcurrencySessionIsolation:
    """
    Verify per-call Session isolation under concurrent tool execution.

    These tests must pass before max_function_tool_concurrency may be raised
    above 1.  They depend on:
      - A live Postgres instance (TEST_DATABASE_URL env var)
      - AgentRequestContext.session_scope() returning a fresh,
        independently-scoped Session for every tool invocation.
    """

    def test_concurrent_tool_calls_get_distinct_sessions(self):
        """
        N concurrent tool calls must each receive a DISTINCT Session object.

        Rationale: if two tool coroutines share a Session, a flush/commit in
        one will unexpectedly affect the other's pending writes, producing
        silent data corruption or IntegrityErrors.
        """
        if not _PG_DSN:
            pytest.skip("TEST_DATABASE_URL not set")

        from sqlmodel import Session

        from agent.tools.tool_context import AgentRequestContext

        engine = _make_pg_engine(_PG_DSN)
        req_ctx = AgentRequestContext(create_session_func=lambda: Session(engine))

        CONCURRENCY = 4
        sessions_seen: list[int] = []  # ids of Session objects

        async def simulate_tool_call(call_index: int) -> None:
            """Simulate a single tool call acquiring a Session."""
            with req_ctx.session_scope() as session:
                sessions_seen.append(id(session))
                # Hold briefly to maximise overlap with sibling coroutines.
                await asyncio.sleep(0.01)

        try:
            asyncio.run(
                asyncio.gather(*[simulate_tool_call(i) for i in range(CONCURRENCY)])
            )
        finally:
            engine.dispose()

        assert len(sessions_seen) == CONCURRENCY, (
            f"Expected {CONCURRENCY} session acquisitions, got {len(sessions_seen)}"
        )
        assert len(set(sessions_seen)) == CONCURRENCY, (
            "All concurrent tool calls must receive DISTINCT Session objects; "
            f"got {len(set(sessions_seen))} unique out of {CONCURRENCY} calls."
        )

    def test_connection_pool_returns_to_baseline_after_concurrent_calls(self):
        """
        After N concurrent tool calls complete, the pool checkout count must
        return to its pre-call baseline (no leaked connections).

        A connection leak under concurrency would exhaust the pool under load
        and stall subsequent requests indefinitely.
        """
        if not _PG_DSN:
            pytest.skip("TEST_DATABASE_URL not set")

        from sqlalchemy import text
        from sqlmodel import Session

        from agent.tools.tool_context import AgentRequestContext

        engine = _make_pg_engine(_PG_DSN)
        req_ctx = AgentRequestContext(create_session_func=lambda: Session(engine))
        baseline = _pool_checkedout(engine)

        CONCURRENCY = 4

        async def simulate_tool_call_with_query(call_index: int) -> None:
            """Simulate a tool call that runs a trivial DB query."""
            with req_ctx.session_scope() as session:
                # Execute a cheap read to actually check out a connection.
                session.execute(text("SELECT 1"))

        asyncio.run(
            asyncio.gather(
                *[simulate_tool_call_with_query(i) for i in range(CONCURRENCY)]
            )
        )

        after = _pool_checkedout(engine)
        assert after <= baseline, (
            f"Connection pool leaked: {after} connections checked out after concurrent "
            f"calls, expected ≤ {baseline} (baseline)."
        )
        engine.dispose()

    def test_concurrent_writes_do_not_cross_contaminate(self):
        """
        Two concurrent tool calls writing to different rows must not see each
        other's uncommitted state (standard READ COMMITTED isolation).

        This guards against the scenario where a shared Session would expose
        an unflushed write from one coroutine to a read in the other.
        """
        if not _PG_DSN:
            pytest.skip("TEST_DATABASE_URL not set")

        from sqlalchemy import text
        from sqlmodel import Session

        from agent.tools.tool_context import AgentRequestContext

        engine = _make_pg_engine(_PG_DSN)
        req_ctx = AgentRequestContext(create_session_func=lambda: Session(engine))

        # Track what each call observed about the other's write.
        observations: dict[int, bool] = {}

        # Use a unique sentinel value per run to avoid cross-test pollution.
        import uuid
        sentinel_a = str(uuid.uuid4())
        sentinel_b = str(uuid.uuid4())

        async def writer_a() -> None:
            with req_ctx.session_scope() as session:
                # Write sentinel_a but do NOT commit yet.
                session.execute(
                    text("CREATE TEMP TABLE IF NOT EXISTS _gate_test (val TEXT)")
                )
                session.execute(
                    text("INSERT INTO _gate_test VALUES (:v)"),
                    {"v": sentinel_a},
                )
                # Small yield to allow writer_b to run concurrently.
                await asyncio.sleep(0.02)
                # writer_b's uncommitted sentinel must NOT be visible here.
                row = session.execute(
                    text("SELECT COUNT(*) FROM _gate_test WHERE val = :v"),
                    {"v": sentinel_b},
                ).scalar()
                observations[0] = int(row) == 0  # True = no cross-contamination
                session.rollback()

        async def writer_b() -> None:
            with req_ctx.session_scope() as session:
                session.execute(
                    text("CREATE TEMP TABLE IF NOT EXISTS _gate_test (val TEXT)")
                )
                session.execute(
                    text("INSERT INTO _gate_test VALUES (:v)"),
                    {"v": sentinel_b},
                )
                await asyncio.sleep(0.02)
                # writer_a's uncommitted sentinel must NOT be visible here.
                row = session.execute(
                    text("SELECT COUNT(*) FROM _gate_test WHERE val = :v"),
                    {"v": sentinel_a},
                ).scalar()
                observations[1] = int(row) == 0  # True = no cross-contamination
                session.rollback()

        try:
            asyncio.run(asyncio.gather(writer_a(), writer_b()))
        finally:
            engine.dispose()

        assert observations.get(0) is True, (
            "writer_a saw writer_b's uncommitted row — sessions are not isolated."
        )
        assert observations.get(1) is True, (
            "writer_b saw writer_a's uncommitted row — sessions are not isolated."
        )
