"""Contract and service tests for Phase-1 Admin UX Health & Chat Votes endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlmodel import Session

from models import (
    ACTIVATION_EVENT_DASHBOARD_VIEWED,
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
    ActivationEvent,
    ChatMessage,
    ChatSession,
    File,
    FileVersion,
    Project,
    User,
)
from models.llm_usage import (
    CALL_OUTCOME_ERROR,
    CALL_OUTCOME_SUCCESS,
    LLMUsageEvent,
)
from services.core.auth_service import hash_password
from services.usage.admin_ux_metrics_service import (
    get_ux_health_dashboard,
    list_chat_feedback_votes,
)

NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # 16:00 Beijing


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def _user(
    session: Session,
    name: str,
    created_at: datetime,
    *,
    admin: bool = False,
) -> User:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_superuser=admin,
        is_active=True,
        created_at=_naive(created_at),
        updated_at=_naive(created_at),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _project_and_session(
    session: Session,
    owner: User,
    name: str,
    created_at: datetime,
) -> tuple[Project, ChatSession]:
    project = Project(
        name=name,
        owner_id=owner.id,
        created_at=_naive(created_at),
        updated_at=_naive(created_at),
    )
    session.add(project)
    session.commit()
    session.refresh(project)

    chat_session = ChatSession(
        user_id=owner.id,
        project_id=project.id,
        title=f"Session {name}",
        created_at=_naive(created_at),
        updated_at=_naive(created_at),
    )
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return project, chat_session


def _assistant_message(
    session: Session,
    chat_session: ChatSession,
    created_at: datetime,
    *,
    content: str = "SECRET_MANUSCRIPT_PROSE_DO_NOT_LEAK",
    metadata: dict | None = None,
    tool_calls: list | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        session_id=chat_session.id,
        role="assistant",
        content=content,
        reasoning_content="SECRET_REASONING_DO_NOT_LEAK",
        message_metadata=json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
        tool_calls=json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None,
        created_at=_naive(created_at),
    )
    session.add(msg)
    session.commit()
    session.refresh(msg)
    return msg


def _usage(
    session: Session,
    user: User,
    at: datetime,
    *,
    outcome: str | None = CALL_OUTCOME_SUCCESS,
    ttft_ms: int | None = 1200,
    duration_ms: int | None = 4000,
    cache_hit: int = 800,
    cache_miss: int = 200,
    output_tokens: int = 100,
    source: str = "agent",
    backfilled: bool = False,
) -> None:
    session.add(
        LLMUsageEvent(
            user_id=user.id,
            source=source,
            model="deepseek-flash",
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
            output_tokens=output_tokens,
            price_band="offpeak",
            pricing_version="test",
            occurred_at=_naive(at),
            is_backfilled=backfilled,
            outcome=outcome,
            ttft_ms=ttft_ms,
            duration_ms=duration_ms,
        )
    )


def _activation(
    session: Session,
    user: User,
    event_name: str,
    at: datetime,
    metadata: dict | None = None,
) -> None:
    session.add(
        ActivationEvent(
            user_id=user.id,
            event_name=event_name,
            event_metadata=metadata or {},
            created_at=_naive(at),
        )
    )


def _file_with_versions(
    session: Session,
    project: Project,
    title: str,
    file_type: str,
    versions: list[tuple[int, int, datetime]],
) -> File:
    first_at = versions[0][2]
    file = File(
        project_id=project.id,
        title=title,
        content="x",
        file_type=file_type,
        created_at=_naive(first_at),
        updated_at=_naive(versions[-1][2]),
    )
    session.add(file)
    session.commit()
    session.refresh(file)
    for version_number, word_count, at in versions:
        session.add(
            FileVersion(
                file_id=file.id,
                project_id=project.id,
                version_number=version_number,
                content="",
                is_base_version=True,
                word_count=word_count,
                char_count=word_count,
                change_type="edit",
                change_source="user",
                created_at=_naive(at),
            )
        )
    session.commit()
    return file


async def _headers(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        data={"username": user.username, "password": "password123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.integration
async def test_ux_health_and_chat_votes_guard_validation_and_empty_state(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setattr("services.usage.admin_ux_metrics_service.utcnow", lambda: NOW)
    admin = _user(db_session, "ux_empty_admin", NOW - timedelta(days=40), admin=True)
    normal = _user(db_session, "ux_empty_normal", NOW - timedelta(days=40))

    normal_headers = await _headers(client, normal)
    admin_headers = await _headers(client, admin)

    # Non-superusers get 403 on both endpoints
    assert (await client.get("/api/admin/dashboard/ux-health?days=7", headers=normal_headers)).status_code == 403
    assert (await client.get("/api/admin/feedback/chat-votes?days=7", headers=normal_headers)).status_code == 403

    # Validation checks
    assert (await client.get("/api/admin/dashboard/ux-health?days=10", headers=admin_headers)).status_code == 422
    assert (await client.get("/api/admin/feedback/chat-votes?days=10", headers=admin_headers)).status_code == 422
    assert (await client.get("/api/admin/feedback/chat-votes?days=7&vote=maybe", headers=admin_headers)).status_code == 422

    # Empty state: safe zero/null values, no division by zero
    ux_resp = await client.get("/api/admin/dashboard/ux-health?days=7", headers=admin_headers)
    assert ux_resp.status_code == 200
    ux = ux_resp.json()
    assert ux["days"] == 7
    assert ux["timezone"] == "Asia/Shanghai"
    m = ux["current"]["metrics"]
    assert m["ttft_p50_ms"] is None
    assert m["ttft_p95_ms"] is None
    assert m["ttft_slow_tail_rate"] is None
    assert m["duration_slow_tail_rate"] is None
    assert m["prompt_cache_hit_rate"] is None
    assert m["total_rounds"] == 0
    assert m["healthy_round_rate"] is None
    assert m["user_stopped_rate"] is None
    assert m["client_disconnected_rate"] is None
    assert m["ai_edit_undo_rate"] is None
    assert m["ai_delete_restore_rate"] is None
    assert m["thumbs_up_rate"] is None
    assert m["ttfv_p50_minutes"] is None
    assert m["w1_retention_rate"] is None
    assert m["chapters_written"] == 0
    assert m["net_words_written"] == 0
    assert m["mobile_share"] is None
    assert len(ux["daily"]) == 7
    assert all(day["rounds"] == 0 and day["healthy_rate"] is None for day in ux["daily"])
    assert ux["delta"]["healthy_round_rate"] is None

    votes_resp = await client.get("/api/admin/feedback/chat-votes?days=7", headers=admin_headers)
    assert votes_resp.status_code == 200
    votes = votes_resp.json()
    assert votes["summary"] == {
        "up": 0,
        "down": 0,
        "total_votes": 0,
        "up_rate": None,
        "preset_counts": [],
    }
    assert votes["items"] == []
    assert votes["total"] == 0


@pytest.mark.integration
async def test_ux_health_metrics_prior_period_delta_and_daily_trend(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setattr("services.usage.admin_ux_metrics_service.utcnow", lambda: NOW)
    admin = _user(db_session, "ux_admin", NOW - timedelta(days=30), admin=True)
    writer_a = _user(db_session, "ux_writer_a", NOW - timedelta(days=3))
    writer_b = _user(db_session, "ux_writer_b", NOW - timedelta(days=2))
    returning_writer = _user(db_session, "ux_returning", NOW - timedelta(days=20))
    churned_writer = _user(db_session, "ux_churned", NOW - timedelta(days=20))

    proj_a, sess_a = _project_and_session(db_session, writer_a, "Novel A", NOW - timedelta(days=3))
    proj_b, sess_b = _project_and_session(db_session, writer_b, "Novel B", NOW - timedelta(days=2))
    _proj_adm, sess_adm = _project_and_session(db_session, admin, "Admin Proj", NOW - timedelta(days=3))
    _proj_ret, sess_ret = _project_and_session(db_session, returning_writer, "Returning", NOW - timedelta(days=15))

    # --- Current 7d window (2026-09-30 16:00Z .. 2026-10-07 08:00Z) ---
    # LLM usage: 4 customer successful calls + 1 error + 1 backfilled + 1 superuser (excluded)
    _usage(db_session, writer_a, NOW - timedelta(days=2), ttft_ms=1000, duration_ms=4000, cache_hit=800, cache_miss=200)
    _usage(db_session, writer_a, NOW - timedelta(days=1), ttft_ms=2000, duration_ms=6000, cache_hit=600, cache_miss=400)
    _usage(db_session, writer_b, NOW - timedelta(hours=5), ttft_ms=12000, duration_ms=15000, cache_hit=900, cache_miss=100)
    _usage(db_session, returning_writer, NOW - timedelta(hours=2), ttft_ms=4000, duration_ms=11000, cache_hit=700, cache_miss=300)
    # Failed attempt: excluded from ttft/duration success samples, tokens still 0
    _usage(db_session, writer_b, NOW - timedelta(hours=4), outcome=CALL_OUTCOME_ERROR, ttft_ms=None, duration_ms=3000, cache_hit=0, cache_miss=0, output_tokens=0)
    # Excluded: backfilled and superuser
    _usage(db_session, writer_a, NOW - timedelta(days=1), ttft_ms=99999, duration_ms=99999, cache_hit=0, cache_miss=10000, backfilled=True)
    _usage(db_session, admin, NOW - timedelta(hours=1), ttft_ms=99999, duration_ms=99999, cache_hit=0, cache_miss=10000)

    # Prior 7d window (2026-09-23 16:00Z .. 2026-09-30 08:00Z)
    _usage(db_session, returning_writer, NOW - timedelta(days=9), ttft_ms=5000, duration_ms=9000, cache_hit=500, cache_miss=500)
    _usage(db_session, churned_writer, NOW - timedelta(days=8), ttft_ms=5000, duration_ms=9000, cache_hit=500, cache_miss=500)
    db_session.commit()

    # Assistant messages in current window (8 customer rounds + 1 superuser excluded):
    # 1. Healthy + 1 undoable edit (not undone) + 1 undone write_chapter + thumbs up
    _assistant_message(
        db_session,
        sess_a,
        NOW - timedelta(days=2),
        metadata={
            "stop_reason": "end_turn",
            "feedback": {"vote": "up", "preset": None, "comment": "Great pacing", "created_at": (NOW - timedelta(days=2)).isoformat()},
        },
        tool_calls=[
            {"id": "tc1", "name": "edit_file", "status": "success", "result": {"data": {"id": "f1", "undo": {"before_version_number": 1, "undone": False}}}},
            {"id": "tc2", "name": "write_chapter", "status": "success", "result": {"id": "f2", "undo": {"before_version_number": 1, "undone": True}}},
            # Failed edit card should not count
            {"id": "tc3", "name": "edit_file", "status": "error", "result": {"undo": {"before_version_number": 1, "undone": False}}},
        ],
    )
    # 2. Healthy + 2 delete cards (1 restored, 1 not) + thumbs up
    _assistant_message(
        db_session,
        sess_a,
        NOW - timedelta(days=1),
        metadata={
            "stop_reason": "end_turn",
            "feedback": {"vote": "up", "preset": None, "comment": None, "created_at": (NOW - timedelta(days=1)).isoformat()},
        },
        tool_calls=[
            {"id": "tc4", "name": "delete_file", "status": "success", "result": {"id": "f3", "mutation_applied": True, "restored": True}},
            {"id": "tc5", "name": "delete_file", "status": "success", "result": {"id": "f4", "mutation_applied": True}},
        ],
    )
    # 3. Healthy
    _assistant_message(db_session, sess_b, NOW - timedelta(hours=6), metadata={"stop_reason": "end_turn"})
    # 4. User stopped
    _assistant_message(
        db_session,
        sess_b,
        NOW - timedelta(hours=5),
        metadata={"stop_reason": "user_stopped", "stop_outcome": {"reason": "user_stopped"}},
    )
    # 5. Client disconnected
    _assistant_message(
        db_session,
        sess_b,
        NOW - timedelta(hours=4),
        metadata={"stop_reason": "client_disconnected", "stop_outcome": {"reason": "client_disconnected"}},
    )
    # 6. Abnormal: run_deadline_exceeded + thumbs down preset="too_slow"
    _assistant_message(
        db_session,
        sess_b,
        NOW - timedelta(hours=3),
        metadata={
            "stop_reason": "run_deadline_exceeded",
            "feedback": {"vote": "down", "preset": "too_slow", "comment": "Timed out", "created_at": (NOW - timedelta(hours=3)).isoformat()},
        },
    )
    # 7. Abnormal: end_turn with unexecuted_tool_text + thumbs down preset="off_topic"
    _assistant_message(
        db_session,
        sess_b,
        NOW - timedelta(hours=2),
        metadata={
            "stop_reason": "end_turn",
            "stop_outcome": {"reason": "completed", "unexecuted_tool_text": True},
            "feedback": {"vote": "down", "preset": "off_topic", "comment": "Did not edit file", "created_at": (NOW - timedelta(hours=2)).isoformat()},
        },
    )
    # 8. Abnormal: tool_failure_circuit_open + thumbs down preset="too_slow"
    _assistant_message(
        db_session,
        sess_ret,
        NOW - timedelta(hours=1),
        metadata={
            "stop_reason": "tool_failure_circuit_open",
            "feedback": {"vote": "down", "preset": "too_slow", "comment": None, "created_at": (NOW - timedelta(hours=1)).isoformat()},
        },
    )
    # Excluded superuser message
    _assistant_message(
        db_session,
        sess_adm,
        NOW - timedelta(hours=1),
        metadata={
            "stop_reason": "terminal_model_failure",
            "feedback": {"vote": "down", "preset": "admin_only", "comment": "Should not appear"},
        },
    )

    # Prior period assistant message (1 healthy, 1 thumbs up)
    _assistant_message(
        db_session,
        sess_ret,
        NOW - timedelta(days=9),
        metadata={
            "stop_reason": "end_turn",
            "feedback": {"vote": "up", "created_at": (NOW - timedelta(days=9)).isoformat()},
        },
        tool_calls=[
            {"id": "prev1", "name": "edit_file", "status": "success", "result": {"id": "fp", "undo": {"before_version_number": 1, "undone": False}}},
        ],
    )

    # Activation events: TTFV & device split
    # writer_a signed up NOW - 3d -> first_file_saved 10 min later, first_ai_action_accepted 30 min later -> earliest = 10.0 min
    _activation(db_session, writer_a, ACTIVATION_EVENT_FIRST_FILE_SAVED, NOW - timedelta(days=3) + timedelta(minutes=10))
    _activation(db_session, writer_a, ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED, NOW - timedelta(days=3) + timedelta(minutes=30))
    _activation(db_session, writer_a, ACTIVATION_EVENT_DASHBOARD_VIEWED, NOW - timedelta(days=3) + timedelta(minutes=2), {"is_mobile": True})
    # writer_b signed up NOW - 2d -> first_ai_action_accepted 20 min later -> 20.0 min; median(10, 20) = 15.0 min
    _activation(db_session, writer_b, ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED, NOW - timedelta(days=2) + timedelta(minutes=20))
    _activation(db_session, writer_b, ACTIVATION_EVENT_DASHBOARD_VIEWED, NOW - timedelta(days=2) + timedelta(minutes=1), {"is_mobile": False})
    db_session.commit()

    # Chapters & net words written:
    # Chapter 1 (draft): pre-window baseline v1=500 words, in-window v2=1200 words -> net +700
    _file_with_versions(
        db_session,
        proj_a,
        "Chapter 1",
        "draft",
        [(1, 500, NOW - timedelta(days=10)), (2, 1200, NOW - timedelta(days=1))],
    )
    # Episode 1 (script): new in window v1=800 -> net +800
    _file_with_versions(
        db_session,
        proj_b,
        "Episode 1",
        "script",
        [(1, 800, NOW - timedelta(hours=3))],
    )
    # Outline file (non-content): ignored
    _file_with_versions(
        db_session,
        proj_a,
        "Outline",
        "outline",
        [(1, 5000, NOW - timedelta(hours=2))],
    )

    response = await client.get(
        "/api/admin/dashboard/ux-health?days=7",
        headers=await _headers(client, admin),
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["days"] == 7
    assert payload["current"]["period_start"] == "2026-09-30T16:00:00+00:00"
    assert payload["current"]["period_end"] == "2026-10-07T08:00:00+00:00"
    assert payload["previous"]["period_start"] == "2026-09-23T16:00:00+00:00"
    assert payload["previous"]["period_end"] == "2026-09-30T08:00:00+00:00"

    m = payload["current"]["metrics"]
    # TTFT sorted: [1000, 2000, 4000, 12000] -> nearest-rank p50=2000, p95=12000
    assert m["ttft_p50_ms"] == 2000
    assert m["ttft_p95_ms"] == 12000
    assert m["ttft_samples"] == 4
    assert m["ttft_slow_tail_count"] == 1
    assert m["ttft_slow_tail_rate"] == 0.25
    assert m["duration_samples"] == 4
    assert m["duration_slow_tail_count"] == 2
    assert m["duration_slow_tail_rate"] == 0.5
    # Cache: hit = 800+600+900+700 = 3000, miss = 200+400+100+300 = 1000 -> 3000/4000 = 0.75
    assert m["prompt_cache_hit_rate"] == 0.75

    # Rounds: 8 total -> 3 healthy, 1 user_stopped, 1 client_disconnected, 3 abnormal
    assert m["total_rounds"] == 8
    assert m["healthy_rounds"] == 3
    assert m["healthy_round_rate"] == 0.375
    assert m["user_stopped_rounds"] == 1
    assert m["user_stopped_rate"] == 0.125
    assert m["client_disconnected_rounds"] == 1
    assert m["client_disconnected_rate"] == 0.125
    assert m["abnormal_stop_rounds"] == 3
    assert m["abnormal_stop_rate"] == 0.375
    assert {item["reason"]: item["count"] for item in m["abnormal_stops"]} == {
        "run_deadline_exceeded": 1,
        "unexecuted_tool_text": 1,
        "tool_failure_circuit_open": 1,
    }

    # Edit undo & delete restore
    assert m["ai_edit_cards"] == 2
    assert m["ai_edit_undone"] == 1
    assert m["ai_edit_undo_rate"] == 0.5
    assert m["ai_delete_cards"] == 2
    assert m["ai_delete_restored"] == 1
    assert m["ai_delete_restore_rate"] == 0.5

    # Feedback
    assert m["feedback_up"] == 2
    assert m["feedback_down"] == 3
    assert m["feedback_total"] == 5
    assert m["thumbs_up_rate"] == 0.4
    assert m["top_dislike_presets"] == [
        {"preset": "too_slow", "count": 2},
        {"preset": "off_topic", "count": 1},
    ]

    # Creator value
    assert m["ttfv_p50_minutes"] == 15.0
    assert m["ttfv_samples"] == 2
    # W1 returning: prior week had {returning_writer, churned_writer, writer_a (v1 of Chapter 1 at -10d)}
    # Actually proj_a belongs to writer_a and had v1 at NOW - 10d -> 3 prior writers, 2 returned (returning_writer + writer_a)
    assert m["w1_eligible_writers"] == 3
    assert m["w1_returning_writers"] == 2
    assert m["w1_retention_rate"] == 0.6667
    assert m["chapters_written"] == 2
    assert m["net_words_written"] == 1500
    assert m["mobile_users"] == 1
    assert m["desktop_users"] == 1
    assert m["mobile_share"] == 0.5

    # Prior period & delta
    prev = payload["previous"]["metrics"]
    assert prev["healthy_round_rate"] == 1.0
    assert prev["thumbs_up_rate"] == 1.0
    assert payload["delta"]["healthy_round_rate"] == -0.625
    assert payload["delta"]["thumbs_up_rate"] == -0.6
    assert payload["delta"]["ai_edit_undo_rate"] == 0.5
    assert payload["delta"]["ttft_p95_ms"] == 7000

    # Daily trend sums to 8 rounds across 7 Beijing days
    assert len(payload["daily"]) == 7
    assert sum(day["rounds"] for day in payload["daily"]) == 8


@pytest.mark.integration
async def test_chat_votes_endpoint_filters_paginates_and_never_leaks_content(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setattr("services.usage.admin_ux_metrics_service.utcnow", lambda: NOW)
    admin = _user(db_session, "votes_admin", NOW - timedelta(days=10), admin=True)
    writer = _user(db_session, "votes_writer", NOW - timedelta(days=10))
    _p1, sess_writer = _project_and_session(db_session, writer, "P1", NOW - timedelta(days=5))
    _p2, sess_admin = _project_and_session(db_session, admin, "PA", NOW - timedelta(days=5))

    _assistant_message(
        db_session,
        sess_writer,
        NOW - timedelta(hours=3),
        content="PRIVATE_NOVEL_CHAPTER_TEXT_1",
        metadata={
            "stop_reason": "end_turn",
            "feedback": {"vote": "up", "preset": None, "comment": "Nice!", "updated_at": (NOW - timedelta(hours=3)).isoformat()},
        },
    )
    _assistant_message(
        db_session,
        sess_writer,
        NOW - timedelta(hours=2),
        content="PRIVATE_NOVEL_CHAPTER_TEXT_2",
        metadata={
            "stop_reason": "end_turn",
            "feedback": {"vote": "down", "preset": "inaccurate", "comment": "Wrong character name", "updated_at": (NOW - timedelta(hours=2)).isoformat()},
        },
    )
    _assistant_message(
        db_session,
        sess_writer,
        NOW - timedelta(hours=1),
        content="PRIVATE_NOVEL_CHAPTER_TEXT_3",
        metadata={
            "stop_reason": "end_turn",
            "feedback": {"vote": "down", "preset": "inaccurate", "comment": "Repeated scene", "updated_at": (NOW - timedelta(hours=1)).isoformat()},
        },
    )
    # Superuser vote excluded
    _assistant_message(
        db_session,
        sess_admin,
        NOW - timedelta(minutes=30),
        content="PRIVATE_ADMIN_TEXT",
        metadata={
            "stop_reason": "end_turn",
            "feedback": {"vote": "down", "preset": "admin_secret", "comment": "Admin vote"},
        },
    )

    headers = await _headers(client, admin)

    # All votes, page_size=2 -> page 1 has 2 newest, page 2 has 1
    resp_all = await client.get("/api/admin/feedback/chat-votes?days=7&vote=all&page=1&page_size=2", headers=headers)
    assert resp_all.status_code == 200
    raw_body = resp_all.text
    assert "PRIVATE_NOVEL_CHAPTER_TEXT" not in raw_body
    assert "SECRET_REASONING" not in raw_body
    assert "admin_secret" not in raw_body

    data = resp_all.json()
    assert data["summary"] == {
        "up": 1,
        "down": 2,
        "total_votes": 3,
        "up_rate": 0.3333,
        "preset_counts": [{"preset": "inaccurate", "count": 2}],
    }
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert set(data["items"][0].keys()) == {
        "message_id",
        "user_id",
        "username",
        "vote",
        "preset",
        "comment",
        "created_at",
    }
    assert data["items"][0]["comment"] == "Repeated scene"

    # Filter vote=down
    resp_down = await client.get("/api/admin/feedback/chat-votes?days=7&vote=down&page=1&page_size=20", headers=headers)
    assert resp_down.status_code == 200
    down_data = resp_down.json()
    assert down_data["total"] == 2
    assert all(item["vote"] == "down" for item in down_data["items"])
    # Summary still reflects full window
    assert down_data["summary"]["up"] == 1
    assert down_data["summary"]["down"] == 2

    # Direct service check for page 2
    page2 = list_chat_feedback_votes(db_session, days=7, vote="all", page=2, page_size=2, now=NOW)
    assert len(page2["items"]) == 1
    assert page2["items"][0]["vote"] == "up"


@pytest.mark.integration
def test_ux_health_30_days_has_bounded_query_count(db_session: Session):
    statements = 0

    def count_statement(*_args):
        nonlocal statements
        statements += 1

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", count_statement)
    try:
        payload = get_ux_health_dashboard(db_session, days=30, now=NOW)
    finally:
        event.remove(bind, "before_cursor_execute", count_statement)

    assert len(payload["daily"]) == 30
    assert statements <= 20


@pytest.mark.integration
def test_ux_health_and_votes_stream_multi_mb_rows_with_yield_per(
    db_session: Session,
    monkeypatch,
):
    """Multi-MB message_metadata/tool_calls must stream via yield_per and skip non-card tool_calls."""
    from sqlalchemy.exc import DataError

    import services.usage.admin_ux_metrics_service as ux_svc

    writer = _user(db_session, "ux_stream_writer", NOW - timedelta(days=10))
    _proj, chat_sess = _project_and_session(db_session, writer, "Large Novel", NOW - timedelta(days=5))

    big_prose = "长篇正文内容" * 50_000  # ~300k chars (~900 KB UTF-8) per blob
    big_thinking = "深度思考过程" * 40_000

    # 1. Read-only query_files turn (no edit/write/delete cards) with giant blobs
    _assistant_message(
        db_session,
        chat_sess,
        NOW - timedelta(hours=3),
        metadata={
            "stop_reason": "end_turn",
            "display_events": [{"type": "thinking_content", "content": big_thinking}],
            "feedback": {"vote": "up", "comment": "Good analysis", "created_at": (NOW - timedelta(hours=3)).isoformat()},
        },
        tool_calls=[
            {
                "id": "tc-read",
                "name": "query_files",
                "status": "success",
                "result": [{"id": "f-1", "title": "Ch 1", "file_type": "draft", "content": big_prose}],
            }
        ],
    )
    # 2. Mixed read + edit/delete turn with giant query_files blob alongside undo/restore cards
    _assistant_message(
        db_session,
        chat_sess,
        NOW - timedelta(hours=2),
        metadata={
            "stop_reason": "end_turn",
            "display_events": [{"type": "thinking_content", "content": big_thinking}],
            "feedback": {"vote": "down", "preset": "too_slow", "comment": "Slow", "created_at": (NOW - timedelta(hours=2)).isoformat()},
        },
        tool_calls=[
            {
                "id": "tc-read-2",
                "name": "query_files",
                "status": "success",
                "result": [{"id": "f-2", "title": "Ch 2", "file_type": "draft", "content": big_prose}],
            },
            {
                "id": "tc-edit",
                "name": "edit_file",
                "status": "success",
                "result": {"data": {"id": "f-2", "undo": {"before_version_number": 1, "undone": True}}},
            },
            {
                "id": "tc-del",
                "name": "delete_file",
                "status": "success",
                "result": {"id": "f-3", "mutation_applied": True, "restored": True},
            },
        ],
    )
    # 3. Malformed JSON metadata/tool_calls row must never crash metrics
    bad_msg = ChatMessage(
        session_id=chat_sess.id,
        role="assistant",
        content="corrupt",
        message_metadata="{not-valid-json",
        tool_calls="[not-valid-json",
        created_at=_naive(NOW - timedelta(hours=1)),
    )
    db_session.add(bad_msg)
    db_session.commit()

    recorded_exec_options: list[dict] = []
    seen_tool_call_values: list[str | None] = []
    ux_health_toast_queries: list[str] = []
    metadata_queries_with_tool_calls: list[str] = []
    original_exec = db_session.exec

    def spy_exec(stmt, *args, **kwargs):
        opts = kwargs.get("execution_options")
        if opts:
            recorded_exec_options.append(dict(opts))
        result = original_exec(stmt, *args, **kwargs)
        sql_text = str(stmt)
        if "chat_message.message_metadata" in sql_text and "chat_message.tool_calls" in sql_text:
            metadata_queries_with_tool_calls.append(sql_text)
        if "chat_message.message_metadata" in sql_text or "chat_message.tool_calls" in sql_text:
            ux_health_toast_queries.append(sql_text)
        if "chat_message.tool_calls" in sql_text:
            # Intercept streamed tuples without altering yield_per semantics
            class _SpyResult:
                def __iter__(self_inner):
                    for row in result:
                        seen_tool_call_values.append(row[1])
                        yield row

                def close(self_inner):
                    result.close()

            return _SpyResult()
        return result

    monkeypatch.setattr(db_session, "exec", spy_exec)
    payload = get_ux_health_dashboard(db_session, days=7, now=NOW)
    # Fast path via chat_message_ux_summary must NEVER select message_metadata or tool_calls!
    assert ux_health_toast_queries == []

    votes = list_chat_feedback_votes(db_session, days=7, vote="all", now=NOW)

    m = payload["current"]["metrics"]
    assert m["total_rounds"] == 3
    assert m["healthy_rounds"] == 2
    assert m["abnormal_stop_rounds"] == 1
    assert m["ai_edit_cards"] == 1
    assert m["ai_edit_undone"] == 1
    assert m["ai_delete_cards"] == 1
    assert m["ai_delete_restored"] == 1
    assert m["feedback_up"] == 1
    assert m["feedback_down"] == 1
    assert votes["summary"]["total_votes"] == 2

    # Simulate un-backfilled legacy rows by clearing chat_message_ux_summary:
    # fallback path must stream via yield_per, keep metadata/tool_calls decoupled,
    # skip read-only/corrupt tool_calls, and match summary metrics identically.
    from sqlalchemy import delete as sa_delete

    from models import ChatMessageUxSummary

    original_exec(sa_delete(ChatMessageUxSummary))
    db_session.commit()

    recorded_exec_options.clear()
    seen_tool_call_values.clear()
    metadata_queries_with_tool_calls.clear()

    unbackfilled_payload = get_ux_health_dashboard(db_session, days=7, now=NOW)
    assert any(opt.get("yield_per") == ux_svc._STREAM_CHAT_BATCH_SIZE for opt in recorded_exec_options)
    assert any(opt.get("yield_per") == ux_svc._STREAM_SCALAR_BATCH_SIZE for opt in recorded_exec_options)
    assert metadata_queries_with_tool_calls == []
    assert len(seen_tool_call_values) == 1
    assert seen_tool_call_values[0] is not None
    assert unbackfilled_payload["current"]["metrics"] == payload["current"]["metrics"]

    # Verify PostgreSQL projection path falls back cleanly on DataError (e.g. corrupt JSON row)
    monkeypatch.setattr(ux_svc, "_is_postgres_session", lambda _s: True)
    pg_attempts = 0

    def fail_pg_projected_exec(stmt, *args, **kwargs):
        nonlocal pg_attempts
        sql_text = str(stmt)
        if "projected_metadata" in sql_text or "projected_feedback" in sql_text:
            pg_attempts += 1
            raise DataError("SELECT ...", {}, Exception("invalid input syntax for type json"))
        return original_exec(stmt, *args, **kwargs)

    monkeypatch.setattr(db_session, "exec", fail_pg_projected_exec)
    fallback_payload = get_ux_health_dashboard(db_session, days=7, now=NOW)
    fallback_votes = list_chat_feedback_votes(db_session, days=7, vote="all", now=NOW)

    assert pg_attempts == 2  # un-backfilled metadata window + chat_votes
    assert fallback_payload["current"]["metrics"] == payload["current"]["metrics"]
    assert fallback_votes["summary"] == votes["summary"]


@pytest.mark.integration
def test_chat_message_ux_summary_syncs_across_mutations_and_backfill(db_session: Session):
    """chat_message_ux_summary stays in sync across insert, feedback, undo/restore, load_only, and backfill."""
    from sqlalchemy import delete as sa_delete
    from sqlalchemy.orm import load_only
    from sqlmodel import select

    from models import ChatMessageUxSummary
    from services.chat_feedback_service import chat_feedback_service
    from services.chat_tool_cards import record_delete_card_restored, record_edit_card_undone
    from services.usage.chat_message_ux_summary_backfill import (
        backfill_chat_message_ux_summaries,
        count_pending_chat_message_ux_summaries,
    )

    writer = _user(db_session, "ux_sync_writer", NOW - timedelta(days=5))
    proj, chat_sess = _project_and_session(db_session, writer, "Sync Novel", NOW - timedelta(days=3))
    after_token = NOW - timedelta(hours=2)

    msg = _assistant_message(
        db_session,
        chat_sess,
        NOW - timedelta(hours=2),
        metadata={"stop_reason": "end_turn"},
        tool_calls=[
            {
                "id": "tc-edit-sync",
                "name": "edit_file",
                "status": "success",
                "result": {
                    "data": {
                        "id": "file-1",
                        "undo": {
                            "before_version_number": 1,
                            "expected_after_updated_at": after_token.isoformat(),
                            "undone": False,
                        },
                    }
                },
            },
            {
                "id": "tc-del-sync",
                "name": "delete_file",
                "status": "success",
                "result": {"id": "file-2", "mutation_applied": True, "restored": False},
            },
        ],
    )

    summary = db_session.get(ChatMessageUxSummary, msg.id)
    assert summary is not None
    assert summary.round_category == "healthy"
    assert summary.feedback_vote is None
    assert summary.ai_edit_cards == 1
    assert summary.ai_edit_undone == 0
    assert summary.ai_delete_cards == 1
    assert summary.ai_delete_restored == 0

    # 1. Feedback update syncs vote + preset
    chat_feedback_service.submit_feedback(
        session=db_session,
        current_user=writer,
        message_id=msg.id,
        vote="down",
        preset="too_slow",
        comment="slow response",
    )
    db_session.expire_all()
    summary = db_session.get(ChatMessageUxSummary, msg.id)
    assert summary is not None
    assert summary.feedback_vote == "down"
    assert summary.feedback_preset == "too_slow"

    # 2. Edit undo & delete restore card updates sync counters
    assert record_edit_card_undone(
        db_session,
        user_id=writer.id,
        project_id=proj.id,
        file_id="file-1",
        message_id=msg.id,
        tool_use_id="tc-edit-sync",
        before_version_number=1,
        expected_after_updated_at=after_token,
    )
    assert record_delete_card_restored(
        db_session,
        user_id=writer.id,
        project_id=proj.id,
        file_id="file-2",
        message_id=msg.id,
        tool_use_id="tc-del-sync",
    )
    db_session.expire_all()
    summary = db_session.get(ChatMessageUxSummary, msg.id)
    assert summary is not None
    assert summary.ai_edit_undone == 1
    assert summary.ai_delete_restored == 1

    # 3. Partial load_only update (chat_storage archival pattern) must not fail or corrupt summary
    locked = db_session.exec(
        select(ChatMessage)
        .options(load_only(ChatMessage.id, ChatMessage.tool_calls))
        .where(ChatMessage.id == msg.id)
    ).one()
    locked.tool_calls = locked.tool_calls  # no-op touch on load_only object
    db_session.add(locked)
    db_session.commit()

    # 4. Deleting summary and running backfill restores identical row
    db_session.exec(sa_delete(ChatMessageUxSummary))
    db_session.commit()
    assert count_pending_chat_message_ux_summaries(db_session.connection()) == 1
    backfilled = backfill_chat_message_ux_summaries(db_session.connection(), batch_size=10)
    db_session.commit()
    assert backfilled == 1
    assert count_pending_chat_message_ux_summaries(db_session.connection()) == 0

    rebuilt = db_session.get(ChatMessageUxSummary, msg.id)
    assert rebuilt is not None
    assert rebuilt.round_category == "healthy"
    assert rebuilt.feedback_vote == "down"
    assert rebuilt.feedback_preset == "too_slow"
    assert rebuilt.ai_edit_cards == 1
    assert rebuilt.ai_edit_undone == 1
    assert rebuilt.ai_delete_cards == 1
    assert rebuilt.ai_delete_restored == 1


def test_postgres_autocommit_backfill_skips_savepoint():
    """Inside Alembic's autocommit_block(), SQLAlchemy reports in_transaction()=True
    on a fake transaction while PostgreSQL is in AUTOCOMMIT; backfill must never emit SAVEPOINT."""
    from types import SimpleNamespace

    from services.usage.chat_message_ux_summary_backfill import backfill_chat_message_ux_summaries

    executed_sql: list[str] = []
    select_calls = 0

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _FakePgAutocommitConnection:
        dialect = SimpleNamespace(name="postgresql")

        def get_execution_options(self):
            return {"isolation_level": "AUTOCOMMIT"}

        def get_isolation_level(self):
            return "AUTOCOMMIT"

        def in_transaction(self):
            return True

        def begin_nested(self):
            raise AssertionError("SAVEPOINT must not be used in AUTOCOMMIT mode")

        def execute(self, stmt, params=None):
            nonlocal select_calls
            sql = str(stmt)
            executed_sql.append(sql)
            if "SELECT cm.id" in sql:
                select_calls += 1
                return _FakeResult([("m1",), ("m2",)] if select_calls == 1 else [])
            return _FakeResult([])

    processed = backfill_chat_message_ux_summaries(
        _FakePgAutocommitConnection(),  # type: ignore[arg-type]
        batch_size=10,
        commit_each_batch=False,
    )
    assert processed == 2
    assert any("INSERT INTO chat_message_ux_summary" in sql for sql in executed_sql)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        # Clean completion stays healthy
        ({"stop_reason": "end_turn", "stop_outcome": {"reason": "completed"}}, ("healthy", None)),
        # Collaboration 12-iteration cap with stop_reason='end_turn' must NOT be healthy
        (
            {
                "stop_reason": "end_turn",
                "stop_outcome": {"reason": "incomplete", "charged": True, "saved_output": True},
                "status_cards": [
                    {"type": "iteration_exhausted", "layer": "collaboration", "iterationsUsed": 12, "maxIterations": 12}
                ],
            },
            ("abnormal", "iteration_exhausted"),
        ),
        # Empty-file correction exhaustion (even after rollback leaves no unwritten_files)
        (
            {
                "stop_reason": "end_turn",
                "stop_outcome": {"reason": "incomplete", "charged": True, "saved_output": False},
                "status_cards": [
                    {
                        "type": "iteration_exhausted",
                        "layer": "empty_file_correction",
                        "rolledBackFiles": [{"id": "f1", "title": "第1章"}],
                    }
                ],
            },
            ("abnormal", "empty_file_correction"),
        ),
        # Wall-clock pause card under end_turn maps to run_deadline_exceeded
        (
            {
                "stop_reason": "end_turn",
                "status_cards": [
                    {"type": "iteration_exhausted", "layer": "collaboration", "limit": "wall_clock"}
                ],
            },
            ("abnormal", "run_deadline_exceeded"),
        ),
        # Unexecuted tool text card even if stop_outcome was not persisted
        (
            {
                "stop_reason": "end_turn",
                "status_cards": [{"type": "iteration_exhausted", "layer": "unexecuted_tool_text"}],
            },
            ("abnormal", "unexecuted_tool_text"),
        ),
        # Tool call layer card under end_turn maps to max_turns_exceeded
        (
            {
                "stop_reason": "end_turn",
                "status_cards": [{"type": "iteration_exhausted", "layer": "tool_call"}],
            },
            ("abnormal", "max_turns_exceeded"),
        ),
        # Incomplete stop_outcome without status cards still counts as abnormal
        (
            {"stop_reason": "end_turn", "stop_outcome": {"reason": "incomplete"}},
            ("abnormal", "incomplete"),
        ),
        # Unwritten files without exhaustion cards keeps unwritten_files reason
        (
            {
                "stop_reason": "end_turn",
                "stop_outcome": {"reason": "incomplete", "unwritten_files": [{"id": "f1", "title": "Ch1"}]},
            },
            ("abnormal", "unwritten_files"),
        ),
    ],
)
def test_classify_round_marks_iteration_exhausted_and_incomplete_rounds_abnormal(metadata, expected):
    from models.chat_message_ux_summary import classify_round

    assert classify_round(metadata) == expected


@pytest.mark.integration
def test_ux_health_dashboard_and_fallback_classify_iteration_exhausted_as_abnormal(
    db_session: Session,
):
    """End-to-end verification across chat_message_ux_summary fast path and un-backfilled fallback."""
    from sqlalchemy import delete as sa_delete

    from models import ChatMessageUxSummary

    writer = _user(db_session, "ux_exhausted_writer", NOW - timedelta(days=5))
    _, chat_sess = _project_and_session(db_session, writer, "Exhausted Novel", NOW - timedelta(days=2))

    # 1. Clean healthy end_turn
    _assistant_message(
        db_session,
        chat_sess,
        NOW - timedelta(hours=3),
        metadata={"stop_reason": "end_turn", "stop_outcome": {"reason": "completed"}},
    )
    # 2. Collaboration cap hit (0-char chat text + iteration_exhausted card, stop_reason='end_turn')
    collab_msg = _assistant_message(
        db_session,
        chat_sess,
        NOW - timedelta(hours=2),
        content="",
        metadata={
            "stop_reason": "end_turn",
            "stop_outcome": {"reason": "incomplete", "charged": True, "saved_output": True},
            "status_cards": [
                {"type": "iteration_exhausted", "layer": "collaboration", "iterationsUsed": 12, "maxIterations": 12}
            ],
        },
    )
    # 3. Empty file correction exhaustion (stop_reason='end_turn')
    empty_msg = _assistant_message(
        db_session,
        chat_sess,
        NOW - timedelta(hours=1),
        content="",
        metadata={
            "stop_reason": "end_turn",
            "stop_outcome": {"reason": "incomplete", "charged": True, "saved_output": False},
            "status_cards": [
                {"type": "iteration_exhausted", "layer": "empty_file_correction", "rolledBackFiles": []}
            ],
        },
    )

    collab_summary = db_session.get(ChatMessageUxSummary, collab_msg.id)
    empty_summary = db_session.get(ChatMessageUxSummary, empty_msg.id)
    assert collab_summary is not None
    assert collab_summary.round_category == "abnormal"
    assert collab_summary.abnormal_reason == "iteration_exhausted"
    assert empty_summary is not None
    assert empty_summary.round_category == "abnormal"
    assert empty_summary.abnormal_reason == "empty_file_correction"

    fast_payload = get_ux_health_dashboard(db_session, days=7, now=NOW)
    m = fast_payload["current"]["metrics"]
    assert m["total_rounds"] == 3
    assert m["healthy_rounds"] == 1
    assert m["abnormal_stop_rounds"] == 2
    reasons = {item["reason"]: item["count"] for item in m["abnormal_stops"]}
    assert reasons == {"iteration_exhausted": 1, "empty_file_correction": 1}

    # Clear summary table -> un-backfilled fallback path must produce identical metrics
    db_session.exec(sa_delete(ChatMessageUxSummary))
    db_session.commit()
    fallback_payload = get_ux_health_dashboard(db_session, days=7, now=NOW)
    assert fallback_payload["current"]["metrics"] == fast_payload["current"]["metrics"]

