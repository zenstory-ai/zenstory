"""账号级写作任务名额：原子获取、续租、失效回收、Redis 故障时放行、真实运行生命周期。

名额逻辑在内存后端和真实 redis-server（CI 用 ZENSTORY_TEST_REDIS_URL，本机临时起 unix socket）
上跑同一组用例，Lua 脚本真实执行。这里不调用任何真实模型。
"""

import asyncio
import gc
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlmodel import Session

import agent.core.account_run_limit as limit
from models import Project, User
from services.core.auth_service import hash_password

# 复用 F-28 回归里的真实 Redis fixture。
from tests.test_agent.test_steering_live_run_enqueue import real_redis  # noqa: F401

SERVER_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["memory", "redis"])
def backend(request, monkeypatch):
    if request.param == "redis":
        client = request.getfixturevalue("real_redis")
        monkeypatch.setenv("REDIS_URL", "redis://account-run-limit-test")
        monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: client)
        return client
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    return None


def _user() -> str:
    return f"account-run-{uuid4().hex}"


async def _acquire_results(user_id: str, count: int) -> list:
    return await asyncio.gather(
        *(limit.acquire_account_run_slot(user_id) for _ in range(count)),
        return_exceptions=True,
    )


async def _release_all(results: list) -> None:
    for item in results:
        if isinstance(item, limit.AccountRunSlot):
            await item.release()


def test_limit_is_one_for_every_account():
    assert limit.ACCOUNT_MAX_CONCURRENT_RUNS == 1


def test_heartbeat_renews_well_within_the_stale_window():
    assert limit.SLOT_HEARTBEAT_INTERVAL_S * 3 <= limit.SLOT_STALE_AFTER_S


async def test_three_simultaneous_acquires_admit_exactly_one(backend):
    user_id = _user()
    results = await _acquire_results(user_id, 3)
    try:
        slots = [item for item in results if isinstance(item, limit.AccountRunSlot)]
        refused = [item for item in results if isinstance(item, limit.AccountRunLimitExceeded)]
        assert len(slots) == 1
        assert len(refused) == 2
    finally:
        await _release_all(results)


async def test_accounts_are_counted_independently(backend):
    first, second = _user(), _user()
    first_slots = await _acquire_results(first, 1)
    second_slots = await _acquire_results(second, 1)
    try:
        assert all(isinstance(item, limit.AccountRunSlot) for item in [*first_slots, *second_slots])
        with pytest.raises(limit.AccountRunLimitExceeded):
            await limit.acquire_account_run_slot(first)
    finally:
        await _release_all([*first_slots, *second_slots])


async def test_release_frees_only_its_own_slot_and_is_idempotent(backend):
    user_id = _user()
    one = await limit.acquire_account_run_slot(user_id)
    await one.release()
    await one.release()
    two = await limit.acquire_account_run_slot(user_id)
    try:
        with pytest.raises(limit.AccountRunLimitExceeded):
            await limit.acquire_account_run_slot(user_id)
    finally:
        await two.release()


async def test_crashed_slots_recover_after_the_stale_window(backend, monkeypatch):
    """进程被杀：心跳停了、finally 没跑。超过失效窗口后名额自动恢复。"""
    user_id = _user()
    crashed = await _acquire_results(user_id, 1)
    for slot in crashed:  # 进程死了：心跳停了
        slot._heartbeat_task.cancel()

    real_time = time.time
    monkeypatch.setattr(limit.time, "time", lambda: real_time() + limit.SLOT_STALE_AFTER_S + 1)
    fresh = await limit.acquire_account_run_slot(user_id)
    await _release_all([fresh, *crashed])


async def test_renewed_long_run_keeps_its_slot_past_the_stale_window(backend, monkeypatch):
    """长任务一直续租：超过失效窗口也不会被回收。"""
    user_id = _user()
    slots = await _acquire_results(user_id, 1)
    real_time = time.time
    try:
        monkeypatch.setattr(limit.time, "time", lambda: real_time() + limit.SLOT_STALE_AFTER_S - 1)
        for slot in slots:
            await slot.renew()
        monkeypatch.setattr(limit.time, "time", lambda: real_time() + limit.SLOT_STALE_AFTER_S + 1)
        with pytest.raises(limit.AccountRunLimitExceeded):
            await limit.acquire_account_run_slot(user_id)
    finally:
        await _release_all(slots)


