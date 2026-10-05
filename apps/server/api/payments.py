"""User-facing Zpay checkout and payment notification endpoints."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select

from config.payment_settings import get_payment_settings
from database import get_session
from middleware.rate_limit import check_rate_limit
from models.entities import User
from models.payment import PaymentOrder
from services.core.auth_service import get_current_active_user
from services.subscription.zpay_service import (
    CallbackError,
    PaymentError,
    order_to_dict,
    zpay_service,
)

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


class PaymentOptionsResponse(BaseModel):
    enabled: bool
    payment_methods: list[Literal["alipay"]]


class PaymentOrderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_name: Literal["pro"]
    cycle: Literal["month", "year"]
    payment_method: Literal["alipay"]


class PaymentOrderResponse(BaseModel):
    id: str
    out_trade_no: str
    trade_no: str | None
    user_id: str
    plan_name: str
    plan_display_name: str
    cycle: Literal["month", "year"]
    amount_cents: int
    payment_method: Literal["alipay"]
    status: Literal["pending", "paid"]
    fulfillment_status: Literal["pending", "succeeded", "failed"]
    created_at: datetime
    paid_at: datetime | None
    fulfilled_at: datetime | None
    failure_reason: str | None


class CheckoutResponse(BaseModel):
    action: str
    method: Literal["POST"]
    fields: dict[str, str]


class PaymentOrderCreateResponse(BaseModel):
    order: PaymentOrderResponse
    checkout: CheckoutResponse


@router.get("/options", response_model=PaymentOptionsResponse)
def get_payment_options():
    enabled = get_payment_settings().checkout_enabled
    return {"enabled": enabled, "payment_methods": ["alipay"] if enabled else []}


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
            detail="Too many payment order requests",
        )
    try:
        order, checkout = zpay_service.create_order(
            session,
            user_id=current_user.id,
            plan_name=payload.plan_name,
            cycle=payload.cycle,
            payment_method=payload.payment_method,
            settings=get_payment_settings(),
        )
    except PaymentError as exc:
        unavailable = str(exc) == "Online payment is unavailable"
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if unavailable
                else status.HTTP_400_BAD_REQUEST
            ),
            detail=str(exc),
        ) from exc
    return {"order": order_to_dict(order), "checkout": checkout}


@router.get("/orders/{out_trade_no}", response_model=PaymentOrderResponse)
def get_payment_order(
    out_trade_no: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    order = session.exec(
        select(PaymentOrder).where(
            PaymentOrder.out_trade_no == out_trade_no,
            PaymentOrder.user_id == current_user.id,
        )
    ).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment order not found")
    return order_to_dict(order)


@router.get("/zpay/notify", response_class=PlainTextResponse)
def zpay_notify(request: Request, session: Session = Depends(get_session)):
    items = list(request.query_params.multi_items())
    keys = [key for key, _value in items]
    if len(keys) != len(set(keys)):
        return PlainTextResponse("fail")
    try:
        success = zpay_service.fulfill_callback(
            session, dict(items), get_payment_settings()
        )
    except CallbackError:
        return PlainTextResponse("fail")
    return PlainTextResponse("success" if success else "fail")
