"""Cold-session AI summary measurements, with independent functional oracles.

Query counts characterize the current implementation; they are not performance
budgets. Listeners never consume cursor rows or retain loaded ORM objects.
"""

import json
import statistics
import time
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

import services.features.writing_stats_service as stats_module
from models import ChatMessage, ChatSession, Project, User
from services.features.writing_stats_service import writing_stats_service

REFERENCE_DATE = date(2026, 10, 14)  # Wednesday: both week and month have prior days.
BODY = ("夜色渐深，窗外的风穿过树林。她合上书，回想白天的对话。\n" * 1500)[:32768]
TOKEN_FIELDS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "total_tokens", "estimated_tokens",
)
RATES = (2, 3, 1, 4)


@dataclass(frozen=True)
class MessageOracle:
    id: str
    role: str
    created_at: datetime
    # These are generated independently of the service's metadata parser.
    categories: tuple[int, int, int, int] = (0, 0, 0, 0)
    total_tokens: int = 0
    legacy: bool = False


@dataclass(frozen=True)
class SummaryCase:
    engine: Engine
    user_id: str
    project_id: str
    session_ids: tuple[str, ...]
    active_session_id: str | None
    messages: tuple[MessageOracle, ...]
    scale: int


def _metrics(messages: list[MessageOracle]) -> dict[str, int | float]:
    assistants = [message for message in messages if message.role == "assistant"]
    categories = [sum(message.categories[i] for message in assistants) for i in range(4)]
    total = sum(message.total_tokens for message in assistants)
    return {
        **dict(zip(TOKEN_FIELDS[:4], categories, strict=True)),
        "total_tokens": total,
        "estimated_tokens": total,
        "estimated_cost_usd": round(sum(n * rate for n, rate in zip(categories, RATES, strict=True)) / 1_000_000, 6),
    }


def _expected(case: SummaryCase) -> dict[str, Any]:
    messages = list(case.messages)
    current = {
        "total_sessions": len(case.session_ids),
        "active_session_id": case.active_session_id,
        "total_messages": len(messages),
        "user_messages": sum(message.role == "user" for message in messages),
        "ai_messages": sum(message.role == "assistant" for message in messages),
        "tool_messages": sum(message.role == "tool" for message in messages),
        **_metrics(messages),
        "first_interaction_date": min((message.created_at for message in messages), default=None),
        "last_interaction_date": max((message.created_at for message in messages), default=None),
    }
    for key in ("first_interaction_date", "last_interaction_date"):
        value = current[key]
        current[key] = value.isoformat() if isinstance(value, datetime) else None
    end = datetime.combine(REFERENCE_DATE + timedelta(days=1), datetime.min.time())
    starts = {
        "today": REFERENCE_DATE,
        "this_week": REFERENCE_DATE - timedelta(days=REFERENCE_DATE.weekday()),
        "this_month": REFERENCE_DATE.replace(day=1),
    }
    expected: dict[str, Any] = {"current": current}
    for period, start in starts.items():
        lower = datetime.combine(start, datetime.min.time())
        selected = [message for message in messages if lower <= message.created_at < end]
        expected[period] = {
            "total": len(selected),
            "user": sum(message.role == "user" for message in selected),
            "ai": sum(message.role == "assistant" for message in selected),
            **_metrics(selected),
        }
    return expected


