"""
Steering message system for runtime intervention.

Allows users to inject messages into the running agent loop
for mid-execution guidance.
"""

import asyncio
import json
import os
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# Constants for message validation
MAX_STEERING_MESSAGE_LENGTH: Final[int] = 10000
CONTROL_CHAR_PATTERN = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

# session 级键（owner/msgs/runs）在无活动后过期的时间。
_STEERING_TTL_S: Final[int] = 3600  # session keys expire after 1h of inactivity

# 单个 run 的持有多久没有心跳就判定为僵尸。run_id 是进程内 UUID，唯一的释放
# 路径是 process_stream 的 finally；worker 被 SIGKILL / 部署重启 / OOM 杀掉时
# 那段 finally 永远不会执行，持有记录就永久留在 runs 集合里——此后该 session
# 的每次释放都因「还有其它持有者」而跳过删除，队列永不回收，陈旧引导消息会被
# 后续完全无关的 run 当作用户输入注入模型。活着的 run 每次轮询 steering
# （run 启动、工具输出边界、agent 边界、历史落库前）都会续期心跳。
# 除了轮询，service.process_stream 在整轮生成期间还会起一个独立的周期心跳
# task（每 _RUN_HEARTBEAT_INTERVAL_S 秒调用 heartbeat_steering_run_async），
# 长时间的模型调用 / 工具执行期间也持续续期，所以僵尸判定可以缩到 90 秒：
# 部署或崩溃杀掉进行中的生成后，该会话最多被锁 90 秒，而不是 1 小时。
_RUN_HEARTBEAT_TTL_S: Final[int] = 90
_RUN_HEARTBEAT_INTERVAL_S: Final[float] = 20.0


class SteeringSessionBusyError(RuntimeError):
    """A chat session already has an active generation run."""


def sanitize_steering_content(content: str, max_length: int = MAX_STEERING_MESSAGE_LENGTH) -> str:
    """
    Sanitize and validate steering message content.

    Args:
        content: Raw message content
        max_length: Maximum allowed length

    Returns:
        Sanitized content

    Raises:
        ValueError: If content is empty or only whitespace
    """
    if not content or not content.strip():
        raise ValueError("Steering message content cannot be empty")

    # Remove control characters
    sanitized = CONTROL_CHAR_PATTERN.sub('', content)

    # Truncate if too long
    if len(sanitized) > max_length:
        log_with_context(
            logger, 30,  # WARNING
            "Steering message truncated",
            original_length=len(sanitized),
            max_length=max_length,
        )
        sanitized = sanitized[:max_length]

    return sanitized


@dataclass
class SteeringMessage:
    """A steering message from the user."""
    id: str
    content: str
    created_at: datetime
    processed: bool = False
    processed_at: datetime | None = None


@dataclass
class SteeringQueue:
    """
    Async-safe queue for steering messages.
    """
    _messages: deque[SteeringMessage] = field(default_factory=deque)
    # 每次 get_pending() 轮询后调用的心跳回调，由 SteeringQueueManager 在建队时
    # 绑定到本 session。轮询是 run 仍然活着的唯一可观测信号（run 启动、工具输出
    # 边界、agent 边界、历史落库前都会轮询），用它续期持有者的心跳时间戳，
    # 长时间不心跳的 run 会被当作僵尸回收（见 _RUN_HEARTBEAT_TTL_S）。
    # 必须是同步、不加锁的轻量函数：它在队列自己的锁内被调用。
    on_poll: Callable[[], None] | None = None
    _lock: asyncio.Lock = field(init=False)

    def __post_init__(self):
        self._lock = asyncio.Lock()

    async def add(self, content: str) -> SteeringMessage:
        """Add a steering message to the queue with sanitization."""
        # Sanitize content before adding
        sanitized_content = sanitize_steering_content(content)

        async with self._lock:
            msg = SteeringMessage(
                id=f"steer-{datetime.now().timestamp()}",
                content=sanitized_content,
                created_at=datetime.now(),
            )
            self._messages.append(msg)
            log_with_context(
                logger,
                20,  # INFO
                "Steering message added",
                message_id=msg.id,
                content_preview=sanitized_content[:50],
            )
            return msg

    async def get_pending(self) -> list[SteeringMessage]:
        """Get all pending steering messages and mark as processed."""
        async with self._lock:
            if self.on_poll is not None:
                # 轮询即心跳：与 Redis 后端的 _pop_all_sync 保持同一语义。
                self.on_poll()
            pending = [m for m in self._messages if not m.processed]
            for m in pending:
                m.processed = True
                m.processed_at = datetime.now()
            if pending:
                log_with_context(
                    logger,
                    20,  # INFO
                    "Steering messages retrieved and marked processed",
                    count=len(pending),
                )
            return pending

    async def peek(self) -> list[SteeringMessage]:
        """Peek at pending messages without marking processed."""
        async with self._lock:
            return [m for m in self._messages if not m.processed]

    async def clear(self) -> None:
        """Clear all messages."""
        async with self._lock:
            self._messages.clear()
            log_with_context(logger, 20, "Steering queue cleared")


