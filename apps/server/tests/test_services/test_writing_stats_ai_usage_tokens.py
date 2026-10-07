"""Tests for AI usage real-token aggregation and cost observability."""

import json
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session

import services.features.writing_stats_service as writing_stats_module
from models import ChatMessage, ChatSession, Project, User
from services.features.writing_stats_service import writing_stats_service


@pytest.fixture
def ai_usage_project(db_session: Session) -> tuple[User, Project]:
    """Create a user/project pair for AI usage tests."""
    user = User(
        email="ai-usage-tests@example.com",
        username="ai_usage_tests",
        hashed_password="hashed_password",
    )
    project = Project(
        name="AI Usage Metrics Project",
        owner_id=user.id,
        project_type="novel",
    )

    db_session.add(user)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(project)
    return user, project


def _create_chat_session(db_session: Session, user: User, project: Project, *, is_active: bool = True) -> ChatSession:
    chat_session = ChatSession(
        user_id=user.id,
        project_id=project.id,
        title="AI usage test session",
        is_active=is_active,
        message_count=0,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)
    return chat_session


@pytest.mark.unit
def test_get_ai_usage_stats_uses_real_usage_tokens_and_cost(
    db_session: Session,
    ai_usage_project: tuple[User, Project],
    monkeypatch: pytest.MonkeyPatch,
):
    """Stats should aggregate real usage metadata and estimate cost from token pricing."""
    user, project = ai_usage_project
    chat_session = _create_chat_session(db_session, user, project)

    monkeypatch.setattr(writing_stats_module, "AI_USAGE_INPUT_COST_PER_1M_USD", 2.0)
    monkeypatch.setattr(writing_stats_module, "AI_USAGE_OUTPUT_COST_PER_1M_USD", 3.0)
    monkeypatch.setattr(writing_stats_module, "AI_USAGE_CACHE_READ_COST_PER_1M_USD", 1.0)
    monkeypatch.setattr(writing_stats_module, "AI_USAGE_CACHE_WRITE_COST_PER_1M_USD", 4.0)

    now = datetime.utcnow()
    db_session.add_all(
        [
            ChatMessage(
                session_id=chat_session.id,
                role="user",
                content="请帮我润色这一段。",
                created_at=now - timedelta(minutes=5),
            ),
            ChatMessage(
                session_id=chat_session.id,
                role="assistant",
                content="当然可以，这里是润色后的版本。",
                message_metadata=json.dumps(
                    {
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 40,
                            "cache_read_tokens": 10,
                            "cache_write_tokens": 5,
                        }
                    }
                ),
                created_at=now - timedelta(minutes=4),
            ),
            ChatMessage(
                session_id=chat_session.id,
                role="assistant",
                content="我再给你一个更简洁的版本。",
                message_metadata=json.dumps(
                    {
                        "usage": {
                            "input_tokens": 60,
                            "output_tokens": 20,
                            "cache_write_tokens": 10,
                            "total_tokens": 90,
                        }
                    }
                ),
                created_at=now - timedelta(minutes=3),
            ),
            ChatMessage(
                session_id=chat_session.id,
                role="tool",
                content='{"action":"read_file"}',
                created_at=now - timedelta(minutes=2),
            ),
        ]
    )
    db_session.commit()

    stats = writing_stats_service.get_ai_usage_stats(db_session, user.id, project.id)

    assert stats["total_sessions"] == 1
    assert stats["total_messages"] == 4
    assert stats["user_messages"] == 1
    assert stats["ai_messages"] == 2
    assert stats["tool_messages"] == 1

    assert stats["input_tokens"] == 160
    assert stats["output_tokens"] == 60
    assert stats["cache_read_tokens"] == 10
    assert stats["cache_write_tokens"] == 15
    assert stats["total_tokens"] == 245
    assert stats["estimated_tokens"] == 245
    assert stats["estimated_cost_usd"] == pytest.approx(0.00057)


