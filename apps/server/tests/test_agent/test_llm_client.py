from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from agent.core.llm_client import DEEPSEEK_CHAT_MODEL, LLMClient


class _NestedPayload(BaseModel):
    flag: bool = Field(default=True, description="nested flag")


class _StructuredPayload(BaseModel):
    name: str = Field(description="payload name")
    nested: _NestedPayload


class _DummyAsyncCompletions:
    def __init__(self, responses: list[object]):
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _DummyAsyncClient:
    def __init__(self, responses: list[object]):
        self.chat = SimpleNamespace(completions=_DummyAsyncCompletions(responses))


class _DummySyncCompletions:
    def __init__(self, response_or_stream):
        self.response_or_stream = response_or_stream
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response_or_stream


class _DummySyncClient:
    def __init__(self, response_or_stream):
        self.chat = SimpleNamespace(completions=_DummySyncCompletions(response_or_stream))


def _set_deepseek_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")


def _response(*, content: str = "", tool_calls: list[object] | None = None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=None,
    )


@pytest.mark.unit
def test_structured_messages_prepends_schema_instruction(monkeypatch):
    _set_deepseek_env(monkeypatch)
    client = LLMClient()

    original = [{"role": "user", "content": "give structured"}]
    messages = client._structured_messages(original, _StructuredPayload)

    # Original messages are preserved after a prepended system instruction.
    assert messages[1:] == original
    assert messages[0]["role"] == "system"
    instruction = messages[0]["content"]
    # The literal word "json" is required for DeepSeek's json_object response_format.
    assert "json" in instruction.lower()
    # The target schema is embedded so the model knows the expected shape.
    assert "name" in instruction and "nested" in instruction


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acomplete_structured_uses_json_object_response_format(monkeypatch):
    _set_deepseek_env(monkeypatch)
    client = LLMClient()
    dummy_client = _DummyAsyncClient([
        _response(content=json.dumps({"name": "payload", "nested": {"flag": True}})),
    ])
    client._async_client = dummy_client

    await client.acomplete_structured(
        [{"role": "user", "content": "give structured"}],
        _StructuredPayload,
    )

    call = dummy_client.chat.completions.calls[0]
    # DeepSeek does not support response_format=json_schema; we must use json_object.
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"][0]["role"] == "system"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acomplete_disables_thinking_via_extra_body(monkeypatch):
    _set_deepseek_env(monkeypatch)
    client = LLMClient()
    dummy_client = _DummyAsyncClient([_response(content="done")])
    client._async_client = dummy_client

    result = await client.acomplete([{"role": "user", "content": "hi"}], thinking_enabled=False)

    assert result == "done"
    # deepseek-flash 默认推理且 reasoning token 计入 max_tokens；关闭时必须显式下发。
    assert dummy_client.chat.completions.calls[0].get("extra_body") == {
        "thinking": {"type": "disabled"}
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acomplete_keeps_default_thinking_without_extra_body(monkeypatch):
    _set_deepseek_env(monkeypatch)
    client = LLMClient()
    dummy_client = _DummyAsyncClient([_response(content="done")])
    client._async_client = dummy_client

    await client.acomplete([{"role": "user", "content": "hi"}])

    assert dummy_client.chat.completions.calls[0].get("extra_body") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acomplete_provider_failure_log_is_fixed_and_redacted(monkeypatch, caplog):
    _set_deepseek_env(monkeypatch)
    client = LLMClient()
    private_sentinel = "PRIVATE_PROVIDER_SENTINEL"
    dummy_client = _DummyAsyncClient([RuntimeError(private_sentinel)])
    client._async_client = dummy_client

    with (
        caplog.at_level(logging.ERROR, logger="agent.core.llm_client"),
        pytest.raises(RuntimeError, match=private_sentinel),
    ):
        await client.acomplete([{"role": "user", "content": "synthetic"}])

    assert len(dummy_client.chat.completions.calls) == 1
    failure = next(
        record for record in caplog.records if record.getMessage() == "LLM async completion failed"
    )
    assert not failure.exc_info
    assert failure.custom_fields == {
        "model": DEEPSEEK_CHAT_MODEL,
        "error_type": "RuntimeError",
        "failure_category": "provider_request_failed",
        "status_code": None,
        "duration_ms": failure.custom_fields["duration_ms"],
    }
    assert isinstance(failure.custom_fields["duration_ms"], int)
    assert private_sentinel not in repr((failure.getMessage(), failure.custom_fields))


@pytest.mark.unit
def test_complete_uses_default_sampling_parameters_and_returns_content(monkeypatch):
    _set_deepseek_env(monkeypatch)
    client = LLMClient()
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="sync-done"))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )
    dummy_client = _DummySyncClient(response)
    client._sync_client = dummy_client

    result = client.complete([{"role": "user", "content": "hi"}], max_tokens=123)

    assert result == "sync-done"
    call = dummy_client.chat.completions.calls[0]
    assert call["temperature"] == client.DEFAULT_TEMPERATURE
    assert call["top_p"] == client.DEFAULT_TOP_P
    assert call["max_tokens"] == 123


@pytest.mark.unit
def test_complete_stream_yields_only_non_empty_chunks(monkeypatch):
    _set_deepseek_env(monkeypatch)
    client = LLMClient()
    stream = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="he"))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="llo"))]),
    ]
    dummy_client = _DummySyncClient(stream)
    client._sync_client = dummy_client

    chunks = list(client.complete_stream([{"role": "user", "content": "hi"}]))

    assert chunks == ["he", "llo"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acomplete_structured_validates_response_model(monkeypatch):
    _set_deepseek_env(monkeypatch)
    client = LLMClient()
    dummy_client = _DummyAsyncClient([
        _response(content=json.dumps({"name": "payload", "nested": {"flag": False}})),
    ])
    client._async_client = dummy_client

    result = await client.acomplete_structured(
        [{"role": "user", "content": "give structured"}],
        _StructuredPayload,
    )

    assert result.name == "payload"
    assert result.nested.flag is False

@pytest.mark.unit
def test_llm_client_rejects_legacy_model_override(monkeypatch):
    _set_deepseek_env(monkeypatch)
    client = LLMClient()
    dummy_client = _DummySyncClient(_response(content="unused"))
    client._sync_client = dummy_client

    with pytest.raises(ValueError, match="only supports"):
        client.complete([{"role": "user", "content": "hi"}], model="legacy-model")

    assert dummy_client.chat.completions.calls == []


@pytest.mark.unit
def test_llm_client_accepts_only_deepseek_model(monkeypatch):
    _set_deepseek_env(monkeypatch)
    client = LLMClient()
    dummy_client = _DummySyncClient(_response(content="ok"))
    client._sync_client = dummy_client

    assert client.complete([{"role": "user", "content": "hi"}], model=DEEPSEEK_CHAT_MODEL) == "ok"
    assert dummy_client.chat.completions.calls[0]["model"] == DEEPSEEK_CHAT_MODEL
