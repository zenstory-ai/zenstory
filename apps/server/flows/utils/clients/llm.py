"""Chat model client for material and Prefect flows (primary/backup failover)."""

import json
from dataclasses import dataclass
from typing import Any

from agent.core import llm_failover, llm_providers
from config.material_settings import material_settings as settings
from core.error_codes import ErrorCode
from flows.utils.helpers.exceptions import LLMAPIError, LLMNonRetryableError, LLMOutputError
from flows.utils.helpers.logging import get_logger, log_error_with_context

# HTTP statuses that mean the provider account itself is unusable (bad key,
# no balance, forbidden, unknown model): every further call fails the same way.
_ACCOUNT_FAILURE_STATUSES = {401, 402, 403, 404}
# Other 4xx (context too long, malformed request) fail the same way on retry.
_NON_RETRYABLE_STATUSES = {400, 413, 422}
_SAFE_FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_calls", "function_call"})


def _safe_finish_reason(finish_reason: Any) -> str:
    """Return only provider finish reasons that are safe to expose in metadata."""
    if isinstance(finish_reason, str) and finish_reason in _SAFE_FINISH_REASONS:
        return finish_reason
    return "unknown"


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

    if isinstance(error, llm_failover.JSONResponseFormatUnavailableError):
        return LLMNonRetryableError(
            "当前模型暂不支持所需的 JSON 输出格式",
            ErrorCode.MATERIAL_LLM_UNAVAILABLE,
        )
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


class MaterialLLMClient:
    """OpenAI-compatible chat client for material flows.

    Each call goes to the primary provider and, when it is unavailable, to the
    backup (``agent/core/llm_failover.py``). Failover state is shared with the
    API through Redis when the worker has ``REDIS_URL``.
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ):
        self.max_tokens = max_tokens or settings.LLM_MAX_TOKENS
        self.temperature = temperature or settings.LLM_TEMPERATURE
        try:
            self.model = llm_providers.primary_provider().model
        except ValueError as error:
            raise LLMNonRetryableError(
                f"模型凭证未配置：{error}", ErrorCode.MATERIAL_LLM_UNAVAILABLE
            ) from error

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
        """调用聊天补全接口（主供应商不可用时用备用供应商）。

        usage_novel_id / usage_chapter_id：这次调用所处理的小说或章节，用来把
        用量记到小说所有者名下（llm_usage_event）。两者都不传时不记账。
        """
        logger = get_logger(__name__)

        try:
            return self._call_model(
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
            log_error_with_context(e, "模型 API 调用失败", logger)
            raise _classify_llm_exception(e) from e

    def _call_model(
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
        """调用 OpenAI 兼容接口。"""
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
            # Explicit bounds: the SDK default is a 600s timeout; Prefect task
            # retries add their own attempts on top of these.
            response, provider = llm_failover.create_chat_completion(
                call_site="material",
                timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
                max_retries=settings.LLM_SDK_MAX_RETRIES,
                messages=full_messages,
                max_tokens=max_tokens or self.max_tokens,
                temperature=temperature or self.temperature,
                **kwargs,
            )

        content = response.choices[0].message.content or ""
        usage = response.usage.model_dump() if response.usage else {}

        logger.info(f"模型 API 调用成功（{provider.name} / {provider.model}），tokens: {usage}")
        # 每次真实调用都记账（含 Prefect 重试、JSON 解析失败后的重跑），
        # 按小说所有者计费；记账失败只记日志，不影响拆解。
        _meter_material_call(
            model=provider.model,
            usage=response.usage,
            novel_id=usage_novel_id,
            chapter_id=usage_chapter_id,
        )

        return LLMResponse(
            content=content,
            usage=usage,
            model=response.model or provider.model,
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
                    logger.warning(
                        "LLM 响应 JSON 非法，已用 json_repair 修复 "
                        f"(finish_reason={_safe_finish_reason(response.finish_reason)})"
                    )
                    return repaired

        raise LLMOutputError(
            "无法从响应中提取有效的 JSON "
            f"(failure=invalid_json, finish_reason={_safe_finish_reason(response.finish_reason)}, "
            f"content_length={len(content)})"
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


_material_llm_client: MaterialLLMClient | None = None


def get_material_llm_client() -> MaterialLLMClient:
    """获取全局素材模型客户端实例。"""
    global _material_llm_client
    if _material_llm_client is None:
        _material_llm_client = MaterialLLMClient()
    return _material_llm_client


def call_material_llm(
    messages: list[dict[str, str]],
    system_prompt: str | None = None,
    **kwargs,
) -> LLMResponse:
    """便捷的素材模型调用函数。

    计费归属：传 usage_novel_id 或 usage_chapter_id（见 chat_completion）。
    """
    client = get_material_llm_client()
    return client.chat_completion(messages, system_prompt, **kwargs)
