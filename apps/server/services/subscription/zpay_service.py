"""Zpay order creation, signature validation, and idempotent fulfillment."""

import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from config.payment_settings import ZPAY_CHECKOUT_ACTION, PaymentSettings
from models.payment import PaymentOrder
from models.subscription import SubscriptionPlan
from services.subscription.subscription_service import subscription_service


class PaymentError(ValueError):
    """Safe payment error whose message can be returned to callers."""


class CallbackError(ValueError):
    """Internal callback rejection; details must not be returned publicly."""


CYCLE_DAYS = {"month": 30, "year": 365}
_MONEY_RE = re.compile(r"^[0-9]+(?:\.[0-9]{1,2})?$")
_DIGITS_RE = re.compile(r"^[0-9]{1,32}$")
_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def canonical_signing_string(params: Mapping[str, object]) -> str:
    pairs = []
    for key in sorted(params):
        value = params[key]
        if key in {"sign", "sign_type"} or value is None or str(value) == "":
            continue
        pairs.append(f"{key}={value}")
    return "&".join(pairs)


def sign_params(params: Mapping[str, object], key: str) -> str:
    payload = (canonical_signing_string(params) + key).encode("utf-8")
    return hashlib.md5(payload).hexdigest()


def verify_signature(params: Mapping[str, object], key: str, signature: str) -> bool:
    if not _MD5_RE.fullmatch(signature):
        return False
    return hmac.compare_digest(sign_params(params, key), signature.lower())


def _money_to_cents(value: str) -> int:
    if not _MONEY_RE.fullmatch(value):
        raise CallbackError("invalid_money")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise CallbackError("invalid_money") from exc
    cents = int(amount * 100)
    if amount <= 0 or Decimal(cents) / 100 != amount:
        raise CallbackError("invalid_money")
    return cents


def _format_money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _new_out_trade_no() -> str:
    # 14 UTC timestamp digits + 16 cryptographically random digits.
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return timestamp + "".join(str(secrets.randbelow(10)) for _ in range(16))


def order_to_dict(order: PaymentOrder) -> dict[str, object]:
    fulfillment = order.fulfillment_status
    if fulfillment == "processing":
        fulfillment = "pending"
    return {
        "id": order.id,
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
    }


