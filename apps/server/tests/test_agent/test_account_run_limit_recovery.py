"""账号任务名额的故障恢复：可用性优先，健康 Redis 恢复后继续限流。"""

import asyncio
import shutil
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

import agent.core.account_run_limit as limit
from tests.test_agent.test_steering_live_run_enqueue import real_redis  # noqa: F401


@pytest.fixture
def redis_backend(real_redis, monkeypatch):  # noqa: F811
    monkeypatch.setenv("REDIS_URL", "redis://account-run-limit-test")
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: real_redis)
    return real_redis


def _user() -> str:
    return f"account-recovery-{uuid4().hex}"


def _count(client, user_id: str) -> int:
    return client.zcard(limit.account_runs_key(user_id))


class _OtherProcess:
    def __init__(self, slot_id: str, user_id: str) -> None:
        self.slot_id = slot_id
        self.user_id = user_id

    def renew(self, client) -> None:
        client.zadd(limit.account_runs_key(self.user_id), {self.slot_id: time.time()})


async def _take_as_other_processes(client, user_id: str, count: int) -> list[_OtherProcess]:
    held = []
    for _ in range(count):
        slot_id = limit._new_slot_id()
        await asyncio.to_thread(limit._acquire_sync, slot_id, user_id)
        held.append(_OtherProcess(slot_id, user_id))
    return held


async def test_empty_redis_immediately_admits_then_limits(redis_backend):
    """首次初始化 / FLUSH 后没有全站恢复窗口：当前请求立即进入。"""
    client = redis_backend
    user_id = _user()
    client.delete(limit.account_runs_key(user_id))
    first = await limit.acquire_account_run_slot(user_id)
    with pytest.raises(limit.AccountRunLimitExceeded):
        await limit.acquire_account_run_slot(user_id)
    assert _count(client, user_id) == 1
    await first.release()


async def test_state_loss_prefers_availability_until_live_runs_renew(redis_backend):
    client = redis_backend
    user_id = _user()
    old = await _take_as_other_processes(client, user_id, 1)
    client.delete(limit.account_runs_key(user_id))
    fresh = [await limit.acquire_account_run_slot(user_id)]
    with pytest.raises(limit.AccountRunLimitExceeded):
        await limit.acquire_account_run_slot(user_id)
    for run in old:
        run.renew(client)
    assert _count(client, user_id) == 2
    with pytest.raises(limit.AccountRunLimitExceeded):
        await limit.acquire_account_run_slot(user_id)
    for slot in fresh:
        await slot.release()
    client.zrem(limit.account_runs_key(user_id), *(run.slot_id for run in old))


async def test_ambiguous_acquire_timeout_reuses_slot_id_for_cleanup(redis_backend, monkeypatch):
    """脚本已写入但客户端超时：请求放行，release 仍删除同一个成员。"""
    client = redis_backend
    user_id = _user()

    class AppliedThenTimedOutClient:
        def eval(self, script, numkeys, *args):
            client.eval(script, numkeys, *args)
            raise TimeoutError("reply lost after Redis applied the script")

        def zrem(self, *args):
            return client.zrem(*args)

    monkeypatch.setattr(
        "services.infra.redis_client.get_redis_client", AppliedThenTimedOutClient
    )
    slot = await limit.acquire_account_run_slot(user_id)
    assert client.zrange(limit.account_runs_key(user_id), 0, -1) == [slot.slot_id]
    await slot.release()
    assert _count(client, user_id) == 0


async def test_release_during_another_acquire_does_not_resurrect_finished_slot(
    redis_backend, monkeypatch
):
    """获取与兄弟任务释放交错时，获取脚本不能把已释放成员从旧快照写回。"""
    client = redis_backend
    user_id = _user()
    finished = await limit.acquire_account_run_slot(user_id)
    entered, proceed = threading.Event(), threading.Event()

    class PausedClient:
        def eval(self, script, numkeys, *args):
            entered.set()
            assert proceed.wait(5)
            return client.eval(script, numkeys, *args)

        def zrem(self, *args):
            return client.zrem(*args)

    monkeypatch.setattr("services.infra.redis_client.get_redis_client", PausedClient)
    acquiring = asyncio.create_task(limit.acquire_account_run_slot(user_id))
    await asyncio.to_thread(entered.wait, 5)
    await finished.release()
    proceed.set()
    second = await acquiring
    with pytest.raises(limit.AccountRunLimitExceeded):
        await limit.acquire_account_run_slot(user_id)
    assert client.zrange(limit.account_runs_key(user_id), 0, -1) == [second.slot_id]
    await second.release()


