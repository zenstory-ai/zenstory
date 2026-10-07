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
from services.features.natural_polish_service import natural_polish_service
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


def _refund_ai_conversation(
    session: Session,
    user_id: str,
    period_start: datetime,
    *,
    reason: str,
) -> bool:
    """润色失败时退还已预扣的 AI 对话额度，失败只记日志不影响主流程。

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
    except Exception as refund_error:
        log_with_context(
            logger,
            30,  # WARNING
            "Failed to refund AI conversation quota after natural polish failure",
            user_id=user_id,
            reason=reason,
            error=str(refund_error),
            error_type=type(refund_error).__name__,
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

    allowed, used, limit = quota_service.check_ai_conversation_quota(session, current_user.id)
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
        current_user.id,
    )
    if charged_period_start is None:
        raise APIException(
            error_code=ErrorCode.QUOTA_AI_CONVERSATIONS_EXCEEDED,
            status_code=402,
            detail="AI conversation quota exceeded. Please upgrade your plan.",
        )

    lang = (accept_language or "").split(",")[0].split("-")[0].strip().lower() or "zh"

    try:
        result = await natural_polish_service.natural_polish(
            selected_text=body.selected_text,
            language=lang,
            user_id=current_user.id,
            project_id=body.project_id,
        )
    except APIException:
        _refund_ai_conversation(
            session,
            current_user.id,
            charged_period_start,
            reason="api_exception",
        )
        raise
    except Exception as exc:
        _refund_ai_conversation(
            session,
            current_user.id,
            charged_period_start,
            reason=type(exc).__name__,
        )
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            detail=f"Natural polish failed: {type(exc).__name__}",
        ) from exc

    return NaturalPolishResponse(
        text=result.polished_text,
        model=result.model,
    )
