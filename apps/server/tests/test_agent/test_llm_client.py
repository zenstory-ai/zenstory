from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from agent.core import llm_providers
from agent.core.llm_client import LLMClient


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


@pytest.fixture
def dummy_provider(monkeypatch):
    """Primary-only DeepSeek config whose client is a recording dummy."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")
    monkeypatch.delenv("LLM_PRIMARY_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)

    def install(responses: list[object]) -> _DummyAsyncClient:
        client = _DummyAsyncClient(responses)
        monkeypatch.setattr(llm_providers, "get_async_client", lambda _provider: client)
        return client

    return install


def _response(*, content: str = ""):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        usage=None,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acomplete_disables_thinking_via_extra_body(dummy_provider):
    dummy_client = dummy_provider([_response(content="done")])
    sink: dict[str, str] = {}

    result = await LLMClient().acomplete(
        [{"role": "user", "content": "hi"}], thinking_enabled=False, served_model_sink=sink
    )

    assert result == "done"
    call = dummy_client.chat.completions.calls[0]
    # deepseek-flash 默认推理且 reasoning token 计入 max_tokens；关闭时必须显式下发。
    assert call.get("extra_body") == {"thinking": {"type": "disabled"}}
    assert call["model"] == "deepseek-flash"
    # 去AI味的失败日志要带上供应商和 finish_reason（是否被截断），sink 一并带回。
    assert sink == {"model": "deepseek-flash", "provider": "deepseek", "finish_reason": None}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acomplete_failure_leaves_the_last_attempted_provider_in_the_sink(dummy_provider):
    dummy_provider([RuntimeError("provider exploded")])
    sink: dict[str, str] = {}

    with pytest.raises(RuntimeError):
        await LLMClient().acomplete(
            [{"role": "user", "content": "hi"}], thinking_enabled=False, served_model_sink=sink
        )

    # 调用失败时失败日志也要能说出是哪个供应商；没有响应，所以没有 finish_reason。
    assert sink == {"model": "deepseek-flash", "provider": "deepseek"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acomplete_keeps_default_thinking_without_extra_body(dummy_provider):
    dummy_client = dummy_provider([_response(content="done")])

    await LLMClient().acomplete([{"role": "user", "content": "hi"}])

    call = dummy_client.chat.completions.calls[0]
    assert "extra_body" not in call
    assert "reasoning_effort" not in call


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acomplete_provider_failure_log_is_fixed_and_redacted(dummy_provider, caplog):
    private_sentinel = "PRIVATE_PROVIDER_SENTINEL"
    dummy_client = dummy_provider([RuntimeError(private_sentinel)])

    with (
        caplog.at_level(logging.ERROR, logger="agent.core.llm_client"),
        pytest.raises(RuntimeError, match=private_sentinel),
    ):
        await LLMClient().acomplete([{"role": "user", "content": "synthetic"}])

    assert len(dummy_client.chat.completions.calls) == 1
    failure = next(
        record for record in caplog.records if record.getMessage() == "LLM async completion failed"
    )
    assert not failure.exc_info
    assert failure.custom_fields == {
        "error_type": "RuntimeError",
        "failure_category": "provider_request_failed",
        "status_code": None,
        "duration_ms": failure.custom_fields["duration_ms"],
    }
    assert isinstance(failure.custom_fields["duration_ms"], int)
    assert private_sentinel not in repr((failure.getMessage(), failure.custom_fields))
