"""作者主动停止一次 /agent/stream 运行的信号。

前端点「停止生成」时先调 ``POST /agent/stop``（带 ``X-Agent-Run-ID``），再继续读流：
服务端自己结束这次运行、按「主动停止」结算，并在仍然打开的连接上发终止帧和
``quota_refunded``。这样服务端能区分「作者点了停止」和「连接断了」，前端也能拿到
准确的计费结果。

信号按 ``user_id`` 命名空间存放：停止端点只能写当前登录用户自己的键，运行方只读
自己用户的键，因此不需要额外的归属校验。同一 worker 内用 ``asyncio.Event`` 立即
唤醒；配置了 Redis 时同时写 Redis，别的 worker 上的运行最多 ``poll_interval_s``
秒内看到。
"""

from __future__ import annotations

import asyncio
import contextlib

from agent.core.steering import _redis_available
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# 停止信号只在运行期间有意义；墙钟时限之外的键自然过期。
STOP_KEY_TTL_S = 1800
DEFAULT_POLL_INTERVAL_S = 0.5

_local_events: dict[tuple[str, str], asyncio.Event] = {}


def _stop_key(user_id: str, agent_run_id: str) -> str:
    return f"agent:stop:{user_id}:{agent_run_id}"


async def _redis_set_stop(user_id: str, agent_run_id: str) -> bool:
    if not await _redis_available():
        return False
    from services.infra.redis_client import get_redis_client

    key = _stop_key(user_id, agent_run_id)
    await asyncio.to_thread(lambda: get_redis_client().set(key, "1", ex=STOP_KEY_TTL_S))
    return True


async def _redis_stop_requested(user_id: str, agent_run_id: str) -> bool:
    if not await _redis_available():
        return False
    from services.infra.redis_client import get_redis_client

    key = _stop_key(user_id, agent_run_id)
    return bool(await asyncio.to_thread(lambda: get_redis_client().exists(key)))


async def request_stop(user_id: str, agent_run_id: str) -> None:
    """记录作者要停止这次运行；幂等，运行不存在或已结束时什么也不发生。"""
    event = _local_events.get((user_id, agent_run_id))
    if event is not None:
        event.set()
    try:
        await _redis_set_stop(user_id, agent_run_id)
    except Exception as exc:  # noqa: BLE001 — 本 worker 内的信号已经生效
        log_with_context(
            logger,
            30,  # WARNING
            "Failed to publish agent stop request to Redis",
            agent_run_id=agent_run_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )


class RunStopWatch:
    """一次运行的停止信号；``async with`` 期间登记本 worker 的 Event。"""

    def __init__(
        self,
        user_id: str,
        agent_run_id: str,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self._key = (user_id, agent_run_id)
        self._poll_interval_s = poll_interval_s
        self._event = asyncio.Event()

    @property
    def stop_requested(self) -> bool:
        return self._event.is_set()

    def __enter__(self) -> RunStopWatch:
        _local_events[self._key] = self._event
        return self

    def __exit__(self, *_exc: object) -> None:
        if _local_events.get(self._key) is self._event:
            del _local_events[self._key]

    async def wait(self) -> None:
        """等到作者要求停止才返回（本 worker 立即，跨 worker 按轮询间隔）。"""
        user_id, agent_run_id = self._key
        while not self._event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._event.wait(), timeout=self._poll_interval_s)
            if self._event.is_set():
                return
            try:
                if await _redis_stop_requested(user_id, agent_run_id):
                    self._event.set()
            except Exception as exc:  # noqa: BLE001 — 下一轮再试，本 worker 信号不受影响
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Failed to read agent stop request from Redis",
                    agent_run_id=agent_run_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
