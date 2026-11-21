"""MiniMax credential routing stays stable without becoming provider failover."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest

from agent.core import llm_failover, llm_providers
from utils.request_context import bind_request_context, reset_request_context


@pytest.fixture(autouse=True)
def minimax_keys(monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "fake-primary-key")
    monkeypatch.setenv("MINIMAX_SECONDARY_API_KEY", "fake-secondary-key")
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "20")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    llm_providers.reset_provider_client_cache()
    llm_providers.reset_key_routing_state()
    llm_failover.reset_failover_state()
    yield
    llm_providers.reset_provider_client_cache()
    llm_providers.reset_key_routing_state()
    llm_failover.reset_failover_state()


def _provider(
    routing_key: str | None = None,
    *,
    rotate_if_unkeyed: bool = False,
) -> llm_providers.ModelProvider:
    return llm_providers.configured_providers(
        routing_key=routing_key,
        rotate_if_unkeyed=rotate_if_unkeyed,
    )[0]


@pytest.mark.unit
def test_stable_hash_routes_about_twenty_percent_to_secondary():
    first = [_provider(f"run-{index}").key_slot for index in range(10_000)]
    second = [_provider(f"run-{index}").key_slot for index in range(10_000)]

    assert first == second
    secondary_share = first.count("secondary") / len(first)
    assert 0.18 <= secondary_share <= 0.22


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_run_routing_is_stable_and_context_isolated():
    routing_keys = [f"concurrent-run-{index}" for index in range(40)]
    expected = {key: _provider(key).key_slot for key in routing_keys}

    async def read_in_context(routing_key: str) -> tuple[str, list[str]]:
        tokens = bind_request_context(agent_run_id=routing_key)
        try:
            await asyncio.sleep(0)
            return routing_key, [_provider().key_slot for _ in range(4)]
        finally:
            reset_request_context(tokens)

    results = dict(await asyncio.gather(*(read_in_context(key) for key in routing_keys)))

    assert results == {key: [slot] * 4 for key, slot in expected.items()}


@pytest.mark.unit
def test_context_free_calls_use_weighted_rotation_instead_of_one_key():
    # Config inspection (for example MaterialLLMClient.__init__) does not consume
    # a bucket. Rotation is requested only at the real model-call ordering point.
    assert {_provider().key_slot for _ in range(20)} == {"primary"}
    slots = [_provider(rotate_if_unkeyed=True).key_slot for _ in range(100)]

    assert slots.count("secondary") == 20
    assert slots.count("primary") == 80


@pytest.mark.unit
@pytest.mark.parametrize("invalid_weight", ["invalid", "-1", "101", "20.5"])
def test_invalid_secondary_weight_uses_twenty_percent_default(monkeypatch, invalid_weight):
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", invalid_weight)

    actual = [_provider(f"invalid-{index}").key_slot for index in range(1_000)]
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "20")
    expected = [_provider(f"invalid-{index}").key_slot for index in range(1_000)]

    assert actual == expected


@pytest.mark.unit
def test_single_key_and_missing_primary_keep_existing_contract(monkeypatch):
    monkeypatch.delenv("MINIMAX_SECONDARY_API_KEY")
    assert {_provider(f"run-{index}").key_slot for index in range(100)} == {"primary"}

    monkeypatch.setenv("MINIMAX_SECONDARY_API_KEY", "fake-secondary-key")
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "100")
    monkeypatch.delenv("MINIMAX_API_KEY")
    with pytest.raises(ValueError, match="MINIMAX_API_KEY"):
        _provider("run")


@pytest.mark.unit
def test_client_cache_is_isolated_by_selected_key(monkeypatch):
    primary = next(_provider(f"cache-{index}") for index in range(1_000) if _provider(f"cache-{index}").key_slot == "primary")
    secondary = next(
        _provider(f"cache-{index}") for index in range(1_000) if _provider(f"cache-{index}").key_slot == "secondary"
    )
    created: list[str] = []

    class FakeOpenAI:
        def __init__(self, *, api_key, **_kwargs):
            created.append(api_key)

    monkeypatch.setattr(llm_providers, "OpenAI", FakeOpenAI)
    monkeypatch.setattr("services.usage.model_call_guard.install_sync_cost_guard", lambda client: client)

    first_primary = llm_providers.get_sync_client(primary)
    first_secondary = llm_providers.get_sync_client(secondary)

    assert first_primary is llm_providers.get_sync_client(primary)
    assert first_secondary is llm_providers.get_sync_client(secondary)
    assert first_primary is not first_secondary
    assert created == ["fake-primary-key", "fake-secondary-key"]


@pytest.mark.unit
def test_minimax_keys_do_not_create_a_second_failover_provider():
    ordered = llm_failover.ordered_providers("stable-session")

    assert len(ordered) == 1
    assert ordered[0].name == "minimax"
    assert ordered[0].key_slot == _provider("stable-session").key_slot


# --- 会话粘性：连续几轮留在同一把 Key，前缀缓存才接得上 ---------------------------


def _run_slot(run_id: str, session_id: str | None) -> str:
    tokens = bind_request_context(agent_run_id=run_id)
    try:
        return _provider(session_id).key_slot
    finally:
        reset_request_context(tokens)


@pytest.mark.unit
def test_consecutive_runs_of_a_session_stay_on_one_key_across_hour_boundaries(monkeypatch):
    """Consecutive runs of the same session stay pinned to one key across UTC hour boundaries."""
    import time as time_mod

    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    timestamps = [3_599.0, 3_600.0, 7_200.0, 10_800.0, 90_000.0]

    for session in (f"session-{index}" for index in range(50)):
        slots = set()
        for run_idx, ts in enumerate(timestamps):
            monkeypatch.setattr(time_mod, "time", lambda ts=ts: ts)
            slots.add(_run_slot(f"{session}-run-{run_idx}", session))
        assert len(slots) == 1


@pytest.mark.unit
def test_distinct_sessions_distribute_according_to_secondary_weight(monkeypatch):
    """Distinct sessions still split across primary/secondary by configured weight."""
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    slots = [_run_slot(f"run-{index}", f"session-{index}") for index in range(2_000)]

    assert 0.36 < slots.count("secondary") / len(slots) < 0.44


@pytest.mark.unit
def test_a_run_keeps_its_key_across_wall_clock_boundaries(monkeypatch):
    import time as time_mod

    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    for index in range(100):
        monkeypatch.setattr(time_mod, "time", lambda: 0.0)
        first = _run_slot(f"long-run-{index}", f"session-{index}")
        monkeypatch.setattr(time_mod, "time", lambda: 7_200.0)
        assert _run_slot(f"long-run-{index}", f"session-{index}") == first


@pytest.mark.unit
def test_a_run_keeps_its_key_when_a_later_call_has_no_session(monkeypatch):
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    for index in range(100):
        first = _run_slot(f"mixed-run-{index}", f"session-{index}")
        assert _run_slot(f"mixed-run-{index}", None) == first


@pytest.mark.unit
def test_a_client_cannot_compute_its_bucket_without_the_server_key(monkeypatch):
    """session id 可能来自客户端：分桶带服务端密钥，换一把主 Key 结果就不同。"""
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    before = [_provider(f"client-chosen-{index}").key_slot for index in range(200)]
    monkeypatch.setenv("MINIMAX_API_KEY", "another-primary-key")

    assert [_provider(f"client-chosen-{index}").key_slot for index in range(200)] != before


# --- 次 Key 限流只影响次 Key：不熔断整个 MiniMax，也不把会话钉到备用供应商 ---------


def _rate_limited(status_code: int = 429) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://example.invalid")
    return openai.APIStatusError("limited", response=httpx.Response(status_code, request=request), body=None)


def _secondary_session() -> str:
    return next(
        f"cool-{index}"
        for index in range(1_000)
        if llm_failover.ordered_providers(f"cool-{index}")[0].key_slot == "secondary"
    )


@pytest.fixture
def with_backup(monkeypatch):
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-deepseek-key")


@pytest.mark.unit
@pytest.mark.parametrize("status_code", [429, 401, 402, 403])
def test_secondary_key_failure_cools_the_key_and_keeps_the_session_on_minimax(with_backup, status_code):
    session = _secondary_session()
    failing = llm_failover.ordered_providers(session)[0]

    # 超过熔断阈值也不开整个槽位：别的作者和主 Key 不受影响。
    for _ in range(llm_failover.LLM_CIRCUIT_FAILURE_THRESHOLD + 2):
        llm_failover.record_failure(failing, session, _rate_limited(status_code))

    ordered = llm_failover.ordered_providers(session)
    assert [(p.name, p.key_slot) for p in ordered] == [("minimax", "primary"), ("deepseek", "primary")]
    assert ordered[0].api_key == "fake-primary-key"
    assert llm_failover.ordered_providers("someone-else")[0].name == "minimax"
    assert {llm_failover.ordered_providers(f"other-{i}")[0].key_slot for i in range(50)} == {"primary"}


@pytest.mark.unit
def test_cooled_key_returns_after_the_cooldown(with_backup, monkeypatch):
    session = _secondary_session()
    failing = llm_failover.ordered_providers(session)[0]
    llm_failover.record_failure(failing, session, _rate_limited())
    assert llm_failover.ordered_providers(session)[0].key_slot == "primary"

    llm_failover.reset_failover_state()  # the cooldown key expires by TTL in the store

    assert llm_failover.ordered_providers(session)[0].key_slot == "secondary"


def _fake_endpoints(monkeypatch, outcomes: dict[str, list[object]]) -> None:
    def client_for(provider):
        async def create(**_kwargs):
            outcome = outcomes[provider.name].pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(llm_providers, "get_async_client", client_for)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("cooldown_s", [300, 0], ids=["cooling", "backup-outlasted-the-cooldown"])
async def test_backup_answering_a_cooled_key_call_does_not_pin_the_session_to_the_backup(
    with_backup, monkeypatch, cooldown_s
):
    session = _secondary_session()
    monkeypatch.setattr(llm_failover, "LLM_KEY_COOLDOWN_S", cooldown_s)
    answer = SimpleNamespace(choices=[], usage=None)
    _fake_endpoints(monkeypatch, {"minimax": [_rate_limited()], "deepseek": [answer]})

    _response, served = await llm_failover.acreate_chat_completion(
        call_site="test", session_id=session, messages=[{"role": "user", "content": "hi"}], max_tokens=10
    )

    assert served.name == "deepseek"
    assert llm_failover.ordered_providers(session)[0].name == "minimax"


@pytest.mark.unit
def test_outages_on_the_secondary_key_still_count_for_the_provider(with_backup):
    """5xx 不是单把 Key 的问题：沿用会话固定和槽位熔断。"""
    session = _secondary_session()
    failing = llm_failover.ordered_providers(session)[0]

    llm_failover.record_failure(failing, session, _rate_limited(503))

    assert llm_failover.ordered_providers(session)[0].name == "deepseek"


def _primary_session() -> str:
    return next(
        f"pri-{index}"
        for index in range(1_000)
        if llm_failover.ordered_providers(f"pri-{index}")[0].key_slot == "primary"
    )


@pytest.mark.unit
def test_primary_key_rate_limit_moves_later_calls_to_the_secondary_key(with_backup):
    session = _primary_session()
    failing = llm_failover.ordered_providers(session)[0]

    for _ in range(llm_failover.LLM_CIRCUIT_FAILURE_THRESHOLD + 2):
        llm_failover.record_failure(failing, session, _rate_limited())

    ordered = llm_failover.ordered_providers(session)
    assert [(p.name, p.key_slot) for p in ordered] == [("minimax", "secondary"), ("deepseek", "primary")]
    assert ordered[0].api_key == "fake-secondary-key"


@pytest.mark.unit
def test_both_keys_limited_is_a_provider_failure(with_backup):
    session = _primary_session()
    primary = llm_failover.ordered_providers(session)[0]
    llm_failover.record_failure(primary, session, _rate_limited())
    secondary = llm_failover.ordered_providers(session)[0]
    assert secondary.key_slot == "secondary"

    llm_failover.record_failure(secondary, session, _rate_limited())

    assert llm_failover.ordered_providers(session)[0].name == "deepseek"


@pytest.mark.unit
def test_weight_zero_never_sends_traffic_to_the_secondary_key(with_backup, monkeypatch):
    """权重 0 是主动关掉次 Key：主 Key 限流时照旧切备用供应商。"""
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "0")
    failing = llm_failover.ordered_providers("off-session")[0]
    assert failing.key_slot == "primary"

    llm_failover.record_failure(failing, "off-session", _rate_limited())

    assert llm_failover.ordered_providers("off-session")[0].name == "deepseek"
    assert llm_failover.ordered_providers("another")[0].key_slot == "primary"


@pytest.mark.unit
def test_cooldown_works_without_a_backup_provider():
    session = _secondary_session()
    failing = llm_failover.ordered_providers(session)[0]

    llm_failover.record_failure(failing, session, _rate_limited())

    assert llm_failover.ordered_providers(session)[0].key_slot == "primary"


# --- Redis idle sliding TTL sticky routing (Guards #1-#5) ---------------------------


class _ClockedFakeRedis:
    """Clock-aware in-memory Redis double supporting get/mget/set(nx,ex)/expire."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.store: dict[str, tuple[str, float]] = {}
        self.get_calls: list[str] = []
        self.expire_calls: list[tuple[str, int]] = []
        self.set_calls: list[tuple[str, str, bool, int | None]] = []

    def _alive(self, key: str) -> str | None:
        entry = self.store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at <= self.now:
            self.store.pop(key, None)
            return None
        return value

    def get(self, key: str) -> str | None:
        self.get_calls.append(key)
        return self._alive(key)

    def mget(self, keys: list[str]) -> list[str | None]:
        return [self._alive(key) for key in keys]

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        self.set_calls.append((key, value, nx, ex))
        if nx and self._alive(key) is not None:
            return False
        ttl = float(ex) if ex is not None else 3600.0
        self.store[key] = (value, self.now + ttl)
        return True

    def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        value = self._alive(key)
        if value is None:
            return False
        self.store[key] = (value, self.now + float(ttl))
        return True


