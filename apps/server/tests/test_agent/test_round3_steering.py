"""Round-3 回归：steering 队列的僵尸 run 回收（缺陷 #23）。

背景：`run_id` 是 process_stream 生成的进程内 UUID，唯一的释放路径是它的
finally。worker 被 SIGKILL / 部署重启 / OOM 杀掉时那段 finally 永远不会执行，
持有记录就永久留在 runs 集合里；此后该 session 的每次释放都因「还有其它持有
者」而跳过删除 —— 队列永不回收，POST /agent/steer 在没有任何消费者时仍然
200 queued，这条陈旧引导会在下一次完全无关的 run 开头被当作用户消息注入模型
并写进聊天历史。

本文件里的 fake Redis 同时实现 SET（修复前的 runs 表示）与 ZSET（修复后的
表示）两套命令，因此同一批用例在 `git stash` 掉修复后仍然可以运行 —— 红/绿
对比测的是行为，不是实现细节。
"""

import threading

import pytest

# ---------------------------------------------------------------------------
# 一个同时支持 SET / ZSET 的最小 Redis 替身（单线程串行语义）
# ---------------------------------------------------------------------------


class _FakePipeline:
    """MULTI/EXEC：排队的命令在 execute() 内作为一个原子单元执行。"""

    def __init__(self, client: "_FakeRedis"):
        self._client = client
        self._ops: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def queue(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self

        return queue

    def execute(self):
        ops, self._ops = self._ops, []
        with self._client.lock:
            return [self._client.call(name, *a, **kw) for name, a, kw in ops]


class _FakeRedis:
    """够用的 Redis 替身：字符串 / 列表 / 集合 / 有序集合 + EVAL。"""

    def __init__(self):
        self.store: dict = {}
        self.lock = threading.RLock()

    # --- 调度 ---------------------------------------------------------
    def call(self, name: str, *args, **kwargs):
        impl = getattr(self, f"_impl_{name}", None)
        if impl is None:  # pragma: no cover - 出现即为测试替身缺命令
            raise AssertionError(f"FakeRedis 未实现命令: {name}")
        return impl(*args, **kwargs)

    def __getattr__(self, name: str):
        if name.startswith("_") or not hasattr(type(self), f"_impl_{name}"):
            raise AttributeError(name)

        def run(*args, **kwargs):
            with self.lock:
                return self.call(name, *args, **kwargs)

        return run

    def pipeline(self, transaction: bool = True):
        return _FakePipeline(self)

    def ping(self):
        return True

    # --- 测试辅助 ------------------------------------------------------
    def age_runs(self, key: str, seconds: float, member: str | None = None) -> None:
        """把持有者的心跳时间往前拨（模拟 worker 早已死亡）。

        SET 表示（修复前）没有任何时间信息可拨 —— 这正是缺陷本身，此时为 no-op。
        """
        runs = self.store.get(key)
        if not isinstance(runs, dict):
            return
        for m in list(runs):
            if member is None or m == member:
                runs[m] -= seconds

    # --- 命令实现 ------------------------------------------------------
    def _impl_set(self, key, value, ex=None, xx=False, nx=False):
        if xx and key not in self.store:
            return None
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def _impl_get(self, key):
        v = self.store.get(key)
        return v if isinstance(v, str) else None

    def _impl_delete(self, *keys):
        removed = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                removed += 1
        return removed

    def _impl_expire(self, key, ttl):
        return key in self.store

    def _impl_rpush(self, key, *vals):
        lst = self.store.get(key)
        if not isinstance(lst, list):
            lst = []
            self.store[key] = lst
        lst.extend(vals)
        return len(lst)

    def _impl_lrange(self, key, start, end):
        lst = self.store.get(key, [])
        if not isinstance(lst, list):
            return []
        return list(lst[start:]) if end == -1 else list(lst[start : end + 1])

    def _impl_sadd(self, key, *vals):
        s = self.store.get(key)
        if not isinstance(s, set):
            s = set()
            self.store[key] = s
        before = len(s)
        s.update(vals)
        return len(s) - before

    def _impl_srem(self, key, *vals):
        s = self.store.get(key)
        if not isinstance(s, set):
            return 0
        removed = 0
        for v in vals:
            if v in s:
                s.discard(v)
                removed += 1
        return removed

    def _impl_scard(self, key):
        s = self.store.get(key)
        return len(s) if isinstance(s, set) else 0

    def _impl_zadd(self, key, mapping, xx=False):
        z = self.store.get(key)
        if not isinstance(z, dict):
            if xx:
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

    def _impl_zrem(self, key, *members):
        z = self.store.get(key)
        if not isinstance(z, dict):
            return 0
        removed = 0
        for m in members:
            if m in z:
                del z[m]
                removed += 1
        return removed

    def _impl_zcard(self, key):
        z = self.store.get(key)
        return len(z) if isinstance(z, dict) else 0

    def _impl_zscore(self, key, member):
        z = self.store.get(key)
        if not isinstance(z, dict):
            return None
        return z.get(member)

    def _impl_zremrangebyscore(self, key, min_score, max_score):
        z = self.store.get(key)
        if not isinstance(z, dict):
            return 0
        lo = float("-inf") if str(min_score) == "-inf" else float(min_score)
        hi = float("inf") if str(max_score) == "+inf" else float(max_score)
        doomed = [m for m, s in z.items() if lo <= s <= hi]
        for m in doomed:
            del z[m]
        return len(doomed)

    def _impl_eval(self, script, numkeys, *keys_and_args):
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]
        runs_key = keys[0]
        if "ZADD" in script and "local owner" in script:
            owner = self._impl_get(keys[1])
            requested_owner = args[1]
            if owner and owner != requested_owner:
                return -1
            self._impl_zremrangebyscore(runs_key, "-inf", args[2])
            if (
                self._impl_zcard(runs_key) > 0
                and self._impl_zscore(runs_key, args[0]) is None
            ):
                return 0
            self._impl_zadd(runs_key, {args[0]: args[3]})
            self._impl_expire(runs_key, args[4])
            if requested_owner:
                self._impl_set(keys[1], requested_owner, ex=args[4])
            else:
                self._impl_set(keys[1], "", ex=args[4], nx=True)
                self._impl_expire(keys[1], args[4])
            return 1
        if "RPUSH" in script:
            # 原子 hand-back：回收僵尸 -> 确认还有其它 run + owner -> 批量入队。
            self._impl_zremrangebyscore(runs_key, "-inf", args[1])
            n = self._impl_zcard(runs_key)
            if self._impl_zscore(runs_key, args[0]) is not None:
                n -= 1
            if n <= 0 or self._impl_get(keys[1]) != args[2]:
                return 0
            self._impl_rpush(keys[2], *args[4:])
            self._impl_expire(keys[2], args[3])
            self._impl_expire(keys[1], args[3])
            return 1
        if "ZSCORE" in script:
            # 「除自己以外还有几个活跃持有者」：回收僵尸 -> ZCARD -> 减掉自己。
            # 只读判定，不删除任何键（本 run 通常仍持有队列）。
            self._impl_zremrangebyscore(runs_key, "-inf", args[1])
            n = self._impl_zcard(runs_key)
            if self._impl_zscore(runs_key, args[0]) is not None:
                n -= 1
            return n
        if "SREM" in script:
            # 修复前的释放脚本：SREM -> SCARD==0 -> DEL（无僵尸回收）。
            self._impl_srem(runs_key, args[0])
            if self._impl_scard(runs_key) == 0:
                self._impl_delete(*keys)
                return 1
            return 0
        if "'ZREM'" in script:
            # 修复后的释放脚本：ZREM 自己 -> 按 score 回收僵尸 -> ZCARD==0 -> DEL。
            self._impl_zrem(runs_key, args[0])
            self._impl_zremrangebyscore(runs_key, "-inf", args[1])
            if self._impl_zcard(runs_key) == 0:
                self._impl_delete(*keys)
                return 1
            return 0
        # 修复后的查询期回收脚本。
        if self._impl_zcard(runs_key) == 0:
            return 0
        self._impl_zremrangebyscore(runs_key, "-inf", args[0])
        if self._impl_zcard(runs_key) == 0:
            self._impl_delete(*keys)
            return 1
        return 0


