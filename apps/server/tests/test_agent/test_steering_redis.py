"""Tests for the Redis-backed (cross-worker) steering queue.

These simulate the production failure mode: the worker serving POST /agent/steer
is a different process from the one running the SSE stream. With the in-memory
queue that lookup misses (404 "对话会话不存在"); with Redis both workers share state.
"""

import threading
from collections.abc import Callable

import pytest


class FakePipeline:
    """MULTI/EXEC 语义：排队的命令在 execute() 内作为一个原子单元执行，
    执行期间不会被其它 "worker" 线程的命令插入（与真实 Redis 一致）。"""

    def __init__(self, client):
        self.client = client
        self.ops: list[tuple[str, tuple, dict]] = []

    def _queue(self, name, args, kwargs):
        self.ops.append((name, args, kwargs))
        return self

    def rpush(self, *args, **kwargs):
        return self._queue("rpush", args, kwargs)

    def lrange(self, *args, **kwargs):
        return self._queue("lrange", args, kwargs)

    def delete(self, *args, **kwargs):
        return self._queue("delete", args, kwargs)

    def expire(self, *args, **kwargs):
        return self._queue("expire", args, kwargs)

    def set(self, *args, **kwargs):
        return self._queue("set", args, kwargs)

    def sadd(self, *args, **kwargs):
        return self._queue("sadd", args, kwargs)

    def zadd(self, *args, **kwargs):
        return self._queue("zadd", args, kwargs)

    def zremrangebyscore(self, *args, **kwargs):
        return self._queue("zremrangebyscore", args, kwargs)

    def execute(self):
        ops, self.ops = self.ops, []
        return self.client._atomic(
            "exec",
            lambda: [
                getattr(self.client, f"_{name}")(*args, **kwargs)
                for name, args, kwargs in ops
            ],
        )


