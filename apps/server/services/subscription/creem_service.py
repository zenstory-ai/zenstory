"""Creem USD checkout creation, webhook verification, refund/dispute handling, and order sync."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config.payment_settings import (
    CREEM_ALLOWED_CHECKOUT_HOSTS,
    CreemSettings,
)
from models.payment import PaymentOrder
from models.subscription import SubscriptionPlan
from services.subscription.payment_fulfillment import (
    CYCLE_DAYS,
    CallbackError,
    PaymentError,
    PaymentSyncError,
    SyncOutcome,
    new_out_trade_no,
    record_dispute,
    record_paid_amount_mismatch,
    record_refund,
    settle_paid_order,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

CREEM_HTTP_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_TEST_MODES = frozenset({"test", "sandbox"})


def sign_creem_payload(raw_body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_creem_signature(raw_body: bytes, secret: str, signature: str | None) -> bool:
    if not secret or not signature:
        return False
    cleaned = signature.strip()
    if not _HEX64_RE.fullmatch(cleaned):
        return False
    expected = sign_creem_payload(raw_body, secret)
    return hmac.compare_digest(expected, cleaned.lower())


def _is_allowed_checkout_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and hostname in CREEM_ALLOWED_CHECKOUT_HOSTS
        and not parsed.username
        and not parsed.password
    )


def _extract_id(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, dict):
        raw = value.get("id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _parse_amount_cents(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit():
            parsed = int(cleaned)
            return parsed if parsed > 0 else None
    return None


def _extract_currency(obj: dict[str, Any], order_obj: dict[str, Any] | None) -> str | None:
    for source in (order_obj, obj):
        if isinstance(source, dict):
            raw = source.get("currency")
            if isinstance(raw, str) and raw.strip():
                return raw.strip().upper()
    return None


def _extract_amount(obj: dict[str, Any], order_obj: dict[str, Any] | None) -> int | None:
    for source in (order_obj, obj):
        if isinstance(source, dict):
            for key in ("amount", "sub_total", "amount_paid"):
                parsed = _parse_amount_cents(source.get(key))
                if parsed is not None:
                    return parsed
    return None


def _extract_refund_amount(obj: dict[str, Any]) -> int | None:
    """Extract refund amount from the refund object itself (never from nested order.amount)."""
    for key in ("refund_amount", "amount"):
        parsed = _parse_amount_cents(obj.get(key))
        if parsed is not None:
            return parsed
    return None


def _extract_out_trade_no(obj: dict[str, Any]) -> str | None:
    """Look for our out_trade_no in request_id or metadata on top-level or nested order/checkout."""
    candidates: list[dict[str, Any]] = [obj]
    for nested_key in ("checkout", "order", "transaction"):
        nested = obj.get(nested_key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for source in candidates:
        req_id = _extract_id(source.get("request_id"))
        if req_id:
            return req_id
        metadata = source.get("metadata")
        if isinstance(metadata, dict):
            meta_otn = _extract_id(metadata.get("out_trade_no"))
            if meta_otn:
                return meta_otn
    return None


def _mode_matches(payload_mode: Any, *, test_mode: bool) -> bool:
    if not isinstance(payload_mode, str) or not payload_mode.strip():
        return True
    normalized = payload_mode.strip().lower()
    is_payload_test = normalized in _TEST_MODES
    return is_payload_test == test_mode


class CreemService:
    # Tests swap in an httpx.MockTransport; production uses the default transport.
    http_transport: httpx.BaseTransport | None = None

    def create_order(
        self,
        session: Session,
        *,
        user_id: str,
        user_email: str | None,
        plan_name: str,
        cycle: str,
        payment_method: str,
        settings: CreemSettings,
        upgrade_source: str | None = None,
    ) -> tuple[PaymentOrder, dict[str, object]]:
        if not settings.checkout_enabled:
            raise PaymentError("Online payment is unavailable")
        if plan_name != "pro" or cycle not in CYCLE_DAYS or payment_method != "card":
            raise PaymentError("Unsupported payment option")

        product_id = settings.product_id_for_cycle(cycle)
        if not product_id:
            raise PaymentError("Online payment is unavailable")

        plan = session.exec(
            select(SubscriptionPlan).where(
                SubscriptionPlan.name == plan_name,
                SubscriptionPlan.is_active.is_(True),
            )
        ).first()
        if not plan:
            raise PaymentError("Plan is unavailable")
        amount_cents = (
            plan.price_monthly_usd_cents if cycle == "month" else plan.price_yearly_usd_cents
        )
        if amount_cents <= 0:
            raise PaymentError("Plan price is unavailable")

        duration_days = CYCLE_DAYS[cycle]
        product_name = f"ZenStory Pro ({duration_days} Days)"
        out_trade_no = new_out_trade_no()
        order = PaymentOrder(
            provider="creem",
            currency="USD",
            out_trade_no=out_trade_no,
            user_id=user_id,
            plan_name=plan.name,
            plan_display_name=plan.display_name,
            product_name=product_name,
            cycle=cycle,
            amount_cents=amount_cents,
            duration_days=duration_days,
            payment_method="card",
            upgrade_source=upgrade_source,
        )
        session.add(order)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise PaymentError("Could not create payment order") from exc
        session.refresh(order)

        success_url = f"{settings.return_url}?{urlencode({'out_trade_no': order.out_trade_no})}"
        body: dict[str, Any] = {
            "product_id": product_id,
            "request_id": order.out_trade_no,
            "success_url": success_url,
            "metadata": {
                "order_id": order.id,
                "out_trade_no": order.out_trade_no,
                "user_id": user_id,
                "cycle": cycle,
            },
        }
        if user_email and "@" in user_email:
            body["customer"] = {"email": user_email}

        checkout_data = self._create_remote_checkout(body, settings)
        if checkout_data is None:
            self._mark_checkout_creation_failed(session, order.id)
            raise PaymentError("Could not create payment order")

        checkout_id = _extract_id(checkout_data.get("id"))
        checkout_url = checkout_data.get("checkout_url")
        if (
            not checkout_id
            or len(checkout_id) > 64
            or not isinstance(checkout_url, str)
            or not _is_allowed_checkout_url(checkout_url)
            or not _mode_matches(checkout_data.get("mode"), test_mode=settings.test_mode)
        ):
            log_with_context(
                logger,
                logging.ERROR,
                "Creem checkout response failed validation",
                out_trade_no=order.out_trade_no,
                has_checkout_id=bool(checkout_id),
                mode=str(checkout_data.get("mode") or "")[:16],
            )
            self._mark_checkout_creation_failed(session, order.id)
            raise PaymentError("Could not create payment order")

        session.exec(
            update(PaymentOrder)
            .where(PaymentOrder.id == order.id)
            .values(provider_checkout_id=checkout_id)
        )
        session.commit()
        session.refresh(order)
        return order, {"action": checkout_url, "method": "GET", "fields": {}}

    def _mark_checkout_creation_failed(self, session: Session, order_id: str) -> None:
        session.exec(
            update(PaymentOrder)
            .where(PaymentOrder.id == order_id, PaymentOrder.status == "pending")
            .values(status="failed", failure_reason="provider_checkout_failed")
        )
        session.commit()

    def _create_remote_checkout(
        self, payload: dict[str, Any], settings: CreemSettings
    ) -> dict[str, Any] | None:
        url = f"{settings.api_base_url}/checkouts"
        try:
            with httpx.Client(
                timeout=CREEM_HTTP_TIMEOUT,
                transport=self.http_transport,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    url,
                    json=payload,
                    headers={"x-api-key": settings.api_key, "Content-Type": "application/json"},
                )
        except httpx.HTTPError:
            log_with_context(logger, logging.ERROR, "Creem checkout request failed (transport)")
            return None
        if response.status_code not in (200, 201):
            log_with_context(
                logger,
                logging.ERROR,
                "Creem checkout request rejected",
                status_code=response.status_code,
            )
            return None
        try:
            data = response.json()
        except ValueError:
            log_with_context(logger, logging.ERROR, "Creem checkout returned invalid JSON")
            return None
        return data if isinstance(data, dict) else None

    def query_remote_checkout(
        self, checkout_id: str, settings: CreemSettings
    ) -> dict[str, Any] | None:
        url = f"{settings.api_base_url}/checkouts"
        try:
            with httpx.Client(
                timeout=CREEM_HTTP_TIMEOUT,
                transport=self.http_transport,
                follow_redirects=False,
            ) as client:
                response = client.get(
                    url,
                    params={"checkout_id": checkout_id},
                    headers={"x-api-key": settings.api_key},
                )
        except httpx.HTTPError:
            raise PaymentSyncError("provider_unavailable") from None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise PaymentSyncError("provider_unavailable")
        try:
            data = response.json()
        except ValueError:
            raise PaymentSyncError("provider_invalid_response") from None
        if not isinstance(data, dict):
            raise PaymentSyncError("provider_invalid_response")
        return data

    def query_remote_product(
        self,
        product_id: str,
        settings: CreemSettings,
        *,
        client: httpx.Client | None = None,
    ) -> dict[str, Any] | None:
        url = f"{settings.api_base_url}/products"
        try:
            if client is not None:
                response = client.get(
                    url,
                    params={"product_id": product_id},
                    headers={"x-api-key": settings.api_key},
                )
            else:
                with httpx.Client(
                    timeout=CREEM_HTTP_TIMEOUT,
                    transport=self.http_transport,
                    follow_redirects=False,
                ) as owned_client:
                    response = owned_client.get(
                        url,
                        params={"product_id": product_id},
                        headers={"x-api-key": settings.api_key},
                    )
        except httpx.HTTPError:
            raise PaymentSyncError("provider_unavailable") from None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise PaymentSyncError("provider_unavailable")
        try:
            data = response.json()
        except ValueError:
            raise PaymentSyncError("provider_invalid_response") from None
        if not isinstance(data, dict):
            raise PaymentSyncError("provider_invalid_response")
        return data

    def _find_creem_order(
        self,
        session: Session,
        *,
        out_trade_no: str | None = None,
        trade_no: str | None = None,
        checkout_id: str | None = None,
    ) -> PaymentOrder | None:
        if out_trade_no:
            found = session.exec(
                select(PaymentOrder).where(
                    PaymentOrder.provider == "creem",
                    PaymentOrder.out_trade_no == out_trade_no,
                )
            ).first()
            if found:
                return found
        if trade_no:
            found = session.exec(
                select(PaymentOrder).where(
                    PaymentOrder.provider == "creem",
                    PaymentOrder.trade_no == trade_no,
                )
            ).first()
            if found:
                return found
        if checkout_id:
            return session.exec(
                select(PaymentOrder).where(
                    PaymentOrder.provider == "creem",
                    PaymentOrder.provider_checkout_id == checkout_id,
                )
            ).first()
        return None

    def _validate_product_for_order(
        self,
        order: PaymentOrder,
        remote_product_id: str | None,
        settings: CreemSettings,
    ) -> None:
        expected = settings.product_id_for_cycle(order.cycle)
        if expected and remote_product_id and remote_product_id != expected:
            raise CallbackError("order_mismatch")

    def handle_webhook(
        self,
        session: Session,
        *,
        raw_body: bytes,
        signature: str | None,
        settings: CreemSettings,
    ) -> dict[str, object]:
        if not settings.webhook_configured:
            raise CallbackError("not_configured")
        if not verify_creem_signature(raw_body, settings.webhook_secret, signature):
            raise CallbackError("invalid_signature")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CallbackError("invalid_payload") from exc
        if not isinstance(payload, dict):
            raise CallbackError("invalid_payload")

        event_type = payload.get("eventType")
        event_id = str(payload.get("id") or "")[:64]
        obj = payload.get("object")
        if not isinstance(event_type, str) or not isinstance(obj, dict):
            raise CallbackError("invalid_payload")
        if not _mode_matches(obj.get("mode"), test_mode=settings.test_mode):
            raise CallbackError("mode_mismatch")

        if event_type == "checkout.completed":
            self._handle_checkout_completed(session, obj, settings)
            return {"received": True, "event_type": event_type, "event_id": event_id}
        if event_type == "refund.created":
            self._handle_refund_created(session, obj)
            return {"received": True, "event_type": event_type, "event_id": event_id}
        if event_type == "dispute.created":
            self._handle_dispute_created(session, obj)
            return {"received": True, "event_type": event_type, "event_id": event_id}

        log_with_context(
            logger,
            logging.INFO,
            "Creem webhook event ignored",
            event_type=event_type[:64],
            event_id=event_id,
        )
        return {"received": True, "ignored": True, "event_type": event_type}

    def _handle_checkout_completed(
        self,
        session: Session,
        obj: dict[str, Any],
        settings: CreemSettings,
    ) -> None:
        status_val = str(obj.get("status") or "").strip().lower()
        if status_val and status_val != "completed":
            raise CallbackError("not_successful")

        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        out_trade_no = _extract_id(obj.get("request_id")) or _extract_id(metadata.get("out_trade_no"))
        checkout_id = _extract_id(obj.get("id"))
        order_obj = obj.get("order") if isinstance(obj.get("order"), dict) else None
        trade_no = _extract_id(obj.get("order"))
        if not trade_no or len(trade_no) > 64:
            raise CallbackError("invalid_trade_number")
        if order_obj:
            order_status = str(order_obj.get("status") or "").strip().lower()
            if order_status and order_status != "paid":
                raise CallbackError("not_successful")

        order = self._find_creem_order(
            session, out_trade_no=out_trade_no, checkout_id=checkout_id
        )
        if not order:
            raise CallbackError("unknown_order")
        if out_trade_no and order.out_trade_no != out_trade_no:
            raise CallbackError("order_mismatch")

        remote_product_id = _extract_id(obj.get("product")) or (
            _extract_id(order_obj.get("product")) if order_obj else None
        )
        self._validate_product_for_order(order, remote_product_id, settings)

        currency = _extract_currency(obj, order_obj)
        amount_cents = _extract_amount(obj, order_obj)
        if not currency or amount_cents is None:
            raise CallbackError("missing_fields")

        if checkout_id and not order.provider_checkout_id:
            session.exec(
                update(PaymentOrder)
                .where(PaymentOrder.id == order.id, PaymentOrder.provider_checkout_id.is_(None))
                .values(provider_checkout_id=checkout_id)
            )
            session.commit()

        if order.currency.upper() != currency.upper() or order.amount_cents != amount_cents:
            record_paid_amount_mismatch(
                session,
                order=order,
                trade_no=trade_no,
                actual_amount_cents=amount_cents,
                actual_currency=currency,
                channel="webhook",
            )
            raise CallbackError("amount_mismatch")

        settle_paid_order(
            session,
            provider="creem",
            out_trade_no=order.out_trade_no,
            trade_no=trade_no,
            amount_cents=amount_cents,
            currency=currency,
            payment_method="card",
            product_name=None,
            channel="webhook",
        )

    def _handle_refund_created(self, session: Session, obj: dict[str, Any]) -> None:
        refund_status = str(obj.get("status") or "").strip().lower()
        if refund_status and refund_status not in {"succeeded", "completed", "refunded"}:
            return
        refund_id = _extract_id(obj.get("id"))
        out_trade_no = _extract_out_trade_no(obj)
        trade_no = _extract_id(obj.get("order"))
        checkout_id = _extract_id(obj.get("checkout"))
        refund_amount_cents = _extract_refund_amount(obj)
        order = self._find_creem_order(
            session,
            out_trade_no=out_trade_no,
            trade_no=trade_no,
            checkout_id=checkout_id,
        )
        if not order:
            raise CallbackError("unknown_order")
        record_refund(
            session,
            order=order,
            refund_id=refund_id,
            refund_amount_cents=refund_amount_cents,
            trade_no=trade_no,
            channel="webhook",
        )

    def _handle_dispute_created(self, session: Session, obj: dict[str, Any]) -> None:
        dispute_id = _extract_id(obj.get("id"))
        out_trade_no = _extract_out_trade_no(obj)
        trade_no = _extract_id(obj.get("order"))
        if not trade_no and isinstance(obj.get("transaction"), dict):
            trade_no = _extract_id(obj["transaction"].get("order"))
        checkout_id = _extract_id(obj.get("checkout"))
        order = self._find_creem_order(
            session,
            out_trade_no=out_trade_no,
            trade_no=trade_no,
            checkout_id=checkout_id,
        )
        if not order:
            raise CallbackError("unknown_order")
        record_dispute(session, order=order, dispute_id=dispute_id, trade_no=trade_no)

    def sync_order(
        self, session: Session, order: PaymentOrder, settings: CreemSettings
    ) -> SyncOutcome:
        """Compensate a missed Creem webhook by querying GET /v1/checkouts."""
        if order.fulfillment_status == "succeeded":
            return "already_fulfilled"
        if not settings.credentials_configured:
            raise PaymentSyncError("not_configured")
        if not order.provider_checkout_id:
            return "not_found"

        remote = self.query_remote_checkout(order.provider_checkout_id, settings)
        if remote is None:
            return "not_found"
        if not _mode_matches(remote.get("mode"), test_mode=settings.test_mode):
            raise PaymentSyncError("mode_mismatch")
        if str(remote.get("status") or "").strip().lower() != "completed":
            return "unpaid"

        remote_request_id = _extract_id(remote.get("request_id"))
        if remote_request_id and remote_request_id != order.out_trade_no:
            raise PaymentSyncError("order_mismatch")

        order_obj = remote.get("order") if isinstance(remote.get("order"), dict) else None
        trade_no = _extract_id(remote.get("order"))
        if not trade_no or len(trade_no) > 64:
            raise PaymentSyncError("invalid_trade_number")

        remote_product_id = _extract_id(remote.get("product")) or (
            _extract_id(order_obj.get("product")) if order_obj else None
        )
        try:
            self._validate_product_for_order(order, remote_product_id, settings)
        except CallbackError as exc:
            raise PaymentSyncError(exc.reason) from exc

        currency = _extract_currency(remote, order_obj) or order.currency
        amount_cents = _extract_amount(remote, order_obj)
        if amount_cents is None:
            # GET /v1/checkouts returns order as a string ID on some Creem versions;
            # only settle if the verified product_id matched the order's cycle.
            expected_product = settings.product_id_for_cycle(order.cycle)
            if not expected_product or remote_product_id != expected_product:
                raise PaymentSyncError("order_mismatch")
            amount_cents = order.amount_cents
        elif currency.upper() != order.currency.upper() or amount_cents != order.amount_cents:
            try:
                record_paid_amount_mismatch(
                    session,
                    order=order,
                    trade_no=trade_no,
                    actual_amount_cents=amount_cents,
                    actual_currency=currency,
                    channel="query",
                )
            except CallbackError as exc:
                raise PaymentSyncError(exc.reason) from exc
            raise PaymentSyncError("amount_mismatch")

        try:
            settle_paid_order(
                session,
                provider="creem",
                out_trade_no=order.out_trade_no,
                trade_no=trade_no,
                amount_cents=amount_cents,
                currency=currency,
                payment_method="card",
                product_name=None,
                channel="query",
            )
        except CallbackError as exc:
            raise PaymentSyncError(exc.reason) from exc
        return "fulfilled"

    def log_configuration_status(self, settings: CreemSettings) -> None:
        if not settings.enabled:
            return
        problems = settings.configuration_problems()
        if problems:
            log_with_context(
                logger,
                logging.ERROR,
                "CREEM_ENABLED=true but Creem checkout is misconfigured; card checkout is disabled",
                problems=problems,
            )


creem_service = CreemService()
