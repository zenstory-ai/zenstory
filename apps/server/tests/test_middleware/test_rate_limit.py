"""Tests for rate limit helper IP extraction and window behavior."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, BrokenBarrierError, Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import middleware.rate_limit as rate_limit_module
from middleware.rate_limit import (
    _rate_limit_store,
    check_rate_limit,
    get_client_ip,
    require_agent_rate_limit,
    require_user_beijing_daily_rate_limit,
    require_user_rate_limit,
)


def _build_request(
    *,
    headers: dict[str, str] | None = None,
    client_host: str = "127.0.0.1",
) -> Request:
    encoded_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": encoded_headers,
        "client": (client_host, 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _clear_rate_limit_store(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("RATE_LIMIT_REDIS_PREFIX", raising=False)
    monkeypatch.setattr(rate_limit_module, "_redis_retry_after_monotonic", 0.0)
    _rate_limit_store.clear()
    yield
    _rate_limit_store.clear()


@pytest.fixture
def _client_ip_env(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    monkeypatch.delenv("CLIENT_IP_CLOUDFLARE", raising=False)
    monkeypatch.delenv("CLOUDFLARE_IP_RANGES", raising=False)
    return monkeypatch


@pytest.mark.unit
def test_get_client_ip_ignores_client_supplied_x_real_ip(_client_ip_env):
    request = _build_request(
        headers={
            "X-Real-IP": "198.51.100.8",
            "X-Forwarded-For": "203.0.113.1, 70.41.3.18",
        },
        client_host="10.0.0.9",
    )
    # Default: one trusted proxy (Railway edge) appended the rightmost entry.
    assert get_client_ip(request) == "70.41.3.18"


@pytest.mark.unit
def test_get_client_ip_rejects_spoofed_leftmost_forwarded_entry(_client_ip_env):
    # A client sending its own X-Forwarded-For cannot pick its rate-limit key:
    # the edge appends the real peer on the right.
    request = _build_request(
        headers={"X-Forwarded-For": "1.2.3.4, 198.51.100.9"},
        client_host="10.0.0.9",
    )
    assert get_client_ip(request) == "198.51.100.9"


@pytest.mark.unit
def test_get_client_ip_honours_trusted_proxy_hops(_client_ip_env):
    _client_ip_env.setenv("TRUSTED_PROXY_HOPS", "2")
    request = _build_request(
        headers={"X-Forwarded-For": "1.2.3.4, 198.51.100.9, 10.1.1.1"},
        client_host="10.0.0.9",
    )
    assert get_client_ip(request) == "198.51.100.9"


@pytest.mark.unit
def test_get_client_ip_with_fewer_entries_than_hops_uses_leftmost(_client_ip_env):
    _client_ip_env.setenv("TRUSTED_PROXY_HOPS", "3")
    request = _build_request(
        headers={"X-Forwarded-For": "198.51.100.9, 10.1.1.1"},
        client_host="10.0.0.9",
    )
    assert get_client_ip(request) == "198.51.100.9"


@pytest.mark.unit
def test_get_client_ip_zero_hops_uses_socket_peer(_client_ip_env):
    _client_ip_env.setenv("TRUSTED_PROXY_HOPS", "0")
    request = _build_request(
        headers={"X-Forwarded-For": "198.51.100.9"},
        client_host="10.0.0.9",
    )
    assert get_client_ip(request) == "10.0.0.9"


@pytest.mark.unit
def test_get_client_ip_falls_back_to_socket_peer_without_forwarded(_client_ip_env):
    request = _build_request(client_host="10.0.0.9")
    assert get_client_ip(request) == "10.0.0.9"


@pytest.mark.unit
def test_get_client_ip_uses_cf_connecting_ip_when_peer_is_cloudflare(_client_ip_env):
    request = _build_request(
        headers={
            "CF-Connecting-IP": "198.51.100.77",
            # Cloudflare edge address appended by the Railway edge.
            "X-Forwarded-For": "198.51.100.77, 172.70.1.2",
        },
        client_host="10.0.0.9",
    )
    assert get_client_ip(request) == "198.51.100.77"


@pytest.mark.unit
def test_get_client_ip_ignores_forged_cf_connecting_ip(_client_ip_env):
    # Direct hit on the origin: the peer is not a Cloudflare address.
    request = _build_request(
        headers={
            "CF-Connecting-IP": "1.2.3.4",
            "X-Forwarded-For": "198.51.100.9",
        },
        client_host="10.0.0.9",
    )
    assert get_client_ip(request) == "198.51.100.9"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "expected"),
    [("always", "1.2.3.4"), ("off", "172.70.1.2")],
)
def test_get_client_ip_cloudflare_mode_switch(_client_ip_env, mode, expected):
    _client_ip_env.setenv("CLIENT_IP_CLOUDFLARE", mode)
    peer = "172.70.1.2" if mode == "off" else "198.51.100.9"
    request = _build_request(
        headers={"CF-Connecting-IP": "1.2.3.4", "X-Forwarded-For": peer},
        client_host="10.0.0.9",
    )
    assert get_client_ip(request) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"X-Forwarded-For": "[2001:db8::1]"}, "2001:db8::1"),
        ({"X-Forwarded-For": "198.51.100.12:443"}, "198.51.100.12"),
        ({"X-Forwarded-For": "not-an-ip"}, "10.0.0.9"),
    ],
)
def test_get_client_ip_normalizes_ipv6_and_ipv4_port_formats(_client_ip_env, headers, expected):
    request = _build_request(headers=headers, client_host="10.0.0.9")
    assert get_client_ip(request) == expected


@pytest.mark.unit
def test_get_client_ip_falls_back_to_request_client_host():
    request = _build_request(client_host="192.0.2.77")
    assert get_client_ip(request) == "192.0.2.77"


@pytest.mark.unit
def test_check_rate_limit_uses_resolved_client_ip_key():
    request = _build_request(headers={"X-Forwarded-For": "198.51.100.10"})
    allowed1, remaining1 = check_rate_limit(request, "auth_login_ip", 1, 60)
    allowed2, remaining2 = check_rate_limit(request, "auth_login_ip", 1, 60)

    assert allowed1 is True
    assert remaining1 == 0
    assert allowed2 is False
    assert remaining2 == 0


@pytest.mark.unit
def test_check_rate_limit_can_scope_without_client_ip():
    request_from_ip1 = _build_request(headers={"X-Forwarded-For": "198.51.100.11"})
    request_from_ip2 = _build_request(headers={"X-Forwarded-For": "198.51.100.12"})

    allowed1, remaining1 = check_rate_limit(
        request_from_ip1,
        "auth_login_identifier:test@example.com",
        1,
        60,
        include_client_ip=False,
    )
    allowed2, remaining2 = check_rate_limit(
        request_from_ip2,
        "auth_login_identifier:test@example.com",
        1,
        60,
        include_client_ip=False,
    )

    assert allowed1 is True
    assert remaining1 == 0
    assert allowed2 is False
    assert remaining2 == 0


@pytest.mark.unit
def test_user_rate_limit_ignores_untrusted_agent_api_key_header():
    """Bearer users cannot rotate an unauthenticated header to escape one bucket."""
    enforce = require_user_rate_limit("agent_stream", 1, 60)
    user = SimpleNamespace(id="same-user")

    remaining = enforce(
        _build_request(headers={"X-Agent-API-Key": "aaaaaaaa-first"}),
        current_user=user,
    )

    assert remaining == 0
    with pytest.raises(HTTPException) as exc_info:
        enforce(
            _build_request(headers={"X-Agent-API-Key": "bbbbbbbb-second"}),
            current_user=user,
        )
    assert exc_info.value.status_code == 429


@pytest.mark.unit
def test_user_beijing_daily_limit_resets_at_beijing_midnight(monkeypatch):
    """A full old-day bucket becomes available exactly at 00:00 Asia/Shanghai."""
    enforce = require_user_beijing_daily_rate_limit("agent_suggest_daily", 1)
    user = SimpleNamespace(id="same-user")
    request = _build_request()

    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 5, 15, 59, 59, tzinfo=UTC),
    )
    assert enforce(request, current_user=user) == 0
    with pytest.raises(HTTPException):
        enforce(request, current_user=user)

    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 5, 16, 0, 0, tzinfo=UTC),
    )
    assert enforce(request, current_user=user) == 0

    assert set(_rate_limit_store) == {
        "agent_suggest_daily:user_same-user:beijing_day:2026-10-06",
    }


@pytest.mark.unit
def test_user_beijing_daily_limit_does_not_reset_at_utc_midnight(monkeypatch):
    """UTC midnight is 08:00 Beijing and must remain in the same daily bucket."""
    enforce = require_user_beijing_daily_rate_limit("agent_suggest_daily", 1)
    user = SimpleNamespace(id="same-user")
    request = _build_request()

    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 5, 16, 0, 0, tzinfo=UTC),
    )
    assert enforce(request, current_user=user) == 0

    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 6, 0, 0, 0, tzinfo=UTC),
    )
    with pytest.raises(HTTPException):
        enforce(request, current_user=user)


@pytest.mark.unit
def test_user_beijing_daily_limit_skips_idle_days_without_shifting_boundary(monkeypatch):
    enforce = require_user_beijing_daily_rate_limit("agent_suggest_daily", 1)
    user = SimpleNamespace(id="same-user")
    request = _build_request()

    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 1, 20, 0, 0, tzinfo=UTC),
    )
    assert enforce(request, current_user=user) == 0

    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 5, 10, 0, 0, tzinfo=UTC),
    )
    assert enforce(request, current_user=user) == 0
    with pytest.raises(HTTPException) as exc_info:
        enforce(request, current_user=user)

    assert exc_info.value.headers == {"Retry-After": "21600"}


@pytest.mark.unit
def test_user_beijing_daily_memory_limit_is_atomic_under_concurrency(monkeypatch):
    """Concurrent sync dependencies must not both pass a one-request limit."""
    enforce = require_user_beijing_daily_rate_limit("agent_suggest_daily", 1)
    user = SimpleNamespace(id="same-user")
    request = _build_request()
    rate_key = "agent_suggest_daily:user_same-user:beijing_day:2026-10-06"
    interleave = Barrier(2)

    class _InterleavingBucket(list):
        def __len__(self):
            size = super().__len__()
            if size == 0:
                try:
                    interleave.wait(timeout=0.1)
                except BrokenBarrierError:
                    pass
            return size

    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 5, 16, 0, 0, tzinfo=UTC),
    )
    _rate_limit_store[rate_key] = _InterleavingBucket()

    def invoke() -> tuple[str, int]:
        try:
            return "allowed", enforce(request, current_user=user)
        except HTTPException:
            return "blocked", 0

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: invoke(), range(2)))

    assert [status for status, _remaining in results].count("allowed") == 1
    assert [status for status, _remaining in results].count("blocked") == 1
    assert all(remaining >= 0 for _status, remaining in results)
    assert len(_rate_limit_store[rate_key]) == 1


@pytest.mark.unit
def test_user_beijing_daily_midnight_cleanup_is_safe_under_concurrency(monkeypatch):
    """Two first requests after midnight cannot race deleting yesterday's bucket."""
    stale_key = "agent_suggest_daily:user_same-user:beijing_day:2026-10-05"
    current_key = "agent_suggest_daily:user_same-user:beijing_day:2026-10-06"
    interleave = Barrier(2)

    class _InterleavingCleanupStore(defaultdict):
        def __iter__(self):
            keys = list(super().keys())
            if stale_key in keys:
                try:
                    interleave.wait(timeout=0.1)
                except BrokenBarrierError:
                    pass
            return iter(keys)

    store = _InterleavingCleanupStore(list)
    store[stale_key] = [1.0]
    monkeypatch.setattr(rate_limit_module, "_rate_limit_store", store)
    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 5, 16, 0, 0, tzinfo=UTC),
    )
    enforce = require_user_beijing_daily_rate_limit("agent_suggest_daily", 2)
    user = SimpleNamespace(id="same-user")
    request = _build_request()

    def invoke() -> int:
        return enforce(request, current_user=user)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: invoke(), range(2)))

    assert sorted(results) == [0, 1]
    assert stale_key not in store
    assert len(store[current_key]) == 2


