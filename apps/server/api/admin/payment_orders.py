"""Administrative payment order listing and order-query compensation."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlmodel import Session, col, func, select

from api.payments import (
    PaymentOrderResponse,
    log_sync_failure,
    sync_error_status,
)
from config.payment_settings import get_payment_settings
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models.entities import User
from models.payment import PaymentOrder
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from services.subscription.zpay_service import (
    NEEDS_ATTENTION_CONDITION,
    PaymentSyncError,
    order_to_dict,
    zpay_service,
)

router = APIRouter(tags=["admin-payment-orders"])


class AdminPaymentOrderResponse(PaymentOrderResponse):
    username: str
    email: str


class AdminPaymentOrderListResponse(BaseModel):
    items: list[AdminPaymentOrderResponse]
    total: int
    page: int
    page_size: int
    # Paid-but-not-fulfilled orders across the whole table, for the badge.
    needs_attention_total: int


class AdminPaymentOrderSyncResponse(BaseModel):
    outcome: Literal["already_fulfilled", "fulfilled", "unpaid", "not_found"]
    order: AdminPaymentOrderResponse


def _admin_order_payload(session: Session, order: PaymentOrder) -> dict[str, object]:
    user = session.get(User, order.user_id)
    return {
        **order_to_dict(order),
        "username": user.username if user else "",
        "email": user.email if user else "",
    }


@router.get("/payment-orders", response_model=AdminPaymentOrderListResponse)
def list_payment_orders(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Literal["pending", "paid"] | None = None,
    fulfillment_status: Literal["pending", "succeeded", "failed"] | None = None,
    needs_attention: bool = False,
    payment_method: Literal["alipay"] | None = None,
    search: str | None = Query(default=None, max_length=100),
    user_id: str | None = Query(default=None, max_length=64),
    _current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    conditions = []
    if status:
        conditions.append(PaymentOrder.status == status)
    if fulfillment_status == "pending":
        # "processing" is an internal claim state and is shown as pending.
        conditions.append(PaymentOrder.fulfillment_status.in_(("pending", "processing")))
    elif fulfillment_status:
        conditions.append(PaymentOrder.fulfillment_status == fulfillment_status)
    if needs_attention:
        conditions.append(NEEDS_ATTENTION_CONDITION)
    if payment_method:
        conditions.append(PaymentOrder.payment_method == payment_method)
    if user_id:
        conditions.append(PaymentOrder.user_id == user_id)
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                PaymentOrder.out_trade_no.ilike(pattern),
                PaymentOrder.trade_no.ilike(pattern),
                User.username.ilike(pattern),
                User.email.ilike(pattern),
            )
        )

    base = select(PaymentOrder, User).join(User, User.id == PaymentOrder.user_id)
    count = select(func.count()).select_from(PaymentOrder).join(
        User, User.id == PaymentOrder.user_id
    )
    if conditions:
        base = base.where(*conditions)
        count = count.where(*conditions)
    total = session.exec(count).one()
    needs_attention_total = session.exec(
        select(func.count()).select_from(PaymentOrder).where(NEEDS_ATTENTION_CONDITION)
    ).one()
    rows = session.exec(
        base.order_by(col(PaymentOrder.created_at).desc(), col(PaymentOrder.id).desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        {**order_to_dict(order), "username": user.username, "email": user.email}
        for order, user in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "needs_attention_total": needs_attention_total,
    }


@router.post(
    "/payment-orders/{order_id}/sync", response_model=AdminPaymentOrderSyncResponse
)
def sync_payment_order(
    order_id: str,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Query Zpay for one order and, if it was paid, grant it like a notify would."""
    order = zpay_service.reload_order(session, order_id)
    if not order:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment order not found",
        )
    before = {"status": order.status, "fulfillment_status": order.fulfillment_status}
    out_trade_no = order.out_trade_no

    error: PaymentSyncError | None = None
    outcome = None
    try:
        outcome = zpay_service.sync_order(session, order, get_payment_settings())
    except PaymentSyncError as exc:
        log_sync_failure(exc, out_trade_no=out_trade_no, actor="admin")
        error = exc

    refreshed = zpay_service.reload_order(session, order_id)
    admin_audit_service.log_action(
        session,
        current_user.id,
        "sync_payment_order",
        "payment_order",
        order_id,
        old_value=before,
        new_value={
            "status": refreshed.status if refreshed else None,
            "fulfillment_status": refreshed.fulfillment_status if refreshed else None,
            "outcome": outcome,
            "error": error.reason if error else None,
        },
        request=http_request,
    )
    if error is not None:
        raise APIException(
            error_code=ErrorCode.PAYMENT_SYNC_FAILED,
            status_code=sync_error_status(error),
            detail=f"sync_failed:{error.reason}",
        )
    return {"outcome": outcome, "order": _admin_order_payload(session, refreshed)}
