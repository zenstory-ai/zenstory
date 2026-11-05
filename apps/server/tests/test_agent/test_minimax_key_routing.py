"""MiniMax credential routing stays stable without becoming provider failover."""

from __future__ import annotations

import asyncio

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
    llm_providers.reset_provider_client_cache()
    yield
    llm_providers.reset_provider_client_cache()


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
