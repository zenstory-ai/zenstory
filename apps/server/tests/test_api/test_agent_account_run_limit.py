"""POST /agent/stream 的账号级并发上限：拒绝在付费调用和扣额之前，所有收尾路径都释放名额。

模型与工作流全部替身；不调用任何真实模型。
"""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

import agent.core.account_run_limit as limit
from agent.core.steering import SteeringSessionBusyError
from core.error_codes import ERROR_MESSAGES, ErrorCode
from models import Project, User
from services.core.auth_service import hash_password
from services.quota_service import quota_service

_CHARGED_PERIOD = datetime(2026, 11, 5, 16, tzinfo=UTC)
_BUSY_MESSAGE_ZH = "已有 2 个任务正在进行，请等待完成或停止其中一个后再试。"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_STARTED = _sse("session_started", {"session_id": "synthetic-session"})
_DONE = _sse("done", {"session_id": "synthetic-session"})


async def _login(client: AsyncClient, db_session) -> tuple[str, User]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"account_limit_{suffix}",
        email=f"account_limit_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    login = await client.post(
        "/api/auth/login", data={"username": user.username, "password": "password123"}
    )
    assert login.status_code == 200
    return login.json()["access_token"], user


def _project(db_session, user: User, name: str = "Limit") -> Project:
    project = Project(name=name, owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    return project


def _post(client: AsyncClient, token: str, project: Project, **extra):
    return client.post(
        "/api/v1/agent/stream",
        json={"project_id": str(project.id), "message": "写第五章", **extra},
        headers={"Authorization": f"Bearer {token}"},
    )


class _Service:
    """记下每次真正开始的运行；frames 决定它产出什么。"""

    def __init__(self, frames):
        self._frames = frames
        self.started = 0

    async def process_stream(self, **_kwargs):
        self.started += 1
        async for frame in self._frames():
            yield frame


async def _once_frames():
    yield _STARTED
    yield _DONE


async def _assert_both_free(user_id: str) -> None:
    results = await asyncio.gather(
        *(limit.acquire_account_run_slot(user_id) for _ in range(2)), return_exceptions=True
    )
    assert all(isinstance(item, limit.AccountRunSlot) for item in results), results
    for slot in results:
        await slot.release()


def test_busy_message_is_the_confirmed_copy():
    assert ERROR_MESSAGES["zh"][ErrorCode.ACCOUNT_RUNS_BUSY] == _BUSY_MESSAGE_ZH
    assert ErrorCode.ACCOUNT_RUNS_BUSY in ERROR_MESSAGES["en"]


@pytest.mark.integration
async def test_three_simultaneous_requests_only_two_enter(client: AsyncClient, db_session):
    """跨项目同时到达 3 个：2 个进入并扣额，第 3 个 409，且没有开始运行、没有扣额。"""
    token, user = await _login(client, db_session)
    projects = [_project(db_session, user, f"Limit {index}") for index in range(3)]
    finish = asyncio.Event()

    async def held_frames():
        yield _STARTED
        await finish.wait()
        yield _DONE

    service = _Service(held_frames)

    async def post_one(project):
        response = await _post(client, token, project)
        if response.status_code == 409:
            finish.set()
        return response

    with (
        patch("api.agent.get_agent_service", return_value=service),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD) as reserve,
    ):
        responses = await asyncio.wait_for(
            asyncio.gather(*(post_one(project) for project in projects)), 30
        )

    assert sorted(response.status_code for response in responses) == [200, 200, 409]
    refused = next(response for response in responses if response.status_code == 409)
    assert refused.json()["error_code"] == "ERR_ACCOUNT_RUNS_BUSY"
    assert service.started == 2
    assert reserve.call_count == 2
    await _assert_both_free(str(user.id))


