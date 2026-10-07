"""Unit tests for NaturalPolishService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.features.natural_polish_service import (
    DEFAULT_NATURAL_POLISH_PROMPT_EN,
    DEFAULT_NATURAL_POLISH_PROMPT_ZH,
    NATURAL_POLISH_MAX_TOKENS,
    NaturalPolishService,
)
from services.usage.llm_usage_service import LLMUsageAttribution


@pytest.mark.unit
def test_natural_polish_max_tokens_is_large_enough():
    assert NATURAL_POLISH_MAX_TOKENS == 16000


@pytest.mark.unit
def test_resolve_prompt_uses_server_defaults_by_language():
    service = NaturalPolishService()

    assert service._resolve_prompt("zh") == DEFAULT_NATURAL_POLISH_PROMPT_ZH
    assert service._resolve_prompt("en-US") == DEFAULT_NATURAL_POLISH_PROMPT_EN


@pytest.mark.unit
@pytest.mark.asyncio
async def test_natural_polish_calls_llm_with_single_round_settings():
    service = NaturalPolishService()
    llm_client = MagicMock()
    llm_client.MODEL_QUALITY = "quality-model"
    llm_client.acomplete = AsyncMock(return_value="rewritten")

    with patch(
        "services.features.natural_polish_service.get_llm_client",
        return_value=llm_client,
    ):
        result = await service.natural_polish(
            selected_text="x" * 6000,
            language="zh",
            user_id="user-1",
            project_id="project-1",
        )

    assert result.polished_text == "rewritten"
    assert result.model == "quality-model"
    llm_client.acomplete.assert_awaited_once_with(
        messages=[
            {"role": "system", "content": service._resolve_prompt("zh")},
            {"role": "user", "content": "x" * 6000},
        ],
        model="quality-model",
        max_tokens=NATURAL_POLISH_MAX_TOKENS,
        thinking_enabled=False,
        usage_attribution=LLMUsageAttribution(
            user_id="user-1", source="polish", project_id="project-1"
        ),
    )