@dataclass
class SteeringQueueEntry:
    """Metadata wrapper for a steering queue session."""

    queue: SteeringQueue
    owner_user_id: str | None = None
    # 同一 chat session 的并发 stream run 共享同一个队列，但生命周期以 run
    # 为单位：cleanup 只在最后一个持有 run 释放后才真正删除队列，避免先结
    # 束的 run 删掉另一个仍在流式生成的 run 正在使用的队列。
    # run_id -> 最近一次心跳的 unix 时间戳；与 Redis 后端的 runs ZSET 同构，
    # 超过 _RUN_HEARTBEAT_TTL_S 未心跳的持有者按僵尸回收。
    active_runs: dict[str, float] = field(default_factory=dict)


class SteeringQueueManager:
    """
    Async manager for all session steering queues.

    Uses asyncio.Lock for consistent concurrency model.
    """

    def __init__(self):
        self._queues: dict[str, SteeringQueueEntry] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _reap_stale_runs(session_id: str, entry: SteeringQueueEntry) -> int:
        """回收长时间没有心跳的持有者，返回被回收的数量。

        与 Redis 后端的 ZREMRANGEBYSCORE 回收同语义：僵尸持有者不得继续把
        队列钉住。调用方必须已持有 self._lock（本方法不加锁、不 await）。
        """
        cutoff = time.time() - _RUN_HEARTBEAT_TTL_S
        stale = [rid for rid, ts in entry.active_runs.items() if ts <= cutoff]
        for rid in stale:
            del entry.active_runs[rid]
        if stale:
            log_with_context(
                logger,
                30,  # WARNING
                "Reaped stale steering run holds",
                session_id=session_id,
                stale_run_ids=stale,
                remaining_runs=len(entry.active_runs),
            )
        return len(stale)

    def _touch_runs(self, session_id: str) -> None:
        """把该 session 全部在册持有者的心跳推到当前时间。

        内存后端的队列对象按 session 共享（get_queue 对同一 session 返回同一个
        SteeringQueue），轮询时无法区分是哪个 run 在轮询，所以一次轮询续期全部
        持有者。方向是保守的：只会推迟回收，不会误杀仍在生成的 run。
        同步且不加锁——它由 SteeringQueue.get_pending 在队列锁内调用，事件循环
        单线程执行期间不会与 manager 的临界区交错。
        """
        entry = self._queues.get(session_id)
        if entry is None:
            return
        now = time.time()
        for rid in entry.active_runs:
            entry.active_runs[rid] = now

    async def get_queue(
        self,
        session_id: str,
        owner_user_id: str | None = None,
        create_if_missing: bool = True,
        run_id: str | None = None,
        exclusive_run: bool = False,
    ) -> SteeringQueue:
        """Get or create steering queue for a session."""
        async with self._lock:
            entry = self._queues.get(session_id)
            if entry is None:
                if not create_if_missing:
                    raise KeyError(session_id)
                queue = SteeringQueue()
                # 轮询驱动的心跳：见 SteeringQueue.on_poll。
                queue.on_poll = lambda sid=session_id: self._touch_runs(sid)
                entry = SteeringQueueEntry(
                    queue=queue,
                    owner_user_id=owner_user_id,
                )
                self._queues[session_id] = entry
                log_with_context(
                    logger,
                    20,  # INFO
                    "Steering queue created for session",
                    session_id=session_id,
                    owner_user_id=owner_user_id,
                )

            if (
                owner_user_id is not None
                and entry.owner_user_id is not None
                and entry.owner_user_id != owner_user_id
            ):
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Steering queue owner mismatch",
                    session_id=session_id,
                    requested_owner_user_id=owner_user_id,
                    bound_owner_user_id=entry.owner_user_id,
                )
                raise PermissionError(
                    f"Steering session {session_id} does not belong to user {owner_user_id}"
                )

            # Backfill legacy ownerless sessions when an owner is first known.
            if entry.owner_user_id is None and owner_user_id is not None:
                entry.owner_user_id = owner_user_id
                log_with_context(
                    logger,
                    20,
                    "Steering queue owner bound",
                    session_id=session_id,
                    owner_user_id=owner_user_id,
                )

            if run_id:
                # 注册前先回收僵尸持有者，避免崩溃残留把队列永久钉住
                # （与 _redis_create_sync 的 ZREMRANGEBYSCORE 同语义）。
                self._reap_stale_runs(session_id, entry)
                if exclusive_run and any(
                    active_run_id != run_id
                    for active_run_id in entry.active_runs
                ):
                    raise SteeringSessionBusyError(
                        f"Steering session {session_id} already has an active run"
                    )
                entry.active_runs[run_id] = time.time()

            return entry.queue

    async def get_queue_for_user(self, session_id: str, user_id: str) -> SteeringQueue:
        """Get queue only if it belongs to the requesting user."""
        async with self._lock:
            entry = self._queues.get(session_id)
            if entry is None:
                raise KeyError(session_id)
            # 查询路径同样回收僵尸：若该 session 曾经有持有者、而全部都是僵尸，
            # 说明没有任何 run 会来消费这条引导消息，必须按「会话不存在」处理
            # （POST /agent/steer -> 404），否则消息会滞留到下一次无关的 run 被
            # 当作用户输入注入。从未登记过持有者的队列（get_steering_queue_async
            # 建的占位队列）保持旧语义，不做删除。
            if entry.active_runs:
                self._reap_stale_runs(session_id, entry)
                if not entry.active_runs:
                    del self._queues[session_id]
                    raise KeyError(session_id)
            if entry.owner_user_id is not None and entry.owner_user_id != user_id:
                raise PermissionError(
                    f"Steering session {session_id} does not belong to user {user_id}"
                )
            # Backfill ownerless entry to first authenticated owner.
            if entry.owner_user_id is None:
                entry.owner_user_id = user_id
                log_with_context(
                    logger,
                    20,
                    "Steering queue owner bound from authorized access",
                    session_id=session_id,
                    owner_user_id=user_id,
                )
            return entry.queue

    async def has_other_active_runs(self, session_id: str, run_id: str) -> bool:
        """该 session 除了 run_id 之外是否还有别的活跃持有者。

        用于「先结束的 run 不得吞掉仍在生成的 run 的引导消息」这条不变量：
        队列只按 session 寻址（POST /agent/steer 的请求体没有 run_id），所以
        无法把消息定向投递给某个 run；能做到的是让结束中的 run 在发现还有别
        的 run 在生成时把消息交还回队列。
        判定前先回收僵尸持有者，避免崩溃残留的 run 让判定恒为「还有别人」，
        进而让消息在队列里空转到 TTL 过期（与 Redis 后端的
        ZREMRANGEBYSCORE 同语义）。
        """
        async with self._lock:
            entry = self._queues.get(session_id)
            if entry is None:
                return False
            self._reap_stale_runs(session_id, entry)
            return any(rid != run_id for rid in entry.active_runs)

    async def heartbeat_run(self, session_id: str, run_id: str) -> None:
        """续期单个持有者的心跳；已释放（不在册）的 run 不会被加回去。"""
        async with self._lock:
            entry = self._queues.get(session_id)
            if entry is not None and run_id in entry.active_runs:
                entry.active_runs[run_id] = time.time()

    async def has_active_runs(self, session_id: str) -> bool:
        """Whether a session has any live generation holder."""
        async with self._lock:
            entry = self._queues.get(session_id)
            if entry is None:
                return False
            self._reap_stale_runs(session_id, entry)
            return bool(entry.active_runs)

    async def requeue_if_other_active(
        self,
        session_id: str,
        run_id: str,
        owner_user_id: str,
        contents: list[str],
    ) -> bool:
        """Atomically requeue messages while another run still owns the session.

        The manager lock intentionally remains held through ``queue.add``.  Without
        that lifetime guard, the final concurrent run can delete the manager entry
        after the holder check but before the append, leaving messages on a detached
        ``SteeringQueue`` object that can never be looked up again.
        """
        if not contents:
            return False

        async with self._lock:
            entry = self._queues.get(session_id)
            if entry is None or entry.owner_user_id != owner_user_id:
                return False
            self._reap_stale_runs(session_id, entry)
            if not any(rid != run_id for rid in entry.active_runs):
                return False
            for content in contents:
                await entry.queue.add(content)
            return True

    async def cleanup(self, session_id: str, run_id: str | None = None) -> None:
        """Release a run's hold on the queue; delete only when no runs remain.

        run_id 为 None 时保持旧语义：无条件删除整个队列。
        """
        async with self._lock:
            entry = self._queues.get(session_id)
            if entry is None:
                return
            if run_id is not None:
                entry.active_runs.pop(run_id, None)
                # 释放时回收僵尸：崩溃残留的持有者不得让「最后一个 run 退出」
                # 这个判定永远为假（与 _RELEASE_RUN_SCRIPT 同语义）。
                self._reap_stale_runs(session_id, entry)
                if entry.active_runs:
                    log_with_context(
                        logger,
                        20,  # INFO
                        "Steering queue retained; other runs still active",
                        session_id=session_id,
                        run_id=run_id,
                        active_runs=len(entry.active_runs),
                    )
                    return
            del self._queues[session_id]
            log_with_context(
                logger,
                20,  # INFO
                "Steering queue cleaned up for session",
                session_id=session_id,
            )


