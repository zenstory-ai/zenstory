"""Provider-agnostic payment settlement, refund revocation, and dispute recording."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Literal

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from models.payment import PaymentOrder
from services.subscription.subscription_service import subscription_service
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


class PaymentError(ValueError):
    """Safe payment error whose message can be returned to callers."""


class CallbackError(ValueError):
    """Internal callback rejection; details must not be returned publicly."""

    @property
    def reason(self) -> str:
        return str(self)


class PaymentSyncError(ValueError):
    """Order query compensation failed; the message is a short machine reason."""

    @property
    def reason(self) -> str:
        return str(self)


SyncOutcome = Literal["already_fulfilled", "fulfilled", "unpaid", "not_found"]

CYCLE_DAYS = {"month": 30, "year": 365}
FULFILLMENT_FAILURE_REASON = "subscription_fulfillment_failed"

# Paid orders whose entitlement is not (yet) granted, or disputed orders:
# the admin "needs attention" view.
NEEDS_ATTENTION_CONDITION = or_(
    (PaymentOrder.status == "paid") & (PaymentOrder.fulfillment_status != "succeeded"),
    PaymentOrder.fulfillment_status == "failed",
    PaymentOrder.status == "disputed",
)


def new_out_trade_no() -> str:
    """14 UTC timestamp digits + 16 cryptographically random digits."""
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return timestamp + "".join(str(secrets.randbelow(10)) for _ in range(16))


def begin_serialized_write(session: Session) -> None:
    """Acquire SQLite's writer lock up front; PostgreSQL serializes via user-row lock."""
    if session.in_transaction():
        return
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def order_to_dict(order: PaymentOrder) -> dict[str, object]:
    fulfillment = order.fulfillment_status
    if fulfillment == "processing":
        fulfillment = "pending"
    return {
        "id": order.id,
        "provider": order.provider,
        "currency": order.currency,
        "out_trade_no": order.out_trade_no,
        "trade_no": order.trade_no,
        "user_id": order.user_id,
        "plan_name": order.plan_name,
        "plan_display_name": order.plan_display_name,
        "cycle": order.cycle,
        "amount_cents": order.amount_cents,
        "payment_method": order.payment_method,
        "status": order.status,
        "fulfillment_status": fulfillment,
        "created_at": order.created_at,
        "paid_at": order.paid_at,
        "fulfilled_at": order.fulfilled_at,
        "failure_reason": order.failure_reason,
        "upgrade_source": order.upgrade_source,
    }


def record_fulfillment_failure(
    session: Session,
    order_id: str,
    reason: str = FULFILLMENT_FAILURE_REASON,
) -> None:
    """Record a retryable failure without overwriting a concurrent success."""
    result = session.exec(
        update(PaymentOrder)
        .where(
            PaymentOrder.id == order_id,
            PaymentOrder.status.in_(("pending", "paid")),
            PaymentOrder.fulfillment_status.in_(("pending", "failed")),
        )
        .values(fulfillment_status="failed", failure_reason=reason)
    )
    if result.rowcount == 1:
        session.commit()
    else:
        session.rollback()


def find_user_order(
    session: Session, *, user_id: str, out_trade_no: str
) -> PaymentOrder | None:
    return session.exec(
        select(PaymentOrder).where(
            PaymentOrder.out_trade_no == out_trade_no,
            PaymentOrder.user_id == user_id,
        )
    ).first()


def reload_order(session: Session, order_id: str) -> PaymentOrder | None:
    return session.exec(
        select(PaymentOrder)
        .where(PaymentOrder.id == order_id)
        .execution_options(populate_existing=True)
    ).first()


