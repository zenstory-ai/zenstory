"""Tests for Phase 2 UX metrics: agent_round_metric ledger, chapter_first_prose_ms,
non-blocking settlement persistence, admin stage-latency API, and Alembic round trip.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, SQLModel, select

import models  # noqa: F401
from agent.core.events import EventType, StreamEvent, done_event
from agent.core.run_meter import AgentRunMeter
from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
from agent.core.workflow_events import StreamEventType
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from models import (
    CHANGE_SOURCE_AI,
    CHANGE_SOURCE_USER,
    CHANGE_TYPE_CREATE,
    CHANGE_TYPE_EDIT,
    CHANGE_TYPE_RESTORE,
    FILE_TYPE_DRAFT,
    FILE_TYPE_OUTLINE,
    FILE_TYPE_SCRIPT,
    AgentRoundMetric,
    File,
    FileVersion,
    Project,
    User,
)
from services.core.auth_service import hash_password
from services.usage import admin_stage_latency_service
from services.usage.admin_stage_latency_service import (
    build_agent_round_metric,
    get_stage_latency_metrics,
    record_agent_round_metric,
)

# Wednesday 2026-10-07 10:00 Beijing (= 02:00 UTC).
NOW = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)
SERVER_DIR = Path(__file__).resolve().parents[2]
REVISION = "20261122_000000"
DOWN_REVISION = "20261120_000000"


def _create_user(db_session: Session, name: str, *, admin: bool = False) -> User:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_superuser=admin,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _auth_headers(client: AsyncClient, username: str) -> dict[str, str]:
    resp = await client.post("/api/auth/login", data={"username": username, "password": "password123"})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_project(db_session: Session, owner: User, name: str = "Novel", *, deleted: bool = False) -> Project:
    project = Project(name=name, owner_id=owner.id, is_deleted=deleted)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


# ---------------------------------------------------------------------------
# 1. Exact chapter_first_prose_ms & leaked_tool_call_count measurement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_adapter_measures_chapter_first_prose_ms_and_leaked_tool_calls(monkeypatch):
    now_s = [100.0]
    monkeypatch.setattr("agent.core.run_meter.time.monotonic", lambda: now_s[0])

    meter = AgentRunMeter()
    adapter = StreamAdapter(
        StreamAdapterConfig(
            project_id="proj-1",
            user_id="user-1",
            run_meter=meter,
        )
    )
    saved_calls: list[tuple[str, str]] = []

    async def fake_save_file_content(file_id: str, content: str, **_kwargs: Any) -> bool:
        saved_calls.append((file_id, content))
        return True

    monkeypatch.setattr(adapter, "_save_file_content", fake_save_file_content)

    async def workflow_events():
        now_s[0] = 100.0
        yield WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "write_chapter",
                "tool_use_id": "call-1",
                "result": {
                    "status": "success",
                    "data": {
                        "id": "file-ch1",
                        "title": "第1章 开端",
                        "file_type": "draft",
                        "action": "created",
                        "content": "",
                        "updated_at": "2026-10-07T00:00:00Z",
                    },
                },
            },
        )
        # Whitespace chunk inside <file> should not stop the timer prematurely; first real prose chunk does.
        now_s[0] = 101.0
        yield WorkflowStreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n   \n"})
        now_s[0] = 102.35
        yield WorkflowStreamEvent(
            type=StreamEventType.TEXT,
            data={"text": "夜色渐深，灯火初明，城楼上的更夫敲响了三更鼓，风雪沿着青石阶缓缓铺开，远山只剩一道淡青色的轮廓。"},
        )
        now_s[0] = 105.0
        yield WorkflowStreamEvent(type=StreamEventType.TEXT, data={"text": "\n第二段正文，落雪无声。\n</file>"})
        yield WorkflowStreamEvent(
            type=StreamEventType.ITERATION_EXHAUSTED,
            data={"layer": "unexecuted_tool_text", "iterations_used": 1, "max_iterations": 1, "reason": "leaked"},
        )

    emitted = [event async for event in adapter.process_workflow_events(workflow_events())]
    assert any(event.type == EventType.FILE_CONTENT for event in emitted)
    assert saved_calls and saved_calls[0][0] == "file-ch1"
    # Armed at 100.0 -> first non-whitespace prose chunk at 102.35 => 2350.0 ms; subsequent chunks don't overwrite.
    assert meter.chapter_first_prose_ms == 2350.0
    assert meter.leaked_tool_call_count == 1
    report = meter.timing_report()
    assert report["chapter_first_prose_ms"] == 2350.0
    assert report["leaked_tool_call_count"] == 1


@pytest.mark.asyncio
async def test_stream_adapter_increments_leaked_tool_call_count_on_leaked_error():
    meter = AgentRunMeter()
    adapter = StreamAdapter(StreamAdapterConfig(project_id="proj-1", user_id="user-1", run_meter=meter))

    async def workflow_events():
        yield WorkflowStreamEvent(
            type=StreamEventType.ERROR,
            data={"error": "leaked tool call", "reason": "leaked_tool_call"},
        )

    _ = [event async for event in adapter.process_workflow_events(workflow_events())]
    assert meter.leaked_tool_call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason", ["leaked_tool_call_early_stop", "leaked_tool_call"])
async def test_stream_adapter_increments_leaked_tool_call_count_on_message_end_stop_reason(stop_reason: str):
    meter = AgentRunMeter()
    adapter = StreamAdapter(StreamAdapterConfig(project_id="proj-1", user_id="user-1", run_meter=meter))

    async def workflow_events():
        yield WorkflowStreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={"stop_reason": stop_reason, "usage": {"input_tokens": 10, "output_tokens": 5}},
        )

    emitted = [event async for event in adapter.process_workflow_events(workflow_events())]
    assert any(event.type == EventType.DONE for event in emitted)
    assert meter.leaked_tool_call_count == 1
    assert meter.timing_report()["leaked_tool_call_count"] == 1


def test_client_ui_action_prefers_top_level_over_metadata():
    from api.agent import _client_ui_action

    assert _client_ui_action({"ui_action": "fallback_action"}, top_level="natural_polish") == "natural_polish"
    assert _client_ui_action({"ui_action": "natural_polish"}, top_level=None) == "natural_polish"
    assert _client_ui_action({"ui_action": "INVALID ACTION!!"}, top_level=None) is None
    assert _client_ui_action({}, top_level="   ") is None


# ---------------------------------------------------------------------------
# 2. Settlement persistence & fault isolation
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_stream_settlement_persists_round_metric_and_isolates_db_failures(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    writer = _create_user(db_session, "metric_writer")
    project = _create_project(db_session, writer, "Metric Novel")
    headers = await _auth_headers(client, "metric_writer")

    class _FakeAgentService:
        async def process_stream(self, **kwargs: Any):
            run_report = kwargs.get("run_report")
            if isinstance(run_report, dict):
                run_report.update(
                    {
                        "model": "deepseek-flash",
                        "duration_ms": 4321,
                        "stop_reason": "end_turn",
                        "tool_calls_count": 3,
                        "session_id": "sess-metric-1",
                        "first_tool_started_ms": 420.4,
                        "chapter_first_prose_ms": 1850.6,
                        "first_body_persisted_ms": 3900.2,
                        "leaked_tool_call_count": 0,
                        "stage_timings": {
                            "prepare_context": {"count": 1, "total_ms": 125.7, "max_ms": 125.7},
                        },
                        "tool_failures": {
                            "edit_file": {
                                "count": 2,
                                "rolled_back_batches": 0,
                                "error_types": {"file_body_pending": 1, "anchor_not_found": 1},
                            }
                        },
                    }
                )
            yield StreamEvent(type=EventType.SESSION_STARTED, data={"session_id": "sess-metric-1"}).to_sse()
            yield StreamEvent(type=EventType.FILE_CREATED, data={"file_id": "f1", "file_type": "draft", "title": "Ch1"}).to_sse()
            yield StreamEvent(type=EventType.FILE_CONTENT, data={"file_id": "f1", "chunk": "正文内容"}).to_sse()
            yield done_event(session_id="sess-metric-1", file_mutated=True).to_sse()

    with patch("api.agent.get_agent_service", return_value=_FakeAgentService()):
        resp = await client.post(
            "/api/v1/agent/stream",
            headers=headers,
            json={
                "project_id": project.id,
                "message": "写第一章",
                "metadata": {"is_mobile": True, "ui_action": "natural_polish"},
            },
        )
    assert resp.status_code == 200
    assert "event: done" in resp.text
    run_id = resp.headers["X-Agent-Run-ID"]

    rows = list(db_session.exec(select(AgentRoundMetric).where(AgentRoundMetric.agent_run_id == run_id)).all())
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == writer.id
    assert row.project_id == project.id
    assert row.session_id == "sess-metric-1"
    assert row.is_mobile is True
    assert row.ui_action == "natural_polish"
    assert row.primary_model == "deepseek-flash"
    assert row.prepare_context_ms == 126
    assert row.first_tool_started_ms == 420
    assert row.chapter_first_prose_ms == 1851
    assert row.first_body_persisted_ms == 3900
    assert row.total_duration_ms == 4321
    assert row.first_output_ms is not None and row.first_output_ms >= 0
    assert row.stop_reason == "end_turn"
    assert row.billing_reason == "completed"
    assert row.write_succeeded is True
    assert row.user_stopped is False
    assert row.client_disconnected is False
    assert row.deadline_exceeded is False
    assert row.runaway_stop is False
    assert row.file_body_pending_count == 1
    assert row.leaked_tool_call_count == 0
    assert row.tool_calls_count == 3
    assert row.tool_failures_count == 2

    # Fault isolation: if metric DB persistence explodes, the user stream still succeeds completely.
    def _exploding_session_factory():
        raise RuntimeError("simulated metric db outage")

    monkeypatch.setattr(admin_stage_latency_service, "_session_factory", _exploding_session_factory)
    with patch("api.agent.get_agent_service", return_value=_FakeAgentService()):
        resp_fault = await client.post(
            "/api/v1/agent/stream",
            headers=headers,
            json={"project_id": project.id, "message": "再写一章"},
        )
    assert resp_fault.status_code == 200
    assert "event: done" in resp_fault.text


def test_record_agent_round_metric_is_idempotent_on_duplicate_run_id(db_session: Session):
    user = _create_user(db_session, "dup_user")
    first = build_agent_round_metric(user_id=user.id, agent_run_id="dup-run-1", billing_reason="completed")
    second = build_agent_round_metric(user_id=user.id, agent_run_id="dup-run-1", billing_reason="completed")
    assert record_agent_round_metric(first) is True
    assert record_agent_round_metric(second) is False


# ---------------------------------------------------------------------------
# 3. Admin Stage Latency & 24h AI Draft Survival API
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_stage_latency_auth_and_validation(client: AsyncClient, db_session: Session):
    _create_user(db_session, "stage_admin", admin=True)
    _create_user(db_session, "stage_member", admin=False)

    assert (await client.get("/api/admin/usage/stage-latency")).status_code == 401

    member_headers = await _auth_headers(client, "stage_member")
    assert (await client.get("/api/admin/usage/stage-latency", headers=member_headers)).status_code == 403

    admin_headers = await _auth_headers(client, "stage_admin")
    assert (
        await client.get("/api/admin/usage/stage-latency", params={"days": 5}, headers=admin_headers)
    ).status_code == 422


@pytest.mark.integration
async def test_stage_latency_percentiles_device_split_and_draft_survival(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setattr(admin_stage_latency_service, "utcnow", lambda: NOW)
    admin = _create_user(db_session, "lat_admin", admin=True)
    alice = _create_user(db_session, "lat_alice", admin=False)
    bob = _create_user(db_session, "lat_bob", admin=False)

    # Seed 4 customer rounds in 7d window (Oct 1 - Oct 7 Beijing) + 1 admin round + 1 out-of-window round.
    rounds = [
        # Oct 7 Beijing (01:00 UTC = 09:00 Beijing) - Desktop, fast
        AgentRoundMetric(
            occurred_at=datetime(2026, 10, 7, 1, 0),
            user_id=alice.id,
            agent_run_id="r-1",
            is_mobile=False,
            prepare_context_ms=100,
            first_output_ms=2_000,
            chapter_first_prose_ms=3_000,
            first_body_persisted_ms=8_000,
            total_duration_ms=10_000,
            write_succeeded=True,
            billing_reason="completed",
        ),
        # Oct 7 Beijing (00:30 UTC = 08:30 Beijing) - Mobile, slow (>=10s), file_body_pending & user_stopped
        AgentRoundMetric(
            occurred_at=datetime(2026, 10, 7, 0, 30),
            user_id=bob.id,
            agent_run_id="r-2",
            is_mobile=True,
            prepare_context_ms=500,
            first_output_ms=12_000,
            chapter_first_prose_ms=15_000,
            total_duration_ms=20_000,
            user_stopped=True,
            file_body_pending_count=2,
            leaked_tool_call_count=1,
            billing_reason="user_stopped",
        ),
        # Oct 6 Beijing (15:00 UTC = 23:00 Beijing) - Desktop, medium
        AgentRoundMetric(
            occurred_at=datetime(2026, 10, 6, 15, 0),
            user_id=alice.id,
            agent_run_id="r-3",
            is_mobile=False,
            prepare_context_ms=300,
            first_output_ms=6_000,
            chapter_first_prose_ms=9_000,
            first_body_persisted_ms=14_000,
            total_duration_ms=15_000,
            write_succeeded=True,
            billing_reason="completed",
        ),
        # Oct 1 Beijing (16:00 UTC on Sep 30 = 00:00 Beijing on Oct 1) - Mobile, disconnected, no prose
        AgentRoundMetric(
            occurred_at=datetime(2026, 9, 30, 16, 0),
            user_id=bob.id,
            agent_run_id="r-4",
            is_mobile=True,
            prepare_context_ms=200,
            first_output_ms=10_000,  # exactly >= 10s slow threshold
            chapter_first_prose_ms=None,
            total_duration_ms=11_000,
            client_disconnected=True,
            billing_reason="client_disconnected",
        ),
        # Superuser round inside window -> MUST be excluded!
        AgentRoundMetric(
            occurred_at=datetime(2026, 10, 7, 1, 15),
            user_id=admin.id,
            agent_run_id="r-admin",
            is_mobile=False,
            prepare_context_ms=99_999,
            first_output_ms=99_999,
            chapter_first_prose_ms=99_999,
            file_body_pending_count=5,
            leaked_tool_call_count=5,
        ),
        # Outside 7d window (Sep 30 23:59:59 Beijing = Sep 30 15:59:59 UTC) -> MUST be excluded!
        AgentRoundMetric(
            occurred_at=datetime(2026, 9, 30, 15, 59, 59),
            user_id=alice.id,
            agent_run_id="r-old",
            is_mobile=False,
            prepare_context_ms=88_888,
            first_output_ms=88_888,
        ),
    ]
    for item in rounds:
        db_session.add(item)

    # Seed AI-created drafts/scripts for 24h survival / co-edit rate.
    # NOW is 2026-10-07 02:00 UTC -> 24h cutoff is 2026-10-06 02:00 UTC.
    alice_project = _create_project(db_session, alice, "Alice Book")
    admin_project = _create_project(db_session, admin, "Admin Book")

    def _add_file_with_versions(
        project: Project,
        *,
        title: str,
        file_type: str,
        created_at: datetime,
        versions: list[tuple[str, str, int]],
        is_deleted: bool = False,
    ) -> None:
        f = File(
            project_id=project.id,
            title=title,
            file_type=file_type,
            content="text",
            created_at=created_at,
            updated_at=created_at,
            is_deleted=is_deleted,
        )
        db_session.add(f)
        db_session.flush()
        for idx, (source, change_type, words) in enumerate(versions, start=1):
            db_session.add(
                FileVersion(
                    file_id=f.id,
                    project_id=project.id,
                    version_number=idx,
                    content="v",
                    is_base_version=(idx == 1),
                    word_count=words,
                    char_count=words,
                    change_type=change_type,
                    change_source=source,
                    created_at=created_at + timedelta(minutes=idx),
                )
            )

    eligible_time = datetime(2026, 10, 4, 10, 0)
    too_recent_time = datetime(2026, 10, 6, 12, 0)  # < 24h old

    # 1) Eligible AI draft, survived & co-edited by user
    _add_file_with_versions(
        alice_project,
        title="第1章",
        file_type=FILE_TYPE_DRAFT,
        created_at=eligible_time,
        versions=[(CHANGE_SOURCE_AI, CHANGE_TYPE_CREATE, 1200), (CHANGE_SOURCE_USER, CHANGE_TYPE_EDIT, 1400)],
    )
    # 2) Eligible AI script, survived untouched
    _add_file_with_versions(
        alice_project,
        title="第1集",
        file_type=FILE_TYPE_SCRIPT,
        created_at=eligible_time,
        versions=[(CHANGE_SOURCE_AI, CHANGE_TYPE_CREATE, 900)],
    )
    # 3) Eligible AI draft, deleted by user
    _add_file_with_versions(
        alice_project,
        title="第2章",
        file_type=FILE_TYPE_DRAFT,
        created_at=eligible_time,
        versions=[(CHANGE_SOURCE_AI, CHANGE_TYPE_CREATE, 800)],
        is_deleted=True,
    )
    # 4) Eligible AI draft, reverted via restore
    _add_file_with_versions(
        alice_project,
        title="第3章",
        file_type=FILE_TYPE_DRAFT,
        created_at=eligible_time,
        versions=[(CHANGE_SOURCE_AI, CHANGE_TYPE_CREATE, 800), (CHANGE_SOURCE_USER, CHANGE_TYPE_RESTORE, 0)],
    )
    # Excluded: <24h old AI draft
    _add_file_with_versions(
        alice_project,
        title="第4章",
        file_type=FILE_TYPE_DRAFT,
        created_at=too_recent_time,
        versions=[(CHANGE_SOURCE_AI, CHANGE_TYPE_CREATE, 1000)],
    )
    # Excluded: user-created draft
    _add_file_with_versions(
        alice_project,
        title="第5章",
        file_type=FILE_TYPE_DRAFT,
        created_at=eligible_time,
        versions=[(CHANGE_SOURCE_USER, CHANGE_TYPE_CREATE, 1000)],
    )
    # Excluded: AI outline (not draft/script)
    _add_file_with_versions(
        alice_project,
        title="大纲",
        file_type=FILE_TYPE_OUTLINE,
        created_at=eligible_time,
        versions=[(CHANGE_SOURCE_AI, CHANGE_TYPE_CREATE, 500)],
    )
    # Excluded: superuser AI draft
    _add_file_with_versions(
        admin_project,
        title="管理员第1章",
        file_type=FILE_TYPE_DRAFT,
        created_at=eligible_time,
        versions=[(CHANGE_SOURCE_AI, CHANGE_TYPE_CREATE, 1000)],
    )
    db_session.commit()

    admin_headers = await _auth_headers(client, "lat_admin")
    resp = await client.get("/api/admin/usage/stage-latency", params={"days": 7}, headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["days"] == 7
    assert body["timezone"] == "Asia/Shanghai"
    assert (body["period_start"], body["period_end"]) == ("2026-10-01", "2026-10-07")
    assert body["slow_threshold_ms"] == 10_000
    assert body["total_rounds"] == 4

    # prepare_context: [100, 200, 300, 500] -> nearest-rank p50=200, p95=500
    assert body["prepare_context_ms"] == {"p50": 200, "p95": 500, "samples": 4}
    # first_output: [2000, 6000, 10000, 12000] -> p50=6000, p95=12000, slow(>=10000)=2/4=0.5
    assert body["first_output_ms"] == {
        "p50": 6000,
        "p95": 12000,
        "samples": 4,
        "slow_count": 2,
        "slow_rate": 0.5,
    }
    # chapter_first_prose: [3000, 9000, 15000] -> p50=9000, p90=15000, p95=15000, slow=1/3=0.3333
    assert body["chapter_first_prose_ms"] == {
        "p50": 9000,
        "p90": 15000,
        "p95": 15000,
        "samples": 3,
        "slow_count": 1,
        "slow_rate": 0.3333,
    }
    assert body["first_body_persisted_ms"] == {"p50": 8000, "p95": 14000, "samples": 2}
    assert body["file_body_pending_rounds"] == 1
    assert body["file_body_pending_rate"] == 0.25
    assert body["leaked_tool_call_rounds"] == 1
    assert body["leaked_tool_call_rate"] == 0.25
    assert body["user_stopped_rounds"] == 1
    assert body["user_stopped_rate"] == 0.25
    assert body["client_disconnected_rounds"] == 1
    assert body["client_disconnected_rate"] == 0.25

    # Desktop vs mobile split
    desktop = body["desktop_vs_mobile"]["desktop"]
    mobile = body["desktop_vs_mobile"]["mobile"]
    unknown = body["desktop_vs_mobile"]["unknown"]
    assert desktop["rounds"] == 2
    assert desktop["first_output_ms"]["slow_rate"] == 0.0
    assert desktop["client_disconnected_rate"] == 0.0
    assert mobile["rounds"] == 2
    assert mobile["first_output_ms"]["slow_rate"] == 1.0
    assert mobile["client_disconnected_rate"] == 0.5
    assert unknown["rounds"] == 0
    assert unknown["first_output_ms"]["p50"] is None

    # 24h AI draft survival: 4 eligible (2 survived, 1 co-edited, 2 deleted/reverted)
    assert body["ai_draft_survival"] == {
        "eligible_drafts": 4,
        "survived_drafts": 2,
        "co_edited_drafts": 1,
        "deleted_or_reverted_drafts": 2,
        "survival_rate": 0.5,
        "co_edit_rate": 0.25,
        "observation_hours": 24,
    }

    # Daily breakdown: 7 Beijing calendar days, zero-filled for days without rounds
    assert [row["date"] for row in body["daily"]] == [f"2026-10-0{d}" for d in range(1, 8)]
    by_day = {row["date"]: row for row in body["daily"]}
    assert by_day["2026-10-01"]["rounds"] == 1
    assert by_day["2026-10-01"]["client_disconnected_rate"] == 1.0
    assert by_day["2026-10-03"]["rounds"] == 0
    assert by_day["2026-10-03"]["first_output_p50_ms"] is None
    assert by_day["2026-10-07"]["rounds"] == 2
    assert by_day["2026-10-07"]["file_body_pending_rate"] == 0.5


def test_stage_latency_empty_database_returns_none_rates(db_session: Session):
    result = get_stage_latency_metrics(db_session, days=7, now=NOW)
    assert result["total_rounds"] == 0
    assert result["prepare_context_ms"] == {"p50": None, "p95": None, "samples": 0}
    assert result["first_output_ms"]["slow_rate"] is None
    assert result["chapter_first_prose_ms"]["p90"] is None
    assert result["file_body_pending_rate"] is None
    assert result["ai_draft_survival"]["eligible_drafts"] == 0
    assert result["ai_draft_survival"]["survival_rate"] is None
    assert result["ai_draft_survival"]["co_edit_rate"] is None


# ---------------------------------------------------------------------------
# 4. Alembic upgrade & downgrade round trip on disposable SQLite DB
# ---------------------------------------------------------------------------


def _alembic(db_url: str, *args: str) -> None:
    candidate = Path(sys.executable).parent / "alembic"
    executable = str(candidate) if candidate.exists() else shutil.which("alembic")
    assert executable, "alembic CLI not found"
    completed = subprocess.run(
        [executable, *args],
        cwd=SERVER_DIR,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.integration
@pytest.mark.slow
def test_agent_round_metric_alembic_upgrade_and_downgrade_round_trip(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'agent-round-metric-migration.db'}"
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    _alembic(db_url, "stamp", "head")
    _alembic(db_url, "downgrade", "-1")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "agent_round_metric" not in inspector.get_table_names()
    chat_indexes = {idx["name"] for idx in inspector.get_indexes("chat_message")}
    fv_indexes = {idx["name"] for idx in inspector.get_indexes("file_version")}
    assert "ix_chat_message_created_at" not in chat_indexes
    assert "ix_file_version_created_at" not in fv_indexes
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == DOWN_REVISION
    engine.dispose()

    _alembic(db_url, "upgrade", "head")
    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "agent_round_metric" in inspector.get_table_names()
    metric_indexes = {idx["name"]: idx for idx in inspector.get_indexes("agent_round_metric")}
    assert metric_indexes["ix_agent_round_metric_occurred_at"]["column_names"] == ["occurred_at"]
    assert metric_indexes["ix_agent_round_metric_user_id"]["column_names"] == ["user_id"]
    assert metric_indexes["ix_agent_round_metric_user_id_occurred_at"]["column_names"] == [
        "user_id",
        "occurred_at",
    ]
    assert metric_indexes["ix_agent_round_metric_agent_run_id"]["column_names"] == ["agent_run_id"]
    assert bool(metric_indexes["ix_agent_round_metric_agent_run_id"]["unique"]) is True

    chat_indexes = {idx["name"]: idx["column_names"] for idx in inspector.get_indexes("chat_message")}
    fv_indexes = {idx["name"]: idx["column_names"] for idx in inspector.get_indexes("file_version")}
    assert chat_indexes["ix_chat_message_created_at"] == ["created_at"]
    assert fv_indexes["ix_file_version_created_at"] == ["created_at"]

    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
        # Raw insert omitting defaulted boolean/int columns verifies server defaults work cleanly.
        conn.execute(
            text(
                "INSERT INTO agent_round_metric (id, occurred_at, user_id, agent_run_id) "
                "VALUES ('m1', '2026-10-07 02:00:00', 'u1', 'run-1')"
            )
        )
        defaults_row = conn.execute(
            text(
                "SELECT user_stopped, client_disconnected, deadline_exceeded, write_succeeded, "
                "runaway_stop, file_body_pending_count, leaked_tool_call_count, tool_calls_count, tool_failures_count "
                "FROM agent_round_metric WHERE id = 'm1'"
            )
        ).one()
        assert tuple(defaults_row) == (0, 0, 0, 0, 0, 0, 0, 0, 0)
    engine.dispose()
