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
AMOUNT_MISMATCH_FAILURE_REASON = "amount_mismatch"
AMOUNT_UNVERIFIED_FAILURE_REASON = "amount_unverified"

# Paid orders whose entitlement is not (yet) granted, pending/paid fulfillment
# failures, partially refunded paid orders, or disputed orders (even if already
# refunded): the admin "needs attention" view.
NEEDS_ATTENTION_CONDITION = or_(
    (PaymentOrder.status == "paid") & (PaymentOrder.fulfillment_status != "succeeded"),
    PaymentOrder.status.in_(("pending", "paid")) & (PaymentOrder.fulfillment_status == "failed"),
    PaymentOrder.status == "disputed",
    PaymentOrder.disputed_at.is_not(None),
    PaymentOrder.status.in_(("pending", "paid")) & (PaymentOrder.refunded_amount_cents > 0),
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

    if order.failure_reason == AMOUNT_MISMATCH_FAILURE_REASON:
        raise CallbackError("amount_mismatch")

    order_id = order.id
    paid_at = order.paid_at or utcnow()
    try:
        recorded = session.exec(
            update(PaymentOrder)
            .where(
                PaymentOrder.id == order_id,
                PaymentOrder.status.in_(("pending", "paid")),
                PaymentOrder.refunded_at.is_(None),
                PaymentOrder.disputed_at.is_(None),
                PaymentOrder.failure_reason.is_distinct_from(AMOUNT_MISMATCH_FAILURE_REASON),
                or_(PaymentOrder.trade_no.is_(None), PaymentOrder.trade_no == trade_no),
            )
            .values(
                status="paid",
                trade_no=trade_no,
                paid_at=paid_at,
                paid_amount_cents=amount_cents,
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
            PaymentOrder.refunded_at.is_(None),
            PaymentOrder.disputed_at.is_(None),
            PaymentOrder.failure_reason.is_distinct_from(AMOUNT_MISMATCH_FAILURE_REASON),
        )
        .values(fulfillment_status="processing")
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
        if refreshed and refreshed.failure_reason == AMOUNT_MISMATCH_FAILURE_REASON:
            raise CallbackError("amount_mismatch")
        if refreshed and (refreshed.refunded_at is not None or refreshed.disputed_at is not None):
            raise CallbackError("order_state_conflict")
        raise CallbackError("processing")

    try:
        claimed_order = reload_order(session, order_id)
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
        if (claimed_order.refunded_amount_cents or 0) > 0:
            if (
                claimed_order.disputed_at is not None
                or claimed_order.failure_reason == AMOUNT_MISMATCH_FAILURE_REASON
            ):
                final_failure_reason = claimed_order.failure_reason
            else:
                threshold = (
                    claimed_order.paid_amount_cents
                    if (
                        claimed_order.paid_amount_cents is not None
                        and claimed_order.paid_amount_cents > 0
                    )
                    else claimed_order.amount_cents
                )
                final_failure_reason = f"partial_refund:{claimed_order.refunded_amount_cents}/{threshold}"[:100]
        else:
            final_failure_reason = None
        session.exec(
            update(PaymentOrder)
            .where(PaymentOrder.id == order_id)
            .values(
                fulfillment_status="succeeded",
                fulfilled_at=fulfilled_at,
                failure_reason=final_failure_reason,
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


def record_paid_amount_mismatch(
    session: Session,
    *,
    order: PaymentOrder,
    trade_no: str,
    actual_amount_cents: int,
    actual_currency: str,
    channel: str = "webhook",
) -> bool:
    """
    Persist payment evidence when a provider confirms payment for a verified
    order/product whose charged amount or currency differs from our DB snapshot.

    The order is marked paid + paid_amount_cents + fulfillment_status=failed
    (failure_reason='amount_mismatch') so it surfaces in the admin
    needs-attention queue without granting Pro.
    """
    begin_serialized_write(session)
    order_id = order.id
    paid_at = order.paid_at or utcnow()
    try:
        updated = session.exec(
            update(PaymentOrder)
            .where(
                PaymentOrder.id == order_id,
                PaymentOrder.status.in_(("pending", "paid")),
                PaymentOrder.fulfillment_status != "succeeded",
                PaymentOrder.refunded_at.is_(None),
                PaymentOrder.disputed_at.is_(None),
                or_(PaymentOrder.trade_no.is_(None), PaymentOrder.trade_no == trade_no),
            )
            .values(
                status="paid",
                trade_no=trade_no,
                paid_at=paid_at,
                paid_amount_cents=actual_amount_cents,
                fulfillment_status="failed",
                failure_reason=AMOUNT_MISMATCH_FAILURE_REASON,
            )
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        record_fulfillment_failure(session, order_id, reason="duplicate_trade_no")
        raise CallbackError("duplicate_trade_no") from exc

    log_with_context(
        logger,
        logging.ERROR,
        "Payment amount or currency mismatch recorded for manual review",
        provider=order.provider,
        payment_order_id=order_id,
        out_trade_no=order.out_trade_no,
        trade_no=trade_no,
        expected_amount_cents=order.amount_cents,
        actual_amount_cents=actual_amount_cents,
        expected_currency=order.currency,
        actual_currency=actual_currency,
        settled_via=channel,
        recorded=updated.rowcount == 1,
    )
    return updated.rowcount == 1


def record_paid_amount_unverified(
    session: Session,
    *,
    order: PaymentOrder,
    trade_no: str,
    channel: str = "query",
) -> bool:
    """
    Persist payment evidence when provider sync confirms completion but cannot
    return an authoritative charged amount/currency. Enters needs-attention
    without granting Pro.
    """
    begin_serialized_write(session)
    order_id = order.id
    paid_at = order.paid_at or utcnow()
    try:
        updated = session.exec(
            update(PaymentOrder)
            .where(
                PaymentOrder.id == order_id,
                PaymentOrder.status.in_(("pending", "paid")),
                PaymentOrder.fulfillment_status != "succeeded",
                PaymentOrder.refunded_at.is_(None),
                PaymentOrder.disputed_at.is_(None),
                PaymentOrder.failure_reason.is_distinct_from(AMOUNT_MISMATCH_FAILURE_REASON),
                or_(PaymentOrder.trade_no.is_(None), PaymentOrder.trade_no == trade_no),
            )
            .values(
                status="paid",
                trade_no=trade_no,
                paid_at=paid_at,
                fulfillment_status="failed",
                failure_reason=AMOUNT_UNVERIFIED_FAILURE_REASON,
            )
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        record_fulfillment_failure(session, order_id, reason="duplicate_trade_no")
        raise CallbackError("duplicate_trade_no") from exc

    log_with_context(
        logger,
        logging.ERROR,
        "Payment sync completed without authoritative amount; recorded for manual review",
        provider=order.provider,
        payment_order_id=order_id,
        out_trade_no=order.out_trade_no,
        trade_no=trade_no,
        settled_via=channel,
        recorded=updated.rowcount == 1,
    )
    return updated.rowcount == 1


def record_refund(
    session: Session,
    *,
    order: PaymentOrder,
    refund_id: str | None = None,
    refund_amount_cents: int | None = None,
    trade_no: str | None = None,
    channel: str = "webhook",
) -> bool:
    """
    Atomically record a partial or full refund and deduct granted days at most once.

    Uses ``FOR UPDATE`` with ``populate_existing=True`` plus compare-and-set
    ``UPDATE`` predicates so concurrent webhooks/settlements always read
    post-lock state and can never lose partial-refund increments or skip day
    revocation.
    """
    begin_serialized_write(session)
    order_id = order.id
    row = session.exec(
        select(PaymentOrder)
        .where(PaymentOrder.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if not row:
        session.rollback()
        raise CallbackError("unknown_order")

    if row.refunded_at is not None:
        session.rollback()
        return True

    if row.fulfillment_status == "processing":
        session.rollback()
        raise CallbackError("processing")

    seen_ids = [item for item in (row.refund_ids or "").split(",") if item]
    if refund_id and refund_id in seen_ids:
        session.rollback()
        return True

    if row.status not in ("pending", "paid", "disputed"):
        session.rollback()
        raise CallbackError("order_state_conflict")

    refund_threshold = (
        row.paid_amount_cents
        if (row.paid_amount_cents is not None and row.paid_amount_cents > 0)
        else row.amount_cents
    )
    effective_refund = refund_threshold if refund_amount_cents is None else refund_amount_cents
    if effective_refund <= 0:
        session.rollback()
        raise CallbackError("invalid_payload")

    prev_refunded_cents = row.refunded_amount_cents or 0
    new_total = prev_refunded_cents + effective_refund
    if refund_id:
        seen_ids.append(refund_id)
    serialized_ids = ",".join(seen_ids) or None
    next_trade_no = row.trade_no or trade_no

    if new_total < refund_threshold:
        preserve_reason = (
            row.disputed_at is not None
            or row.failure_reason == AMOUNT_MISMATCH_FAILURE_REASON
        )
        partial_reason = (
            row.failure_reason
            if preserve_reason
            else f"partial_refund:{new_total}/{refund_threshold}"[:100]
        )
        try:
            updated = session.exec(
                update(PaymentOrder)
                .where(
                    PaymentOrder.id == order_id,
                    PaymentOrder.refunded_at.is_(None),
                    PaymentOrder.status == row.status,
                    PaymentOrder.refunded_amount_cents == prev_refunded_cents,
                    PaymentOrder.fulfillment_status == row.fulfillment_status,
                )
                .values(
                    refunded_amount_cents=new_total,
                    refund_ids=serialized_ids,
                    failure_reason=partial_reason,
                    trade_no=next_trade_no,
                )
            )
            if updated.rowcount != 1:
                session.rollback()
                raise CallbackError("processing")
            session.commit()
        except CallbackError:
            raise
        except IntegrityError as exc:
            session.rollback()
            raise CallbackError("duplicate_trade_no") from exc
        except Exception as exc:
            session.rollback()
            raise CallbackError("refund_processing_failed") from exc

        log_with_context(
            logger,
            logging.WARNING,
            "Payment order partially refunded; awaiting full refund or manual review",
            provider=row.provider,
            payment_order_id=order_id,
            out_trade_no=row.out_trade_no,
            trade_no=next_trade_no,
            refund_id=refund_id,
            refund_amount_cents=effective_refund,
            cumulative_refunded_cents=new_total,
            order_amount_cents=refund_threshold,
        )
        return True

    if row.disputed_at is not None:
        next_failure_reason = row.failure_reason
    elif row.status == "pending":
        next_failure_reason = "refunded_before_settlement"
    else:
        next_failure_reason = None

    now = utcnow()
    try:
        updated = session.exec(
            update(PaymentOrder)
            .where(
                PaymentOrder.id == order_id,
                PaymentOrder.refunded_at.is_(None),
                PaymentOrder.status == row.status,
                PaymentOrder.refunded_amount_cents == prev_refunded_cents,
                PaymentOrder.fulfillment_status == row.fulfillment_status,
            )
            .values(
                status="refunded",
                refunded_at=now,
                refunded_amount_cents=min(new_total, refund_threshold),
                refund_ids=serialized_ids,
                failure_reason=next_failure_reason,
                trade_no=next_trade_no,
            )
        )
    except IntegrityError as exc:
        session.rollback()
        raise CallbackError("duplicate_trade_no") from exc

    if updated.rowcount != 1:
        session.rollback()
        refreshed = reload_order(session, order_id)
        session.rollback()
        if refreshed and refreshed.refunded_at is not None:
            return True
        raise CallbackError("processing")

    try:
        if row.fulfillment_status == "succeeded":
            metadata: dict[str, object] = {
                "source": row.provider,
                "payment_order_id": order_id,
                "settled_via": channel,
                "currency": row.currency,
            }
            if refund_id:
                metadata["refund_id"] = refund_id
            subscription_service.deduct_refunded_days(
                session,
                user_id=row.user_id,
                plan_name=row.plan_name,
                duration_days=row.duration_days,
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
        provider=row.provider,
        payment_order_id=order_id,
        out_trade_no=row.out_trade_no,
        trade_no=next_trade_no,
        refund_id=refund_id,
        cumulative_refunded_cents=min(new_total, refund_threshold),
    )
    return True


def record_dispute(
    session: Session,
    *,
    order: PaymentOrder,
    dispute_id: str | None = None,
    trade_no: str | None = None,
) -> bool:
    """
    Record a dispute so it surfaces in the admin needs-attention queue.

    Uses ``FOR UPDATE`` with ``populate_existing=True`` plus compare-and-set
    on ``status`` and ``refunded_at`` so a concurrent full refund can never be
    overwritten back to ``'disputed'``.
    """
    begin_serialized_write(session)
    order_id = order.id
    row = session.exec(
        select(PaymentOrder)
        .where(PaymentOrder.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if not row:
        session.rollback()
        raise CallbackError("unknown_order")

    reason = f"disputed:{dispute_id}"[:100] if dispute_id else "disputed"
    now = utcnow()
    next_trade_no = row.trade_no or trade_no
    if row.status == "refunded" or row.refunded_at is not None:
        next_status = "refunded"
    elif row.status in ("pending", "paid", "disputed"):
        next_status = "disputed"
    else:
        session.rollback()
        raise CallbackError("order_state_conflict")

    cas_refunded = (
        PaymentOrder.refunded_at.is_(None)
        if row.refunded_at is None
        else PaymentOrder.refunded_at == row.refunded_at
    )
    try:
        updated = session.exec(
            update(PaymentOrder)
            .where(
                PaymentOrder.id == order_id,
                PaymentOrder.status == row.status,
                cas_refunded,
            )
            .values(
                status=next_status,
                disputed_at=row.disputed_at or now,
                failure_reason=reason,
                trade_no=next_trade_no,
            )
        )
        if updated.rowcount != 1:
            session.rollback()
            raise CallbackError("processing")
        session.commit()
    except CallbackError:
        raise
    except IntegrityError as exc:
        session.rollback()
        raise CallbackError("duplicate_trade_no") from exc
    log_with_context(
        logger,
        logging.ERROR,
        "Payment order disputed",
        provider=row.provider,
        payment_order_id=order_id,
        out_trade_no=row.out_trade_no,
        trade_no=next_trade_no,
        dispute_id=dispute_id,
        order_status=next_status,
    )
    return True