class ZpayService:
    def _record_fulfillment_failure(self, session: Session, order_id: str) -> None:
        """Record a retryable failure without overwriting a concurrent success."""
        result = session.exec(
            update(PaymentOrder)
            .where(
                PaymentOrder.id == order_id,
                PaymentOrder.status == "pending",
                PaymentOrder.fulfillment_status.in_(("pending", "failed")),
            )
            .values(
                fulfillment_status="failed",
                failure_reason="subscription_fulfillment_failed",
            )
        )
        if result.rowcount == 1:
            session.commit()
        else:
            session.rollback()

    def create_order(
        self,
        session: Session,
        *,
        user_id: str,
        plan_name: str,
        cycle: str,
        payment_method: str,
        settings: PaymentSettings,
    ) -> tuple[PaymentOrder, dict[str, object]]:
        if not settings.checkout_enabled:
            raise PaymentError("Online payment is unavailable")
        if plan_name != "pro" or cycle not in CYCLE_DAYS or payment_method != "alipay":
            raise PaymentError("Unsupported payment option")

        plan = session.exec(
            select(SubscriptionPlan).where(
                SubscriptionPlan.name == plan_name,
                SubscriptionPlan.is_active.is_(True),
            )
        ).first()
        if not plan:
            raise PaymentError("Plan is unavailable")
        amount_cents = (
            plan.price_monthly_cents if cycle == "month" else plan.price_yearly_cents
        )
        if amount_cents <= 0:
            raise PaymentError("Plan price is unavailable")

        duration_days = CYCLE_DAYS[cycle]
        cycle_label = "月度" if cycle == "month" else "年度"
        product_name = f"ZenStory Pro {cycle_label}会员（{duration_days}天）"
        order = PaymentOrder(
            out_trade_no=_new_out_trade_no(),
            user_id=user_id,
            plan_name=plan.name,
            plan_display_name=plan.display_name,
            product_name=product_name,
            cycle=cycle,
            amount_cents=amount_cents,
            duration_days=duration_days,
            payment_method=payment_method,
        )
        session.add(order)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise PaymentError("Could not create payment order") from exc
        session.refresh(order)

        fields = {
            "name": order.product_name,
            "money": _format_money(order.amount_cents),
            "type": order.payment_method,
            "out_trade_no": order.out_trade_no,
            "notify_url": settings.notify_url,
            "pid": settings.pid,
            "return_url": settings.return_url,
            "sign_type": "MD5",
        }
        if settings.cid:
            fields["cid"] = settings.cid
        fields["sign"] = sign_params(fields, settings.key)
        return order, {"action": ZPAY_CHECKOUT_ACTION, "method": "POST", "fields": fields}

    def fulfill_callback(
        self,
        session: Session,
        params: Mapping[str, str],
        settings: PaymentSettings,
    ) -> bool:
        if not settings.credentials_configured:
            raise CallbackError("not_configured")
        required = {
            "pid", "name", "money", "out_trade_no", "trade_no", "trade_status",
            "type", "sign", "sign_type",
        }
        if any(not params.get(key) for key in required):
            raise CallbackError("missing_fields")
        if params["sign_type"].upper() != "MD5":
            raise CallbackError("invalid_sign_type")
        if params["pid"] != settings.pid:
            raise CallbackError("invalid_pid")
        if params["type"] != "alipay":
            raise CallbackError("invalid_type")
        if params["trade_status"] != "TRADE_SUCCESS":
            raise CallbackError("not_successful")
        if not _DIGITS_RE.fullmatch(params["out_trade_no"]):
            raise CallbackError("invalid_order_number")
        if not verify_signature(params, settings.key, params["sign"]):
            raise CallbackError("invalid_signature")
        if len(params["trade_no"]) > 64:
            raise CallbackError("invalid_trade_number")
        amount_cents = _money_to_cents(params["money"])

        # SQLite ignores SELECT ... FOR UPDATE. Acquiring its single writer lock
        # before reading prevents two callback sessions from both observing stale
        # subscription state. PostgreSQL is serialized later by the user-row lock.
        had_transaction = session.in_transaction()
        connection = session.connection()
        if connection.dialect.name == "sqlite" and not had_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")

        order = session.exec(
            select(PaymentOrder).where(PaymentOrder.out_trade_no == params["out_trade_no"])
        ).first()
        if not order:
            raise CallbackError("unknown_order")
        if (
            order.product_name != params["name"]
            or order.payment_method != params["type"]
            or order.amount_cents != amount_cents
        ):
            raise CallbackError("order_mismatch")

        if order.fulfillment_status == "succeeded":
            if order.status == "paid" and order.trade_no == params["trade_no"]:
                return True
            raise CallbackError("trade_mismatch")
        if order.trade_no and order.trade_no != params["trade_no"]:
            raise CallbackError("trade_mismatch")

        claim = session.exec(
            update(PaymentOrder)
            .where(
                PaymentOrder.id == order.id,
                PaymentOrder.status == "pending",
                PaymentOrder.fulfillment_status.in_(("pending", "failed")),
            )
            .values(fulfillment_status="processing", failure_reason=None)
        )
        if claim.rowcount != 1:
            session.rollback()
            refreshed = session.exec(
                select(PaymentOrder).where(PaymentOrder.id == order.id)
            ).first()
            if (
                refreshed
                and refreshed.fulfillment_status == "succeeded"
                and refreshed.status == "paid"
                and refreshed.trade_no == params["trade_no"]
            ):
                return True
            raise CallbackError("processing")

        now = utcnow()
        try:
            subscription_service.create_user_subscription(
                session=session,
                user_id=order.user_id,
                plan_name=order.plan_name,
                duration_days=order.duration_days,
                metadata={"source": "zpay", "payment_order_id": order.id},
                commit=False,
            )
            claimed_order = session.get(PaymentOrder, order.id)
            if not claimed_order:
                raise RuntimeError("payment_order_missing")
            claimed_order.trade_no = params["trade_no"]
            claimed_order.status = "paid"
            claimed_order.fulfillment_status = "succeeded"
            claimed_order.paid_at = now
            claimed_order.fulfilled_at = now
            claimed_order.failure_reason = None
            session.add(claimed_order)
            session.commit()
            return True
        except Exception as exc:
            session.rollback()
            self._record_fulfillment_failure(session, order.id)
            raise CallbackError("fulfillment_failed") from exc


zpay_service = ZpayService()
