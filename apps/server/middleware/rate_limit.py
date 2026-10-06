"""
Rate Limiting - Simple in-memory rate limiter with proxy support.
"""

import logging
import math
import os
import time
from collections import defaultdict
from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from fastapi import Depends, HTTPException, Request, status

from config.datetime_utils import (
    beijing_date,
    beijing_day_bounds,
    normalize_datetime_to_utc,
    utcnow,
)
from services.infra.redis_client import get_redis_client
from utils.logger import get_logger, log_with_context

# In-memory fallback store
_rate_limit_store: dict = defaultdict(list)
_redis_retry_after_monotonic: float = 0.0

logger = get_logger(__name__)


def _parse_ip(candidate: str | None) -> str | None:
    """Return normalized IP if valid, otherwise None."""
    if not candidate:
        return None

    raw = candidate.strip()
    if not raw:
        return None

    if raw.startswith("[") and "]" in raw:
        raw = raw[1 : raw.index("]")]

    try:
        return str(ip_address(raw))
    except ValueError:
        # Handle common IPv4 with port format: "1.2.3.4:5678"
        if raw.count(":") == 1:
            maybe_host, _sep, _port = raw.partition(":")
            try:
                return str(ip_address(maybe_host))
            except ValueError:
                return None
        return None


# Cloudflare edge ranges (https://www.cloudflare.com/ips/). Used to decide
# whether CF-Connecting-IP was set by Cloudflare or forged by the client.
# Override with CLOUDFLARE_IP_RANGES (comma-separated CIDRs) if they change.
_DEFAULT_CLOUDFLARE_IP_RANGES = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)
_CLOUDFLARE_MODES = {"auto", "always", "off"}


def _trusted_proxy_hops() -> int:
    """How many proxies in front of the app append to X-Forwarded-For.

    Default 1: the Railway edge appends the address that connected to it.
    0 ignores X-Forwarded-For and uses the socket peer.
    """
    raw = os.getenv("TRUSTED_PROXY_HOPS", "1").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 1


def _cloudflare_mode() -> str:
    """CF-Connecting-IP policy: auto (only when the peer is a Cloudflare edge), always, off."""
    mode = os.getenv("CLIENT_IP_CLOUDFLARE", "auto").strip().lower()
    return mode if mode in _CLOUDFLARE_MODES else "auto"