async def test_periodic_heartbeat_task_renews_until_release(backend, monkeypatch):
    monkeypatch.setattr(limit, "SLOT_HEARTBEAT_INTERVAL_S", 0.01)
    user_id = _user()
    slot = await limit.acquire_account_run_slot(user_id)
    with patch.object(slot, "_renew_sync", wraps=slot._renew_sync) as renew:
        await asyncio.sleep(0.08)
        assert renew.call_count >= 2
        await slot.release()
        calls = renew.call_count
        await asyncio.sleep(0.05)
        assert renew.call_count == calls


async def test_renew_cannot_resurrect_a_released_slot(backend):
    user_id = _user()
    slot = await limit.acquire_account_run_slot(user_id)
    await slot.release()
    await slot.renew()  # 在途的续租晚于释放到达
    other = await limit.acquire_account_run_slot(user_id)
    try:
        with pytest.raises(limit.AccountRunLimitExceeded):
            await limit.acquire_account_run_slot(user_id)
    finally:
        await other.release()


async def test_lease_stops_renewing_past_the_hard_ceiling(backend, monkeypatch):
    """泄漏兜底：超过墙钟时限加收尾宽限后不再续租，账号不会被永久占住。"""
    monkeypatch.setattr(limit, "SLOT_HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(limit, "SLOT_MAX_LIFETIME_S", 0.03)
    slot = await limit.acquire_account_run_slot(_user())
    with patch.object(slot, "_renew_sync", wraps=slot._renew_sync) as renew:
        await asyncio.sleep(0.1)
        calls = renew.call_count
        await asyncio.sleep(0.05)
        assert renew.call_count == calls
    await slot.release()


class _BrokenRedis:
    def eval(self, *_args, **_kwargs):
        raise ConnectionError("redis down")

    def zadd(self, *_args, **_kwargs):
        raise ConnectionError("redis down")

    def expire(self, *_args, **_kwargs):
        raise ConnectionError("redis down")

    def zrem(self, *_args, **_kwargs):
        raise ConnectionError("redis down")

    def pipeline(self, *_args, **_kwargs):
        raise ConnectionError("redis down")


async def test_redis_outage_allows_acquire_and_keeps_retrying_the_same_slot(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://account-run-limit-test")
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", _BrokenRedis)
    slot = await limit.acquire_account_run_slot(_user())
    assert slot._backend == "redis"
    assert slot.renewing
    await slot.release()


@pytest.mark.parametrize("environment", ["production", "staging", " Production "])
async def test_hosted_environment_without_redis_allows_without_counting_per_process(
    monkeypatch, environment
):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("ENVIRONMENT", environment)
    user_id = _user()
    slots = [await limit.acquire_account_run_slot(user_id) for _ in range(3)]
    assert all(slot._backend == "untracked" for slot in slots)
    assert all(not slot.renewing for slot in slots)
    await _release_all(slots)


async def test_outage_during_a_run_keeps_it_running_and_release_does_not_raise(
    real_redis, monkeypatch  # noqa: F811
):
    monkeypatch.setenv("REDIS_URL", "redis://account-run-limit-test")
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: real_redis)
    monkeypatch.setattr(limit, "SLOT_HEARTBEAT_INTERVAL_S", 0.01)
    slot = await limit.acquire_account_run_slot(_user())
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", _BrokenRedis)
    await asyncio.sleep(0.05)
    assert slot.renewing  # 心跳失败不终止运行、不终止续租
    await slot.release()


async def test_acquire_is_atomic_across_real_processes(real_redis):  # noqa: F811
    """多个独立进程共用一个 Redis：6 个同时抢，只有 1 个进。"""
    kwargs = real_redis.connection_pool.connection_kwargs
    connection = {key: kwargs[key] for key in ("path", "host", "port", "db") if key in kwargs}
    user_id = _user()
    gate = f"account-run-test-gate:{uuid4().hex}"
    script = """
import json, os, sys
import redis
os.environ["REDIS_URL"] = "redis://account-run-limit-test"
connection, user_id, gate = json.loads(sys.argv[1])
if "path" in connection:
    client = redis.Redis(unix_socket_path=connection["path"], decode_responses=True, protocol=2)
else:
    client = redis.Redis(decode_responses=True, **connection)
import services.infra.redis_client as redis_client
redis_client.get_redis_client = lambda: client
import agent.core.account_run_limit as limit
print("ready", flush=True)
client.blpop(gate, timeout=20)
try:
    limit._acquire_sync(limit._new_slot_id(), user_id)
except limit.AccountRunLimitExceeded:
    print("refused", flush=True)
else:
    print("admitted", flush=True)
"""
    argument = json.dumps([connection, user_id, gate])
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, argument],
            cwd=SERVER_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(6)
    ]
    try:
        for process in processes:
            assert await asyncio.to_thread(process.stdout.readline) == "ready\n"
        real_redis.rpush(gate, *["go"] * 6)
        outputs = [await asyncio.to_thread(process.communicate, None, 30) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
        real_redis.delete(limit.account_runs_key(user_id), gate)
    verdicts = sorted(out.strip() for out, _err in outputs)
    assert verdicts == ["admitted", "refused", "refused", "refused", "refused", "refused"], outputs


# ==================== process_stream 的真实生命周期（模型与工作流全部替身） ====================


@pytest.fixture
def real_service():
    from agent.schemas.context import ContextData
    from agent.service import AgentService

    assembler = MagicMock()
    assembler.assemble.return_value = ContextData(items=[], context="", token_estimate=0)
    return AgentService(context_assembler=assembler)


@pytest.fixture
def owner_and_project(db_session: Session):
    user = User(
        email=f"account_run_{uuid4().hex[:8]}@example.com",
        username=f"accountrun{uuid4().hex[:8]}",
        hashed_password=hash_password("password123"),
        name="Account Run",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="Account Run", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


async def _assert_slot_count_free(user_id: str) -> None:
    """唯一名额能拿到，说明之前的任务已经释放。"""
    slots = await _acquire_results(user_id, 1)
    assert all(isinstance(item, limit.AccountRunSlot) for item in slots), slots
    await _release_all(slots)


async def _assert_slot_held(user_id: str) -> None:
    """拿不到名额，说明之前的任务仍占着唯一名额。"""
    results = await _acquire_results(user_id, 1)
    held = [item for item in results if isinstance(item, limit.AccountRunSlot)]
    await _release_all(results)
    assert len(held) == 0, results


async def _wait_all_background_cleanup() -> None:
    from agent.service import AgentService

    while AgentService.background_cleanup_tasks:
        await asyncio.wait(set(AgentService.background_cleanup_tasks))


@pytest.mark.usefixtures("writing_prompt_configs")
async def test_process_stream_releases_after_normal_completion_not_before_cleanup(
    real_service, owner_and_project, db_session: Session
):
    import agent.service as service_module
    from agent.core.workflow_events import StreamEvent, StreamEventType

    user, project = owner_and_project
    user_id = str(user.id)
    slot = await limit.acquire_account_run_slot(user_id)
    real_cleanup = service_module.cleanup_steering_queue_async
    held_during_cleanup: list[bool] = []

    async def checking_cleanup(session_id, run_id=None):
        await _assert_slot_held(user_id)
        held_during_cleanup.append(True)
        await real_cleanup(session_id, run_id=run_id)

    async def workflow():
        await _assert_slot_held(user_id)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "雪落下来。"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    bind = db_session.get_bind()
    with (
        patch("agent.service.run_writing_workflow_streaming", return_value=workflow()),
        patch("agent.service.create_session", side_effect=lambda: Session(bind)),
        patch("agent.service.cleanup_steering_queue_async", checking_cleanup),
    ):
        async for _frame in real_service.process_stream(
            project_id=str(project.id), user_id=user_id, message="写一段",
            session=db_session, account_run_slot=slot,
        ):
            assert slot.adopted
    assert held_during_cleanup == [True]
    await slot.release()  # 等 process_stream 排上的释放真正落定
    await _assert_slot_count_free(user_id)


@pytest.mark.usefixtures("writing_prompt_configs")
async def test_process_stream_releases_after_workflow_failure(
    real_service, owner_and_project, db_session: Session
):
    user, project = owner_and_project
    user_id = str(user.id)
    slot = await limit.acquire_account_run_slot(user_id)

    async def workflow():
        raise RuntimeError("synthetic workflow failure")
        yield

    bind = db_session.get_bind()
    frames: list[str] = []
    with (
        patch("agent.service.run_writing_workflow_streaming", return_value=workflow()),
        patch("agent.service.create_session", side_effect=lambda: Session(bind)),
    ):
        async for frame in real_service.process_stream(
            project_id=str(project.id), user_id=user_id, message="写一段",
            session=db_session, account_run_slot=slot,
        ):
            frames.append(frame)
    assert any(frame.startswith("event: error") for frame in frames)
    await slot.release()
    await _assert_slot_count_free(user_id)


@pytest.mark.usefixtures("writing_prompt_configs")
async def test_cancelled_run_keeps_its_slot_until_cancellation_cleanup_finishes(
    real_service, owner_and_project, db_session: Session
):
    """断线 / 停止走取消路径：残稿补存和会话释放做完之前，名额不能提前放掉。"""
    from agent.core.workflow_events import StreamEvent, StreamEventType

    user, project = owner_and_project
    user_id = str(user.id)
    slot = await limit.acquire_account_run_slot(user_id)
    started = asyncio.Event()
    cleanup_gate = asyncio.Event()

    async def workflow():
        started.set()
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "写到一半"})
        await asyncio.sleep(3600)

    async def blocked_cleanup(_session_id, run_id=None):
        await cleanup_gate.wait()

    async def consume():
        async for _frame in real_service.process_stream(
            project_id=str(project.id), user_id=user_id, message="写一段",
            session=db_session, account_run_slot=slot,
        ):
            pass

    bind = db_session.get_bind()
    with (
        patch("agent.service.run_writing_workflow_streaming", return_value=workflow()),
        patch("agent.service.create_session", side_effect=lambda: Session(bind)),
        patch("agent.service.cleanup_steering_queue_async", blocked_cleanup),
    ):
        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)
        await _assert_slot_held(user_id)  # 生成器已退出，收尾还没做完
        assert slot.renewing
        cleanup_gate.set()
        await _wait_all_background_cleanup()
    await slot.release()
    await _assert_slot_count_free(user_id)


