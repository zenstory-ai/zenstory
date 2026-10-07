"""One-off backfill of agent chat usage into the llm_usage_event ledger."""

import importlib
import json
import os
from datetime import datetime
from unittest import mock

import pytest
from sqlmodel import Session, select

from models import ChatMessage, ChatSession, LLMUsageEvent, Project, User
from services.usage.llm_usage_service import UsageTokens, build_usage_event
from tests.conftest import TestSessionLocal

# The script loads .env and defaults DATABASE_URL at import time; keep that
# out of this worker's environment so other tests see the CI settings.
with mock.patch.dict(os.environ):
    backfill_mod = importlib.import_module("scripts.backfill_llm_usage_from_chat")


def _usage_message(session_id: str, usage: dict | None, at: datetime, *, role: str = "assistant") -> ChatMessage:
    metadata = json.dumps({"usage": usage, "stop_reason": "end_turn"}) if usage is not None else None
    return ChatMessage(session_id=session_id, role=role, content="x", message_metadata=metadata, created_at=at)


@pytest.fixture
def chat(db_session: Session):
    user = User(username="backfill_user", email="backfill@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    project = Project(name="Book", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    chat_session = ChatSession(user_id=user.id, project_id=project.id)
    db_session.add(chat_session)
    db_session.commit()
    return user, project, chat_session


def _events(db_session: Session) -> list[LLMUsageEvent]:
    db_session.expire_all()
    return list(db_session.exec(select(LLMUsageEvent).order_by(LLMUsageEvent.occurred_at)).all())


@pytest.mark.integration
def test_backfill_maps_canonical_usage_and_is_idempotent(db_session: Session, chat):
    user, project, chat_session = chat
    # Thursday 2026-10-08 10:00 Beijing, after the holiday (peak).
    peak = _usage_message(
        chat_session.id,
        {"input_tokens": 300, "cache_read_tokens": 700, "output_tokens": 50, "total_tokens": 1050},
        datetime(2026, 10, 8, 2, 0),
    )
    # Zero-valued keys are omitted by the writer; Saturday is off-peak.
    no_cache = _usage_message(
        chat_session.id, {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}, datetime(2026, 10, 10, 2, 0)
    )
    legacy = _usage_message(
        chat_session.id,
        {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3},
        datetime(2026, 6, 1),
    )
    no_usage = _usage_message(chat_session.id, None, datetime(2026, 10, 5))
    user_row = _usage_message(
        chat_session.id, {"input_tokens": 1, "total_tokens": 1}, datetime(2026, 10, 5), role="user"
    )
    broken = ChatMessage(
        session_id=chat_session.id, role="assistant", content="x", message_metadata='{"usage": oops',
        created_at=datetime(2026, 10, 5),
    )
    db_session.add_all([peak, no_cache, legacy, no_usage, user_row, broken])
    db_session.commit()

    dry = backfill_mod.backfill(TestSessionLocal, dry_run=True)
    assert (dry.inserted, dry.skipped_format) == (2, 2)
    assert _events(db_session) == []

    report = backfill_mod.backfill(TestSessionLocal)
    assert (report.scanned, report.inserted, report.skipped_format, report.cutoff) == (4, 2, 2, None)
    first, second = _events(db_session)
    assert (first.user_id, first.project_id, first.source) == (user.id, project.id, "agent")
    assert (first.cache_hit_tokens, first.cache_miss_tokens, first.output_tokens) == (700, 300, 50)
    assert (first.price_band, first.is_backfilled) == ("peak", True)
    assert first.correlation_id == f"chat_message:{peak.id}"
    assert first.occurred_at == datetime(2026, 10, 8, 2, 0)
    assert (second.cache_hit_tokens, second.price_band) == (0, "offpeak")

    again = backfill_mod.backfill(TestSessionLocal)
    assert (again.inserted, again.already_backfilled) == (0, 2)
    assert len(_events(db_session)) == 2


@pytest.mark.integration
def test_backfill_stops_where_live_metering_starts(db_session: Session, chat):
    user, _project, chat_session = chat
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    before = _usage_message(chat_session.id, usage, datetime(2026, 10, 5, 1, 0))
    after = _usage_message(chat_session.id, usage, datetime(2026, 10, 6, 1, 0))
    db_session.add_all([before, after])
    # Live metering (agent) began at 2026-10-06 00:00 UTC; suggest rows don't move the cutoff.
    db_session.add(build_usage_event(
        user_id=user.id, source="suggest", model="m", tokens=UsageTokens(output=1),
        occurred_at=datetime(2026, 10, 1),
    ))
    db_session.add(build_usage_event(
        user_id=user.id, source="agent", model="m", tokens=UsageTokens(output=1),
        occurred_at=datetime(2026, 10, 6),
    ))
    db_session.commit()

    report = backfill_mod.backfill(TestSessionLocal)
    assert report.cutoff == datetime(2026, 10, 6)
    assert report.inserted == 1
    backfilled = [event for event in _events(db_session) if event.is_backfilled]
    assert [event.correlation_id for event in backfilled] == [f"chat_message:{before.id}"]

    # An explicit --before overrides the cutoff and still skips what is done.
    override = backfill_mod.backfill(TestSessionLocal, before=datetime(2026, 10, 7))
    assert (override.inserted, override.already_backfilled) == (1, 1)


@pytest.mark.unit
def test_tokens_from_metadata_rejects_other_formats():
    assert backfill_mod.tokens_from_metadata(None) is None
    assert backfill_mod.tokens_from_metadata("[]") is None
    assert backfill_mod.tokens_from_metadata('{"usage": "n/a"}') is None
    assert backfill_mod.tokens_from_metadata('{"usage": {"total_tokens": 0}}') is None
    assert backfill_mod.tokens_from_metadata('{"usage": {"output_tokens": 0, "total_tokens": 0}}') is None
    assert backfill_mod.tokens_from_metadata(
        '{"usage": {"output_tokens": 3, "total_tokens": 3}}'
    ) == UsageTokens(output=3)


@pytest.mark.integration
def test_main_prints_report(monkeypatch, capsys, db_session: Session, chat):
    _user, _project, chat_session = chat
    db_session.add(
        _usage_message(chat_session.id, {"output_tokens": 4, "total_tokens": 4}, datetime(2026, 10, 5))
    )
    db_session.commit()
    import database

    monkeypatch.setattr(database, "sync_engine", TestSessionLocal.kw["bind"])
    assert backfill_mod.main(["--dry-run", "--before", "2026-10-06T00:00:00Z"]) == 0
    out = capsys.readouterr().out
    assert "Would insert 1 row(s)" in out
    assert "cutoff 2026-10-06T00:00:00" in out
    assert _events(db_session) == []