@pytest.fixture
def sticky_redis(monkeypatch):
    client = _ClockedFakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake-key-route/0")
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: client)
    llm_providers.reset_key_routing_state()
    llm_failover.reset_failover_state()
    yield client
    llm_providers.reset_key_routing_state()
    llm_failover.reset_failover_state()


@pytest.mark.unit
def test_active_session_stays_pinned_across_utc_hour_boundaries_with_sliding_ttl_and_intra_run_lock(
    sticky_redis, monkeypatch
):
    """Guard #2 & #3: active session refreshes TTL on each run and never flips across UTC
    hour boundaries; subsequent calls within the same run use memory with 0 extra Redis IO."""
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    monkeypatch.setattr(llm_providers, "LLM_KEY_ROUTING_WINDOW_S", 1800.0)
    sticky_redis.now = 3_000.0  # 10 min before 3600s UTC hour boundary

    session = "active-whale-session"
    first_slot = _run_slot("run-0", session)
    assert sticky_redis.store[f"llm:key_route:{session}"][0] == first_slot

    # Advance 15 min per turn across 10 consecutive UTC hour boundaries (> 2.5 hours active).
    for idx in range(1, 12):
        sticky_redis.now += 900.0  # < 1800s sliding TTL
        assert _run_slot(f"run-{idx}", session) == first_slot

    assert len(sticky_redis.expire_calls) == 11
    assert sticky_redis.expire_calls[-1] == (f"llm:key_route:{session}", 1800)

    # Guard #2: within one run, subsequent calls incur 0 extra Redis IO (even without session_key).
    gets_before = len(sticky_redis.get_calls)
    expires_before = len(sticky_redis.expire_calls)
    tokens = bind_request_context(agent_run_id="run-intra")
    try:
        slot_1 = _provider(session).key_slot
        assert len(sticky_redis.get_calls) == gets_before + 1
        assert len(sticky_redis.expire_calls) == expires_before + 1
        # Calls 2..5 (e.g. main loop after compaction, or session-less call) hit memory only.
        for _ in range(3):
            assert _provider(session).key_slot == slot_1
        assert _provider(None).key_slot == slot_1
        assert len(sticky_redis.get_calls) == gets_before + 1
        assert len(sticky_redis.expire_calls) == expires_before + 1
    finally:
        reset_request_context(tokens)