@pytest.mark.unit
def test_daily_cleanup_and_generic_memory_limit_share_one_lock(monkeypatch):
    """A generic new bucket cannot mutate the store during daily cleanup."""
    stale_key = "agent_suggest_daily:user_same-user:beijing_day:2026-10-05"
    current_key = "agent_suggest_daily:user_same-user:beijing_day:2026-10-06"
    generic_key = "generic_concurrent"
    scan_started = Event()
    generic_inserted = Event()

    class _MutationDetectingStore(defaultdict):
        def __missing__(self, key):
            value = super().__missing__(key)
            if key == generic_key:
                generic_inserted.set()
            return value

        def __iter__(self):
            keys = list(super().keys())
            scan_started.set()
            generic_inserted.wait(timeout=0.1)
            if list(super().keys()) != keys:
                raise RuntimeError("dictionary changed size during iteration")
            return iter(keys)

    store = _MutationDetectingStore(list)
    store[stale_key] = [1.0]
    monkeypatch.setattr(rate_limit_module, "_rate_limit_store", store)
    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 5, 16, 0, 0, tzinfo=UTC),
    )
    daily = require_user_beijing_daily_rate_limit("agent_suggest_daily", 2)
    user = SimpleNamespace(id="same-user")
    request = _build_request()

    with ThreadPoolExecutor(max_workers=2) as executor:
        daily_result = executor.submit(daily, request, current_user=user)
        assert scan_started.wait(timeout=1)
        generic_result = executor.submit(
            check_rate_limit,
            request,
            generic_key,
            2,
            60,
            include_client_ip=False,
        )
        assert daily_result.result(timeout=1) == 1
        assert generic_result.result(timeout=1) == (True, 1)

    assert stale_key not in store
    assert len(store[current_key]) == 1
    assert len(store[generic_key]) == 1


