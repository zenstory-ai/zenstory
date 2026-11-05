"""账号级写作任务名额：同一账号最多同时运行 2 个 /agent/stream 任务。

名额按完整的用户任务计：路由、内部模型调用、审稿和工具不另占；跨项目、会话、设备和进程共享。
与会话生成槽（agent.core.steering）同一套做法：每个任务是 Redis ZSET 里的一个成员，score 是
最近一次心跳；Lua 脚本先回收失效成员再判断容量，所以获取是原子的。

生命周期：路由层在预扣额度之前获取（心跳随之开始）；process_stream 走到自己的 try 时接管
（adopt），之后由它的收尾在真正结束（含取消路径的残稿补存）时释放。接管之前的任何失败由路由层
释放。进程被杀时心跳停止，名额在 SLOT_STALE_AFTER_S 后被下一次获取回收。

Redis 是成本保护，不是写作可用性的硬依赖。只有 Lua 明确看到已有 2 个活跃任务时才拒绝；Redis
未配置、连接失败、超时或状态丢失都放行。配置了 Redis 但获取结果未知时，仍保留同一个 slot id，
后续心跳会尝试登记、释放会尝试删除，避免服务端已经执行脚本而客户端超时后留下无法清理的成员。
staging / production 没配 Redis 时使用无跟踪名额，不退回进程内计数，否则多进程会各自误拦。

这是可用性优先的 best-effort 上限：Redis 故障、无持久化重启、局部断联或滚动发布期间可能短暂超过
2 个；Redis 恢复且运行中的任务完成一次心跳后，计数会逐步恢复。任务仍在运行时不中止。
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Final
from uuid import uuid4

from config.agent_runtime import AGENT_RUN_WALL_CLOCK_TIMEOUT_S
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

ACCOUNT_MAX_CONCURRENT_RUNS: Final[int] = 2

SLOT_STALE_AFTER_S: Final[int] = 90
SLOT_HEARTBEAT_INTERVAL_S: float = 20.0
# 墙钟时限由 SSEStreamPump 强制，之后只剩有上限的收尾。超过这个时长还在续租只可能是泄漏
# （生成器没被关闭）：停止续租，让名额失效，账号不会被一直占住。
SLOT_MAX_LIFETIME_S: float = AGENT_RUN_WALL_CLOCK_TIMEOUT_S + 300

_KEY_TTL_S: Final[int] = SLOT_STALE_AFTER_S * 2
_HOSTED_ENVIRONMENTS: Final[frozenset[str]] = frozenset({"production", "staging"})

# KEYS: runs；ARGV: slot_id, 失效截止时间, now, 上限, 键 TTL。返回 1 已获取，0 已满。
_ACQUIRE_SCRIPT: Final[str] = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[2])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[4]) then
    return 0
end
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""

# 不用 XX：Redis 丢了成员时，还在运行的任务要回到计数里。
# KEYS: runs；ARGV: slot_id, now, 键 TTL。成员本来就在返回 0，否则返回写回后的总数。
_RENEW_SCRIPT: Final[str] = """
local existed = redis.call('ZSCORE', KEYS[1], ARGV[1])
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
if existed then
    return 0