async def test_released_and_ceiling_expired_slots_are_not_written_back(redis_backend, monkeypatch):
    client = redis_backend
    user_id = _user()
    monkeypatch.setattr(limit, "SLOT_HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(limit, "SLOT_MAX_LIFETIME_S", 0.02)
    leaked = await limit.acquire_account_run_slot(user_id)
    await asyncio.sleep(0.1)
    assert not leaked.renewing
    real_time = time.time
    monkeypatch.setattr(limit.time, "time", lambda: real_time() + limit.SLOT_STALE_AFTER_S + 5)
    released = await limit.acquire_account_run_slot(user_id)
    await released.release()
    fresh = await limit.acquire_account_run_slot(user_id)
    assert client.zrange(limit.account_runs_key(user_id), 0, -1) == [fresh.slot_id]
    await fresh.release()
    await leaked.release()


async def test_cancelled_acquire_cleans_up_a_slot_the_thread_already_took(redis_backend):
    client = redis_backend
    user_id = _user()
    entered, proceed = threading.Event(), threading.Event()
    real_acquire = limit._acquire_sync

    def slow_acquire(slot_id, owner):
        entered.set()
        assert proceed.wait(5)
        return real_acquire(slot_id, owner)

    with patch.object(limit, "_acquire_sync", slow_acquire):
        task = asyncio.create_task(limit.acquire_account_run_slot(user_id))
        await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        proceed.set()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if _count(client, user_id) == 0:
                break
    assert _count(client, user_id) == 0


async def test_cancelled_refused_acquire_leaves_existing_slots_only(redis_backend, caplog):
    user_id = _user()
    held = [await limit.acquire_account_run_slot(user_id)]
    proceed = threading.Event()
    real_acquire = limit._acquire_sync

    def slow_acquire(slot_id, owner):
        assert proceed.wait(5)
        return real_acquire(slot_id, owner)

    with patch.object(limit, "_acquire_sync", slow_acquire):
        task = asyncio.create_task(limit.acquire_account_run_slot(user_id))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        proceed.set()
        await asyncio.sleep(0.05)
    assert _count(redis_backend, user_id) == 1
    assert "Task exception was never retrieved" not in caplog.text
    for slot in held:
        await slot.release()


async def test_renewal_of_lost_lease_restores_same_slot_and_logs_count(redis_backend):
    client = redis_backend
    user_id = _user()
    slot = await limit.acquire_account_run_slot(user_id)
    client.delete(limit.account_runs_key(user_id))
    with patch.object(limit, "log_with_context") as log:
        await slot.renew()
    log.assert_called_once()
    assert log.call_args.kwargs["active_runs"] == 1
    assert log.call_args.kwargs["over_limit"] is False
    assert client.zrange(limit.account_runs_key(user_id), 0, -1) == [slot.slot_id]
    await slot.release()


@pytest.fixture
def restartable_redis():
    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("local Redis executable unavailable")
    import tempfile

    import redis

    directory = tempfile.mkdtemp(prefix="zs-acct-", dir="/tmp")
    socket = str(Path(directory) / "r.sock")

    def start():
        return subprocess.Popen(
            [executable, "--port", "0", "--unixsocket", socket, "--save", "", "--appendonly", "no"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def ready(client):
        deadline = time.monotonic() + 5
        while True:
            try:
                assert client.ping()
                return
            except (redis.ConnectionError, FileNotFoundError):
                assert time.monotonic() < deadline
                time.sleep(0.01)

    client = redis.Redis(unix_socket_path=socket, decode_responses=True, protocol=2,
                         socket_connect_timeout=0.2, socket_timeout=2)
    process = start()
    ready(client)
    state = {"process": process}

    def restart():
        state["process"].terminate()
        state["process"].wait(timeout=5)
        state["process"] = start()
        ready(client)

    try:
        yield client, restart
    finally:
        state["process"].kill()
        state["process"].wait(timeout=5)
        client.close()
        shutil.rmtree(directory, ignore_errors=True)


async def test_real_redis_restart_allows_immediately_then_recovers_limit(restartable_redis, monkeypatch):
    client, restart = restartable_redis
    monkeypatch.setenv("REDIS_URL", "redis://account-run-limit-test")
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: client)
    user_id = _user()
    old = await limit.acquire_account_run_slot(user_id)
    restart()
    fresh = await limit.acquire_account_run_slot(user_id)
    await old.renew()
    assert _count(client, user_id) == 2
    with pytest.raises(limit.AccountRunLimitExceeded):
        await limit.acquire_account_run_slot(user_id)
    await old.release()
    await fresh.release()