@pytest.mark.unit
def test_queued_old_day_request_does_not_delete_new_day_bucket(monkeypatch):
    """An old request acquiring the lock late must preserve newer-day usage."""
    old_key = "agent_suggest_daily:user_same-user:beijing_day:2026-10-05"
    new_key = "agent_suggest_daily:user_same-user:beijing_day:2026-10-06"
    _rate_limit_store[new_key] = [datetime(2026, 10, 5, 16, 0, tzinfo=UTC).timestamp()]
    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 5, 15, 59, 59, tzinfo=UTC),
    )
    daily = require_user_beijing_daily_rate_limit("agent_suggest_daily", 2)

    assert daily(_build_request(), current_user=SimpleNamespace(id="same-user")) == 1
    assert len(_rate_limit_store[old_key]) == 1
    assert len(_rate_limit_store[new_key]) == 1


@pytest.mark.unit
def test_agent_rate_limit_uses_validated_key_identity_not_raw_header():
    """Rotating raw header text cannot change a validated Agent key's bucket."""
    enforce = require_agent_rate_limit("agent_read", 1, 60)
    context = (None, "same-user", SimpleNamespace(id="validated-key-id"))

    remaining = enforce(
        _build_request(headers={"X-Agent-API-Key": "attacker-value-one"}),
        context=context,
    )

    assert remaining == 0
    with pytest.raises(HTTPException) as exc_info:
        enforce(
            _build_request(headers={"X-Agent-API-Key": "attacker-value-two"}),
            context=context,
        )
    assert exc_info.value.status_code == 429


