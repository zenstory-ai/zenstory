"""Actual HTTP, durable-row and real memory-cache record transaction contracts."""

from __future__ import annotations

import asyncio
import importlib
import json
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from api import stats as stats_api
from models.writing_stats import WritingStats, WritingStreak
from tests.test_api.test_project_stats_integrity import TODAY, _http
from tests.test_api.test_project_stats_integrity import stats_probe as stats_probe

cache_module = importlib.import_module("services.infra.dashboard_cache")


@pytest.fixture
def transaction_probe(stats_probe, monkeypatch, record_property):
    """Keep the shared owned HTTP fixture, replacing only its mocked cache facade."""
    engine, ids, *_ = stats_probe
    monkeypatch.setenv("DASHBOARD_CACHE_BACKEND", "memory")
    monkeypatch.setenv("DASHBOARD_PROJECT_STATS_CACHE_TTL_SECONDS", "60")
    monkeypatch.setattr(stats_api, "dashboard_cache", cache_module.DashboardCache())
    scope = f":{ids['user']}:{ids['project']}"
    version_key = f"project_ver:v1{scope}"
    with cache_module._memory_lock:
        assert version_key not in cache_module._memory_versions
        assert not any(scope in key for key in cache_module._memory_cache)
    with Session(engine) as seed:
        seed.add(WritingStats(user_id=ids["user"], project_id=ids["project"], stats_date=TODAY))
        seed.add(WritingStreak(
            user_id=ids["user"], project_id=ids["project"], current_streak=1,
            longest_streak=1, last_writing_date=TODAY - timedelta(days=1),
            streak_start_date=TODAY - timedelta(days=1),
        ))
        seed.commit()
    try:
        yield stats_probe
    finally:
        with cache_module._memory_lock:
            owned = [key for key in cache_module._memory_cache if scope in key]
            for key in owned:
                del cache_module._memory_cache[key]
            cache_module._memory_versions.pop(version_key, None)
            assert not any(scope in key for key in cache_module._memory_cache)
            assert version_key not in cache_module._memory_versions
        record_property("owned_cache_cleanup", json.dumps({"scope": scope, "removed": owned}))


def _snapshot(probe):
    engine, ids, *_ = probe
    with Session(engine) as reader:
        daily = reader.exec(select(WritingStats).where(
            WritingStats.user_id == ids["user"], WritingStats.project_id == ids["project"],
            WritingStats.stats_date == TODAY,
        )).one()
        streak = reader.exec(select(WritingStreak).where(
            WritingStreak.user_id == ids["user"], WritingStreak.project_id == ids["project"],
        )).one()
        return {
            "daily": daily.model_dump(mode="json"), "streak": streak.model_dump(mode="json"),
            "version": cache_module.get_project_version(ids["user"], ids["project"]),
        }


def _advancing_streak(session, probe):
    engine, ids, *_ = probe
    return session.get_bind() is engine and any(
        isinstance(row, WritingStreak) and row.user_id == ids["user"]
        and row.project_id == ids["project"] and row.last_writing_date == TODAY
        for row in [*session.identity_map.values(), *session.new]
    )


def _facts(record_property, finding, **facts):
    value = json.dumps({"finding": finding, **facts}, sort_keys=True)
    print(f"STATS_RECORD_TRANSACTION {value}")
    record_property("stats_record_transaction", value)


def _payload(day=TODAY, *, activity=100):
    return {"stats_date": day.isoformat(), "word_count": 100, "words_added": activity,
            "words_deleted": 0, "edit_time_seconds": 5}


async def test_failed_streak_commit_rolls_back_daily_and_retry_records_once(transaction_probe, record_property):
    probe = transaction_probe
    before = _snapshot(probe)
    injected = []

    def fail_once(session):
        if not injected and _advancing_streak(session, probe):
            injected.append("real streak commit")
            raise RuntimeError("owned test: fail the advancing streak commit")

    event.listen(Session, "before_commit", fail_once)
    try:
        failed = await _http(probe, "POST", "/stats/record", payload=_payload())
        after_failure = _snapshot(probe)
        retry = await _http(probe, "POST", "/stats/record", payload=_payload())
        after_retry = _snapshot(probe)
    finally:
        event.remove(Session, "before_commit", fail_once)
    _facts(record_property, "W2-failed-commit-replay", injected=injected,
           failed_status=failed.status_code, retry_status=retry.status_code,
           before=before, after_failure=after_failure, after_retry=after_retry)
    assert injected == ["real streak commit"], "instrumentation must hit the real advancing commit"
    assert failed.status_code == 500, failed.text
    assert retry.status_code == 201, retry.text
    assert after_failure == before, "HTTP failure must not retain daily increments or publish a version"
    assert after_retry["daily"]["words_added"] == 100
    assert after_retry["daily"]["edit_sessions"] == 1
    assert after_retry["streak"]["current_streak"] == 2
    assert after_retry["version"] == before["version"] + 1


