"""steering 的 Redis 健康缓存不得跨 REDIS_URL、跨用例泄漏。

背景：`_redis_available_sync` 把「Redis 健康」缓存 30 秒。fake Redis 夹具把缓存
标成健康后，同一 xdist worker 上的下一个用例换回开发者 `.env` 里的真实
REDIS_URL（指向没在跑的 6380），却仍读到「健康」，于是直连真实 Redis：
要么 ConnectionRefused 失败，要么卡住整个 worker。
"""

import time

import pytest

LEAK_URL = "redis://leak-check:6379/0"


class _HealthyRedis:
    def ping(self):
        return True


def _unreachable_redis():
    raise ConnectionRefusedError("nothing listens here")


@pytest.mark.unit
def test_switching_redis_url_rechecks_health(monkeypatch):
    import agent.core.steering as st

    monkeypatch.setenv("REDIS_URL", "redis://fake-a:6379/0")
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: _HealthyRedis())
    assert st._redis_available_sync() is True

    # 换成另一个地址，且这个地址连不上：必须重新 ping，而不是沿用 30 秒内的「健康」。
    monkeypatch.setenv("REDIS_URL", "redis://fake-b:6379/0")
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", _unreachable_redis)
    assert st._redis_available_sync() is False


@pytest.mark.unit
def test_same_redis_url_within_ttl_uses_cached_result(monkeypatch):
    """生产里 REDIS_URL 不变：TTL 内不重复 ping，行为与加固前一致。"""
    import agent.core.steering as st

    pings: list[int] = []

    class _CountingRedis:
        def ping(self):
            pings.append(1)
            return True

    monkeypatch.setenv("REDIS_URL", "redis://fake-same:6379/0")
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: _CountingRedis())
    assert st._redis_available_sync() is True
    assert st._redis_available_sync() is True
    assert len(pings) == 1


@pytest.mark.unit
class TestHealthStateDoesNotLeakAcrossTests:
    """两个用例同类同模块，--dist=loadscope 下落在同一 worker，按文件顺序执行。"""

    def test_a_leaves_healthy_state_behind(self, monkeypatch):
        import agent.core.steering as st

        monkeypatch.setenv("REDIS_URL", LEAK_URL)
        monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: _HealthyRedis())
        assert st._redis_available_sync() is True
        # 模拟旧夹具的写法：直接给模块全局赋值，用例结束后不还原。
        st._redis_is_healthy = True
        st._redis_health_checked_at = time.monotonic()
        st._redis_health_url = LEAK_URL

    def test_b_does_not_inherit_healthy_state(self, monkeypatch):
        import agent.core.steering as st

        # 同一个 REDIS_URL，但这次 Redis 连不上：缓存若残留就会误判为健康。
        monkeypatch.setenv("REDIS_URL", LEAK_URL)
        monkeypatch.setattr("services.infra.redis_client.get_redis_client", _unreachable_redis)
        assert st._redis_available_sync() is False