@lru_cache(maxsize=4)
def _parse_networks(raw: str) -> tuple[IPv4Network | IPv6Network, ...]:
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ip_network(item, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _cloudflare_networks() -> tuple[IPv4Network | IPv6Network, ...]:
    raw = os.getenv("CLOUDFLARE_IP_RANGES", "").strip() or ",".join(_DEFAULT_CLOUDFLARE_IP_RANGES)
    return _parse_networks(raw)


def _is_cloudflare_ip(candidate: str | None) -> bool:
    if not candidate:
        return False
    try:
        address = ip_address(candidate)
    except ValueError:
        return False
    return any(address in network for network in _cloudflare_networks())


def _proxy_peer_ip(request: Request) -> str | None:
    """Address that connected to our outermost trusted proxy.

    Proxies append to X-Forwarded-For, so only the rightmost ``hops`` entries
    were written by infrastructure we trust; anything further left came from
    the client and can be forged.
    """
    hops = _trusted_proxy_hops()
    socket_peer = _parse_ip(request.client.host if request.client else None)
    if hops == 0:
        return socket_peer

    forwarded = request.headers.get("X-Forwarded-For")
    entries = [token for token in (forwarded or "").split(",") if token.strip()]
    if not entries:
        return socket_peer

    # Fewer entries than hops means every entry was proxy-written; take the
    # leftmost (closest to the client).
    index = len(entries) - hops if len(entries) >= hops else 0
    return _parse_ip(entries[index]) or socket_peer


def get_client_ip(request: Request) -> str:
    """Resolve the client IP without trusting client-controlled headers.

    1. CF-Connecting-IP, when the request came through Cloudflare
       (CLIENT_IP_CLOUDFLARE=auto checks the proxy peer against Cloudflare's
       ranges; ``always`` trusts it whenever present; ``off`` ignores it).
    2. The X-Forwarded-For entry appended by the outermost trusted proxy
       (TRUSTED_PROXY_HOPS, default 1).
    3. The socket peer.
    """
    peer_ip = _proxy_peer_ip(request)

    mode = _cloudflare_mode()
    if mode != "off":
        cf_ip = _parse_ip(request.headers.get("CF-Connecting-IP"))
        if cf_ip and (mode == "always" or _is_cloudflare_ip(peer_ip)):
            return cf_ip

    return peer_ip or "unknown"


def _get_rate_limit_backend() -> str:
    """
    Read rate-limit storage backend.

    - memory: in-process dict
    - redis: redis first, memory fallback on errors
    - auto: redis when REDIS_URL is configured, otherwise memory
    """
    return os.getenv("RATE_LIMIT_BACKEND", "auto").strip().lower()


def _get_redis_error_cooldown_seconds() -> int:
    raw = os.getenv("RATE_LIMIT_REDIS_ERROR_COOLDOWN_SECONDS", "30").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 30


def _build_redis_rate_key(rate_key: str) -> str:
    prefix = os.getenv("RATE_LIMIT_REDIS_PREFIX", "rate_limit").strip() or "rate_limit"
    return f"{prefix}:{rate_key}"


def _should_try_redis() -> bool:
    global _redis_retry_after_monotonic

    backend = _get_rate_limit_backend()
    if backend == "memory":
        return False

    if backend == "redis":
        return True

    # auto mode: use redis only when REDIS_URL exists and retry window is open
    if not os.getenv("REDIS_URL"):
        return False

    return time.monotonic() >= _redis_retry_after_monotonic


def _record_redis_failure(error: Exception) -> None:
    global _redis_retry_after_monotonic
    _redis_retry_after_monotonic = time.monotonic() + _get_redis_error_cooldown_seconds()
    log_with_context(
        logger,
        logging.WARNING,
        "Rate limit Redis backend unavailable; falling back to in-memory store",
        error=str(error),
        error_type=type(error).__name__,
        retry_after_seconds=_get_redis_error_cooldown_seconds(),
    )


def _check_rate_limit_redis(
    rate_key: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int] | None:
    """
    Check rate limit via Redis.

    Returns None when Redis backend should be skipped/fallback.
    """
    if not _should_try_redis():
        return None

    try:
        client = get_redis_client()
        redis_key = _build_redis_rate_key(rate_key)

        current_count = int(client.incr(redis_key))
        if current_count == 1:
            client.expire(redis_key, window_seconds)
        else:
            ttl = int(client.ttl(redis_key))
            if ttl < 0:
                client.expire(redis_key, window_seconds)

        if current_count > max_requests:
            return False, 0

        return True, max_requests - current_count
    except Exception as exc:  # pragma: no cover - depends on runtime infra
        _record_redis_failure(exc)
        return None


def check_rate_limit(
    request: Request,
    key: str,
    max_requests: int,
    window_seconds: int,
    *,
    include_client_ip: bool = True,
) -> tuple[bool, int]:
    """
    Check rate limit for a key.

    Returns: (allowed, remaining_requests)
    """
    if include_client_ip:
        client_ip = get_client_ip(request)
        rate_key = f"{key}:{client_ip}"
    else:
        rate_key = key

    redis_result = _check_rate_limit_redis(
        rate_key=rate_key,
        max_requests=max_requests,
        window_seconds=window_seconds,
    )
    if redis_result is not None:
        return redis_result

    now = time.time()
    window_start = now - window_seconds

    # Clean old entries
    _rate_limit_store[rate_key] = [t for t in _rate_limit_store[rate_key] if t > window_start]

    # Check limit
    if len(_rate_limit_store[rate_key]) >= max_requests:
        return False, 0

    # Record request
    _rate_limit_store[rate_key].append(now)
    return True, max_requests - len(_rate_limit_store[rate_key])


def require_rate_limit(key: str, max_requests: int, window_seconds: int):
    """Enforce a generic IP-scoped rate limit.

    Never derive a bucket from authentication headers here: until a credential
    has been validated, an attacker can rotate arbitrary header values.
    """

    def check(request: Request):
        allowed, remaining = check_rate_limit(request, key, max_requests, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded. Please try again later."
            )
        return remaining

    return check


def require_agent_rate_limit(key: str, max_requests: int, window_seconds: int):
    """Enforce pre-auth IP and post-auth Agent-key limits."""
    # Import lazily to keep the generic middleware usable without loading the
    # Agent API authentication stack.
    from services.agent_auth_service import get_agent_user

    pre_auth_limit = require_rate_limit(
        f"{key}:pre_auth",
        max_requests,
        window_seconds,
    )

    def check(
        request: Request,
        _pre_auth_remaining: int = Depends(pre_auth_limit),
        context=Depends(get_agent_user),
    ):
        _session, _user_id, api_key = context
        allowed, remaining = check_rate_limit(
            request,
            f"{key}:agent_key:{api_key.id}",
            max_requests,
            window_seconds,
            include_client_ip=False,
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please try again later.",
                headers={"Retry-After": str(window_seconds)},
            )
        return remaining

    return check


def require_user_rate_limit(key: str, max_requests: int, window_seconds: int):
    """构造「按登录用户限流」的依赖项。

    与 require_rate_limit 的区别是限流主体：这里把 user_id 拼进限流 key 并关闭
    IP 维度，保证同一账号无论从哪个 IP 发起请求都共享同一个额度窗口。
    按 IP 计数对「单账号刷 LLM 接口」这类滥用无效——换 IP / 走代理即可绕过，
    而 LLM 的成本是按账号结算的。

    所有会触发真实远端 LLM 调用的已登录端点都应该挂这个依赖
    （/agent/stream、/agent/suggest、/agent/steer、/editor/natural-polish）。
    """
    # 延迟 import：services.auth 会反向依赖 middleware，模块级 import 会成环
    from fastapi import Depends
    from services.auth import get_current_active_user

    def check(
        request: Request,
        current_user=Depends(get_current_active_user),
    ) -> int:
        allowed, remaining = check_rate_limit(
            request,
            f"{key}:user_{current_user.id}",
            max_requests,
            window_seconds,
            include_client_ip=False,
        )
        if not allowed:
            log_with_context(
                logger,
                30,  # WARNING
                "LLM endpoint rate limit exceeded",
                rate_limit_key=key,
                user_id=current_user.id,
                max_requests=max_requests,
                window_seconds=window_seconds,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please try again later.",
            )
        return remaining

    # 挂到闭包上，便于路由层做「所有 LLM 端点都已限流」的静态自检。
    check.rate_limit_key = key
    check.rate_limit_max_requests = max_requests
    check.rate_limit_window_seconds = window_seconds
    return check


def require_user_beijing_daily_rate_limit(key: str, max_requests: int):
    """构造按登录用户、北京时间自然日计数的限流依赖。

    日期被写入 Redis 与内存使用的同一 bucket key，因此北京时间零点会切换到
    新 bucket；Redis TTL 和 429 的 Retry-After 都精确到下一次北京时间零点。
    小时、分钟等滚动窗口继续使用 :func:`require_user_rate_limit`。
    """
    # 延迟 import，避免 services.auth 与 middleware 形成模块级循环依赖。
    from services.auth import get_current_active_user

    def check(
        _request: Request,
        current_user=Depends(get_current_active_user),
    ) -> int:
        now = normalize_datetime_to_utc(utcnow())
        _, period_end = beijing_day_bounds(now)
        retry_after = max(1, math.ceil((period_end - now).total_seconds()))
        bucket_prefix = f"{key}:user_{current_user.id}:beijing_day:"
        rate_key = f"{bucket_prefix}{beijing_date(now).isoformat()}"

        redis_result = _check_rate_limit_redis(
            rate_key=rate_key,
            max_requests=max_requests,
            window_seconds=retry_after,
        )
        if redis_result is not None:
            allowed, remaining = redis_result
        else:
            # A natural-day bucket must not use the rolling-window timestamp
            # cleanup in check_rate_limit: near midnight its shrinking TTL
            # would incorrectly discard requests made earlier the same day.
            for stale_key in tuple(_rate_limit_store):
                if stale_key.startswith(bucket_prefix) and stale_key != rate_key:
                    del _rate_limit_store[stale_key]
            bucket = _rate_limit_store[rate_key]
            if len(bucket) >= max_requests:
                allowed, remaining = False, 0
            else:
                bucket.append(now.timestamp())
                allowed, remaining = True, max_requests - len(bucket)

        if not allowed:
            log_with_context(
                logger,
                logging.WARNING,
                "LLM endpoint Beijing daily rate limit exceeded",
                rate_limit_key=key,
                user_id=current_user.id,
                max_requests=max_requests,
                retry_after_seconds=retry_after,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Daily rate limit exceeded. Please try again after 00:00 Beijing time.",
                headers={"Retry-After": str(retry_after)},
            )
        return remaining

    # Keep the same route-introspection contract as rolling-window limiters,
    # while making the fixed calendar period explicit to callers/tests.
    check.rate_limit_key = key
    check.rate_limit_max_requests = max_requests
    check.rate_limit_window_seconds = 86400
    check.rate_limit_period = "beijing_day"
    return check
