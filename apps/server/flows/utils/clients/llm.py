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


def _repair_json_object(json_str: str) -> dict[str, Any] | None:
    """用 json_repair 修复非法 JSON；只返回非空 dict，其他情况返回 None。"""
    try:
        from json_repair import loads as json_repair_loads

        repaired = json_repair_loads(json_str, skip_json_loads=True)
    except Exception:
        return None
    return repaired if isinstance(repaired, dict) and repaired else None


def _classify_llm_exception(error: Exception) -> LLMAPIError:
    """Wrap an OpenAI SDK error; only transient failures stay retryable."""
    from core.error_handler import APIException
    from services.usage.cost_budget import COST_LIMIT_CODE, UNAVAILABLE_CODE
    if isinstance(error, APIException) and error.error_code in {COST_LIMIT_CODE, UNAVAILABLE_CODE}:
        return LLMNonRetryableError("今日免费模型预算已用尽或暂不可用", error.error_code)
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


def _meter_material_call(
    *,
    model: str,
    usage: Any,
    novel_id: int | None,
    chapter_id: int | None,
) -> None:
    if novel_id is None and chapter_id is None:
        return
    from flows.database_session import create_prefect_session
    from services.usage.llm_usage_service import record_material_llm_usage

    record_material_llm_usage(
        model=model,
        usage=usage,
        novel_id=novel_id,
        chapter_id=chapter_id,
        session_factory=create_prefect_session,
    )


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
        from services.usage.model_call_guard import install_sync_cost_guard
        install_sync_cost_guard(self.client)

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        usage_novel_id: int | None = None,
        usage_chapter_id: int | None = None,
        **kwargs,
    ) -> LLMResponse:
        """调用 DeepSeek OpenAI-compatible 聊天补全接口。

        usage_novel_id / usage_chapter_id：这次调用所处理的小说或章节，用来把
        用量记到小说所有者名下（llm_usage_event）。两者都不传时不记账。
        """
        logger = get_logger(__name__)

        try:
            return self._call_deepseek(
                messages,
                system_prompt,
                temperature,
                max_tokens,
                logger,
                usage_novel_id=usage_novel_id,
                usage_chapter_id=usage_chapter_id,
                **kwargs,
            )
        except Exception as e:
            log_error_with_context(e, "DeepSeek API 调用失败", logger)
            raise _classify_llm_exception(e) from e

    def _call_deepseek(
        self,
        messages,
        system_prompt,
        temperature,
        max_tokens,
        logger,
        *,
        usage_novel_id: int | None = None,
        usage_chapter_id: int | None = None,
        **kwargs,
    ) -> LLMResponse:
        """调用 DeepSeek OpenAI-compatible API。"""
        logger.info(f"调用 DeepSeek API: {self.model}")

        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        from flows.database_session import create_prefect_session
        from services.usage.cost_budget import budget_attribution
        from services.usage.llm_usage_service import LLMUsageAttribution, resolve_material_owner
        owner = None
        if usage_novel_id is not None or usage_chapter_id is not None:
            with create_prefect_session() as owner_session:
                owner, _ = resolve_material_owner(owner_session, novel_id=usage_novel_id, chapter_id=usage_chapter_id)
        with budget_attribution(LLMUsageAttribution(user_id=owner, source="material")):
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
        # 每次真实调用都记账（含 Prefect 重试、JSON 解析失败后的重跑），
        # 按小说所有者计费；记账失败只记日志，不影响拆解。
        _meter_material_call(
            model=response.model or self.model,
            usage=response.usage,
            novel_id=usage_novel_id,
            chapter_id=usage_chapter_id,
        )

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

            # 模型常在中文描述里直接写英文双引号（如 "以"禹韭"为名"），整段 JSON
            # 因此非法、整章/整个角色被丢弃。严格解析都失败后用 json_repair 修一次，
            # 只接受非空对象。被截断的输出（finish_reason=length）不修：补全出来的
            # 内容会悄悄缺一截，宁可按失败处理。
            if response.finish_reason != "length":
                repaired = _repair_json_object(json_str)
                if repaired is not None:
                    logger = get_logger(__name__)
                    logger.warning(f"LLM 响应 JSON 非法，已用 json_repair 修复 (finish_reason={response.finish_reason})")
                    return repaired

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
    """便捷的 DeepSeek API 调用函数。

    计费归属：传 usage_novel_id 或 usage_chapter_id（见 chat_completion）。
    """
    client = get_deepseek_client()
    return client.chat_completion(messages, system_prompt, **kwargs)