@pytest.fixture
def summary_case(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> Iterator[SummaryCase]:
    scale, active = request.param
    path = tmp_path / "owned-summary.sqlite"
    engine = create_engine(f"sqlite:///{path}")

    def foreign_keys(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    event.listen(engine, "connect", foreign_keys)
    for name, rate in zip(
        ("INPUT", "OUTPUT", "CACHE_READ", "CACHE_WRITE"), RATES, strict=True,
    ):
        monkeypatch.setattr(stats_module, f"AI_USAGE_{name}_COST_PER_1M_USD", float(rate))
    try:
        SQLModel.metadata.create_all(
            engine, tables=[SQLModel.metadata.tables[name] for name in ("user", "project", "chat_session", "chat_message")],
        )
        with Session(engine) as seed:
            assert seed.expire_on_commit is True
            user = User(username="summary-owner", email="owner@example.invalid", hashed_password="dummy")
            other = User(username="summary-other", email="other@example.invalid", hashed_password="dummy")
            seed.add_all([user, other])
            seed.flush()
            project = Project(name="Owned summary", owner_id=user.id)
            elsewhere = Project(name="Other project", owner_id=user.id)
            seed.add_all([project, elsewhere])
            seed.flush()
            user_id, project_id = user.id, project.id
            owned = [
                ChatSession(user_id=user.id, project_id=project.id, is_active=active and i == 0)
                for i in range(2 if scale else 0)
            ]
            foreign = [
                ChatSession(user_id=other.id, project_id=project.id, is_active=True),
                ChatSession(user_id=user.id, project_id=elsewhere.id, is_active=True),
            ]
            seed.add_all(owned + foreign)
            seed.flush()
            session_ids = tuple(chat.id for chat in owned)
            active_id = owned[0].id if active and owned else None
            dates = [
                datetime(2026, 9, 30, 23, 59, 59), datetime(2026, 10, 1),
                datetime(2026, 10, 10, 12), datetime(2026, 10, 12),
                datetime(2026, 10, 13, 23, 59, 59), datetime(2026, 10, 14),
                datetime(2026, 10, 14, 23, 59, 59), datetime(2026, 10, 15),
            ]
            oracles = []
            for i in range(scale):
                legacy = i % 3 == 0
                categories = (0, 0, 0, 0) if legacy else (100 + i, 40 + i, 10, 5)
                total = len(BODY) // 4 if legacy else sum(categories) + (7 if i % 5 == 0 else 0)
                if legacy:
                    metadata = None
                else:
                    input_key, output_key = ("prompt_tokens", "completion_tokens") if i % 2 else ("input_tokens", "output_tokens")
                    metadata = json.dumps({"usage": {
                        input_key: categories[0], output_key: categories[1],
                        "cache_read_tokens": categories[2], "cache_write_tokens": categories[3],
                        "total_tokens": total,
                    }})
                for role in ("assistant", "user", "tool"):
                    message = ChatMessage(
                        session_id=owned[i % 2].id, role=role,
                        content=BODY if role == "assistant" else f"Owned {role} request {i}",
                        message_metadata=metadata if role == "assistant" else None,
                        created_at=dates[i % len(dates)],
                    )
                    seed.add(message)
                    oracles.append(MessageOracle(
                        message.id, role, message.created_at,
                        categories if role == "assistant" else (0, 0, 0, 0),
                        total if role == "assistant" else 0,
                        legacy if role == "assistant" else False,
                    ))
            for chat in foreign:
                seed.add(ChatMessage(
                    session_id=chat.id, role="assistant", content=BODY,
                    message_metadata=json.dumps({"usage": {"total_tokens": 999999999}}),
                    created_at=datetime(2026, 10, 14, 12),
                ))
            seed.commit()
        yield SummaryCase(engine, user_id, project_id, session_ids, active_id, tuple(oracles), scale)
    finally:
        engine.dispose()
        event.remove(engine, "connect", foreign_keys)
        path.unlink(missing_ok=True)
        record_property("owned_storage_cleanup", json.dumps({"path": str(path), "absent": not path.exists()}))
        assert not path.exists()


def _cold_measurement(case: SummaryCase) -> tuple[dict[str, Any], dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    identities: dict[str, set[str]] = {"ChatSession": set(), "ChatMessage": set()}
    allowed_messages = {message.id for message in case.messages if message.role == "assistant"}
    with Session(case.engine) as session:
        assert session.expire_on_commit is True
        assert len(session.identity_map) == 0

        def before_cursor(
            _connection: Any, _cursor: Any, statement: str, parameters: Any,
            _context: Any, _executemany: bool,
        ) -> None:
            normalized = " ".join(statement.split())
            assert normalized.upper().startswith("SELECT "), "Summary must be read-only"
            projection = normalized.split(" FROM ", 1)[0]
            queries.append({
                "sql": normalized,
                "parameters": repr(parameters),
                "includes_assistant_body": "chat_message.content" in projection,
                "frames": [
                    {"function": frame.name, "line": frame.lineno}
                    for frame in traceback.extract_stack()
                    if frame.filename.endswith("services/features/writing_stats_service.py")
                ],
                "instance_load_events": {},
                "loaded_column_sets": {},
            })

        def loaded(_session: Session, instance: Any) -> None:
            assert isinstance(instance, (ChatSession, ChatMessage))
            name = type(instance).__name__
            # __dict__ avoids deferred-attribute SELECTs and retains no ORM refs.
            identity = instance.__dict__["id"]
            if isinstance(instance, ChatMessage):
                assert identity in allowed_messages
            else:
                assert identity in case.session_ids
            identities[name].add(identity)
            counts = queries[-1]["instance_load_events"]
            counts[name] = counts.get(name, 0) + 1
            queries[-1]["loaded_column_sets"][name] = sorted(
                key for key in instance.__dict__ if key != "_sa_instance_state"
            )

        event.listen(case.engine, "before_cursor_execute", before_cursor)
        event.listen(session, "loaded_as_persistent", loaded)
        try:
            started = time.perf_counter()
            result = writing_stats_service.get_ai_usage_summary(
                session, case.user_id, case.project_id, reference_date=REFERENCE_DATE,
            )
            elapsed = time.perf_counter() - started
            assert not session.new and not session.dirty and not session.deleted
        finally:
            event.remove(session, "loaded_as_persistent", loaded)
            event.remove(case.engine, "before_cursor_execute", before_cursor)
    return result, {
        "elapsed_seconds_including_listeners": elapsed,
        "select_count": len(queries),
        "body_projection_select_count": sum(query["includes_assistant_body"] for query in queries),
        "distinct_loaded_database_identities": {key: len(value) for key, value in identities.items()},
        "queries": queries,
    }


def _assert_summary_optimization_target(case: SummaryCase, runs: list[dict[str, Any]]) -> None:
    """Approved optimization TARGET; not a retrospective functional failure."""
    for run in runs:
        if case.session_ids:
            assert run["select_count"] <= 6, "Optimization TARGET: at most six SELECTs"
            assert run["body_projection_select_count"] == 1, "Optimization TARGET: one assistant-body projection"
        else:
            assert run["body_projection_select_count"] == 0
        loads = sum(query["instance_load_events"].get("ChatMessage", 0) for query in run["queries"])
        assert loads == run["distinct_loaded_database_identities"]["ChatMessage"] == case.scale, (
            "Optimization TARGET: each scoped assistant is materialized once"
        )


@pytest.mark.parametrize(
    "summary_case", [(32, True), (256, True), (8, False), (0, False)], indirect=True,
    ids=["32-assistants", "256-assistants", "all-inactive-sessions", "no-owned-sessions"],
)
def test_cold_ai_summary_queries_and_functional_contract(
    summary_case: SummaryCase,
    record_property: Callable[[str, object], None],
) -> None:
    expected = _expected(summary_case)
    repeats = 3 if summary_case.scale in (32, 256) else 1
    runs = []
    for _ in range(repeats):
        result, observation = _cold_measurement(summary_case)
        assert result == expected
        runs.append(observation)
    times = [run["elapsed_seconds_including_listeners"] for run in runs]
    measurement = {
        "assistant_scale": summary_case.scale,
        "reference_date": REFERENCE_DATE.isoformat(),
        "body_characters_per_assistant": len(BODY),
        "legacy_assistants": sum(message.legacy for message in summary_case.messages),
        "expected_dto": expected,
        "fixture_matching_assistant_rows_by_period_not_cursor_rowcounts": {
            "current": expected["current"]["ai_messages"],
            **{period: expected[period]["ai"] for period in ("today", "this_week", "this_month")},
        },
        "median_seconds_including_listeners": statistics.median(times),
        "range_seconds_including_listeners": [min(times), max(times)],
        "runs": runs,
    }
    encoded = json.dumps(measurement, ensure_ascii=False)
    record_property("ai_summary_measurement", encoded)
    print("AI_SUMMARY_MEASUREMENT", encoded)
    _assert_summary_optimization_target(summary_case, runs)
