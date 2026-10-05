"""Read-only administrative payment order listing."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlmodel import Session, func, select

from api.payments import PaymentOrderResponse
from database import get_session
from models.entities import User
from models.payment import PaymentOrder
from services.core.auth_service import get_current_superuser
from services.subscription.zpay_service import order_to_dict

router = APIRouter(tags=["admin-payment-orders"])


class AdminPaymentOrderResponse(PaymentOrderResponse):
    username: str
    email: str


class AdminPaymentOrderListResponse(BaseModel):
    items: list[AdminPaymentOrderResponse]
    total: int
    page: int
    page_size: int


@router.get("/payment-orders", response_model=AdminPaymentOrderListResponse)
def list_payment_orders(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Literal["pending", "paid"] | None = None,
    payment_method: Literal["alipay"] | None = None,
    search: str | None = Query(default=None, max_length=100),
    _current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    conditions = []
    if status:
        conditions.append(PaymentOrder.status == status)
    if payment_method:
        conditions.append(PaymentOrder.payment_method == payment_method)
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
    rows = session.exec(
        base.order_by(PaymentOrder.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        {**order_to_dict(order), "username": user.username, "email": user.email}
        for order, user in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
