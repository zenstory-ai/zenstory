"""Persona onboarding requirement calculation shared with authentication responses."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from config.feature_flags import is_first_work_onboarding_enabled
from models import User, UserPersonaProfile, WorkspaceOnboarding
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

PERSONA_ONBOARDING_ROLLOUT_AT_ENV = "PERSONA_ONBOARDING_ROLLOUT_AT"
PERSONA_ONBOARDING_WINDOW_DAYS_ENV = "PERSONA_ONBOARDING_NEW_USER_WINDOW_DAYS"
PERSONA_ONBOARDING_DEFAULT_ROLLOUT_AT = "2026-03-05T16:00:00Z"
PERSONA_ONBOARDING_DEFAULT_WINDOW_DAYS = 7


def _normalize_datetime_to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def get_rollout_datetime() -> datetime:
    raw = os.getenv(PERSONA_ONBOARDING_ROLLOUT_AT_ENV, PERSONA_ONBOARDING_DEFAULT_ROLLOUT_AT).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = datetime.fromisoformat(PERSONA_ONBOARDING_DEFAULT_ROLLOUT_AT.replace("Z", "+00:00"))
    return _normalize_datetime_to_utc(parsed)


def get_rollout_window_days() -> int:
    raw = (os.getenv(PERSONA_ONBOARDING_WINDOW_DAYS_ENV, str(PERSONA_ONBOARDING_DEFAULT_WINDOW_DAYS)) or "").strip()
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return PERSONA_ONBOARDING_DEFAULT_WINDOW_DAYS


def is_legacy_onboarding_required(*, user: User, has_profile: bool) -> bool:
    if has_profile:
        return False

    rollout_at = get_rollout_datetime()
    created_at = _normalize_datetime_to_utc(user.created_at)
    if created_at < rollout_at:
        return False

    account_age = _normalize_datetime_to_utc(utcnow()) - created_at
    max_window = timedelta(days=get_rollout_window_days())
    return timedelta(0) <= account_age <= max_window


def is_persona_onboarding_required(session: Session, user: User) -> bool:
    """Compute whether the user must complete persona onboarding.

    Matches `GET /api/v1/persona/onboarding`'s `required` semantics exactly:
    - False once a `UserPersonaProfile` row exists (answered or skipped).
    - True when the account falls inside the rollout new-user age window.
    - True when first-work onboarding is enabled and a pending `WorkspaceOnboarding` row awaits a profile.
    """
    profile_id = session.exec(
        select(UserPersonaProfile.id).where(UserPersonaProfile.user_id == user.id)
    ).first()
    if profile_id is not None:
        return False

    if is_legacy_onboarding_required(user=user, has_profile=False):
        return True

    if not is_first_work_onboarding_enabled():
        return False

    first_work = session.get(WorkspaceOnboarding, user.id)
    return first_work is not None and first_work.status == "pending"


def get_onboarding_required_for_auth(session: Session, user: User) -> bool | None:
    """Best-effort onboarding check for auth payloads (`/me`, `/login`, `/refresh`).

    Uses a savepoint when already inside a transaction so a lookup failure rolls
    back only this read and never breaks sign-in or token refresh. Returning
    `None` signals the client to fall back to `GET /api/v1/persona/onboarding`.
    """
    in_tx = session.in_transaction()
    try:
        if in_tx:
            with session.begin_nested():
                return is_persona_onboarding_required(session, user)
        return is_persona_onboarding_required(session, user)
    except SQLAlchemyError as exc:
        if not in_tx:
            session.rollback()
        log_with_context(
            logger,
            logging.WARNING,
            "Could not compute persona onboarding requirement for auth response",
            user_id=user.id,
            error_type=type(exc).__name__,
        )
        return None
