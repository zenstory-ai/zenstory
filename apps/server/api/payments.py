"""User-facing Zpay & Creem checkout and payment notification endpoints."""

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from config.payment_settings import get_creem_settings, get_payment_settings
from database import get_session
from middleware.rate_limit import check_rate_limit
from models.entities import User
from services.core.auth_service import get_current_active_user
from services.subscription.creem_service import creem_service
from services.subscription.zpay_service import (
    CallbackError,
    PaymentError,
    PaymentSyncError,
    callback_log_fields,
    order_to_dict,
    zpay_service,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

# Rejections that mean money may have arrived without an entitlement, or that
# someone is forging callbacks: page an operator. Everything else is a warning.
ALERTING_CALLBACK_REASONS = frozenset({
    "invalid_signature",
    "unknown_order",
    "order_mismatch",
    "trade_mismatch",
    "duplicate_trade_no",
    "order_state_conflict",
    "fulfillment_failed",
    "refund_processing_failed",
    "mode_mismatch",
    "not_configured",
})
TRANSIENT_WEBHOOK_REASONS = frozenset({
    "fulfillment_failed",
    "refund_processing_failed",
    "processing",
    "not_configured",
})
PAYMENT_ERROR_CODES = {
    "Online payment is unavailable": "ERR_PAYMENT_UNAVAILABLE",
    "Unsupported payment option": "ERR_PAYMENT_UNSUPPORTED_OPTION",
    "Plan is unavailable": "ERR_PAYMENT_PLAN_UNAVAILABLE",
    "Plan price is unavailable": "ERR_PAYMENT_PLAN_UNAVAILABLE",
    "Could not create payment order": "ERR_PAYMENT_ORDER_CREATE_FAILED",
}
SYNC_ERROR_STATUS = {
    "not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
    "provider_unavailable": status.HTTP_502_BAD_GATEWAY,
    "provider_invalid_response": status.HTTP_502_BAD_GATEWAY,
}


def log_sync_failure(exc: PaymentSyncError, *, out_trade_no: str, actor: str) -> None:
    """Structured log for a failed order query; keeps the fulfillment stack trace."""
    fields = {"reason": exc.reason, "out_trade_no": out_trade_no, "actor": actor}
    if exc.reason == "fulfillment_failed":
        logger.exception(
            "Payment order sync could not grant the subscription",
            extra={"custom_fields": fields},
        )
        return
    level = logging.WARNING if exc.reason in SYNC_ERROR_STATUS else logging.ERROR
    log_with_context(logger, level, "Payment order sync failed", **fields)


def sync_error_status(exc: PaymentSyncError) -> int:
    return SYNC_ERROR_STATUS.get(exc.reason, status.HTTP_409_CONFLICT)


class PaymentOptionsResponse(BaseModel):
    enabled: bool
    payment_methods: list[Literal["alipay", "card"]]


class PaymentOrderCreateRequest(BaseModel):
    # The web app can deploy before the API, so a newer client's extra fields are ignored.
    model_config = ConfigDict(extra="ignore")

    plan_name: Literal["pro"]
    cycle: Literal["month", "year"]
    payment_method: Literal["alipay", "card"]
    upgrade_source: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$"
    )


class PaymentOrderResponse(BaseModel):
    # Columns are free-form strings in the DB (operators may hand-edit a row, e.g.
    # mark a refund); plain str keeps one odd row from failing a whole list.
    id: str
    provider: str = "zpay"
    currency: str = "CNY"
    out_trade_no: str
    trade_no: str | None
    user_id: str
    plan_name: str
    plan_display_name: str
    cycle: str
    amount_cents: int
    payment_method: str
    status: str
    fulfillment_status: str
    created_at: datetime
    paid_at: datetime | None
    fulfilled_at: datetime | None
    failure_reason: str | None
    upgrade_source: str | None = None


class CheckoutResponse(BaseModel):
    action: str
    method: Literal["POST", "GET"]
    fields: dict[str, str]


class PaymentOrderCreateResponse(BaseModel):
    order: PaymentOrderResponse
    checkout: CheckoutResponse


@router.get("/options", response_model=PaymentOptionsResponse)
def get_payment_options():
    zpay_enabled = get_payment_settings().checkout_enabled
    creem_enabled = get_creem_settings().checkout_enabled
    payment_methods: list[Literal["alipay", "card"]] = []
    if zpay_enabled:
        payment_methods.append("alipay")
    if creem_enabled:
        payment_methods.append("card")
    return {
        "enabled": bool(payment_methods),
        "payment_methods": payment_methods,
    }