@pytest.mark.unit
def test_idle_session_expires_and_redraws_at_configured_weight(sticky_redis, monkeypatch):
    """Guard #3: once a session is idle longer than LLM_KEY_ROUTING_WINDOW_S, its Redis key
    expires and re-draws at MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT so one session cannot camp
    on secondary across days."""
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    monkeypatch.setattr(llm_providers, "LLM_KEY_ROUTING_WINDOW_S", 1800.0)

    slots = []
    for idx in range(1_500):
        sticky_redis.now += 1801.0  # strictly past idle window
        slots.append(_run_slot(f"idle-run-{idx}", "same-whale-session"))

    secondary_share = slots.count("secondary") / len(slots)
    assert 0.35 < secondary_share < 0.45


@pytest.mark.unit
@pytest.mark.parametrize("disable_mode", ["weight_zero", "missing_secondary_key"])
def test_weight_zero_or_missing_secondary_key_switches_back_instantly_before_redis(
    sticky_redis, monkeypatch, disable_mode: str
):
    """Guard #1: disabling secondary key (weight=0 or unset key) returns primary immediately
    with 0 Redis reads even when Redis holds a live 'secondary' sticky entry."""
    session = "stale-secondary-session"
    sticky_redis.set(f"llm:key_route:{session}", "secondary", ex=1800)
    sticky_redis.get_calls.clear()

    if disable_mode == "weight_zero":
        monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "0")
    else:
        monkeypatch.delenv("MINIMAX_SECONDARY_API_KEY")

    assert _run_slot("switchback-run", session) == "primary"
    assert sticky_redis.get_calls == []