# ---------------------------------------------------------------------------
# Redis-backed steering (cross-worker)
#
# The in-memory SteeringQueueManager above only works within a SINGLE process.
# Production runs multiple uvicorn workers (WEB_CONCURRENCY), so the worker that
# serves POST /agent/steer is usually NOT the worker running the SSE stream that
# created the queue — the lookup misses and the user gets a 404 "对话会话不存在".
# When Redis is configured we keep the per-session owner + pending messages in
# Redis so every worker sees the same queue. Dev without REDIS_URL falls back to
# the in-memory manager, which is correct for a single worker.
# ---------------------------------------------------------------------------

_REDIS_HEALTH_TTL_S: Final[float] = 30.0

_redis_health_checked_at: float = 0.0
_redis_is_healthy: bool = False
# The REDIS_URL the cached verdict belongs to. A different URL (tests swapping a
# fake for the developer's real one) must not inherit a stale "healthy".
_redis_health_url: str | None = None


def _owner_key(session_id: str) -> str:
    return f"steering:owner:{session_id}"


def _msgs_key(session_id: str) -> str:
    return f"steering:msgs:{session_id}"


def _runs_key(session_id: str) -> str:
    return f"steering:runs:{session_id}"


def _redis_available_sync() -> bool:
    """Whether to use Redis for steering (configured + reachable), cached briefly."""
    global _redis_health_checked_at, _redis_is_healthy, _redis_health_url
    # Mirror the rate-limiter's "auto" rule: only use Redis when explicitly
    # configured, so dev without a local Redis doesn't pay a connect timeout.
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return False
    now = time.monotonic()
    if redis_url == _redis_health_url and now < _redis_health_checked_at + _REDIS_HEALTH_TTL_S:
        return _redis_is_healthy
    try:
        from services.infra.redis_client import get_redis_client

        get_redis_client().ping()
        healthy = True
    except Exception as exc:  # noqa: BLE001 — any failure means fall back to memory
        healthy = False
        log_with_context(
            logger,
            30,  # WARNING
            "Steering Redis unavailable; falling back to in-memory queue "
            "(cross-worker steering will not work under multiple workers)",
            error=str(exc),
            error_type=type(exc).__name__,
        )
    _redis_is_healthy = healthy
    _redis_health_checked_at = now
    _redis_health_url = redis_url
    return healthy


