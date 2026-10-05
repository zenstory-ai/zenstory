"""
自定义异常类
"""


class DeepNovelException(Exception):
    """基础异常类"""
    pass


class LLMAPIError(DeepNovelException):
    """LLM API 调用错误"""
    pass


class ValidationError(DeepNovelException):
    """数据验证错误"""
    pass


class FileProcessingError(DeepNovelException):
    """文件处理错误"""
    pass


class DatabaseError(DeepNovelException):
    """数据库操作错误"""
    pass


class LLMNonRetryableError(LLMAPIError):
    """
    LLM 错误：重试也不会成功（鉴权失败、余额不足、请求非法如上下文超长、
    输出无法解析）。error_code 为 ERR_MATERIAL_LLM_UNAVAILABLE 时表示
    账号级故障，整次拆解应立即失败而不是逐章空转。
    """

    def __init__(self, message: str, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code

    def __reduce__(self):
        return (self.__class__, (str(self), self.error_code))


class LLMOutputError(LLMNonRetryableError):
    """LLM 输出无法解析为预期的 JSON。"""


def is_retryable_exception(exc: BaseException | None) -> bool:
    """任务级重试判定：不可重试的 LLM 错误与数据校验错误直接失败。"""
    if exc is None:
        return True
    from services.material.job_errors import MaterialPipelineError

    return not isinstance(exc, (LLMNonRetryableError, ValidationError, MaterialPipelineError))


def is_llm_account_failure(exc: BaseException | None) -> bool:
    """DeepSeek 账号级故障（401/402/403、未配置密钥）：继续调用只会全部失败。"""
    from core.error_codes import ErrorCode

    return (
        isinstance(exc, LLMNonRetryableError)
        and exc.error_code == ErrorCode.MATERIAL_LLM_UNAVAILABLE
    )


def is_llm_service_failure(exc: BaseException | None) -> bool:
    """调用 LLM 本身失败（账号故障，或重试耗尽后的超时/连接/5xx/429）。"""
    if is_llm_account_failure(exc):
        return True
    return isinstance(exc, LLMAPIError) and not isinstance(exc, LLMNonRetryableError)