def settle_paid_order(
    session: Session,
    *,
    provider: str,
    out_trade_no: str,
    trade_no: str,
    amount_cents: int,
    currency: str,
    payment_method: str,
    product_name: str | None,
    channel: str,
    failure_recorder=None,
) -> bool:
    """
    Settle a provider-confirmed payment in two durable steps.

    1. Persist the payment fact (status=paid, trade_no, paid_at) and commit, so a
       later fulfillment failure can never erase evidence that money arrived.
    2. Claim fulfillment with a conditional UPDATE (pending/failed -> processing)
       and grant the subscription in the same transaction. Exactly one caller
       wins the claim; a failure only marks fulfillment_status=failed, and the
       next webhook retry or order query sync re-runs step 2.
    """
    begin_serialized_write(session)
    order = session.exec(
        select(PaymentOrder).where(PaymentOrder.out_trade_no == out_trade_no)
    ).first()
    if not order:
        raise CallbackError("unknown_order")
    if (
        order.provider != provider
        or order.currency.upper() != currency.upper()
        or order.payment_method != payment_method
        or order.amount_cents != amount_cents
        or (product_name is not None and order.product_name != product_name)
    ):
        raise CallbackError("order_mismatch")

    if order.fulfillment_status == "succeeded":
        if order.status in ("paid", "refunded", "disputed") and order.trade_no == trade_no:
            session.rollback()
            return True
        raise CallbackError("trade_mismatch")
    if order.trade_no and order.trade_no != trade_no:
        raise CallbackError("trade_mismatch")

    order_id = order.id
    paid_at = order.paid_at or utcnow()
    try:
        recorded = session.exec(
            update(PaymentOrder)
            .where(
                PaymentOrder.id == order_id,
                PaymentOrder.status.in_(("pending", "paid")),
                or_(PaymentOrder.trade_no.is_(None), PaymentOrder.trade_no == trade_no),
            )
            .values(
                status="paid",
                trade_no=trade_no,
                paid_at=paid_at,
            )
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        record_fulfillment_failure(session, order_id, reason="duplicate_trade_no")
        raise CallbackError("duplicate_trade_no") from exc
    if recorded.rowcount != 1:
        raise CallbackError("order_state_conflict")

    begin_serialized_write(session)
    claim = session.exec(
        update(PaymentOrder)
        .where(
            PaymentOrder.id == order_id,
            PaymentOrder.status == "paid",
            PaymentOrder.trade_no == trade_no,
            PaymentOrder.fulfillment_status.in_(("pending", "failed")),
        )
        .values(fulfillment_status="processing", failure_reason=None)
    )
    if claim.rowcount != 1:
        session.rollback()
        refreshed = reload_order(session, order_id)
        session.rollback()
        if (
            refreshed
            and refreshed.fulfillment_status == "succeeded"
            and refreshed.status in ("paid", "refunded", "disputed")
            and refreshed.trade_no == trade_no
        ):
            return True
        raise CallbackError("processing")

    try:
        claimed_order = session.get(PaymentOrder, order_id)
        if not claimed_order:
            raise RuntimeError("payment_order_missing")
        metadata: dict[str, object] = {
            "source": claimed_order.provider,
            "payment_order_id": order_id,
            "settled_via": channel,
            "currency": claimed_order.currency,
        }
        if claimed_order.upgrade_source:
            metadata["upgrade_source"] = claimed_order.upgrade_source
        subscription_service.create_user_subscription(
            session=session,
            user_id=claimed_order.user_id,
            plan_name=claimed_order.plan_name,
            duration_days=claimed_order.duration_days,
            metadata=metadata,
            commit=False,
        )
        fulfilled_at = utcnow()
        session.exec(
            update(PaymentOrder)
            .where(PaymentOrder.id == order_id)
            .values(
                fulfillment_status="succeeded",
                fulfilled_at=fulfilled_at,
                failure_reason=None,
            )
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        if failure_recorder is not None:
            failure_recorder(session, order_id)
        else:
            record_fulfillment_failure(session, order_id)
        raise CallbackError("fulfillment_failed") from exc

    log_with_context(
        logger,
        logging.INFO,
        "Payment order fulfilled",
        provider=provider,
        currency=currency,
        payment_order_id=order_id,
        out_trade_no=out_trade_no,
        trade_no=trade_no,
        settled_via=channel,
    )
    return True


def record_refund(
    session: Session,
    *,
    order: PaymentOrder,
    refund_id: str | None = None,
    channel: str = "webhook",
) -> bool:
    """
    Atomically mark a paid order as refunded and deduct its granted days once.

    Replaying the same refund event is a safe no-op.
    """
    begin_serialized_write(session)
    order_id = order.id
    updated = session.exec(
        update(PaymentOrder)
        .where(
            PaymentOrder.id == order_id,
            PaymentOrder.status.in_(("paid", "disputed")),
        )
        .values(status="refunded")
    )
    if updated.rowcount != 1:
        session.rollback()
        refreshed = reload_order(session, order_id)
        session.rollback()
        if refreshed and refreshed.status == "refunded":
            return True
        raise CallbackError("order_state_conflict")

    try:
        refreshed = session.get(PaymentOrder, order_id)
        if not refreshed:
            raise RuntimeError("payment_order_missing")
        if refreshed.fulfillment_status == "succeeded":
            metadata: dict[str, object] = {
                "source": refreshed.provider,
                "payment_order_id": order_id,
                "settled_via": channel,
                "currency": refreshed.currency,
            }
            if refund_id:
                metadata["refund_id"] = refund_id
            subscription_service.deduct_refunded_days(
                session,
                user_id=refreshed.user_id,
                plan_name=refreshed.plan_name,
                duration_days=refreshed.duration_days,
                metadata=metadata,
                commit=False,
            )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise CallbackError("refund_processing_failed") from exc

    log_with_context(
        logger,
        logging.INFO,
        "Payment order refunded",
        provider=order.provider,
        payment_order_id=order_id,
        out_trade_no=order.out_trade_no,
        trade_no=order.trade_no,
        refund_id=refund_id,
    )
    return True


def record_dispute(
    session: Session,
    *,
    order: PaymentOrder,
    dispute_id: str | None = None,
) -> bool:
    """Mark an order disputed so it surfaces in the admin needs-attention queue."""
    begin_serialized_write(session)
    order_id = order.id
    reason = f"disputed:{dispute_id}"[:100] if dispute_id else "disputed"
    updated = session.exec(
        update(PaymentOrder)
        .where(
            PaymentOrder.id == order_id,
            PaymentOrder.status.in_(("paid", "refunded", "disputed")),
        )
        .values(status="disputed", failure_reason=reason)
    )
    if updated.rowcount != 1:
        session.rollback()
        raise CallbackError("order_state_conflict")
    session.commit()
    log_with_context(
        logger,
        logging.ERROR,
        "Payment order disputed",
        provider=order.provider,
        payment_order_id=order_id,
        out_trade_no=order.out_trade_no,
        trade_no=order.trade_no,
        dispute_id=dispute_id,
    )
    return True