async def _redis_available() -> bool:
    return await asyncio.to_thread(_redis_available_sync)


def _parse_created_at(raw: Any) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now()


class RedisSteeringQueue:
    """Steering queue backed by Redis so all workers share the same messages."""

    def __init__(self, session_id: str, run_id: str | None = None):
        self.session_id = session_id
        # 本对象是否代表某个 stream run 的持有者：只有持有者轮询时才续期自己在
        # runs ZSET 里的心跳（见 _pop_all_sync）。/agent/steer 那侧拿到的队列
        # 对象没有 run_id，不能替僵尸续命。
        self.run_id = run_id

    async def add(self, content: str) -> SteeringMessage:
        sanitized = sanitize_steering_content(content)
        msg = SteeringMessage(
            id=f"steer-{datetime.now().timestamp()}",
            content=sanitized,
            created_at=datetime.now(),
        )
        await asyncio.to_thread(self._add_sync, msg)
        log_with_context(
            logger, 20, "Steering message added (redis)",
            session_id=self.session_id, message_id=msg.id,
            content_preview=sanitized[:50],
        )
        return msg

    def _add_sync(self, msg: SteeringMessage) -> None:
        from services.infra.redis_client import get_redis_client

        client = get_redis_client()
        payload = json.dumps(
            {"id": msg.id, "content": msg.content, "created_at": msg.created_at.isoformat()}
        )
        pipe = client.pipeline()
        pipe.rpush(_msgs_key(self.session_id), payload)
        pipe.expire(_msgs_key(self.session_id), _STEERING_TTL_S)
        pipe.expire(_owner_key(self.session_id), _STEERING_TTL_S)
        # 不续期 runs 键：投递引导消息的 worker 不是持有者，让它替持有集合续命
        # 会把崩溃残留的 runs 键无限期保活。runs 的存活只由活着的 run 的心跳
        # 决定（_pop_all_sync）。
        pipe.execute()

    async def get_pending(self) -> list[SteeringMessage]:
        raws = await asyncio.to_thread(self._pop_all_sync)
        messages = [m for m in (self._deserialize(raw) for raw in raws) if m is not None]
        if messages:
            log_with_context(
                logger, 20, "Steering messages retrieved (redis)",
                session_id=self.session_id, count=len(messages),
            )
        return messages

    def _pop_all_sync(self) -> list[str]:
        from services.infra.redis_client import get_redis_client

        client = get_redis_client()
        # Atomically read all queued messages and clear them in one transaction.
        # get_pending() is polled when an agent run starts (initial injection),
        # at tool-output boundaries inside a run, at agent boundaries in the
        # workflow graph, and once more before the stream saves history, so
        # refreshing the owner key TTL here acts as a poll-driven heartbeat
        # that keeps the session alive for the whole stream instead of letting
        # it expire after the fixed 1h TTL mid-run (which would 404 subsequent
        # steering posts). 同一次轮询也顺带续期本 run 在 runs ZSET 里的成员
        # score——这是「持有者还活着」的唯一可观测证据。
        pipe = client.pipeline(transaction=True)
        pipe.lrange(_msgs_key(self.session_id), 0, -1)
        pipe.delete(_msgs_key(self.session_id))
        pipe.expire(_owner_key(self.session_id), _STEERING_TTL_S)
        if self.run_id:
            # 成员级心跳：只续期本 run 自己的 score。xx=True 保证不会把一个已经
            # 释放（ZREM 过）的 run 重新加回集合。runs 键的 TTL 只在确实有活着的
            # 持有者轮询时才续期——否则崩溃残留的 runs 键会被无关调用无限续命。
            pipe.zadd(_runs_key(self.session_id), {self.run_id: time.time()}, xx=True)
            pipe.expire(_runs_key(self.session_id), _STEERING_TTL_S)
        results = pipe.execute()
        return list(results[0] or [])

    async def peek(self) -> list[SteeringMessage]:
        raws = await asyncio.to_thread(self._peek_sync)
        return [m for m in (self._deserialize(raw) for raw in raws) if m is not None]

    def _peek_sync(self) -> list[str]:
        from services.infra.redis_client import get_redis_client

        return list(get_redis_client().lrange(_msgs_key(self.session_id), 0, -1) or [])

    async def clear(self) -> None:
        from services.infra.redis_client import get_redis_client

        await asyncio.to_thread(lambda: get_redis_client().delete(_msgs_key(self.session_id)))

    @staticmethod
    def _deserialize(raw: str) -> SteeringMessage | None:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        content = str(data.get("content") or "")
        if not content:
            return None
        return SteeringMessage(
            id=str(data.get("id") or ""),
            content=content,
            created_at=_parse_created_at(data.get("created_at")),
            processed=True,
            processed_at=datetime.now(),
        )


