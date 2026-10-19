"""Async DeepSeek clients are rebuilt for a new event loop (openai 3 / HTTPX2 upgrade)."""

import asyncio

from agent.core import deepseek_client
from agent.core.llm_client import LLMClient
from agent.openai_agents import model as sdk_model


def _client_in_new_loop():
    async def grab():
        return deepseek_client.get_deepseek_client(), sdk_model.get_deepseek_chat_model()

    return asyncio.run(grab())


def test_each_event_loop_gets_its_own_client_and_sdk_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    sdk_model.reset_deepseek_sdk_cache()
    try:
        first_client, first_model = _client_in_new_loop()
        second_client, second_model = _client_in_new_loop()

        assert first_client is not second_client
        assert first_model is not second_model

        async def same_loop_twice():
            return deepseek_client.get_deepseek_client() is deepseek_client.get_deepseek_client()

        assert asyncio.run(same_loop_twice()) is True
    finally:
        sdk_model.reset_deepseek_sdk_cache()


def test_llm_client_async_client_follows_the_loop(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    client = LLMClient()

    async def grab():
        return client.async_client, client.async_client

    a1, a2 = asyncio.run(grab())
    b1, _ = asyncio.run(grab())

    assert a1 is a2
    assert a1 is not b1
