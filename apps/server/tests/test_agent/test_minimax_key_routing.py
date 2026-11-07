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
def test_consecutive_runs_of_a_session_stay_on_one_key_within_a_window(monkeypatch):
    monkeypatch.setattr(llm_providers.time, "time", lambda: 1_000.0)
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")

    for session in (f"session-{index}" for index in range(50)):
        assert len({_run_slot(f"{session}-run-{index}", session) for index in range(8)}) == 1


@pytest.mark.unit
def test_a_session_is_reassigned_between_windows_so_the_split_holds_over_time(monkeypatch):
    """一个重度会话不能长期压在额度少的次 Key 上；按窗口统计仍接近配置比例。"""
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    window = llm_providers.LLM_KEY_ROUTING_WINDOW_S
    slots = []
    for index in range(2_000):
        monkeypatch.setattr(llm_providers.time, "time", lambda index=index: index * window)
        slots.append(_run_slot(f"run-{index}", "one-heavy-session"))

    assert 0.36 < slots.count("secondary") / len(slots) < 0.44


@pytest.mark.unit
def test_a_run_keeps_its_key_when_it_crosses_a_window(monkeypatch):
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "40")
    window = llm_providers.LLM_KEY_ROUTING_WINDOW_S
    for index in range(100):
        monkeypatch.setattr(llm_providers.time, "time", lambda: 0.0)
        first = _run_slot(f"long-run-{index}", f"session-{index}")
        monkeypatch.setattr(llm_providers.time, "time", lambda: float(window * 7))
        assert _run_slot(f"long-run-{index}", f"session-{index}") == first


@pytest.mark.unit
def test_a_run_keeps_its_key_when_a_later_call_has_no_session(monkeypatch):
    monkeypatch.setattr(llm_providers.time, "time", lambda: 1_000.0)
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
