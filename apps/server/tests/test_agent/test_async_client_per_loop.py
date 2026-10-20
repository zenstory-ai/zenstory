"""Async provider clients are rebuilt for a new event loop (openai 2 / httpx)."""

import asyncio

from agent.core import llm_providers
from agent.openai_agents import model as sdk_model


def _client_in_new_loop():
    async def grab():
        provider = llm_providers.primary_provider()
        return llm_providers.get_async_client(provider), sdk_model._sdk_model_for(provider)  # noqa: SLF001

    return asyncio.run(grab())


def test_each_event_loop_gets_its_own_client_and_sdk_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    sdk_model.reset_sdk_model_cache()
    try:
        first_client, first_model = _client_in_new_loop()
        second_client, second_model = _client_in_new_loop()

        assert first_client is not second_client
        assert first_model is not second_model

        async def same_loop_twice():
            provider = llm_providers.primary_provider()
            return llm_providers.get_async_client(provider) is llm_providers.get_async_client(provider)

        assert asyncio.run(same_loop_twice()) is True
    finally:
        sdk_model.reset_sdk_model_cache()
