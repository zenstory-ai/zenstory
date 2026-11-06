"""One free material trial per client network per Beijing day.

Separate from the AI-message network allowance: starting a trial costs up to the whole
lifetime trial budget, so one network starting many accounts is what this rule stops.
A seat is never given back. A request without a usable client network skips only this
rule; the per-account trial and its cost limit still apply. Logs carry no address.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, text
from sqlmodel import Session, col

from config.datetime_utils import utcnow
from models.material_trial import MaterialTrialNetworkSeat
from services.usage.free_ai_network_quota import network_key
from services.usage.pricing import beijing_today
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

SEAT_RETENTION_DAYS = 7
_INFO = 20
_WARNING = 30


def network_seat_taken(session: Session, client_ip: str | None) -> bool:
    """Read-only check before storing the upload; ``claim_network_seat`` decides."""
    key = network_key(client_ip)
    if key is None:
        return False
    return session.get(MaterialTrialNetworkSeat, (beijing_today(utcnow()), key)) is not None


def claim_network_seat(session: Session, *, user_id: str, client_ip: str | None) -> bool:
    """Take today's seat in the open transaction; False when another account holds it."""
    key = network_key(client_ip)
    if key is None:
        log_with_context(logger, _WARNING, "material_trial_network_limit_skipped", user_id=user_id)
        return True
    now = utcnow()
    day = beijing_today(now)
    # A concurrent claim waits on the uncommitted row and sees the conflict after commit.
    inserted = session.execute(
        text(
            "INSERT INTO material_trial_network_seat (day, ip_key, user_id, created_at) "
            "VALUES (:day, :ip_key, :user_id, :created_at) ON CONFLICT (day, ip_key) DO NOTHING"
        ),
        {"day": day, "ip_key": key, "user_id": user_id, "created_at": now},
    )
    if inserted.rowcount != 1:  # type: ignore[attr-defined]
        log_with_context(
            logger, _INFO, "material_trial_network_limit_reached",
            user_id=user_id, network=key[:12], day=day.isoformat(),
        )
        return False
    session.exec(delete(MaterialTrialNetworkSeat).where(
        col(MaterialTrialNetworkSeat.day) < day - timedelta(days=SEAT_RETENTION_DAYS)
    ))
    return True


__all__ = ["claim_network_seat", "network_seat_taken"]
