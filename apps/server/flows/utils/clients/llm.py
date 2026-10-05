"""DeepSeek LLM client for material and Prefect flows."""

import json
import os
from dataclasses import dataclass
from typing import Any

from agent.core.deepseek_client import DEEPSEEK_CHAT_MODEL, DEFAULT_DEEPSEEK_BASE_URL
from config.material_settings import material_settings as settings
from core.error_codes import ErrorCode
from flows.utils.helpers.exceptions import LLMAPIError, LLMNonRetryableError, LLMOutputError
from flows.utils.helpers.logging import get_logger, log_error_with_context

# HTTP statuses that mean the DeepSeek account itself is unusable (bad key,
# no balance, forbidden, unknown model): every further call fails the same way.
_ACCOUNT_FAILURE_STATUSES = {401, 402, 403, 404}
# Other 4xx (context too long, malformed request) fail the same way on retry.
_NON_RETRYABLE_STATUSES = {400, 413, 422}
_ERROR_PREVIEW_CHARS = 500


def _classify_llm_exception(error: Exception) -> LLMAPIError:
    """Wrap an OpenAI SDK error; only transient failures stay retryable."""
    status_code = getattr(error, "status_code", None)
    message = f"API 调用失败: {type(error).__name__}: {error}"
    if status_code in _ACCOUNT_FAILURE_STATUSES:
        return LLMNonRetryableError(message, ErrorCode.MATERIAL_LLM_UNAVAILABLE)
    if status_code in _NON_RETRYABLE_STATUSES:
        return LLMNonRetryableError(message)
    return LLMAPIError(message)


@dataclass
class LLMResponse:
    """LLM API 响应数据类"""

    content: str
    usage: dict[str, int]
    model: str
    finish_reason: str


class DeepSeekClient:
    """DeepSeek OpenAI-compatible LLM client for material flows."""

    def __init__(
        self,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ):
        self.max_tokens = max_tokens or settings.LLM_MAX_TOKENS
        self.temperature = temperature or settings.LLM_TEMPERATURE
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL
        self.model = DEEPSEEK_CHAT_MODEL

        if not self.api_key:
            raise LLMNonRetryableError(
                "DEEPSEEK_API_KEY 环境变量未设置", ErrorCode.MATERIAL_LLM_UNAVAILABLE
            )

        from openai import OpenAI

        # Explicit bounds: the SDK default is a 600s timeout; Prefect task
        # retries add their own attempts on top of these.
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            max_retries=settings.LLM_SDK_MAX_RETRIES,
        )

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> LLMResponse:
        """调用 DeepSeek OpenAI-compatible 聊天补全接口。"""
        logger = get_logger(__name__)

        try:
            return self._call_deepseek(messages, system_prompt, temperature, max_tokens, logger, **kwargs)
        except Exception as e:
            log_error_with_context(e, "DeepSeek API 调用失败", logger)
            raise _classify_llm_exception(e) from e

    def _call_deepseek(self, messages, system_prompt, temperature, max_tokens, logger, **kwargs) -> LLMResponse:
        """调用 DeepSeek OpenAI-compatible API。"""
        logger.info(f"调用 DeepSeek API: {self.model}")

        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature or self.temperature,
            **kwargs,
        )

        content = response.choices[0].message.content or ""
        usage = response.usage.model_dump() if response.usage else {}

        logger.info(f"DeepSeek API 调用成功，tokens: {usage}")

        return LLMResponse(
            content=content,
            usage=usage,
            model=response.model,
            finish_reason=response.choices[0].finish_reason,
        )

    def extract_json_from_response(self, response: LLMResponse) -> dict[str, Any]:
        """
        从响应中提取 JSON 数据

        Args:
            response: LLM 响应对象

        Returns:
            解析后的 JSON 数据
        """
        content = response.content.strip()

        # strict=False: models put literal newlines inside long strings (e.g. a
        # multi-paragraph synopsis); JSON forbids them only in strict mode.
        # 尝试直接解析
        try:
            return json.loads(content, strict=False)
        except json.JSONDecodeError:
            pass

        # 尝试提取代码块中的 JSON
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            if end != -1:
                json_str = content[start:end].strip()
                try:
                    return json.loads(json_str, strict=False)
                except json.JSONDecodeError:
                    pass

        # 尝试提取 {} 包围的 JSON
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            json_str = content[start:end]
            try:
                return json.loads(json_str, strict=False)
            except json.JSONDecodeError:
                pass

        raise LLMOutputError(
            f"无法从响应中提取有效的 JSON (finish_reason={response.finish_reason}): "
            f"{content[:_ERROR_PREVIEW_CHARS]}"
        )

    def validate_response_format(
        self,
        response: LLMResponse,
        required_fields: list[str],
    ) -> bool:
        """
        验证响应格式

        Args:
            response: LLM 响应对象
            required_fields: 必需字段列表

        Returns:
            是否符合格式要求
        """
        logger = get_logger(__name__)
        try:
            data = self.extract_json_from_response(response)

            # 检查必需字段
            for field in required_fields:
                if field not in data:
                    logger.warning(f"响应缺少必需字段: {field}")
                    return False

            return True

        except Exception as e:
            log_error_with_context(e, "响应格式验证失败", logger)
            return False


_deepseek_client: DeepSeekClient | None = None


def get_deepseek_client() -> DeepSeekClient:
    """获取全局 DeepSeek 客户端实例。"""
    global _deepseek_client
    if _deepseek_client is None:
        _deepseek_client = DeepSeekClient()
    return _deepseek_client


def call_deepseek_api(
    messages: list[dict[str, str]],
    system_prompt: str | None = None,
    **kwargs,
) -> LLMResponse:
    """便捷的 DeepSeek API 调用函数。"""
    client = get_deepseek_client()
    return client.chat_completion(messages, system_prompt, **kwargs)
