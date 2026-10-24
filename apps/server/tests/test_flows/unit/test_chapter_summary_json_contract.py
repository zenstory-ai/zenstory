"""Wire-level JSON contract for chapter summaries and its safe failover boundary."""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import httpx2
import openai
import pytest

from agent.core import llm_failover, llm_providers
from core.error_codes import ErrorCode
from flows.atomic_tasks.summaries import chapter_summary_tasks as chapter_mod
from flows.utils.clients import llm as llm_mod
from flows.utils.decorators import prefect as prefect_decorators
from flows.utils.helpers.exceptions import LLMNonRetryableError


def _ok(content: str = '{"summary": "synthetic summary long enough for validation"}', *, model: str = "wire-model"):
    usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 11, "completion_tokens": 7})
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=usage,
        model=model,
    )


class _SyncEndpoint:
    def __init__(self) -> None:
        self.script: list[object] = []
        self.calls: list[dict] = []

        def create(**kwargs):
            self.calls.append(kwargs)
            outcome = self.script.pop(0) if self.script else _ok()
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _status_error(status_code: int) -> openai.InternalServerError:
    response = httpx.Response(status_code, request=httpx.Request("POST", "https://llm.test"))
    return openai.InternalServerError(f"status {status_code}", response=response, body=None)


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    primary: str,
    primary_model: str,
    backup: str | None = None,
    backup_model: str | None = None,
) -> dict[str, _SyncEndpoint]:
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", primary)
    monkeypatch.setenv("LLM_PRIMARY_MODEL", primary_model)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek")
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-minimax")
    if backup is None:
        monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_BACKUP_MODEL", raising=False)
    else:
        monkeypatch.setenv("LLM_BACKUP_PROVIDER", backup)
        monkeypatch.setenv("LLM_BACKUP_MODEL", backup_model or "")
    endpoints = {"primary": _SyncEndpoint(), "backup": _SyncEndpoint()}
    monkeypatch.setattr(
        llm_providers,
        "get_sync_client",
        lambda provider, **_kwargs: endpoints[provider.slot].client,
    )
    llm_failover.reset_failover_state()
    return endpoints


def _json_call(**kwargs):
    return llm_failover.create_chat_completion(
        call_site="material",
        messages=[{"role": "user", "content": "synthetic"}],
        response_format={"type": "json_object"},
        **kwargs,
    )


@contextmanager
def _fake_session():
    yield MagicMock()


@pytest.mark.unit
def test_chapter_summary_task_requests_json_object_on_the_wire(monkeypatch):
    seen: dict = {}
    logger = MagicMock()
    response = llm_mod.LLMResponse(
        content='{"summary": "这是一个完全合成的章节摘要。它只用于验证任务是否把 JSON 对象格式约束传到模型请求，不包含任何真实作品内容。为了通过现有长度校验，这里再补充一段无意义的测试文字。"}',
        usage={},
        model="test-model",
        finish_reason="stop",
    )

    monkeypatch.setattr(prefect_decorators, "get_run_logger", lambda: logger)
    monkeypatch.setattr(chapter_mod, "get_run_logger", lambda: logger)
    monkeypatch.setattr(chapter_mod, "get_db_session", _fake_session)
    monkeypatch.setattr(chapter_mod, "create_chapter_summary_prompt", lambda: "return JSON")
    monkeypatch.setattr(
        chapter_mod.ChaptersService,
        "get_chapter_core_fields",
        lambda *_args: {"content": "synthetic chapter", "title": "title", "number": 1},
    )

    def call_material_llm(**kwargs):
        seen.update(kwargs)
        return response

    monkeypatch.setattr(chapter_mod, "call_material_llm", call_material_llm)
    monkeypatch.setattr(
        chapter_mod,
        "get_material_llm_client",
        lambda: llm_mod.MaterialLLMClient.__new__(llm_mod.MaterialLLMClient),
    )

    result = chapter_mod.generate_chapter_summary_task.fn(31)

    assert result["chapter_id"] == 31
    assert seen["response_format"] == {"type": "json_object"}


@pytest.mark.unit
def test_deepseek_json_request_reaches_sdk_unchanged(monkeypatch):
    endpoints = _configure(monkeypatch, primary="deepseek", primary_model="deepseek-flash")

    _response, provider = _json_call()

    assert provider.slot == "primary"
    assert endpoints["primary"].calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.unit
def test_json_request_skips_unsupported_primary_for_eligible_backup(monkeypatch):
    endpoints = _configure(
        monkeypatch,
        primary="minimax",
        primary_model="MiniMax-M3.1-Flash",
        backup="deepseek",
        backup_model="deepseek-flash",
    )

    _response, provider = _json_call()

    assert provider.slot == "backup"
    assert endpoints["primary"].calls == []
    assert endpoints["backup"].calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.unit