class FakeRedis:
    """Minimal in-process Redis stand-in shared across "workers" in a test.

    所有状态命令都经过 `_atomic()`：单条命令 / EVAL / MULTI-EXEC 各自持锁执行，
    模拟真实 Redis 的单线程串行语义；锁释放后再触发 `on_atomic` 钩子，测试可以
    借此在「两次 round-trip 之间」精确插入另一个客户端的命令序列。
    """

    def __init__(self):
        self.store: dict = {}
        self._lock = threading.RLock()
        # 每个原子单元执行完毕（且已释放锁）后回调，参数为单元名。
        self.on_atomic: Callable[[str], None] | None = None
        self.atomic_units: list[str] = []

    def _atomic(self, name, fn):
        with self._lock:
            result = fn()
        self.atomic_units.append(name)
        hook = self.on_atomic
        if hook is not None:
            hook(name)
        return result

    def ping(self):
        # 健康探测不改状态，不计入原子单元。
        return True

    def set(self, key, value, ex=None, xx=False, nx=False):
        return self._atomic("set", lambda: self._set(key, value, ex=ex, xx=xx, nx=nx))

    def _set(self, key, value, ex=None, xx=False, nx=False):
        if xx and key not in self.store:
            return None
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self._atomic("get", lambda: self._get(key))

    def _get(self, key):
        v = self.store.get(key)
        return v if isinstance(v, str) else None

    def delete(self, *keys):
        return self._atomic("delete", lambda: self._delete(*keys))

    def _delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                n += 1
        return n

    def rpush(self, key, *vals):
        return self._atomic("rpush", lambda: self._rpush(key, *vals))

    def _rpush(self, key, *vals):
        lst = self.store.get(key)
        if not isinstance(lst, list):
            lst = []
            self.store[key] = lst
        lst.extend(vals)
        return len(lst)

    def lrange(self, key, start, end):
        return self._atomic("lrange", lambda: self._lrange(key, start, end))

    def _lrange(self, key, start, end):
        lst = self.store.get(key, [])
        if not isinstance(lst, list):
            return []
        return list(lst[start:]) if end == -1 else list(lst[start : end + 1])

    def expire(self, key, ttl):
        return self._atomic("expire", lambda: self._expire(key, ttl))

    def _expire(self, key, ttl):
        return key in self.store

    def sadd(self, key, *vals):
        return self._atomic("sadd", lambda: self._sadd(key, *vals))

    def _sadd(self, key, *vals):
        s = self.store.get(key)
        if not isinstance(s, set):
            s = set()
            self.store[key] = s
        before = len(s)
        s.update(vals)
        return len(s) - before

    def srem(self, key, *vals):
        return self._atomic("srem", lambda: self._srem(key, *vals))

    def _srem(self, key, *vals):
        s = self.store.get(key)
        if not isinstance(s, set):
            return 0
        removed = 0
        for v in vals:
            if v in s:
                s.remove(v)
                removed += 1
        return removed

    def scard(self, key):
        return self._atomic("scard", lambda: self._scard(key))

    def _scard(self, key):
        s = self.store.get(key)
        return len(s) if isinstance(s, set) else 0

    def zadd(self, key, mapping, xx=False):
        return self._atomic("zadd", lambda: self._zadd(key, mapping, xx=xx))

    def _zadd(self, key, mapping, xx=False):
        z = self.store.get(key)
        if not isinstance(z, dict):
            if xx:
                # XX：键不存在时什么也不做（不能复活已释放的持有者）。
                return 0
            z = {}
            self.store[key] = z
        added = 0
        for member, score in mapping.items():
            if xx and member not in z:
                continue
            if member not in z:
                added += 1
            z[member] = float(score)
        return added

    def _zrem(self, key, *members):
        z = self.store.get(key)
        if not isinstance(z, dict):
            return 0
        removed = 0
        for m in members:
            if m in z:
                del z[m]
                removed += 1
        return removed

    def _zcard(self, key):
        z = self.store.get(key)
        return len(z) if isinstance(z, dict) else 0

    def zremrangebyscore(self, key, min_score, max_score):
        return self._atomic(
            "zremrangebyscore", lambda: self._zremrangebyscore(key, min_score, max_score)
        )

    def _zremrangebyscore(self, key, min_score, max_score):
        z = self.store.get(key)
        if not isinstance(z, dict):
            return 0
        lo = float("-inf") if str(min_score) == "-inf" else float(min_score)
        hi = float("inf") if str(max_score) == "+inf" else float(max_score)
        # ZREMRANGEBYSCORE 的区间两端默认闭区间。
        doomed = [m for m, s in z.items() if lo <= s <= hi]
        for m in doomed:
            del z[m]
        return len(doomed)

    def eval(self, script, numkeys, *keys_and_args):
        return self._atomic("eval", lambda: self._eval(script, numkeys, *keys_and_args))

    def _eval(self, script, numkeys, *keys_and_args):
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]
        if "'ZADD'" in script:
            runs_key, owner_key = keys
            run_id, owner, cutoff, now, _ttl = args
            existing_owner = self._get(owner_key)
            if existing_owner and owner and existing_owner != owner:
                return -1
            self._zremrangebyscore(runs_key, "-inf", cutoff)
            if self._zcard(runs_key) and run_id not in self.store.get(runs_key, {}):
                return 0
            self._zadd(runs_key, {run_id: now})
            if owner:
                self._set(owner_key, owner)
            elif existing_owner is None:
                self._set(owner_key, "")
            return 1

        # 模拟 steering 的释放/回收 Lua，与真实 Redis 一样在单次调用内原子完成：
        #   释放脚本 _RELEASE_RUN_SCRIPT：ZREM 自己 -> 按 score 回收僵尸 ->
        #                                ZCARD==0 则 DEL 三键
        #   回收脚本 _REAP_STALE_RUNS_SCRIPT：曾有持有者且全部超时 -> DEL 三键
        assert "ZCARD" in script and "ZREMRANGEBYSCORE" in script and "DEL" in script
        runs_key = keys[0]
        is_release = "'ZREM'" in script
        if is_release:
            run_id, cutoff = args[0], args[1]
            self._zrem(runs_key, run_id)
        else:
            cutoff = args[0]
            if self._zcard(runs_key) == 0:
                return 0
        self._zremrangebyscore(runs_key, "-inf", cutoff)
        if self._zcard(runs_key) == 0:
            self._delete(*keys)
            return 1
        return 0

    def pipeline(self, transaction=True):
        return FakePipeline(self)


@pytest.fixture
def redis_steering(monkeypatch):
    """Route steering through a single shared FakeRedis and force the Redis path."""
    import agent.core.steering as st

    fake = FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    monkeypatch.setattr(
        "services.infra.redis_client.get_redis_client", lambda: fake, raising=True
    )
    # Force a fresh health check (and that it resolves to "healthy").
    monkeypatch.setattr(st, "_redis_health_checked_at", 0.0)
    monkeypatch.setattr(st, "_redis_is_healthy", False)
    return st, fake