@router.post("/orders", response_model=PaymentOrderCreateResponse)
def create_payment_order(
    payload: PaymentOrderCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    allowed, _remaining = check_rate_limit(
        request,
        f"payment_order_create:{current_user.id}",
        10,
        60,
        include_client_ip=False,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="ERR_PAYMENT_RATE_LIMITED",
        )
    try:
        if payload.payment_method == "card":
            order, checkout = creem_service.create_order(
                session,
                user_id=current_user.id,
                user_email=current_user.email,
                plan_name=payload.plan_name,
                cycle=payload.cycle,
                payment_method=payload.payment_method,
                settings=get_creem_settings(),
                upgrade_source=payload.upgrade_source,
            )
        else:
            order, checkout = zpay_service.create_order(
                session,
                user_id=current_user.id,
                plan_name=payload.plan_name,
                cycle=payload.cycle,
                payment_method=payload.payment_method,
                settings=get_payment_settings(),
                upgrade_source=payload.upgrade_source,
            )
    except PaymentError as exc:
        unavailable = str(exc) == "Online payment is unavailable"
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if unavailable
                else status.HTTP_400_BAD_REQUEST
            ),
            detail=PAYMENT_ERROR_CODES.get(str(exc), "ERR_PAYMENT_ORDER_CREATE_FAILED"),
        ) from exc
    return {"order": order_to_dict(order), "checkout": checkout}


def _get_own_order_or_404(session: Session, user: User, out_trade_no: str):
    order = zpay_service.find_user_order(
        session, user_id=user.id, out_trade_no=out_trade_no
    )
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment order not found")
    return order


@router.get("/orders/{out_trade_no}", response_model=PaymentOrderResponse)
def get_payment_order(
    out_trade_no: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    return order_to_dict(_get_own_order_or_404(session, current_user, out_trade_no))


@router.post("/orders/{out_trade_no}/sync", response_model=PaymentOrderResponse)
def sync_payment_order(
    out_trade_no: str,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Ask the order's payment provider whether it was paid and settle it if so."""
    allowed, _remaining = check_rate_limit(
        request,
        f"payment_order_sync:{current_user.id}",
        5,
        60,
        include_client_ip=False,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="ERR_PAYMENT_RATE_LIMITED",
        )
    order = _get_own_order_or_404(session, current_user, out_trade_no)
    order_id = order.id
    try:
        if order.provider == "creem":
            creem_service.sync_order(session, order, get_creem_settings())
        else:
            zpay_service.sync_order(session, order, get_payment_settings())
    except PaymentSyncError as exc:
        log_sync_failure(exc, out_trade_no=out_trade_no, actor="user")
        raise HTTPException(
            status_code=sync_error_status(exc), detail="ERR_PAYMENT_SYNC_FAILED"
        ) from None
    return order_to_dict(zpay_service.reload_order(session, order_id))


def _log_callback_rejection(exc: CallbackError, params: dict[str, str]) -> None:
    fields = {"reason": exc.reason, **callback_log_fields(params)}
    if exc.reason == "fulfillment_failed":
        logger.exception(
            "Zpay callback could not grant the subscription",
            extra={"custom_fields": fields},
        )
        return
    level = logging.ERROR if exc.reason in ALERTING_CALLBACK_REASONS else logging.WARNING
    log_with_context(logger, level, "Zpay callback rejected", **fields)


@router.get("/zpay/notify", response_class=PlainTextResponse)
def zpay_notify(request: Request, session: Session = Depends(get_session)):
    items = list(request.query_params.multi_items())
    keys = [key for key, _value in items]
    params = dict(items)
    if len(keys) != len(set(keys)):
        _log_callback_rejection(CallbackError("duplicate_query_keys"), params)
        return PlainTextResponse("fail")
    try:
        success = zpay_service.fulfill_callback(session, params, get_payment_settings())
    except CallbackError as exc:
        _log_callback_rejection(exc, params)
        return PlainTextResponse("fail")
    return PlainTextResponse("success" if success else "fail")


@router.post("/creem/webhook")
async def creem_webhook(request: Request, session: Session = Depends(get_session)):
    raw_body = await request.body()
    signature = request.headers.get("creem-signature")
    try:
        return creem_service.handle_webhook(
            session,
            raw_body=raw_body,
            signature=signature,
            settings=get_creem_settings(),
        )
    except CallbackError as exc:
        level = logging.ERROR if exc.reason in ALERTING_CALLBACK_REASONS else logging.WARNING
        if exc.reason in {"fulfillment_failed", "refund_processing_failed"}:
            logger.exception(
                "Creem webhook processing failed",
                extra={"custom_fields": {"reason": exc.reason}},
            )
        else:
            log_with_context(logger, level, "Creem webhook rejected", reason=exc.reason)
        if exc.reason == "invalid_signature":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_signature"
            ) from None
        if exc.reason in TRANSIENT_WEBHOOK_REASONS:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="webhook_retry"
            ) from None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="webhook_rejected"
        ) from None
