"""Zpay order creation, signature validation, and idempotent fulfillment."""

import hashlib
import hmac
import logging
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config.payment_settings import (
    ZPAY_CHECKOUT_ACTION,
    ZPAY_ORDER_QUERY_ENDPOINT,
    PaymentSettings,
)
from models.payment import PaymentOrder
from models.subscription import SubscriptionPlan
from services.subscription.payment_fulfillment import (
    CYCLE_DAYS,
    FULFILLMENT_FAILURE_REASON,
    NEEDS_ATTENTION_CONDITION,
    CallbackError,
    PaymentError,
    PaymentSyncError,
    SyncOutcome,
    begin_serialized_write,
    find_user_order,
    new_out_trade_no,
    order_to_dict,
    record_fulfillment_failure,
    reload_order,
    settle_paid_order,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

ZPAY_QUERY_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_MONEY_RE = re.compile(r"^[0-9]+(?:\.[0-9]{1,2})?$")
_DIGITS_RE = re.compile(r"^[0-9]{1,32}$")
_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")

__all__ = [
    "CYCLE_DAYS",
    "FULFILLMENT_FAILURE_REASON",
    "NEEDS_ATTENTION_CONDITION",
    "CallbackError",
    "PaymentError",
    "PaymentSyncError",
    "SyncOutcome",
    "ZpayService",
    "callback_log_fields",
    "canonical_signing_string",
    "order_to_dict",
    "sign_params",
    "verify_signature",
    "zpay_service",
]


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
    return new_out_trade_no()


def callback_log_fields(params: Mapping[str, object]) -> dict[str, str | None]:
    """Order identifiers safe for logs: never sign, key, or the raw query."""
    fields: dict[str, str | None] = {}
    for key in ("out_trade_no", "trade_no", "money", "trade_status", "type"):
        value = params.get(key)
        fields[key] = str(value)[:64] if value not in (None, "") else None
    return fields


class ZpayService:
    # Tests swap in an httpx.MockTransport; production uses the default transport.
    http_transport: httpx.BaseTransport | None = None

    def _record_fulfillment_failure(
        self,
        session: Session,
        order_id: str,
        reason: str = FULFILLMENT_FAILURE_REASON,
    ) -> None:
        record_fulfillment_failure(session, order_id, reason=reason)

    @staticmethod
    def _begin_serialized_write(session: Session) -> None:
        begin_serialized_write(session)

    def create_order(
        self,
        session: Session,
        *,
        user_id: str,
        plan_name: str,
        cycle: str,
        payment_method: str,
        settings: PaymentSettings,
        upgrade_source: str | None = None,
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
            provider="zpay",
            currency="CNY",
            out_trade_no=_new_out_trade_no(),
            user_id=user_id,
            plan_name=plan.name,
            plan_display_name=plan.display_name,
            product_name=product_name,
            cycle=cycle,
            amount_cents=amount_cents,
            duration_days=duration_days,
            payment_method=payment_method,
            upgrade_source=upgrade_source,
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
        """Verify a Zpay notify and settle the order it confirms."""
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

        return self._settle_paid_order(
            session,
            out_trade_no=params["out_trade_no"],
            trade_no=params["trade_no"],
            amount_cents=amount_cents,
            payment_type=params["type"],
            product_name=params["name"],
            channel="notify",
        )

    def _settle_paid_order(
        self,
        session: Session,
        *,
        out_trade_no: str,
        trade_no: str,
        amount_cents: int,
        payment_type: str,
        product_name: str | None,
        channel: str,
    ) -> bool:
        return settle_paid_order(
            session,
            provider="zpay",
            out_trade_no=out_trade_no,
            trade_no=trade_no,
            amount_cents=amount_cents,
            currency="CNY",
            payment_method=payment_type,
            product_name=product_name,
            channel=channel,
            failure_recorder=self._record_fulfillment_failure,
        )

    def query_provider_order(
        self, out_trade_no: str, settings: PaymentSettings
    ) -> dict[str, str]:
        """
        Ask Zpay for an order's state (``act=order``).

        The request URL carries the merchant key, so neither the URL nor transport
        exception text (which embeds the URL) may reach logs or callers.
        """
        params = {
            "act": "order",
            "pid": settings.pid,
            "key": settings.key,
            "out_trade_no": out_trade_no,
        }
        try:
            with httpx.Client(
                timeout=ZPAY_QUERY_TIMEOUT,
                transport=self.http_transport,
                follow_redirects=False,
            ) as client:
                response = client.get(ZPAY_ORDER_QUERY_ENDPOINT, params=params)
        except httpx.HTTPError:
            raise PaymentSyncError("provider_unavailable") from None
        if response.status_code != 200:
            raise PaymentSyncError("provider_unavailable")
        try:
            payload = response.json()
        except ValueError:
            raise PaymentSyncError("provider_invalid_response") from None
        if not isinstance(payload, dict):
            raise PaymentSyncError("provider_invalid_response")
        return {
            str(key): "" if value is None else str(value).strip()
            for key, value in payload.items()
        }

    def sync_order(
        self, session: Session, order: PaymentOrder, settings: PaymentSettings
    ) -> SyncOutcome:
        """
        Compensate a missed notify by querying Zpay for one order.

        A paid result goes through the same settlement path as the notify, so a
        concurrent or later notify cannot grant the subscription twice.
        """
        if order.fulfillment_status == "succeeded":
            return "already_fulfilled"
        if not settings.credentials_configured:
            raise PaymentSyncError("not_configured")

        out_trade_no = order.out_trade_no
        remote = self.query_provider_order(out_trade_no, settings)
        if remote.get("code") != "1":
            log_with_context(
                logger,
                logging.INFO,
                "Zpay order query found no provider order",
                out_trade_no=out_trade_no,
                provider_code=remote.get("code", "")[:16],
            )
            return "not_found"
        if remote.get("status") != "1":
            return "unpaid"
        if remote.get("out_trade_no") != out_trade_no:
            raise PaymentSyncError("order_mismatch")
        if remote.get("pid") and remote["pid"] != settings.pid:
            raise PaymentSyncError("order_mismatch")
        trade_no = remote.get("trade_no", "")
        if not trade_no or len(trade_no) > 64:
            raise PaymentSyncError("invalid_trade_number")
        try:
            amount_cents = _money_to_cents(remote.get("money", ""))
            self._settle_paid_order(
                session,
                out_trade_no=out_trade_no,
                trade_no=trade_no,
                amount_cents=amount_cents,
                payment_type=remote.get("type", ""),
                product_name=None,
                channel="query",
            )
        except CallbackError as exc:
            raise PaymentSyncError(exc.reason) from exc
        return "fulfilled"

    def find_user_order(
        self, session: Session, *, user_id: str, out_trade_no: str
    ) -> PaymentOrder | None:
        return find_user_order(session, user_id=user_id, out_trade_no=out_trade_no)

    def reload_order(self, session: Session, order_id: str) -> PaymentOrder | None:
        return reload_order(session, order_id)

    def log_configuration_status(self, settings: PaymentSettings) -> None:
        """Startup check: an enabled but broken config silently hides checkout."""
        if not settings.enabled:
            return
        problems = settings.configuration_problems()
        if problems:
            log_with_context(
                logger,
                logging.ERROR,
                "ZPAY_ENABLED=true but Zpay checkout is misconfigured; checkout is disabled",
                problems=problems,
            )


zpay_service = ZpayService()
