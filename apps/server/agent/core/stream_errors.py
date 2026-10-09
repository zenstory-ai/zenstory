"""Agent 串流错误的对外契约：固定文案 + ERR_ 错误码 + retryable / refundable。

运行中的异常（上游 LLM 429/5xx/超时、上下文超长、数据库错误……）的原始
``str(exc)`` 可能含英文 SDK 报错、SQL 语句与参数，绝不能直接推给前端。这里把
异常归类成固定的错误码，前端按错误码显示 i18n 文案；完整异常只写服务器日志。

- retryable：暂时性故障（上游限流、5xx、超时、连接失败）为 True，前端显示重试。
- refundable：本次失败是否属于「平台侧故障」、可以退还对话额度。api/agent.py
  另外还会看本轮是否已有实质产出（已串流正文 / 已写入文件），有产出一律不退。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.error_codes import ErrorCode, get_error_message
from utils.logger import log_with_context

TOOL_FAILURE_ERROR_TYPE = "ToolFailureCircuitOpen"
MODEL_CALL_LIMIT_ERROR_TYPE = "ModelCallBudgetExhausted"

_CONTEXT_TOO_LONG_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "too many tokens",
    "prompt is too long",
    "reduce the length",
)


@dataclass(frozen=True)
class StreamErrorInfo:
    """一次串流错误对外暴露的全部信息。"""

    code: str
    retryable: bool
    refundable: bool

    @property
    def message(self) -> str:
        return error_message_for(self.code)

    def as_event_data(self, *, error_type: str | None = None, **extra: Any) -> dict[str, Any]:
        """workflow ERROR 事件的 data（StreamAdapter 据此生成 SSE error 帧）。"""
        data: dict[str, Any] = {
            "error": self.message,
            "code": self.code,
            "retryable": self.retryable,
            "refundable": self.refundable,
        }
        if error_type:
            data["error_type"] = error_type
        data.update(extra)
        return data


def error_message_for(code: str | None) -> str:
    """固定的中文兜底文案（前端优先按错误码取 i18n）。"""
    message = get_error_message(code or "", "zh")
    if not code or message == code:
        return get_error_message(ErrorCode.AGENT_RUN_FAILED, "zh")
    return message


def _status_code_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _looks_like_context_overflow(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _CONTEXT_TOO_LONG_MARKERS)


def _is_transport_failure(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    try:
        import httpx
    except Exception:  # pragma: no cover - httpx 是硬依赖
        httpx = None  # type: ignore[assignment]
    if httpx is not None and isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    try:
        import openai
    except Exception:  # pragma: no cover - openai 是硬依赖
        return False
    return isinstance(exc, openai.APITimeoutError | openai.APIConnectionError)


def classify_stream_exception(exc: BaseException) -> StreamErrorInfo:
    """把运行期异常映射成对外错误码；未知异常一律按内部错误处理。"""
    status = _status_code_of(exc)
    # 平台自己抛出的 APIException 已经带着对外错误码（例如写作配置缺失时的
    # ERR_SERVICE_UNAVAILABLE），沿用它，而不是按状态码误判成上游 LLM 故障。
    from core.error_handler import APIException

    if isinstance(exc, APIException):
        code = getattr(exc, "error_code", None)
        if isinstance(code, str) and code.startswith("ERR_"):
            if code == ErrorCode.QUOTA_AI_DAILY_COST_EXCEEDED:
                # The hidden free-user safeguard rejects the model call before
                # transport, so the already-reserved message was never served.
                return StreamErrorInfo(code, False, True)
            if code == ErrorCode.AI_COST_BUDGET_UNAVAILABLE:
                # A budget-store failure is a platform failure before transport.
                return StreamErrorInfo(code, True, True)
            server_side = status is None or status >= 500
            return StreamErrorInfo(code, server_side, server_side)
    if status == 429 or type(exc).__name__ == "RateLimitError":
        return StreamErrorInfo(ErrorCode.AGENT_UPSTREAM_RATE_LIMITED, True, True)
    if status is not None and status >= 500:
        return StreamErrorInfo(ErrorCode.AGENT_UPSTREAM_UNAVAILABLE, True, True)
    if _is_transport_failure(exc):
        return StreamErrorInfo(ErrorCode.AGENT_UPSTREAM_UNAVAILABLE, True, True)
    if status in {400, 413, 422} and _looks_like_context_overflow(exc):
        return StreamErrorInfo(ErrorCode.AGENT_CONTEXT_TOO_LONG, False, True)
    return StreamErrorInfo(ErrorCode.AGENT_RUN_FAILED, True, True)


def stream_error_from_event_data(data: Any) -> StreamErrorInfo:
    """从 workflow ERROR 事件的 data 还原错误信息（缺字段时按内部错误兜底）。"""
    payload = data if isinstance(data, dict) else {}
    code = payload.get("code")
    if not isinstance(code, str) or not code.startswith("ERR_"):
        if payload.get("error_type") == TOOL_FAILURE_ERROR_TYPE:
            code = ErrorCode.AGENT_TOOL_FAILURE_LIMIT
        else:
            code = ErrorCode.AGENT_RUN_FAILED
    retryable = payload.get("retryable")
    refundable = payload.get("refundable")
    defaults = _DEFAULT_FLAGS.get(code, (True, True))
    return StreamErrorInfo(
        code=code,
        retryable=retryable if isinstance(retryable, bool) else defaults[0],
        refundable=refundable if isinstance(refundable, bool) else defaults[1],
    )


# code -> (retryable, refundable) 的默认值。熔断是模型/用户行为造成的停止，
# 不是平台故障：不可重试、不退款。
_DEFAULT_FLAGS: dict[str, tuple[bool, bool]] = {
    ErrorCode.AGENT_TOOL_FAILURE_LIMIT: (False, False),
    ErrorCode.AGENT_CONTEXT_TOO_LONG: (False, True),
    ErrorCode.AGENT_RUN_TIMEOUT: (False, True),
    ErrorCode.AGENT_MODEL_CALL_LIMIT: (False, True),
}


def tool_failure_error() -> StreamErrorInfo:
    return StreamErrorInfo(ErrorCode.AGENT_TOOL_FAILURE_LIMIT, False, False)


def model_call_limit_error() -> StreamErrorInfo:
    return StreamErrorInfo(ErrorCode.AGENT_MODEL_CALL_LIMIT, False, True)


def output_truncated_error() -> StreamErrorInfo:
    """最后一次响应只有思考就撞上输出上限：与同类「回复继续」的停止一样不给重试按钮
    （重试会重发原请求、从头再做一遍）；是否退还交给 stream_billing 的既有规则
    （本轮无产出退还，有产出照常计费）。"""
    return StreamErrorInfo(ErrorCode.AGENT_OUTPUT_TRUNCATED, False, True)


def run_timeout_error() -> StreamErrorInfo:
    return StreamErrorInfo(ErrorCode.AGENT_RUN_TIMEOUT, False, True)


def log_stream_exception(
    logger: logging.Logger,
    message: str,
    exc: BaseException,
    info: StreamErrorInfo,
    **fields: Any,
) -> None:
    """完整异常（含堆栈）只写服务器日志；request_id / agent_run_id 由日志上下文自动带上。"""
    log_with_context(
        logger,
        logging.ERROR,
        message,
        error=str(exc),
        error_type=type(exc).__name__,
        error_code=info.code,
        retryable=info.retryable,
        upstream_status=_status_code_of(exc),
        exc_info=exc,
        **fields,
    )