@pytest.mark.unit
@pytest.mark.asyncio
async def test_steering_message_crosses_workers(redis_steering):
    st, _fake = redis_steering

    # Worker A (the SSE stream) creates the queue.
    queue_a = await st.create_steering_queue_async("sess-1", "user-1", run_id="run-a")
    assert isinstance(queue_a, st.RedisSteeringQueue)

    # Worker B (a DIFFERENT process serving POST /steer) finds the same session
    # and enqueues a steering message.
    queue_b = await st.get_steering_queue_for_user_async("sess-1", "user-1")
    assert isinstance(queue_b, st.RedisSteeringQueue)
    await queue_b.add("把主角改名为林川")

    # Worker A's running stream drains what worker B added — the whole point.
    pending = await queue_a.get_pending()
    assert [m.content for m in pending] == ["把主角改名为林川"]

    # Draining is destructive: a second poll is empty.
    assert await queue_a.get_pending() == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_session_raises_keyerror(redis_steering):
    st, _fake = redis_steering
    # No create_* call -> the /steer endpoint must get KeyError (-> 404), not crash.
    with pytest.raises(KeyError):
        await st.get_steering_queue_for_user_async("does-not-exist", "user-1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_owner_mismatch_raises_permissionerror(redis_steering):
    st, _fake = redis_steering
    await st.create_steering_queue_async("sess-2", "owner-user", run_id="run-a")
    with pytest.raises(PermissionError):
        await st.get_steering_queue_for_user_async("sess-2", "attacker-user")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_removes_session(redis_steering):
    st, fake = redis_steering
    await st.create_steering_queue_async("sess-3", "user-1", run_id="run-a")
    q = await st.get_steering_queue_for_user_async("sess-3", "user-1")
    await q.add("msg")
    await st.cleanup_steering_queue_async("sess-3")
    # After cleanup the session no longer exists.
    assert fake.store == {}
    with pytest.raises(KeyError):
        await st.get_steering_queue_for_user_async("sess-3", "user-1")


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_without_run_id_still_removes_all_keys(redis_steering):
    """不带 run_id 的 cleanup 保持旧语义：无条件删除全部键。"""
    st, fake = redis_steering

    await st.create_steering_queue_async("sess-legacy", "user-1", run_id="run-a")
    q = await st.get_steering_queue_for_user_async("sess-legacy", "user-1")
    await q.add("msg")

    await st.cleanup_steering_queue_async("sess-legacy")
    assert fake.store == {}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_registration_issues_single_atomic_unit(redis_steering):
    """结构性保证：注册只发出一个原子单元（MULTI/EXEC），因此释放脚本不可能
    观察到「owner 已写、runs 未写」的半注册状态。"""
    st, fake = redis_steering

    fake.atomic_units.clear()
    await st.create_steering_queue_async("sess-atomic", "user-1", run_id="run-a")
    assert fake.atomic_units == ["eval"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_after_full_release_rebuilds_consistent_state(redis_steering):
    """释放整段跑在注册之前时，注册要重建出一致状态（owner 与 runs 同时存在）。"""
    st, fake = redis_steering
    session_id = "sess-after-release"

    await st.create_steering_queue_async(session_id, "user-1", run_id="run-a")
    await st.cleanup_steering_queue_async(session_id, run_id="run-a")
    assert fake.store == {}

    await st.create_steering_queue_async(session_id, "user-1", run_id="run-b")
    assert fake.store.get(st._owner_key(session_id)) == "user-1"
    # runs 现在是 ZSET（member=run_id -> 心跳时间戳），只断言成员集合。
    assert set(fake.store.get(st._runs_key(session_id), {})) == {"run-b"}
    queue = await st.get_steering_queue_for_user_async(session_id, "user-1")
    assert isinstance(queue, st.RedisSteeringQueue)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.asyncio
async def test_falls_back_to_memory_without_redis_url(monkeypatch):
    """No REDIS_URL -> in-memory path (correct for a single-worker dev server)."""
    import agent.core.steering as st

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(st, "_redis_health_checked_at", 0.0)
    monkeypatch.setattr(st, "_redis_is_healthy", False)

    queue = await st.create_steering_queue_async("sess-mem", "user-1", run_id="run-a")
    assert isinstance(queue, st.SteeringQueue)  # in-memory, not RedisSteeringQueue
    await queue.add("hi")
    same = await st.get_steering_queue_for_user_async("sess-mem", "user-1")
    pending = await same.get_pending()
    assert [m.content for m in pending] == ["hi"]
    await st.cleanup_steering_queue_async("sess-mem")