class _FakeRedisClient:
    def __init__(self):
        self._counts: dict[str, int] = {}
        self._ttls: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def expire(self, key: str, seconds: int) -> bool:
        self._ttls[key] = seconds
        return True

    def ttl(self, key: str) -> int:
        return self._ttls.get(key, -1)


@pytest.mark.unit
def test_user_beijing_daily_limit_redis_uses_dated_key_and_midnight_ttl(monkeypatch):
    fake_redis = _FakeRedisClient()
    enforce = require_user_beijing_daily_rate_limit("agent_suggest_daily", 1)
    user = SimpleNamespace(id="same-user")
    request = _build_request()

    monkeypatch.setenv("RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 5, 15, 59, 30, tzinfo=UTC),
    )

    assert enforce(request, current_user=user) == 0
    with pytest.raises(HTTPException) as exc_info:
        enforce(request, current_user=user)

    old_key = rate_limit_module._build_redis_rate_key("agent_suggest_daily:user_same-user:beijing_day:2026-10-05")
    assert fake_redis._ttls[old_key] == 30
    assert exc_info.value.headers == {"Retry-After": "30"}

    monkeypatch.setattr(
        rate_limit_module,
        "utcnow",
        lambda: datetime(2026, 10, 5, 16, 0, 0, tzinfo=UTC),
    )
    assert enforce(request, current_user=user) == 0
    new_key = rate_limit_module._build_redis_rate_key("agent_suggest_daily:user_same-user:beijing_day:2026-10-06")
    assert fake_redis._ttls[new_key] == 86400
    assert _rate_limit_store == {}