@pytest.mark.integration
async def test_slot_held_elsewhere_blocks_other_sessions_and_projects_without_charging(
    client: AsyncClient, db_session
):
    """别的设备 / 项目 / 会话占满时：不论这里带什么 session_id 都拒绝，真实额度不动。"""
    token, user = await _login(client, db_session)
    project = _project(db_session, user)
    held = [await limit.acquire_account_run_slot(str(user.id)) for _ in range(2)]
    service = _Service(_once_frames)
    try:
        with patch("api.agent.get_agent_service", return_value=service):
            for extra in ({}, {"session_id": f"brand-new-{uuid4().hex}"}):
                response = await _post(client, token, project, **extra)
                assert response.status_code == 409
                assert response.json()["error_code"] == "ERR_ACCOUNT_RUNS_BUSY"
    finally:
        for slot in held:
            await slot.release()
    assert service.started == 0
    allowed, used, _limit = quota_service.check_ai_conversation_quota(db_session, str(user.id))
    assert allowed and used == 0


@pytest.mark.integration
async def test_refusal_does_not_affect_another_account(client: AsyncClient, db_session):
    token, user = await _login(client, db_session)
    other_token, other = await _login(client, db_session)
    other_project = _project(db_session, other)
    held = [await limit.acquire_account_run_slot(str(user.id)) for _ in range(2)]
    try:
        with (
            patch("api.agent.get_agent_service", return_value=_Service(_once_frames)),
            patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        ):
            response = await _post(client, other_token, other_project)
    finally:
        for slot in held:
            await slot.release()
    assert response.status_code == 200


@pytest.mark.integration
async def test_quota_exceeded_takes_precedence_and_holds_no_slot(client: AsyncClient, db_session):
    token, user = await _login(client, db_session)
    project = _project(db_session, user)
    with patch("api.agent.quota_service.check_ai_conversation_quota", return_value=(False, 3, 3)):
        response = await _post(client, token, project)
    assert response.status_code == 402
    await _assert_both_free(str(user.id))


@pytest.mark.integration
async def test_slot_is_acquired_before_quota_is_reserved(client: AsyncClient, db_session):
    token, user = await _login(client, db_session)
    project = _project(db_session, user)
    order: list[str] = []
    real_acquire = limit.acquire_account_run_slot

    async def spy_acquire(user_id):
        order.append("acquire")
        return await real_acquire(user_id)

    def spy_reserve(*_args, **_kwargs):
        order.append("reserve")
        return _CHARGED_PERIOD

    with (
        patch("api.agent.acquire_account_run_slot", spy_acquire),
        patch("api.agent.quota_service.reserve_ai_conversation", spy_reserve),
        patch("api.agent.get_agent_service", return_value=_Service(_once_frames)),
    ):
        assert (await _post(client, token, project)).status_code == 200
    assert order == ["acquire", "reserve"]


