import os

import pytest


@pytest.mark.integration
@pytest.mark.real_llm
@pytest.mark.parametrize("provider_name", ["deepseek", "minimax"])
async def test_provider_chat_completions_smoke(real_llm_enabled, monkeypatch, provider_name):
    """Minimal real-provider smoke for each provider's client wiring and request options."""
    from agent.core import llm_failover, llm_providers

    kind = llm_providers.PROVIDER_KINDS[provider_name]
    if not os.getenv(kind.api_key_env):
        pytest.skip(f"{kind.api_key_env} not set")
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", provider_name)
    monkeypatch.delenv("LLM_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    llm_providers.reset_provider_client_cache()

    response, provider = await llm_failover.acreate_chat_completion(
        call_site="real_llm_smoke",
        messages=[
            {"role": "system", "content": "Return exactly one short sentence."},
            {"role": "user", "content": "Say migration smoke passed."},
        ],
        temperature=0.0,
        # Both models reason first: reasoning_tokens count against the completion budget,
        # so a tiny max_tokens (e.g. 32) can leave message.content empty.
        max_tokens=512,
        # A short no-reasoning call: DeepSeek turns thinking off, MiniMax gets the lowest effort.
        thinking_enabled=False,
    )

    content = response.choices[0].message.content or ""
    assert provider.name == provider_name
    assert response.model
    assert content.strip()
