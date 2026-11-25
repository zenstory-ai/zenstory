"""
自定义异常类
"""


class DeepNovelException(Exception):
    """基础异常类"""
    pass


class LLMAPIError(DeepNovelException):
    """LLM API 调用错误"""

    def __init__(
        self,
        message: str,
        *,
        failure_reason: str | None = None,
        http_status: int | None = None,
        provider_error_code: int | None = None,
    ):
        super().__init__(message)
        self.failure_reason = failure_reason
        self.http_status = http_status
        self.provider_error_code = provider_error_code

    def __reduce__(self):
        return (
            _rebuild_llm_api_error,
            (
                self.__class__,
                str(self),
                self.failure_reason,
                self.http_status,
                self.provider_error_code,
            ),
        )


def _rebuild_llm_api_error(
    cls: type[LLMAPIError],
    message: str,
    failure_reason: str | None,
    http_status: int | None,
    provider_error_code: int | None,
) -> LLMAPIError:
    return cls(
        message,
        failure_reason=failure_reason,
        http_status=http_status,
        provider_error_code=provider_error_code,
    )


class ValidationError(DeepNovelException):
    """数据验证错误"""
    pass


class LLMNonRetryableError(LLMAPIError):
    """
    LLM 错误：重试也不会成功（鉴权失败、余额不足、请求非法如上下文超长、
    输出无法解析）。error_code 为 ERR_MATERIAL_LLM_UNAVAILABLE 时表示
    账号级故障，整次拆解应立即失败而不是逐章空转。
    """

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        *,
        failure_reason: str | None = None,
        http_status: int | None = None,
        provider_error_code: int | None = None,
    ):
        super().__init__(
            message,
            failure_reason=failure_reason,
            http_status=http_status,
            provider_error_code=provider_error_code,
        )
        self.error_code = error_code

    def __reduce__(self):
        return (
            _rebuild_llm_non_retryable_error,
            (
                self.__class__,
                str(self),
                self.error_code,
                self.failure_reason,
                self.http_status,
                self.provider_error_code,
            ),
        )


def _rebuild_llm_non_retryable_error(
    cls: type[LLMNonRetryableError],
    message: str,
    error_code: str | None,
    failure_reason: str | None,
    http_status: int | None,
    provider_error_code: int | None,
) -> LLMNonRetryableError:
    return cls(
        message,
        error_code,
        failure_reason=failure_reason,
        http_status=http_status,
        provider_error_code=provider_error_code,
    )


class LLMOutputError(LLMNonRetryableError):
    """LLM 输出无法解析为预期的 JSON。"""

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        *,
        failure_reason: str | None = "invalid_llm_output",
        http_status: int | None = None,
        provider_error_code: int | None = None,
    ):
        super().__init__(
            message,
            error_code,
            failure_reason=failure_reason,
            http_status=http_status,
            provider_error_code=provider_error_code,
        )


def is_retryable_exception(exc: BaseException | None) -> bool:
    """任务级重试判定：不可重试的 LLM 错误与数据校验错误直接失败。"""
    if exc is None:
        return True
    from services.material.job_errors import MaterialPipelineError

    return not isinstance(exc, (LLMNonRetryableError, ValidationError, MaterialPipelineError))


def is_llm_fatal_stop(exc: BaseException | None) -> bool:
    """致命非重试终止（供应商鉴权/余额故障、试用额度上限、每日成本熔断）：必须立即中止。"""
    from core.error_codes import ErrorCode

    fatal_codes = {
        ErrorCode.MATERIAL_LLM_UNAVAILABLE,
        ErrorCode.MATERIAL_TRIAL_COST_LIMIT,
        ErrorCode.QUOTA_AI_DAILY_COST_EXCEEDED,
        ErrorCode.AI_COST_BUDGET_UNAVAILABLE,
        ErrorCode.MATERIAL_TRIAL_LIMIT_INVALID,
    }
    return isinstance(exc, LLMNonRetryableError) and exc.error_code in fatal_codes


def is_llm_service_failure(exc: BaseException | None) -> bool:
    """调用 LLM 本身失败（平台账号故障，或重试耗尽后的超时/连接/5xx/429）。"""
    from core.error_codes import ErrorCode

    if isinstance(exc, LLMNonRetryableError):
        return exc.error_code == ErrorCode.MATERIAL_LLM_UNAVAILABLE
    return isinstance(exc, LLMAPIError)
