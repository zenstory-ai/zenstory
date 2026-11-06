"""Daily free AI allowance shared by the accounts on one client network.

The claim runs inside the caller's account-charge transaction, so a refusal rolls the
account charge back and a crash can never leave one count without the other. Everything
lives in PostgreSQL: Redis being down does not change this rule. A database failure
fails the request before any model call, like the account charge itself.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import date, timedelta
from ipaddress import IPv6Address, ip_address, ip_network
from typing import Literal

from fastapi import Request
from sqlalchemy import delete, func, text, update
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models.free_ai_network_quota import FreeAINetworkReservation
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

FREE_NETWORK_DAILY_MESSAGES = 9
FREE_NETWORK_DAILY_ACCOUNTS = 3
# Refunds only ever concern a run's own day; the rest of the week is a safety margin.
RESERVATION_RETENTION_DAYS = 7

_INFO = 20
_WARNING = 30

_last_purge_day: date | None = None


class FreeNetworkQuotaExceeded(Exception):
    """The network has no free allowance left today. The account charge was rolled back."""

    def __init__(self, reason: Literal["messages", "accounts"]):
        super().__init__(reason)
        self.reason = reason


def free_network_quota_exception() -> APIException:
    return APIException(
        error_code=ErrorCode.QUOTA_FREE_NETWORK_DAILY_EXCEEDED,
        status_code=402,
        detail="Today's free AI allowance on this network is used up.",
    )


def require_canonical_request_path(request: Request) -> None:
    """Refuse a percent-encoded spelling of a charged path.

    Such a spelling slips past the production /api fallback's path exclusion and arrives
    from cloud egress addresses, which would count as another network. No client of ours
    encodes these paths.
    """
    raw_path = request.scope.get("raw_path")
    if raw_path is not None and raw_path != request.scope["path"].encode():
        raise APIException(error_code=ErrorCode.NOT_FOUND, status_code=404)


def network_key(client_ip: str | None) -> str | None:
    """Keyed hash of the client's network, or None when the value does not identify one.

    Non-public addresses mean the proxy chain gave us no client address (a misconfigured
    hop count, local development). Pooling those would put every user in one bucket.
    IPv6 counts per /64 because one subscriber holds a whole /64 and can rotate within it.
    A 6to4 or Teredo client picks its own address, so it counts as the public IPv4 inside
    it, or as the address itself: a tunnel address never skips the rule.
    """
    try:
        address = ip_address((client_ip or "").strip())
    except ValueError:
        return None
    is_tunnel = False
    if isinstance(address, IPv6Address):
        tunnelled = address.sixtofour or (address.teredo[1] if address.teredo else None)
        if address.ipv4_mapped:
            address = address.ipv4_mapped
        elif tunnelled:
            is_tunnel = True
            address = tunnelled if tunnelled.is_global else address
    if not address.is_global and not is_tunnel:
        return None
    network = str(ip_network(f"{address}/64", strict=False)) if address.version == 6 else str(address)

    from services.core.auth_service import SECRET_KEY

    digest = hmac.new(SECRET_KEY.encode(), f"free-ai-network:{network}".encode(), hashlib.sha256)
    return digest.hexdigest()


def _lock_network_day(session: Session, key: str, day: date) -> None:
    """Serialize claims per network and day until commit or rollback.

    SQLite already holds its single writer lock from the account charge. Lock order is
    account row, then this key; refunds never take this key, so no cycle exists.
    """
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"zenstory:free-ai-network:{key}:{day.isoformat()}"},
        )


def claim_network_share(
    session: Session, *, reservation_id: str, user_id: str, client_ip: str | None, day: date
) -> None:
    """Record this reservation in the open transaction, or raise FreeNetworkQuotaExceeded."""
    key = network_key(client_ip)
    if key is None:
        # The account limit and cost backstop still apply. Rejecting or pooling here
        # would stop every free user at once when the proxy chain is misconfigured.
        log_with_context(
            logger, _WARNING, "free_ai_network_limit_skipped",
            user_id=user_id, reason="missing" if not client_ip or client_ip == "unknown" else "unusable",
        )
    else:
        _lock_network_day(session, key, day)
        per_account = session.exec(
            select(FreeAINetworkReservation.user_id, func.count())
            .where(
                FreeAINetworkReservation.ip_key == key,
                FreeAINetworkReservation.day == day,
                FreeAINetworkReservation.released_at.is_(None),
            )
            .group_by(FreeAINetworkReservation.user_id)
        ).all()
        messages = sum(count for _account, count in per_account)
        seated = {account for account, _count in per_account}
        reason: Literal["messages", "accounts"] | None = None
        if messages >= FREE_NETWORK_DAILY_MESSAGES:
            reason = "messages"
        elif user_id not in seated and len(seated) >= FREE_NETWORK_DAILY_ACCOUNTS:
            reason = "accounts"
        if reason is not None:
            log_with_context(
                logger, _INFO, "free_ai_network_limit_reached",
                user_id=user_id, reason=reason, network=key[:12], day=day.isoformat(),
            )
            raise FreeNetworkQuotaExceeded(reason)

    session.add(FreeAINetworkReservation(
        id=reservation_id, user_id=user_id, day=day, ip_key=key, created_at=utcnow(),
    ))
    session.flush()


def mark_released(session: Session, *, reservation_id: str, user_id: str) -> bool | None:
    """Mark the caller's reservation released in the open transaction.

    True: this call released it. False: already released, or another account's: the
    caller must not touch any count. None: no such row (a paid account's reservation).
    """
    result = session.exec(
        update(FreeAINetworkReservation)
        .where(
            FreeAINetworkReservation.id == reservation_id,
            FreeAINetworkReservation.user_id == user_id,
            FreeAINetworkReservation.released_at.is_(None),
        )
        .values(released_at=utcnow())
    )
    if result.rowcount:
        return True
    exists = session.exec(
        select(FreeAINetworkReservation.id).where(FreeAINetworkReservation.id == reservation_id)
    ).first()
    return False if exists is not None else None


def purge_expired_reservations(session: Session, *, today: date) -> None:
    """Drop old rows, at most once a day per process; never affects the caller's charge."""
    global _last_purge_day
    if _last_purge_day == today:
        return
    _last_purge_day = today
    try:
        session.exec(delete(FreeAINetworkReservation).where(
            FreeAINetworkReservation.day < today - timedelta(days=RESERVATION_RETENTION_DAYS)
        ))
        session.commit()
    except Exception as exc:
        session.rollback()
        log_with_context(
            logger, _WARNING, "free_ai_network_purge_failed", error_type=type(exc).__name__,
        )