@pytest.mark.unit
def test_get_ai_usage_stats_falls_back_for_legacy_assistant_messages(
    db_session: Session,
    ai_usage_project: tuple[User, Project],
):
    """Legacy assistant messages without usage metadata should use content-length fallback."""
    user, project = ai_usage_project
    chat_session = _create_chat_session(db_session, user, project)

    db_session.add(
        ChatMessage(
            session_id=chat_session.id,
            role="assistant",
            content="x" * 80,  # 80 chars -> 20 fallback tokens
            message_metadata=None,
            created_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    stats = writing_stats_service.get_ai_usage_stats(db_session, user.id, project.id)

    assert stats["total_messages"] == 1
    assert stats["ai_messages"] == 1
    assert stats["input_tokens"] == 0
    assert stats["output_tokens"] == 0
    assert stats["total_tokens"] == 20
    assert stats["estimated_tokens"] == 20
    assert stats["estimated_cost_usd"] == 0.0


@pytest.mark.unit
def test_ai_usage_trend_and_summary_include_real_tokens_and_cost(
    db_session: Session,
    ai_usage_project: tuple[User, Project],
    monkeypatch: pytest.MonkeyPatch,
):
    """Trend and summary payloads should expose real token/cost fields."""
    user, project = ai_usage_project
    chat_session = _create_chat_session(db_session, user, project)

    monkeypatch.setattr(writing_stats_module, "AI_USAGE_INPUT_COST_PER_1M_USD", 1.0)
    monkeypatch.setattr(writing_stats_module, "AI_USAGE_OUTPUT_COST_PER_1M_USD", 2.0)
    monkeypatch.setattr(writing_stats_module, "AI_USAGE_CACHE_READ_COST_PER_1M_USD", 0.0)
    monkeypatch.setattr(writing_stats_module, "AI_USAGE_CACHE_WRITE_COST_PER_1M_USD", 0.0)

    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)

    db_session.add_all(
        [
            ChatMessage(
                session_id=chat_session.id,
                role="assistant",
                content="today assistant",
                message_metadata=json.dumps({"usage": {"input_tokens": 50, "output_tokens": 20}}),
                created_at=now - timedelta(minutes=1),
            ),
            ChatMessage(
                session_id=chat_session.id,
                role="assistant",
                content="yesterday assistant",
                message_metadata=json.dumps({"usage": {"input_tokens": 30, "output_tokens": 10}}),
                created_at=yesterday,
            ),
            ChatMessage(
                session_id=chat_session.id,
                role="user",
                content="user follow-up",
                created_at=now,
            ),
        ]
    )
    db_session.commit()

    trend = writing_stats_service.get_ai_usage_trend(
        db_session,
        user.id,
        project.id,
        period="daily",
        days=7,
    )
    by_date = {item["date"]: item for item in trend}

    today_key = str(now.date())
    yesterday_key = str(yesterday.date())

    assert by_date[today_key]["total_tokens"] == 70
    assert by_date[today_key]["estimated_tokens"] == 70
    assert by_date[today_key]["estimated_cost_usd"] == pytest.approx(0.00009)
    assert by_date[yesterday_key]["total_tokens"] == 40
    assert by_date[yesterday_key]["estimated_cost_usd"] == pytest.approx(0.00005)

    summary = writing_stats_service.get_ai_usage_summary(db_session, user.id, project.id)
    assert summary["current"]["total_tokens"] == 110
    assert summary["current"]["estimated_tokens"] == 110
    assert summary["current"]["estimated_cost_usd"] == pytest.approx(0.00014)
    assert summary["today"]["total_tokens"] == 70
    assert summary["today"]["estimated_cost_usd"] == pytest.approx(0.00009)


@pytest.mark.parametrize(("reference_date", "clock_date", "expected_periods"), [
    ("2026-05-31", "2026-06-01", ("prior", "prior", "prior")),
    ("2026-06-01", "2026-06-01", ("next", "next", "next")),
    (None, "2026-06-01", ("next", "next", "next")),
    ("2026-06-03", "2026-06-03", ("next", "both", "both")),
])
def test_ai_summary_reference_date_and_exclusive_calendar_end(
    db_session, ai_usage_project, monkeypatch, reference_date, clock_date, expected_periods,
):
    """Period aggregates and tokens exclude next midnight; all-time stays unbounded."""
    from datetime import date

    user, project = ai_usage_project
    chat = _create_chat_session(db_session, user, project)
    current = date.fromisoformat(clock_date)
    prior = current - timedelta(days=1)
    monkeypatch.setattr(writing_stats_module, "utcnow", lambda: datetime.combine(current, datetime.min.time()))
    for day, usage in [(prior, {"input_tokens": 7, "output_tokens": 3}),
                       (current, {"input_tokens": 13, "output_tokens": 5})]:
        # Exactly at next-day midnight tests the exclusive upper bound for prior date.
        timestamp = datetime.combine(day, datetime.min.time())
        db_session.add_all([
            ChatMessage(session_id=chat.id, role="user", content="Calendar question", created_at=timestamp),
            ChatMessage(session_id=chat.id, role="assistant", content="Calendar response",
                        created_at=timestamp, message_metadata=json.dumps({"usage": usage})),
        ])
    db_session.commit()
    reference = date.fromisoformat(reference_date) if reference_date else None
    summary = writing_stats_service.get_ai_usage_summary(db_session, user.id, project.id, reference_date=reference)
    assert summary["current"]["total_messages"] == 4
    assert summary["current"]["total_tokens"] == 28
    expected = {"prior": (1, 7, 3), "next": (1, 13, 5), "both": (2, 20, 8)}
    for name, metric in zip(["today", "this_week", "this_month"], expected_periods, strict=True):
        pairs, input_tokens, output_tokens = expected[metric]
        actual = summary[name]
        assert actual["total"] == pairs * 2
        assert actual["user"] == actual["ai"] == pairs
        assert actual["input_tokens"] == input_tokens
        assert actual["output_tokens"] == output_tokens
        assert actual["total_tokens"] == input_tokens + output_tokens
