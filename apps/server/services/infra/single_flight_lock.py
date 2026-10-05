"""跨请求的「同一时刻只跑一份」锁（single-flight）。

用于后台重活的去重，例如同一项目的向量索引重建：Redis 可用时用
``SET key token NX EX ttl`` 抢锁、Lua 比对 token 后删除释放，多 worker 共享；
Redis 不可用（未配置或出错）时退回进程内存，与 middleware.rate_limit 的后端
选择口径一致（RATE_LIMIT_BACKEND=memory|redis|auto）。

锁带 TTL：持有者崩溃没来得及释放时，到期自动失效，不会永久卡住。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid

from services.infra.redis_client import get_redis_client
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

_KEY_PREFIX = "single_flight"

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_memory_locks: dict[str, tuple[str, float]] = {}
_memory_lock = threading.Lock()


def _use_redis() -> bool:
    backend = os.getenv("RATE_LIMIT_BACKEND", "auto").strip().lower()
    if backend == "memory":
        return False
    if backend == "redis":
        return True
    return bool(os.getenv("REDIS_URL"))


def _redis_key(name: str) -> str:
    return f"{_KEY_PREFIX}:{name}"


def _acquire_memory(name: str, token: str, ttl_seconds: int) -> bool:
    now = time.monotonic()
    with _memory_lock:
        current = _memory_locks.get(name)
        if current is not None and current[1] > now:
            return False
        _memory_locks[name] = (token, now + ttl_seconds)
        return True


def _release_memory(name: str, token: str) -> None:
    with _memory_lock:
        current = _memory_locks.get(name)
        if current is not None and current[0] == token:
            del _memory_locks[name]


def acquire_single_flight(name: str, ttl_seconds: int) -> str | None:
    """尝试抢占 ``name`` 对应的锁，成功返回释放用的 token，已被占用返回 None。"""
    token = uuid.uuid4().hex
    if _use_redis():
        try:
            acquired = get_redis_client().set(
                _redis_key(name), token, nx=True, ex=max(1, int(ttl_seconds))
            )
            return token if acquired else None
        except Exception as exc:  # pragma: no cover - depends on runtime infra
            log_with_context(
                logger,
                logging.WARNING,
                "Single-flight Redis backend unavailable; falling back to in-memory lock",
                lock_name=name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
    return token if _acquire_memory(name, token, max(1, int(ttl_seconds))) else None


def release_single_flight(name: str, token: str) -> None:
    """释放自己持有的锁（token 不匹配时什么都不做）。"""
    if _use_redis():
        try:
            get_redis_client().eval(_RELEASE_SCRIPT, 1, _redis_key(name), token)
        except Exception as exc:  # pragma: no cover - depends on runtime infra
            log_with_context(
                logger,
                logging.WARNING,
                "Failed to release single-flight lock in Redis",
                lock_name=name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
    # 抢锁时可能已经退回内存后端，这里总是顺带清一次内存表。
    _release_memory(name, token)


def reset_memory_locks() -> None:
    """测试辅助：清空进程内的锁表。"""
    with _memory_lock:
        _memory_locks.clear()
