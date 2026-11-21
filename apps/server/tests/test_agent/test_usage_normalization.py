"""Unit tests for unified agent token-usage normalization (agent.core.usage)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.core.usage import (
    extract_response_usage,
    merge_usage_dicts,
    normalize_usage_keys,
    sum_response_usages,
)
from agent.openai_agents.runner import _usage_dict_from_result
from agent.stream_adapter import StreamAdapter

pytestmark = pytest.mark.unit


def test_extract_response_usage_handles_chat_completions_shape():
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=200,
        total_tokens=1200,
        prompt_tokens_details=SimpleNamespace(cached_tokens=700),
    )
    assert extract_response_usage(usage) == {
        "input_tokens": 300,
        "cache_read_tokens": 700,
        "output_tokens": 200,
        "total_tokens": 1200,
    }


def test_extract_response_usage_handles_sdk_responses_shape():
    usage = SimpleNamespace(
        input_tokens=500,
        output_tokens=80,
        total_tokens=580,
        input_tokens_details=SimpleNamespace(cached_tokens=128),
    )
    assert extract_response_usage(usage) == {
        "input_tokens": 372,
        "cache_read_tokens": 128,
        "output_tokens": 80,
        "total_tokens": 580,
    }


def test_extract_response_usage_accepts_mappings_and_clamps_malformed_cached():
    assert extract_response_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "prompt_tokens_details": {"cached_tokens": 999},
        }
    ) == {
        "cache_read_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
    }


def test_extract_response_usage_ignores_none_bool_strings_and_mocks():
    assert extract_response_usage(None) == {}
    assert extract_response_usage(MagicMock()) == {}
    assert extract_response_usage(
        {"prompt_tokens": True, "completion_tokens": "5", "total_tokens": False}
    ) == {}


def test_sum_response_usages_matches_runner_usage_dict_from_result():
    responses = [
        SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=10,
                total_tokens=110,
                input_tokens_details=SimpleNamespace(cached_tokens=64),
            )
        ),
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=50,
                completion_tokens=5,
                total_tokens=55,
                prompt_tokens_details=SimpleNamespace(cached_tokens=0),
            )
        ),
        SimpleNamespace(usage=None),
    ]
    expected = {
        "input_tokens": 86,
        "cache_read_tokens": 64,
        "output_tokens": 15,
        "total_tokens": 165,
    }
    assert sum_response_usages(r.usage for r in responses) == expected
    assert _usage_dict_from_result(SimpleNamespace(raw_responses=responses)) == expected


def test_normalize_and_merge_usage_dicts_fold_aliases_and_preserve_non_numeric():
    assert normalize_usage_keys(
        {"prompt_tokens": 10, "input_tokens": 5, "completion_tokens": 4, "output_tokens": 6}
    ) == {"input_tokens": 15, "output_tokens": 10}

    first = merge_usage_dicts(
        None,
        {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14, "model": "m1"},
    )
    assert first == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14, "model": "m1"}

    second = merge_usage_dicts(
        first,
        {"input_tokens": 30, "output_tokens": 6, "total_tokens": 36, "cache_read_tokens": 20, "model": "m2"},
    )
    assert second == {
        "input_tokens": 40,
        "output_tokens": 10,
        "total_tokens": 50,
        "cache_read_tokens": 20,
        "model": "m2",
    }

    # Non-mapping incoming leaves accumulated untouched; StreamAdapter delegates to the same helper.
    assert merge_usage_dicts(second, None) == second
    assert StreamAdapter._merge_usage(first, {"input_tokens": 30, "output_tokens": 6, "total_tokens": 36, "cache_read_tokens": 20, "model": "m2"}) == second