@pytest.mark.unit
def test_concurrent_set_nx_race_converges_on_winners_slot(monkeypatch):
    """Guard #3: if a concurrent request wins SET NX between GET and SET, the loser re-reads
    and returns the winner's slot instead of its own candidate draw."""

    class _RaceRedis:
        def __init__(self) -> None:
            self.value: str | None = None
            self.get_count = 0

        def get(self, _key: str) -> str | None:
            self.get_count += 1
            return self.value

        def set(self, _key: str, _value: str, *, nx: bool = False, ex: int | None = None) -> bool:
            assert nx is True and ex == 1800
            # Simulate concurrent winner writing "secondary" just before our SET NX executes.
            self.value = "secondary"
            return False

        def expire(self, _key: str, _ttl: int) -> bool:
            return True

    race_redis = _RaceRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake-race/0")
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: race_redis)
    # Force our local candidate draw to "primary" so we prove the winner's "secondary" wins.
    monkeypatch.setattr(llm_providers, "_hmac_slot", lambda *_a, **_kw: "primary")

    assert _run_slot("loser-run", "raced-session") == "secondary"
    assert race_redis.get_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redis_outage_fails_open_and_aordered_providers_runs_off_event_loop(monkeypatch):
    """Guard #4: Redis errors fail open silently to deterministic session_key HMAC without
    raising, and aordered_providers executes sync Redis I/O on a worker thread."""
    import threading

    seen_threads: list[int] = []

    class _ThreadRecordingRedis(_ClockedFakeRedis):
        def get(self, key: str) -> str | None:
            seen_threads.append(threading.get_ident())
            return super().get(key)

    main_tid = threading.get_ident()
    recording_redis = _ThreadRecordingRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake-thread-check/0")
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: recording_redis)
    llm_providers.reset_key_routing_state()

    tokens = bind_request_context(agent_run_id="async-run-1")
    try:
        ordered = await llm_failover.aordered_providers("async-session")
    finally:
        reset_request_context(tokens)

    assert len(ordered) == 1
    assert seen_threads and all(tid != main_tid for tid in seen_threads)

    # Now simulate Redis outage: must fail open without raising and stay deterministic.
    class _BrokenRedis:
        def get(self, _key: str):
            raise ConnectionError("redis unreachable")

        def mget(self, _keys):
            raise ConnectionError("redis unreachable")

    monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: _BrokenRedis())
    llm_providers.reset_key_routing_state()

    first = _run_slot("outage-run-1", "outage-session")
    second = _run_slot("outage-run-2", "outage-session")
    assert first == second
    assert first in ("primary", "secondary")