def test_json_request_does_not_fall_back_to_unsupported_backup(monkeypatch):
    endpoints = _configure(
        monkeypatch,
        primary="deepseek",
        primary_model="deepseek-flash",
        backup="minimax",
        backup_model="MiniMax-M3.1-Flash",
    )
    failure = _status_error(503)
    endpoints["primary"].script = [failure]

    with pytest.raises(openai.InternalServerError) as raised:
        _json_call()

    assert raised.value is failure
    assert endpoints["backup"].calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("primary", "primary_model"),
    [
        ("minimax", "MiniMax-M3.1-Flash"),
        ("deepseek", "unverified-deepseek-model"),
    ],
)
def test_json_request_fails_before_sdk_and_maps_to_fatal_material_error(
    monkeypatch,
    primary,
    primary_model,
):
    endpoints = _configure(monkeypatch, primary=primary, primary_model=primary_model)
    meter = MagicMock()
    monkeypatch.setattr(llm_mod, "_meter_material_call", meter)
    client = llm_mod.MaterialLLMClient.__new__(llm_mod.MaterialLLMClient)
    client.max_tokens = 100
    client.temperature = 0.2

    with pytest.raises(LLMNonRetryableError) as raised:
        client.chat_completion(
            [{"role": "user", "content": "synthetic"}],
            response_format={"type": "json_object"},
        )

    assert raised.value.error_code == ErrorCode.MATERIAL_LLM_UNAVAILABLE
    assert endpoints["primary"].calls == []
    meter.assert_not_called()


@pytest.mark.unit
def test_json_request_fails_over_between_two_eligible_slots(monkeypatch):
    endpoints = _configure(
        monkeypatch,
        primary="deepseek",
        primary_model="deepseek-flash",
        backup="deepseek",
        backup_model="deepseek-flash",
    )
    endpoints["primary"].script = [_status_error(503)]

    _response, provider = _json_call(max_tokens=87)

    assert provider.slot == "backup"
    assert endpoints["primary"].calls[0]["response_format"] == {"type": "json_object"}
    assert endpoints["backup"].calls[0]["response_format"] == {"type": "json_object"}
    assert endpoints["backup"].calls[0]["max_tokens"] == 87


@pytest.mark.unit
def test_non_json_requests_keep_original_provider_order(monkeypatch):
    endpoints = _configure(
        monkeypatch,
        primary="minimax",
        primary_model="MiniMax-M3.1-Flash",
        backup="deepseek",
        backup_model="deepseek-flash",
    )

    _response, provider = llm_failover.create_chat_completion(
        call_site="material",
        messages=[{"role": "user", "content": "synthetic"}],
    )

    assert provider.slot == "primary"
    assert "response_format" not in endpoints["primary"].calls[0]
    assert endpoints["backup"].calls == []


@pytest.mark.unit
def test_selected_backup_model_and_usage_are_metered_once(monkeypatch):
    endpoints = _configure(
        monkeypatch,
        primary="minimax",
        primary_model="MiniMax-M3.1-Flash",
        backup="deepseek",
        backup_model="deepseek-flash",
    )
    sdk_response = _ok(model="deepseek-wire-response")
    endpoints["backup"].script = [sdk_response]
    meter = MagicMock()
    monkeypatch.setattr(llm_mod, "_meter_material_call", meter)
    client = llm_mod.MaterialLLMClient.__new__(llm_mod.MaterialLLMClient)
    client.max_tokens = 100
    client.temperature = 0.2

    response = client.chat_completion(
        [{"role": "user", "content": "synthetic"}],
        response_format={"type": "json_object"},
    )

    assert response.model == "deepseek-wire-response"
    meter.assert_called_once()
    assert meter.call_args.kwargs["model"] == "deepseek-flash"
    assert meter.call_args.kwargs["usage"] is sdk_response.usage
    assert endpoints["primary"].calls == []


@pytest.mark.unit
def test_json_format_survives_real_sdk_serialization_and_budget_guard(monkeypatch):
    from services.usage import model_call_guard

    _configure(monkeypatch, primary="deepseek", primary_model="deepseek-flash")
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "synthetic-response",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": '{"summary":"synthetic"}'},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            },
        )

    reserve = MagicMock(return_value=None)
    settle = MagicMock()
    monkeypatch.setattr(model_call_guard, "reserve_model_call", reserve)
    monkeypatch.setattr(model_call_guard, "settle_model_call", settle)
    with openai.OpenAI(
        api_key="synthetic-only",
        base_url="https://llm.test/v1",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
    ) as sdk_client:
        monkeypatch.setattr(
            llm_providers,
            "get_sync_client",
            lambda *_args, **_kwargs: model_call_guard.install_sync_cost_guard(sdk_client),
        )
        response, _provider = _json_call()

    assert len(requests) == 1
    assert requests[0]["model"] == "deepseek-flash"
    assert requests[0]["response_format"] == {"type": "json_object"}
    reserve.assert_called_once()
    assert reserve.call_args.args[0]["response_format"] == {"type": "json_object"}
    settle.assert_called_once_with(None, response.usage)