def _redis_create_sync(
    session_id: str,
    owner_user_id: str | None,
    run_id: str | None = None,
) -> None:
    from services.infra.redis_client import get_redis_client

    # 注册必须和释放（_RELEASE_RUN_SCRIPT）互相原子：若拆成 SET owner 与
    # SADD runs 两次 round-trip，另一个 worker 上并发结束的 run 会整段插在
    # 中间执行释放（SREM → SCARD==0 → DEL owner/msgs/runs），结果本 run 注册
    # 完成后 runs={本 run} 而 owner 已被删——本 run 剩余时间里所有
    # POST /agent/steer 都 404（_add_sync/_pop_all_sync 只 EXPIRE，不会重建
    # owner 键），并且已排队未消费的 steering 消息随 msgs 键一起丢失。
    # MULTI/EXEC 让释放脚本只能看到「注册前」或「注册后」两种状态。
    client = get_redis_client()
    now = time.time()
    pipe = client.pipeline(transaction=True)
    if run_id:
        # 记录持有队列的 run：同一 session 的并发 run 共享 owner/msgs 键，
        # cleanup 只在最后一个 run 释放时才真正删除（见 _RELEASE_RUN_SCRIPT）。
        # 先写 runs 再写 owner：即使将来被拆回多条命令，也不会留下
        # 「owner 已写、runs 未写」这个最坏顺序。
        # runs 是 ZSET（member=run_id, score=最近一次心跳的 unix 时间），先按
        # score 清掉僵尸成员再登记自己：崩溃残留的持有者不能把新 run 结束后的
        # 队列删除挡下来。
        pipe.zremrangebyscore(_runs_key(session_id), "-inf", now - _RUN_HEARTBEAT_TTL_S)
        pipe.zadd(_runs_key(session_id), {run_id: now})
        pipe.expire(_runs_key(session_id), _STEERING_TTL_S)
    if owner_user_id:
        # The session owner starts the stream they own (session_id is their
        # chat_session.id), so binding the owner here is authoritative.
        pipe.set(_owner_key(session_id), owner_user_id, ex=_STEERING_TTL_S)
    else:
        # 调用方不知道 owner 时（get_steering_queue_async）只在键缺失时占位：
        # nx=True 保证不会把已绑定的真实 owner 降级成空串，否则
        # get_steering_queue_for_user_async 会跳过 owner 校验并把队列
        # backfill 给任意请求者。
        pipe.set(_owner_key(session_id), "", ex=_STEERING_TTL_S, nx=True)
        pipe.expire(_owner_key(session_id), _STEERING_TTL_S)
    pipe.execute()