@pytest.fixture
def redis_steering(monkeypatch):
    """把 steering 强制走 Redis 路径，并接到同一个 fake 上。"""
    import agent.core.steering as st

    fake = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    monkeypatch.setattr(
        "services.infra.redis_client.get_redis_client", lambda: fake, raising=True
    )
    monkeypatch.setattr(st, "_redis_health_checked_at", 0.0)
    monkeypatch.setattr(st, "_redis_is_healthy", False)
    return st, fake


@pytest.fixture
def memory_steering(monkeypatch):
    """把 steering 强制走内存回退路径。"""
    import agent.core.steering as st

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(st, "_redis_health_checked_at", 0.0)
    monkeypatch.setattr(st, "_redis_is_healthy", False)
    return st


def _age_memory_runs(entry, seconds: float, run_id: str | None = None) -> None:
    """把内存后端持有者的心跳往前拨；修复前的 set 表示没有时间戳可拨。"""
    runs = getattr(entry, "active_runs", None)
    if not isinstance(runs, dict):
        return
    for rid in list(runs):
        if run_id is None or rid == run_id:
            runs[rid] -= seconds


_ONE_DAY = 24 * 3600


# ---------------------------------------------------------------------------
# Redis 后端
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redis_exclusive_generation_claim_rejects_second_run(redis_steering):
    st, _fake = redis_steering
    session_id = "redis-exclusive-run"

    await st.create_steering_queue_async(
        session_id,
        "user-1",
        run_id="run-a",
        exclusive_run=True,
    )
    with pytest.raises(st.SteeringSessionBusyError):
        await st.create_steering_queue_async(
            session_id,
            "user-1",
            run_id="run-b",
            exclusive_run=True,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_run_registered_before_does_not_block_release(redis_steering):
    """崩溃残留的持有者不得让后来的 run 结束时跳过删除。

    修复前：runs 是 SET，僵尸成员没有时间信息也无法回收，run-a 释放时
    SCARD==1 -> 不 DEL，owner/msgs 永久保留。
    """
    st, fake = redis_steering
    session_id = "sess-zombie-before"

    # 一个被 SIGKILL 掉的 worker 留下的持有记录（注册后再把心跳拨回一天前）。
    await st.create_steering_queue_async(session_id, "user-1", run_id="run-killed")
    fake.age_runs(st._runs_key(session_id), _ONE_DAY)

    # 之后一次正常的 run 完整跑完。
    await st.create_steering_queue_async(session_id, "user-1", run_id="run-a")
    await st.cleanup_steering_queue_async(session_id, run_id="run-a")

    # 没有任何 run 在跑：三个键必须全部回收，POST /agent/steer 必须 404。
    assert fake.store == {}
    with pytest.raises(KeyError):
        await st.get_steering_queue_for_user_async(session_id, "user-1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_run_registered_during_is_reaped_by_release(redis_steering):
    """并发 run 中途崩溃时，最后一个活着的 run 释放要顺带回收它。"""
    st, fake = redis_steering
    session_id = "sess-zombie-during"

    await st.create_steering_queue_async(session_id, "user-1", run_id="run-a")
    await st.create_steering_queue_async(session_id, "user-1", run_id="run-killed")
    # run-killed 所在的 worker 被杀掉：它的心跳从此停在一天前。
    fake.age_runs(st._runs_key(session_id), _ONE_DAY, member="run-killed")

    await st.cleanup_steering_queue_async(session_id, run_id="run-a")

    assert fake.store == {}
    with pytest.raises(KeyError):
        await st.get_steering_queue_for_user_async(session_id, "user-1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_steer_on_session_held_only_by_stale_run_is_404(redis_steering):
    """只剩僵尸持有者的 session 必须按「不存在」处理。

    修复前 owner 键还在，POST /agent/steer 返回 200 queued，但没有任何消费者：
    这条引导会滞留到下一次无关的 run 被当作用户消息注入模型。
    """
    st, fake = redis_steering
    session_id = "sess-zombie-only"

    await st.create_steering_queue_async(session_id, "user-1", run_id="run-killed")
    fake.age_runs(st._runs_key(session_id), _ONE_DAY)

    with pytest.raises(KeyError):
        await st.get_steering_queue_for_user_async(session_id, "user-1")
    # 陈旧的 owner/msgs 键也要一并清掉，不能留给下一轮。
    assert fake.store == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_hold_does_not_swallow_next_runs_steering(redis_steering):
    """端到端：僵尸持有者存在时，新 run 的引导仍然只属于新 run。"""
    st, fake = redis_steering
    session_id = "sess-zombie-e2e"

    await st.create_steering_queue_async(session_id, "user-1", run_id="run-killed")
    fake.age_runs(st._runs_key(session_id), _ONE_DAY)
    # 用户在没有活跃 run 时发了一条引导：必须被拒（404），不能排队。
    with pytest.raises(KeyError):
        await st.get_steering_queue_for_user_async(session_id, "user-1")

    # 下一次全新的 run 启动时，队列里不能有上一轮遗留的消息。
    run_queue = await st.create_steering_queue_async(session_id, "user-1", run_id="run-b")
    assert await run_queue.get_pending() == []
    await st.cleanup_steering_queue_async(session_id, run_id="run-b")
    assert fake.store == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_polling_run_heartbeat_keeps_its_own_hold_alive(redis_steering):
    """轮询即心跳：仍在生成的长 run 不会被误当成僵尸回收。"""
    st, fake = redis_steering
    session_id = "sess-heartbeat"

    queue = await st.create_steering_queue_async(session_id, "user-1", run_id="run-a")
    # 注册时间已经很老，但这个 run 还活着并持续轮询 steering。
    fake.age_runs(st._runs_key(session_id), _ONE_DAY)
    await queue.get_pending()

    # 并发 run 结束触发回收：run-a 的心跳是新的，必须存活。
    await st.create_steering_queue_async(session_id, "user-1", run_id="run-b")
    await st.cleanup_steering_queue_async(session_id, run_id="run-b")

    surviving = await st.get_steering_queue_for_user_async(session_id, "user-1")
    await surviving.add("run-a 还在跑")
    assert [m.content for m in await queue.get_pending()] == ["run-a 还在跑"]

    await st.cleanup_steering_queue_async(session_id, run_id="run-a")
    assert fake.store == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat_is_per_member_not_per_key(redis_steering):
    """心跳必须是成员级的：活着的 run 轮询不能替别的僵尸续命。"""
    st, fake = redis_steering
    session_id = "sess-heartbeat-scope"

    queue_a = await st.create_steering_queue_async(session_id, "user-1", run_id="run-a")
    await st.create_steering_queue_async(session_id, "user-1", run_id="run-killed")
    fake.age_runs(st._runs_key(session_id), _ONE_DAY, member="run-killed")

    # run-a 反复轮询（旧实现在这里无条件 EXPIRE 整个 runs 键，僵尸永不消失）。
    for _ in range(3):
        await queue_a.get_pending()

    await st.cleanup_steering_queue_async(session_id, run_id="run-a")
    assert fake.store == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat_does_not_resurrect_released_run(redis_steering):
    """已释放的 run 迟到的轮询不能把自己重新加回持有集合。"""
    st, fake = redis_steering
    session_id = "sess-late-poll"

    queue_a = await st.create_steering_queue_async(session_id, "user-1", run_id="run-a")
    await st.cleanup_steering_queue_async(session_id, run_id="run-a")
    assert fake.store == {}

    # 释放之后的兜底 drain（service.py 的 finally 里确实会发生）。
    assert await queue_a.get_pending() == []
    assert fake.store == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_placeholder_session_without_runs_keeps_legacy_semantics(redis_steering):
    """从未登记过持有者的占位 session 不受回收影响（旧语义保持不变）。"""
    st, fake = redis_steering

    await st.get_steering_queue_async("sess-placeholder")
    queue = await st.get_steering_queue_for_user_async("sess-placeholder", "user-1")
    await queue.add("占位队列照常可用")
    assert [m.content for m in await queue.peek()] == ["占位队列照常可用"]
    assert fake.store.get(st._owner_key("sess-placeholder")) == "user-1"


# ---------------------------------------------------------------------------
# 内存后端（语义必须与 Redis 一致，历史上两者漂移过）
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_stale_run_does_not_block_cleanup(memory_steering):
    """内存后端同样要回收僵尸持有者，不能与 Redis 后端语义漂移。"""
    st = memory_steering
    session_id = "sess-mem-zombie"
    await st.cleanup_steering_queue_async(session_id)

    await st.create_steering_queue_async(session_id, "user-1", run_id="run-killed")
    entry = st._queue_manager._queues[session_id]
    _age_memory_runs(entry, _ONE_DAY)

    await st.create_steering_queue_async(session_id, "user-1", run_id="run-a")
    await st.cleanup_steering_queue_async(session_id, run_id="run-a")

    assert session_id not in st._queue_manager._queues
    with pytest.raises(KeyError):
        await st.get_steering_queue_for_user_async(session_id, "user-1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_session_held_only_by_stale_run_is_404(memory_steering):
    """内存后端：只剩僵尸持有者的 session 也必须按不存在处理。"""
    st = memory_steering
    session_id = "sess-mem-zombie-only"
    await st.cleanup_steering_queue_async(session_id)

    await st.create_steering_queue_async(session_id, "user-1", run_id="run-killed")
    _age_memory_runs(st._queue_manager._queues[session_id], _ONE_DAY)

    with pytest.raises(KeyError):
        await st.get_steering_queue_for_user_async(session_id, "user-1")
    assert session_id not in st._queue_manager._queues


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_polling_keeps_live_run_alive(memory_steering):
    """内存后端的轮询同样是心跳：仍在生成的 run 不会被回收。"""
    st = memory_steering
    session_id = "sess-mem-heartbeat"
    await st.cleanup_steering_queue_async(session_id)

    queue = await st.create_steering_queue_async(session_id, "user-1", run_id="run-a")
    _age_memory_runs(st._queue_manager._queues[session_id], _ONE_DAY)
    await queue.get_pending()

    await st.create_steering_queue_async(session_id, "user-1", run_id="run-b")
    await st.cleanup_steering_queue_async(session_id, run_id="run-b")

    surviving = await st.get_steering_queue_for_user_async(session_id, "user-1")
    await surviving.add("run-a 还在跑")
    assert [m.content for m in await queue.get_pending()] == ["run-a 还在跑"]

    await st.cleanup_steering_queue_async(session_id, run_id="run-a")
    assert session_id not in st._queue_manager._queues


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_placeholder_session_without_runs_survives(memory_steering):
    """内存后端：没有登记过持有者的占位队列不被查询路径删除。"""
    st = memory_steering
    session_id = "sess-mem-placeholder"
    await st.cleanup_steering_queue_async(session_id)

    await st.get_steering_queue_async(session_id)
    queue = await st.get_steering_queue_for_user_async(session_id, "user-1")
    await queue.add("占位队列照常可用")
    assert [m.content for m in await queue.peek()] == ["占位队列照常可用"]

    await st.cleanup_steering_queue_async(session_id)
