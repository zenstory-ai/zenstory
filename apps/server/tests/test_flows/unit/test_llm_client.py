from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from flows.utils.clients import llm as llm_mod
from flows.utils.helpers.exceptions import LLMAPIError


class TestLLMClientHelpers:
    def _new_client(self):
        return llm_mod.DeepSeekClient.__new__(llm_mod.DeepSeekClient)

    def test_extract_json_from_plain_json(self):
        client = self._new_client()
        response = llm_mod.LLMResponse(content='{"a": 1}', usage={}, model="m", finish_reason="stop")
        assert client.extract_json_from_response(response) == {"a": 1}

    def test_extract_json_from_markdown_codeblock(self):
        client = self._new_client()
        content = """Here is result:\n```json\n{\"a\": 2, \"b\": 3}\n```"""
        response = llm_mod.LLMResponse(content=content, usage={}, model="m", finish_reason="stop")
        assert client.extract_json_from_response(response) == {"a": 2, "b": 3}

    def test_extract_json_from_embedded_object(self):
        client = self._new_client()
        response = llm_mod.LLMResponse(content="prefix {\"ok\": true} suffix", usage={}, model="m", finish_reason="stop")
        assert client.extract_json_from_response(response) == {"ok": True}

    def test_extract_json_repairs_unescaped_inner_quotes(self):
        # 生产实例：模型在中文描述里写英文双引号，整段 JSON 非法，整章角色曾被丢弃。
        client = self._new_client()
        content = (
            '```json\n{\n  "characters": [\n    {\n      "name": "斐潜",\n'
            '      "chapter_description": "本章中斐潜向枣祗介绍青草名为"禹韭"，并请其试种。",\n'
            '      "first_appearance_line": 1\n    },\n'
            '    {\n      "name": "枣祗",\n      "chapter_description": "负责屯田。",\n'
            '      "first_appearance_line": 3\n    }\n  ]\n}\n```'
        )
        response = llm_mod.LLMResponse(content=content, usage={}, model="m", finish_reason="stop")
        data = client.extract_json_from_response(response)
        assert [c["name"] for c in data["characters"]] == ["斐潜", "枣祗"]
        assert '"禹韭"' in data["characters"][0]["chapter_description"]
        assert data["characters"][1]["first_appearance_line"] == 3

    def test_extract_json_does_not_repair_truncated_output(self):
        client = self._new_client()
        content = '{"characters": [{"name": "斐潜", "chapter_description": "以"禹韭"为名'
        response = llm_mod.LLMResponse(content=content, usage={}, model="m", finish_reason="length")
        with pytest.raises(LLMAPIError):
            client.extract_json_from_response(response)

    def test_extract_json_raises_when_no_valid_json(self):
        client = self._new_client()
        response = llm_mod.LLMResponse(content="not a json", usage={}, model="m", finish_reason="stop")
        with pytest.raises(LLMAPIError):
            client.extract_json_from_response(response)

    def test_validate_response_format_missing_field_returns_false(self, monkeypatch):
        client = self._new_client()
        logger = MagicMock()
        monkeypatch.setattr(llm_mod, "get_logger", lambda _name: logger)

        response = llm_mod.LLMResponse(content='{"a": 1}', usage={}, model="m", finish_reason="stop")
        ok = client.validate_response_format(response, ["a", "b"])

        assert ok is False
        assert logger.warning.call_count >= 1

    def test_validate_response_format_invalid_json_logs_error(self, monkeypatch):
        client = self._new_client()
        logger = MagicMock()
        log_error = MagicMock()
        monkeypatch.setattr(llm_mod, "get_logger", lambda _name: logger)
        monkeypatch.setattr(llm_mod, "log_error_with_context", log_error)

        response = llm_mod.LLMResponse(content="invalid", usage={}, model="m", finish_reason="stop")
        ok = client.validate_response_format(response, ["a"])

        assert ok is False
        assert log_error.call_count == 1

    def test_deepseek_client_requires_key(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        with pytest.raises(LLMAPIError, match="DEEPSEEK_API_KEY"):
            llm_mod.DeepSeekClient()

    def test_deepseek_client_ignores_material_key_override(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("MATERIAL_" + "DEEPSEEK_API_KEY", "material-only-key")

        with pytest.raises(LLMAPIError, match="DEEPSEEK_API_KEY"):
            llm_mod.DeepSeekClient()

    def test_deepseek_client_uses_global_key_and_base_url(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://deepseek.example/v1")

        client = llm_mod.DeepSeekClient()

        assert client.api_key == "test-key"
        assert client.base_url == "https://deepseek.example/v1"
        assert client.model == llm_mod.DEEPSEEK_CHAT_MODEL