@pytest.mark.usefixtures("writing_prompt_configs")
async def test_abandoned_primed_stream_releases_through_generator_close(
    real_service, owner_and_project, db_session: Session
):
    """响应头发出失败、事件生成器从未启动：只取过首帧的 process_stream 被关闭时同样释放。"""
    user, project = owner_and_project
    user_id = str(user.id)
    slot = await limit.acquire_account_run_slot(user_id)
    bind = db_session.get_bind()
    with patch("agent.service.create_session", side_effect=lambda: Session(bind)):
        stream = real_service.process_stream(
            project_id=str(project.id), user_id=user_id, message="写一段",
            session=db_session, account_run_slot=slot,
        )
        await stream.__anext__()
        assert slot.adopted
        await stream.aclose()
        await _wait_all_background_cleanup()
    await slot.release()
    await _assert_slot_count_free(user_id)


@pytest.mark.usefixtures("writing_prompt_configs")
async def test_primed_stream_dropped_without_close_releases_when_collected(
    real_service, owner_and_project, db_session: Session
):
    """没有任何人关闭生成器（泵在第一步前被取消）：回收时 asyncio 的 finalizer 走取消路径释放。"""
    user, project = owner_and_project
    user_id = str(user.id)
    slot = await limit.acquire_account_run_slot(user_id)
    bind = db_session.get_bind()
    with patch("agent.service.create_session", side_effect=lambda: Session(bind)):
        stream = real_service.process_stream(
            project_id=str(project.id), user_id=user_id, message="写一段",
            session=db_session, account_run_slot=slot,
        )
        await stream.__anext__()
        del stream
        gc.collect()
        for _ in range(50):
            await asyncio.sleep(0.02)
            await _wait_all_background_cleanup()
            if slot._release_future is not None:
                break
        assert slot._release_future is not None
    await slot.release()
    await _assert_slot_count_free(user_id)


async def test_cleanup_failure_still_releases_the_slot(
    real_service, owner_and_project, db_session: Session, request
):
    request.getfixturevalue("writing_prompt_configs")
    user, project = owner_and_project
    user_id = str(user.id)
    slot = await limit.acquire_account_run_slot(user_id)

    async def workflow():
        return
        yield

    bind = db_session.get_bind()
    with (
        patch("agent.service.run_writing_workflow_streaming", return_value=workflow()),
        patch("agent.service.create_session", side_effect=lambda: Session(bind)),
        patch("agent.service.cleanup_steering_queue_async", AsyncMock(side_effect=ConnectionError("redis down"))),
        pytest.raises(ConnectionError),
    ):
        async for _frame in real_service.process_stream(
            project_id=str(project.id), user_id=user_id, message="写一段",
            session=db_session, account_run_slot=slot,
        ):
            pass
    await slot.release()
    await _assert_slot_count_free(user_id)