_CLAIM_EXCLUSIVE_RUN_SCRIPT: Final[str] = """
local owner = redis.call('GET', KEYS[2])
if owner and owner ~= '' and ARGV[2] ~= '' and owner ~= ARGV[2] then
    return -1
end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[3])
if redis.call('ZCARD', KEYS[1]) > 0 and not redis.call('ZSCORE', KEYS[1], ARGV[1]) then
    return 0
end
redis.call('ZADD', KEYS[1], ARGV[4], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[5])
if ARGV[2] ~= '' then
    redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[5])
else
    redis.call('SET', KEYS[2], '', 'EX', ARGV[5], 'NX')
    redis.call('EXPIRE', KEYS[2], ARGV[5])
end
return 1
"""


def _redis_claim_exclusive_run_sync(
    session_id: str,
    owner_user_id: str | None,
    run_id: str,
) -> None:
    """Atomically claim the sole active generation slot for a chat session."""
    from services.infra.redis_client import get_redis_client

    now = time.time()
    result = get_redis_client().eval(
        _CLAIM_EXCLUSIVE_RUN_SCRIPT,
        2,
        _runs_key(session_id),
        _owner_key(session_id),
        run_id,
        owner_user_id or "",
        now - _RUN_HEARTBEAT_TTL_S,
        now,
        _STEERING_TTL_S,
    )
    if int(result) == -1:
        raise PermissionError(
            f"Steering session {session_id} does not belong to user {owner_user_id}"
        )
    if int(result) == 0:
        raise SteeringSessionBusyError(
            f"Steering session {session_id} already has an active run"
        )


def _redis_heartbeat_run_sync(session_id: str, run_id: str) -> None:
    """成员级心跳：只续期本 run 的 score，xx=True 不会复活已释放的 run。"""
    from services.infra.redis_client import get_redis_client

    pipe = get_redis_client().pipeline(transaction=True)
    pipe.zadd(_runs_key(session_id), {run_id: time.time()}, xx=True)
    pipe.expire(_runs_key(session_id), _STEERING_TTL_S)
    pipe.execute()


def _redis_get_owner_sync(session_id: str) -> str | None:
    from services.infra.redis_client import get_redis_client

    return get_redis_client().get(_owner_key(session_id))


def _redis_backfill_owner_sync(session_id: str, user_id: str) -> None:
    from services.infra.redis_client import get_redis_client

    # xx=True: only set when the key still exists (don't resurrect a cleaned session).
    get_redis_client().set(_owner_key(session_id), user_id, ex=_STEERING_TTL_S, xx=True)


# Lua 脚本保证「移除本 run 的持有 → 回收僵尸持有者 → 判断是否还有其它 run →
# 删除」在 Redis 端原子执行：非原子的 ZREM/ZCARD/DEL 序列之间，另一个 worker
# 上的并发 run 可能刚完成注册，会把它正在使用的队列误删。
# ARGV[2] 是心跳截止时间：score 不晚于它的成员一定来自被 SIGKILL / 部署重启 /
# OOM 杀掉的 worker（活着的 run 每次轮询都会续期），必须先清掉，否则 ZCARD
# 永远大于 0，owner/msgs/runs 三键永不回收。
_RELEASE_RUN_SCRIPT: Final[str] = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[2])
if redis.call('ZCARD', KEYS[1]) == 0 then
    redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
    return 1
end
return 0
"""

# 查询路径上的僵尸回收：曾经有持有者、但全部超时未心跳，说明没有任何 run 会来
# 消费新的引导消息——此时必须连同 owner/msgs 一起删除，让 POST /agent/steer
# 如实 404，而不是 200 queued 之后把这条消息滞留给下一次无关的 run。
# 从未登记过持有者的 session（get_steering_queue_async 建的占位）ZCARD 恒为 0，
# 提前返回以保持旧语义。
_REAP_STALE_RUNS_SCRIPT: Final[str] = """
if redis.call('ZCARD', KEYS[1]) == 0 then
    return 0
end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) == 0 then
    redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
    return 1
end
return 0
"""


# 「除了我自己以外还有几个活跃持有者」——与内存后端的 has_other_active_runs
# 同语义。必须整段原子：拆成 ZREMRANGEBYSCORE / ZCARD / ZSCORE 三次 round-trip
# 时，另一个 worker 上的并发 run 可能正好插在中间完成注册或释放，判定结果既
# 可能凭空多出一个持有者（消息被交还给已经不存在的 run，滞留到 TTL），也可能
# 少算一个（消息被结束中的 run 吞进本轮历史）。
# 与回收脚本不同，这里不删除 owner/msgs 键：调用它时本 run 通常仍持有队列。
_COUNT_OTHER_ACTIVE_RUNS_SCRIPT: Final[str] = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[2])
local n = redis.call('ZCARD', KEYS[1])
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then
    n = n - 1
end
return n
"""