@pytest.mark.integration
async def test_can_enter_again_after_normal_completion(client: AsyncClient, db_session):
    token, user = await _login(client, db_session)
    project = _project(db_session, user)
    with (
        patch("api.agent.get_agent_service", return_value=_Service(_once_frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
    ):
        for _ in range(4):  # 多于上限：只有都释放了才进得去
            assert (await _post(client, token, project)).status_code == 200
    await _assert_both_free(str(user.id))


@pytest.mark.integration
async def test_can_enter_again_after_mid_stream_exception(client: AsyncClient, db_session):
    token, user = await _login(client, db_session)
    project = _project(db_session, user)

    async def failing_frames():
        yield _STARTED
        raise RuntimeError("synthetic failure")

    with (
        patch("api.agent.get_agent_service", return_value=_Service(failing_frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True),
    ):
        with pytest.raises(RuntimeError):
            await _post(client, token, project)
    await _assert_both_free(str(user.id))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("first_step_error", "status"),
    [(SteeringSessionBusyError("busy"), 409), (RuntimeError("startup failure"), 200)],
)
async def test_startup_failure_releases_and_refunds(
    client: AsyncClient, db_session, first_step_error, status
):
    """process_stream 走到第一帧之前就失败（会话忙 / 启动异常）：名额放回、额度退回。"""
    token, user = await _login(client, db_session)
    project = _project(db_session, user)

    async def failing_frames():
        raise first_step_error
        yield

    with (
        patch("api.agent.get_agent_service", return_value=_Service(failing_frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as refund,
    ):
        try:
            response = await _post(client, token, project)
            assert response.status_code == status
        except RuntimeError:
            assert status == 200  # 终止帧发出后异常按原行为继续上抛
    assert refund.call_count == 1
    await _assert_both_free(str(user.id))


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["refused", "raises"])
async def test_quota_reserve_failure_releases_the_slot(client: AsyncClient, db_session, failure):
    token, user = await _login(client, db_session)
    project = _project(db_session, user)
    reserve = {"return_value": None} if failure == "refused" else {"side_effect": ConnectionError("db down")}
    with (
        patch("api.agent.quota_service.reserve_ai_conversation", **reserve),
        patch("api.agent.get_agent_service", return_value=_Service(_once_frames)),
    ):
        if failure == "refused":
            assert (await _post(client, token, project)).status_code == 402
        else:
            assert (await _post(client, token, project)).status_code == 500
    await _assert_both_free(str(user.id))


@pytest.mark.integration
async def test_author_stop_releases_the_slot(client: AsyncClient, db_session):
    token, user = await _login(client, db_session)
    project = _project(db_session, user)
    started = asyncio.Event()

    async def endless_frames():
        yield _STARTED
        started.set()
        await asyncio.sleep(3600)

    import api.agent as agent_api

    async def stop_when_started():
        await asyncio.wait_for(started.wait(), 10)
        ((_user_id, run_id),) = [key for key in agent_api._running_streams if key[0] == str(user.id)]
        stopped = await client.post(
            "/api/v1/agent/stop",
            json={"agent_run_id": run_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert stopped.json()["stop_requested"] is True

    with (
        patch("api.agent.get_agent_service", return_value=_Service(endless_frames)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True),
    ):
        response, _ = await asyncio.wait_for(
            asyncio.gather(_post(client, token, project), stop_when_started()), 30
        )
    assert response.status_code == 200
    await _assert_both_free(str(user.id))


@pytest.mark.integration
async def test_redis_outage_still_starts_and_reserves_the_normal_quota(
    client: AsyncClient, db_session, monkeypatch
):
    token, user = await _login(client, db_session)
    project = _project(db_session, user)
    service = _Service(_once_frames)

    monkeypatch.setenv("REDIS_URL", "redis://account-run-limit-test")

    def unavailable():
        raise ConnectionError("redis down")

    with (
        patch("services.infra.redis_client.get_redis_client", unavailable),
        patch("api.agent.get_agent_service", return_value=service),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD) as reserve,
        patch("api.agent.quota_service.release_ai_conversation", return_value=True),
    ):
        response = await _post(client, token, project)
    assert response.status_code == 200
    assert service.started == 1
    assert reserve.call_count == 1


@pytest.mark.integration
async def test_session_busy_preflight_still_wins_for_the_same_chat(client: AsyncClient, db_session):
    """既有会话忙碌保护保留：同一会话先报会话忙，不占账号名额。"""
    from agent.core.steering import cleanup_steering_queue_async, create_steering_queue_async

    token, user = await _login(client, db_session)
    project = _project(db_session, user)
    session_id = f"limit-busy-{uuid4().hex}"
    await create_steering_queue_async(session_id, str(user.id), run_id="already-running")
    try:
        response = await _post(client, token, project, session_id=session_id)
    finally:
        await cleanup_steering_queue_async(session_id)
    assert response.status_code == 409
    assert response.json()["error_code"] == "ERR_SESSION_BUSY"
    await _assert_both_free(str(user.id))