end
return redis.call('ZCARD', KEYS[1])
"""


class AccountRunLimitExceeded(RuntimeError):
    """这个账号已有 ACCOUNT_MAX_CONCURRENT_RUNS 个任务在运行。"""


def account_runs_key(user_id: str) -> str:
    return f"agent:account_runs:{user_id}"


def _new_slot_id() -> str:
    return uuid4().hex


def _backend() -> str:
    if os.getenv("REDIS_URL"):
        return "redis"
    if os.getenv("ENVIRONMENT", "development").strip().lower() in _HOSTED_ENVIRONMENTS:
        return "untracked"
    return "memory"


# 进程内后端（仅本地开发 / 测试）：user_id -> {slot_id: 最近心跳}。
_memory_runs: dict[str, dict[str, float]] = {}
_memory_lock = threading.Lock()


def _acquire_sync(slot_id: str, user_id: str) -> str:
    now = time.time()
    cutoff = now - SLOT_STALE_AFTER_S
    backend = _backend()
    if backend == "redis":
        from services.infra.redis_client import get_redis_client

        try:
            admitted = int(
                get_redis_client().eval(
                    _ACQUIRE_SCRIPT,
                    1,
                    account_runs_key(user_id),
                    slot_id,
                    cutoff,
                    now,
                    ACCOUNT_MAX_CONCURRENT_RUNS,
                    _KEY_TTL_S,
                )
            )
        except Exception as exc:  # noqa: BLE001 — 无法明确确认已满时，可用性优先
            log_with_context(
                logger,
                30,  # WARNING
                "Account run limit unavailable; allowing the task",
                user_id=user_id,
                error_type=type(exc).__name__,
            )
            return backend
    elif backend == "memory":
        with _memory_lock:
            runs = _memory_runs.setdefault(user_id, {})
            for stale in [slot for slot, beat in runs.items() if beat <= cutoff]:
                del runs[stale]
            admitted = int(len(runs) < ACCOUNT_MAX_CONCURRENT_RUNS)
            if admitted:
                runs[slot_id] = now
    else:
        return backend
    if not admitted:
        raise AccountRunLimitExceeded(f"Account {user_id} already has {ACCOUNT_MAX_CONCURRENT_RUNS} active runs")
    return backend


def _remove_slot_sync(user_id: str, slot_id: str, backend: str) -> None:
    """释放失败不抛出：成员随失效窗口回收。"""
    try:
        if backend == "redis":
            from services.infra.redis_client import get_redis_client

            get_redis_client().zrem(account_runs_key(user_id), slot_id)
        elif backend == "memory":
            with _memory_lock:
                runs = _memory_runs.get(user_id)
                if runs is not None:
                    runs.pop(slot_id, None)
                    if not runs:
                        del _memory_runs[user_id]
    except Exception as exc:  # noqa: BLE001
        log_with_context(
            logger,
            30,  # WARNING
            "Failed to release account run slot; it expires with its heartbeat",
            user_id=user_id,
            stale_after_s=SLOT_STALE_AFTER_S,
            error_type=type(exc).__name__,
        )


class AccountRunSlot:
    """一个已获取的名额。心跳在获取时开始，release 幂等。"""

    def __init__(self, slot_id: str, user_id: str, backend: str) -> None:
        self.slot_id = slot_id
        self.user_id = user_id
        self._backend = backend
        self.adopted = False
        self._released = False
        # 让「续租」和「释放」互斥：线程里在途的续租不能在释放之后把成员写回去。
        self._state_lock = threading.Lock()
        self._release_future: asyncio.Future[None] | None = None
        self._heartbeat_task = (
            asyncio.create_task(self._heartbeat_loop()) if backend != "untracked" else None
        )

    def adopt(self) -> None:
        """process_stream 接管：从这里起由它的收尾负责释放。"""
        self.adopted = True

    @property
    def renewing(self) -> bool:
        return self._heartbeat_task is not None and not self._heartbeat_task.done()

    def _renew_sync(self) -> None:
        with self._state_lock:
            if self._released:
                return
            now = time.time()
            if self._backend == "redis":
                from services.infra.redis_client import get_redis_client

                restored_to = int(
                    get_redis_client().eval(
                        _RENEW_SCRIPT, 1, account_runs_key(self.user_id), self.slot_id, now, _KEY_TTL_S
                    )
                )
                if restored_to:
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Account run slot had lost its lease and was written back",
                        user_id=self.user_id,
                        active_runs=restored_to,
                        over_limit=restored_to > ACCOUNT_MAX_CONCURRENT_RUNS,
                    )
            elif self._backend == "memory":
                with _memory_lock:
                    _memory_runs.setdefault(self.user_id, {})[self.slot_id] = now

    async def renew(self) -> None:
        await asyncio.to_thread(self._renew_sync)

    async def _heartbeat_loop(self) -> None:
        deadline = time.monotonic() + SLOT_MAX_LIFETIME_S
        while True:
            await asyncio.sleep(SLOT_HEARTBEAT_INTERVAL_S)
            if time.monotonic() >= deadline:
                log_with_context(
                    logger,
                    40,  # ERROR
                    "Account run slot outlived its ceiling; no longer renewing",
                    user_id=self.user_id,
                    max_lifetime_s=SLOT_MAX_LIFETIME_S,
                )
                # 不再写回，这个成员才会真的失效。
                return
            try:
                await self.renew()
            except Exception as exc:  # noqa: BLE001 — 心跳失败不影响运行，下一拍再试
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Account run slot heartbeat failed",
                    user_id=self.user_id,
                    error_type=type(exc).__name__,
                )

    def _release_sync(self) -> None:
        with self._state_lock:
            self._released = True
            _remove_slot_sync(self.user_id, self.slot_id, self._backend)

    def release(self) -> asyncio.Future[None]:
        """同步排上释放并返回可等待的 future；断线路径不等也能完成。重复调用返回同一个结果。"""
        if self._release_future is None:
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
            self._release_future = asyncio.get_running_loop().run_in_executor(None, self._release_sync)
        # shield：等待方被取消不能连带取消还在排队的释放，之后再次等待也拿得到结果。
        return asyncio.shield(self._release_future)


async def acquire_account_run_slot(user_id: str) -> AccountRunSlot:
    """原子获取一个名额。只有后端明确确认已满时抛 AccountRunLimitExceeded。"""
    slot_id = _new_slot_id()
    loop = asyncio.get_running_loop()
    taken = loop.run_in_executor(None, _acquire_sync, slot_id, user_id)
    try:
        backend = await asyncio.shield(taken)
    except asyncio.CancelledError:
        # 请求被取消时线程可能已经占位，又没有 slot 对象负责释放：等它结束后撤销。

        def _undo_cancelled_acquire(future: asyncio.Future[str]) -> None:
            if not future.cancelled() and future.exception() is None:
                loop.run_in_executor(None, _remove_slot_sync, user_id, slot_id, future.result())

        taken.add_done_callback(_undo_cancelled_acquire)
        raise
    return AccountRunSlot(slot_id, user_id, backend)