@pytest.mark.unit
def test_check_rate_limit_uses_redis_when_backend_enabled(monkeypatch):
    request = _build_request(headers={"X-Forwarded-For": "198.51.100.20"})
    fake_redis = _FakeRedisClient()

    monkeypatch.setenv("RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake_redis)

    allowed1, remaining1 = check_rate_limit(request, "auth_login_ip", 1, 60)
    allowed2, remaining2 = check_rate_limit(request, "auth_login_ip", 1, 60)

    assert allowed1 is True
    assert remaining1 == 0
    assert allowed2 is False
    assert remaining2 == 0
    assert _rate_limit_store == {}


@pytest.mark.unit
def test_check_rate_limit_falls_back_to_memory_when_redis_unavailable(monkeypatch):
    request = _build_request(headers={"X-Forwarded-For": "198.51.100.30"})

    monkeypatch.setenv("RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")

    def _raise_redis_error():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(rate_limit_module, "get_redis_client", _raise_redis_error)

    allowed1, remaining1 = check_rate_limit(request, "auth_login_ip", 1, 60)
    allowed2, remaining2 = check_rate_limit(request, "auth_login_ip", 1, 60)

    assert allowed1 is True
    assert remaining1 == 0
    assert allowed2 is False
    assert remaining2 == 0


@pytest.mark.unit
def test_check_rate_limit_auto_backend_skips_redis_when_redis_url_missing(monkeypatch):
    request = _build_request(headers={"X-Forwarded-For": "198.51.100.31"})

    monkeypatch.setenv("RATE_LIMIT_BACKEND", "auto")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(
        rate_limit_module,
        "get_redis_client",
        lambda: (_ for _ in ()).throw(AssertionError("redis client should not be requested")),
    )

    allowed1, remaining1 = check_rate_limit(request, "auth_login_ip", 2, 60)
    allowed2, remaining2 = check_rate_limit(request, "auth_login_ip", 2, 60)
    allowed3, remaining3 = check_rate_limit(request, "auth_login_ip", 2, 60)

    assert allowed1 is True and remaining1 == 1
    assert allowed2 is True and remaining2 == 0
    assert allowed3 is False and remaining3 == 0


@pytest.mark.unit
def test_check_rate_limit_auto_backend_cooldown_skips_second_redis_attempt(monkeypatch):
    request = _build_request(headers={"X-Forwarded-For": "198.51.100.32"})
    calls = {"redis": 0}

    monkeypatch.setenv("RATE_LIMIT_BACKEND", "auto")
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")
    monkeypatch.setenv("RATE_LIMIT_REDIS_ERROR_COOLDOWN_SECONDS", "60")

    def _raise_redis_error():
        calls["redis"] += 1
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(rate_limit_module, "get_redis_client", _raise_redis_error)

    # First call attempts Redis and falls back to memory.
    allowed1, remaining1 = check_rate_limit(request, "auth_login_ip", 2, 60)
    # Second call should use cooldown path and skip Redis entirely.
    allowed2, remaining2 = check_rate_limit(request, "auth_login_ip", 2, 60)

    assert calls["redis"] == 1
    assert allowed1 is True and remaining1 == 1
    assert allowed2 is True and remaining2 == 0


class _FakeRedisClientNoTTL(_FakeRedisClient):
    def __init__(self):
        super().__init__()
        self.expire_calls: list[tuple[str, int]] = []

    def expire(self, key: str, seconds: int) -> bool:
        self.expire_calls.append((key, seconds))
        return super().expire(key, seconds)

    def ttl(self, key: str) -> int:
        # Force ttl<0 branch on non-first requests.
        return -1


@pytest.mark.unit
def test_check_rate_limit_redis_repairs_missing_ttl(monkeypatch):
    request = _build_request(headers={"X-Forwarded-For": "198.51.100.40"})
    fake_redis = _FakeRedisClientNoTTL()

    monkeypatch.setenv("RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake_redis)

    allowed1, _ = check_rate_limit(request, "auth_login_ip", 3, 60)
    allowed2, _ = check_rate_limit(request, "auth_login_ip", 3, 60)

    assert allowed1 is True
    assert allowed2 is True
    assert len(fake_redis.expire_calls) >= 2


@pytest.mark.unit
def test_build_redis_rate_key_uses_custom_prefix(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REDIS_PREFIX", "zenstory_rl")
    key = rate_limit_module._build_redis_rate_key("auth_login_ip:198.51.100.50")
    assert key == "zenstory_rl:auth_login_ip:198.51.100.50"