# 把「确认还有其它活跃 run」与「消息重新入队」放在同一个 Redis 原子单元里。
# 若拆成 COUNT / GET owner / RPUSH 三次 round-trip，最后一个并发 run 可以在
# GET 与 RPUSH 之间释放并删除 owner/msgs/runs；随后 RPUSH 会只复活 msgs，留下
# 无 owner、无消费者的孤儿列表，而 service 又会把这些消息从本轮历史中移除。
#
# KEYS: runs, owner, msgs
# ARGV: current_run_id, heartbeat_cutoff, owner_user_id, ttl, payload...
_REQUEUE_IF_OTHER_ACTIVE_SCRIPT: Final[str] = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[2])
local n = redis.call('ZCARD', KEYS[1])
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then
    n = n - 1
end
if n <= 0 then
    return 0
end
local owner = redis.call('GET', KEYS[2])
if not owner or owner ~= ARGV[3] then
    return 0
end
for i = 5, #ARGV do
    redis.call('RPUSH', KEYS[3], ARGV[i])
end
redis.call('EXPIRE', KEYS[3], ARGV[4])
redis.call('EXPIRE', KEYS[2], ARGV[4])
return 1
"""


def _redis_has_other_active_runs_sync(session_id: str, run_id: str) -> bool:
    from services.infra.redis_client import get_redis_client

    others = get_redis_client().eval(
        _COUNT_OTHER_ACTIVE_RUNS_SCRIPT,
        1,
        _runs_key(session_id),
        run_id,
        time.time() - _RUN_HEARTBEAT_TTL_S,
    )
    try:
        return int(others) > 0
    except (TypeError, ValueError):  # pragma: no cover — 替身返回了非数值
        return False


def _redis_requeue_if_other_active_sync(
    session_id: str,
    run_id: str,
    owner_user_id: str,
    contents: list[str],
) -> bool:
    """Atomically append ``contents`` only while another run is still active."""
    from services.infra.redis_client import get_redis_client

    payloads: list[str] = []
    for content in contents:
        sanitized = sanitize_steering_content(content)
        message = SteeringMessage(
            id=f"steer-{datetime.now().timestamp()}",
            content=sanitized,
            created_at=datetime.now(),
        )
        payloads.append(
            json.dumps(
                {
                    "id": message.id,
                    "content": message.content,
                    "created_at": message.created_at.isoformat(),
                }
            )
        )

    accepted = get_redis_client().eval(
        _REQUEUE_IF_OTHER_ACTIVE_SCRIPT,
        3,
        _runs_key(session_id),
        _owner_key(session_id),
        _msgs_key(session_id),
        run_id,
        time.time() - _RUN_HEARTBEAT_TTL_S,
        owner_user_id,
        _STEERING_TTL_S,
        *payloads,
    )
    return bool(accepted)


def _redis_reap_stale_runs_sync(session_id: str) -> bool:
    """回收该 session 的僵尸持有者；返回 True 表示整个 session 已被删除。"""
    from services.infra.redis_client import get_redis_client

    reaped = get_redis_client().eval(
        _REAP_STALE_RUNS_SCRIPT,
        3,
        _runs_key(session_id),
        _owner_key(session_id),
        _msgs_key(session_id),
        time.time() - _RUN_HEARTBEAT_TTL_S,
    )
    if reaped:
        log_with_context(
            logger,
            30,  # WARNING
            "Reaped steering session held only by stale runs",
            session_id=session_id,
        )
    return bool(reaped)


def _redis_cleanup_sync(session_id: str, run_id: str | None = None) -> None:
    from services.infra.redis_client import get_redis_client

    client = get_redis_client()
    if not run_id:
        # 旧语义：无条件删除（测试/运维路径）。
        client.delete(_owner_key(session_id), _msgs_key(session_id), _runs_key(session_id))
        return
    client.eval(
        _RELEASE_RUN_SCRIPT,
        3,
        _runs_key(session_id),
        _owner_key(session_id),
        _msgs_key(session_id),
        run_id,
        time.time() - _RUN_HEARTBEAT_TTL_S,
    )


# Global in-memory queue manager (fallback for single-worker / no-Redis dev).
_queue_manager = SteeringQueueManager()


async def get_steering_queue_async(session_id: str) -> Any:
    """Get or create steering queue for a session (async version).

    不绑定 owner：仅在 owner 键缺失时占位，已绑定的 owner 保持不变。生产入口
    请用 create_steering_queue_async（写入真实 owner）或
    get_steering_queue_for_user_async（校验 owner）。
    """
    if await _redis_available():
        await asyncio.to_thread(_redis_create_sync, session_id, None)
        return RedisSteeringQueue(session_id)
    return await _queue_manager.get_queue(session_id)


async def create_steering_queue_async(
    session_id: str,
    owner_user_id: str | None,
    run_id: str | None = None,
    *,
    exclusive_run: bool = False,
) -> Any:
    """Create/get queue and bind ownership when available.

    ``exclusive_run`` is used by chat generation: one chat session may have
    only one active writer so history order and message_count remain coherent.
    Legacy queue callers keep the multi-holder behavior by default.
    """
    if await _redis_available():
        if exclusive_run and run_id:
            await asyncio.to_thread(
                _redis_claim_exclusive_run_sync,
                session_id,
                owner_user_id,
                run_id,
            )
        else:
            await asyncio.to_thread(
                _redis_create_sync, session_id, owner_user_id, run_id
            )
        return RedisSteeringQueue(session_id, run_id=run_id)
    return await _queue_manager.get_queue(
        session_id,
        owner_user_id=owner_user_id,
        run_id=run_id,
        exclusive_run=exclusive_run,
    )


async def get_steering_queue_for_user_async(session_id: str, user_id: str) -> Any:
    """Get an existing queue for a user; KeyError if absent, PermissionError on mismatch."""
    if await _redis_available():
        # 先回收僵尸持有者：若该 session 只剩崩溃残留的 run，键会在这里被删除，
        # 随后的 GET owner 返回 None -> KeyError -> /agent/steer 如实 404。
        await asyncio.to_thread(_redis_reap_stale_runs_sync, session_id)
        owner = await asyncio.to_thread(_redis_get_owner_sync, session_id)
        if owner is None:
            raise KeyError(session_id)
        if owner and owner != user_id:
            raise PermissionError(
                f"Steering session {session_id} does not belong to user {user_id}"
            )
        if not owner:
            await asyncio.to_thread(_redis_backfill_owner_sync, session_id, user_id)
        return RedisSteeringQueue(session_id)
    return await _queue_manager.get_queue_for_user(session_id, user_id)


async def has_other_active_runs_async(session_id: str, run_id: str) -> bool:
    """该 session 是否还有 run_id 之外的 stream run 在生成中。

    两个后端必须给出同一个答案：内存后端看 SteeringQueueEntry.active_runs，
    Redis 后端看 runs ZSET，两侧都先按心跳回收僵尸持有者。历史上这个模块反
    复出问题的根源就是两套实现语义漂移，所以入口只有这一个。
    """
    if not run_id:
        return False
    if await _redis_available():
        return await asyncio.to_thread(
            _redis_has_other_active_runs_sync, session_id, run_id
        )
    return await _queue_manager.has_other_active_runs(session_id, run_id)


async def heartbeat_steering_run_async(session_id: str, run_id: str) -> None:
    """续期一个仍在生成的 run 的持有心跳（周期心跳 task 调用）。"""
    if not run_id:
        return
    if await _redis_available():
        await asyncio.to_thread(_redis_heartbeat_run_sync, session_id, run_id)
        return
    await _queue_manager.heartbeat_run(session_id, run_id)


async def has_active_runs_async(session_id: str) -> bool:
    """Whether a chat session currently has any live stream run."""
    if await _redis_available():
        return await asyncio.to_thread(
            _redis_has_other_active_runs_sync,
            session_id,
            "__active-run-preflight__",
        )
    return await _queue_manager.has_active_runs(session_id)


async def requeue_steering_if_other_active_async(
    session_id: str,
    run_id: str,
    owner_user_id: str,
    contents: list[str],
) -> bool:
    """Requeue messages iff another active run can consume them.

    The holder decision and append are one atomic lifetime operation on both
    backends. ``False`` tells the caller to retain the messages in this run's
    history instead of discarding them.
    """
    if not contents or not run_id or not owner_user_id:
        return False
    if await _redis_available():
        accepted = await asyncio.to_thread(
            _redis_requeue_if_other_active_sync,
            session_id,
            run_id,
            owner_user_id,
            contents,
        )
    else:
        accepted = await _queue_manager.requeue_if_other_active(
            session_id,
            run_id,
            owner_user_id,
            contents,
        )

    if accepted:
        log_with_context(
            logger,
            20,  # INFO
            "Steering messages handed back to concurrent run",
            session_id=session_id,
            run_id=run_id,
            count=len(contents),
        )
    return accepted


async def cleanup_steering_queue_async(session_id: str, run_id: str | None = None) -> None:
    """Release a run's hold on the steering queue (async version).

    带 run_id 时只释放该 run 的持有，最后一个持有者退出才真正删除；
    不带 run_id 保持旧语义，无条件删除——会连带删掉其它并发 run 正在使用的
    队列，因此仅用于测试/运维清理，生产路径（service.py）始终传 run_id。
    """
    if await _redis_available():
        await asyncio.to_thread(_redis_cleanup_sync, session_id, run_id)
        return
    await _queue_manager.cleanup(session_id, run_id=run_id)