@pytest.mark.unit
def test_key_cooldown_overrides_sticky_slot_without_overwriting_redis_route(
    sticky_redis, with_backup, monkeypatch
):
    """Guard #5: 429 cooldown fails over to other_key() without clobbering llm:key_route:{session};
    once cooldown expires, the session returns to its sticky slot."""
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    session = "sticky-cooldown-session"
    route_key = f"llm:key_route:{session}"
    sticky_redis.set(route_key, "secondary", ex=1800)

    first = llm_failover.ordered_providers(session)[0]
    assert first.key_slot == "secondary"

    # Secondary hits 429 -> cools down for LLM_KEY_COOLDOWN_S, failover serves primary.
    llm_failover.record_failure(first, session, _rate_limited(429))
    during_cooldown = llm_failover.ordered_providers(session)[0]
    assert during_cooldown.key_slot == "primary"
    # Sticky Redis route must NOT have been overwritten by the cooldown failover.
    assert sticky_redis.store[route_key][0] == "secondary"

    # Once cooldown expires (while still inside sticky window), session returns to secondary.
    sticky_redis.now += llm_failover.LLM_KEY_COOLDOWN_S + 1.0
    after_cooldown = llm_failover.ordered_providers(session)[0]
    assert after_cooldown.key_slot == "secondary"
