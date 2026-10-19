"""Editor utility endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from services.auth import get_current_active_user
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from middleware.rate_limit import require_user_rate_limit
from models import User
from services.features.natural_polish_service import (
    NaturalPolishFailure,
    is_noop_rewrite,
    natural_polish_service,
)
from services.first_work_service import observe_first_work_ai_request
from services.quota_service import quota_service
from utils.logger import get_logger, log_with_context
from utils.permission import verify_project_access

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/editor", tags=["editor"])

MAX_SELECTED_TEXT_LENGTH = 6000

# /natural-polish 会触发真实的远端 LLM 调用，成本按账号结算，
# 因此和 /agent/* 一样必须按登录用户限流（按 IP 限流换个代理就绕过了）。
# 单次润色比一轮完整对话便宜，配额取 /agent/suggest 的两倍。
NATURAL_POLISH_RATE_LIMIT_MAX_REQUESTS = 60
NATURAL_POLISH_RATE_LIMIT_WINDOW_SECONDS = 3600


class NaturalPolishRequest(BaseModel):
    """Request payload for natural polish."""

    project_id: str = Field(..., description="Project ID")
    selected_text: str = Field(..., description="Selected text to rewrite")
    metadata: dict[str, Any] = Field(default_factory=dict)


class NaturalPolishResponse(BaseModel):
    """Response payload for natural polish."""

    text: str
    model: str | None = None
    # True：改写和原文只差空白或引号体例，这次已退还额度，text 是原文。
    # 前端据此不进审阅，直接提示「这次不计入今日 AI 消息」；退还失败时保持 False，
    # 走正常审阅，避免提示与实际扣费不符。
    unchanged: bool = False


def _refund_ai_conversation(
    session: Session,
    user_id: str,
    period_start: datetime,
    *,
    reason: str,
) -> bool:
    """退还已预扣的 AI 对话额度（生成失败，或改写没有实质改动），失败只记日志不影响主流程。

    失败的事务可能让共享 session 处于 PendingRollback 状态，先复位再补偿，
    否则退款会静默失效、用户白扣一次额度。
    """
    import contextlib

    try:
        with contextlib.suppress(Exception):
            session.rollback()
        return quota_service.release_ai_conversation(
            session,
            user_id,
            period_start=period_start,
        )
    except Exception:
        log_with_context(
            logger,
            30,  # WARNING
            "Failed to refund AI conversation quota for natural polish",
            stage="refund",
            failure_category="refund_failed",
            refund_reason=reason,
        )
        return False


@router.post("/natural-polish", response_model=NaturalPolishResponse)
async def natural_polish(
    body: NaturalPolishRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
    accept_language: str | None = Header(None, alias="Accept-Language"),
    _rate_limit: int = Depends(
        require_user_rate_limit(
            "editor_natural_polish",
            NATURAL_POLISH_RATE_LIMIT_MAX_REQUESTS,
            NATURAL_POLISH_RATE_LIMIT_WINDOW_SECONDS,
        )
    ),
) -> NaturalPolishResponse:
    """Single-round natural polish (non-streaming)."""
    await verify_project_access(body.project_id, session, current_user)
    user_id = current_user.id

    if not body.selected_text.strip():
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            detail="selected_text cannot be empty.",
        )

    if len(body.selected_text) > MAX_SELECTED_TEXT_LENGTH:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            detail=f"selected_text must be <= {MAX_SELECTED_TEXT_LENGTH} characters.",
        )

    allowed, used, limit = quota_service.check_ai_conversation_quota(session, user_id)
    if not allowed:
        raise APIException(
            error_code=ErrorCode.QUOTA_AI_CONVERSATIONS_EXCEEDED,
            status_code=402,
            detail=f"AI conversation quota exceeded ({used}/{limit}). Please upgrade your plan.",
        )

    # 先扣额度再生成，避免并发绕过；生成失败时在下面补偿退还
    # （与 /agent/stream 的失败退款一致——原注释说"不退款，与 stream 对齐"，
    #  但 stream 早已改成失败退款，注释与实现已经脱节）。
    charged_period_start = quota_service.reserve_ai_conversation(
        session,
        user_id,
    )
    if charged_period_start is None:
        raise APIException(
            error_code=ErrorCode.QUOTA_AI_CONVERSATIONS_EXCEEDED,
            status_code=402,
            detail="AI conversation quota exceeded. Please upgrade your plan.",
        )

    # This explicit editor action is an AI request too; only an accepted
    # reservation counts. Observing it cannot interrupt generation/refunds.
    observe_first_work_ai_request(session, user_id, body.project_id)

    lang = (accept_language or "").split(",")[0].split("-")[0].strip().lower() or "zh"
    raw_file_type = body.metadata.get("current_file_type")
    file_type = raw_file_type if isinstance(raw_file_type, str) and raw_file_type else None

    try:
        result = await natural_polish_service.natural_polish(
            selected_text=body.selected_text,
            language=lang,
            file_type=file_type,
            user_id=user_id,
            project_id=body.project_id,
        )
    except APIException:
        refunded = _refund_ai_conversation(
            session,
            user_id,
            charged_period_start,
            reason="api_exception",
        )
        log_with_context(
            logger,
            30,  # WARNING
            "Natural polish request failed",
            stage="service",
            failure_category="api_exception",
            refund_succeeded=refunded,
        )
        raise
    except NaturalPolishFailure as exc:
        refunded = _refund_ai_conversation(
            session,
            user_id,
            charged_period_start,
            reason=exc.category,
        )
        log_with_context(
            logger,
            30,  # WARNING; do not attach the chained exception or its private payload
            "Natural polish request failed",
            stage=exc.stage,
            failure_category=exc.category,
            refund_succeeded=refunded,
        )
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            detail="Natural polish failed.",
        ) from exc
    except Exception as exc:
        refunded = _refund_ai_conversation(
            session,
            user_id,
            charged_period_start,
            reason="unexpected_error",
        )
        log_with_context(
            logger,
            30,  # WARNING; fixed fields only, without exception text or traceback
            "Natural polish request failed",
            stage="service",
            failure_category="unexpected_error",
            refund_succeeded=refunded,
        )
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            detail="Natural polish failed.",
        ) from exc

    # 只换了引号或空白的改写不算改动：退还这条 AI 消息，并把原文还给前端，
    # 让旧版前端即使忽略 unchanged 也只会看到零处修改。
    if is_noop_rewrite(body.selected_text, result.polished_text):
        refunded = _refund_ai_conversation(
            session,
            user_id,
            charged_period_start,
            reason="no_change",
        )
        if refunded:
            return NaturalPolishResponse(
                text=body.selected_text,
                model=result.model,
                unchanged=True,
            )

    return NaturalPolishResponse(
        text=result.polished_text,
        model=result.model,
    )