async def test_read_during_streak_commit_cannot_poison_final_dashboard_version(transaction_probe, record_property):
    probe = transaction_probe
    held, release = Event(), Event()
    hits = []
    primed = await _http(probe, "GET", "/stats", params={"client_date": TODAY.isoformat()})
    assert primed.status_code == 200, primed.text
    assert primed.json()["streak"]["current_streak"] == 1

    def hold_once(session):
        if not hits and _advancing_streak(session, probe):
            hits.append("real streak commit")
            held.set()
            assert release.wait(15), "owned test barrier must always be released"

    event.listen(Session, "before_commit", hold_once)
    task = asyncio.create_task(_http(probe, "POST", "/stats/record", payload=_payload()))
    try:
        assert await asyncio.to_thread(held.wait, 10), "instrumentation never observed the real commit"
        during = await _http(probe, "GET", "/stats", params={"client_date": TODAY.isoformat()})
        assert during.status_code == 200, during.text
        during_state = _snapshot(probe)
    finally:
        release.set()
        response = await task
        event.remove(Session, "before_commit", hold_once)
    final_state = _snapshot(probe)
    after = await _http(probe, "GET", "/stats", params={"client_date": TODAY.isoformat()})
    _facts(record_property, "W2-cache-commit-gap", hits=hits,
           post_status=response.status_code, during=during.json(), during_state=during_state,
           final_state=final_state, final_get=after.json())
    assert response.status_code == 201, response.text
    assert after.status_code == 200, after.text
    assert final_state["streak"]["current_streak"] == 2
    assert after.json()["streak"]["current_streak"] == 2, "final cache version must contain the committed streak"
    assert after.json()["words_today"] == 100


async def test_normal_record_commits_daily_streak_and_one_version(transaction_probe):
    response = await _http(transaction_probe, "POST", "/stats/record", payload=_payload())
    state = _snapshot(transaction_probe)
    assert response.status_code == 201, response.text
    assert response.json()["streak_updated"] is True
    assert response.json()["new_streak"] == 2
    assert state["daily"]["words_added"] == 100
    assert state["daily"]["edit_sessions"] == 1
    assert state["streak"]["current_streak"] == 2
    assert state["version"] == 2


@pytest.mark.parametrize(("day_offset", "activity"), [(0, 0), (0, 1), (-2, 100)])
async def test_below_threshold_and_historical_records_keep_streak(transaction_probe, day_offset, activity):
    before = _snapshot(transaction_probe)
    response = await _http(transaction_probe, "POST", "/stats/record",
                           payload=_payload(TODAY + timedelta(days=day_offset), activity=activity))
    after = _snapshot(transaction_probe)
    assert response.status_code == 201, response.text
    assert response.json()["words_added"] == activity
    assert response.json()["streak_updated"] is False
    assert response.json()["new_streak"] is None
    assert after["streak"] == before["streak"]
    assert after["version"] == before["version"] + 1


async def test_cache_bump_failure_does_not_turn_committed_record_into_failure(transaction_probe, monkeypatch):
    def fail_bump(*_args):
        raise RuntimeError("owned test: cache invalidation unavailable")

    monkeypatch.setattr(stats_api.dashboard_cache, "bump_project_version", fail_bump)
    response = await _http(transaction_probe, "POST", "/stats/record", payload=_payload())
    state = _snapshot(transaction_probe)
    assert response.status_code == 201, response.text
    assert state["daily"]["words_added"] == 100
    assert state["daily"]["edit_sessions"] == 1
    assert state["streak"]["current_streak"] == 2
    assert state["version"] == 1


async def test_first_daily_and_streak_rows_rollback_on_final_commit_failure(transaction_probe, record_property):
    probe = transaction_probe
    engine, ids, *_ = probe
    with Session(engine) as seed:
        for model in (WritingStats, WritingStreak):
            for row in seed.exec(select(model).where(model.project_id == ids["project"])).all():
                seed.delete(row)
        seed.commit()
    injected = []

    def fail_once(session):
        if not injected and _advancing_streak(session, probe):
            injected.append("real first streak commit")
            raise RuntimeError("owned test: first daily and streak final commit fails")

    event.listen(Session, "before_commit", fail_once)
    try:
        response = await _http(probe, "POST", "/stats/record", payload=_payload())
    finally:
        event.remove(Session, "before_commit", fail_once)
    with Session(engine) as reader:
        counts = {model.__name__: len(reader.exec(select(model).where(
            model.project_id == ids["project"],
        )).all()) for model in (WritingStats, WritingStreak)}
    version = cache_module.get_project_version(ids["user"], ids["project"])
    _facts(record_property, "W2-first-row-rollback", injected=injected,
           http_status=response.status_code, counts=counts, version=version)
    assert injected == ["real first streak commit"]
    assert response.status_code == 500, response.text
    assert counts == {"WritingStats": 0, "WritingStreak": 0}
    assert version == 1


async def test_successful_final_commit_does_not_issue_fallible_response_reads(transaction_probe, record_property):
    probe = transaction_probe
    captured = []

    def final_commit(session):
        if _advancing_streak(session, probe):
            session.info["owned_final_stats_commit"] = True
            captured.append("advancing streak committed")

    def reject_postcommit_read(orm_execute_state):
        if orm_execute_state.session.info.get("owned_final_stats_commit"):
            captured.append("postcommit ORM access")
            raise RuntimeError("owned test: expired ORM response refresh unavailable")

    event.listen(Session, "after_commit", final_commit)
    event.listen(Session, "do_orm_execute", reject_postcommit_read)
    try:
        response = await _http(probe, "POST", "/stats/record", payload=_payload())
    finally:
        event.remove(Session, "after_commit", final_commit)
        event.remove(Session, "do_orm_execute", reject_postcommit_read)
    state = _snapshot(probe)
    _facts(record_property, "W2-postcommit-response", captured=captured,
           http_status=response.status_code, durable=state)
    assert captured == ["advancing streak committed"]
    assert response.status_code == 201, response.text
    assert response.json()["words_added"] == 100
    assert state["streak"]["current_streak"] == 2
