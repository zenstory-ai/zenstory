"""Account interface language: read it after sign-in, store it on sign-up and when the user switches."""
import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from config.datetime_utils import utcnow
from models import UserPreference
from models.user_preference import SUPPORTED_UI_LANGUAGES
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


def normalize_ui_language(value: str | None) -> str | None:
    """'en-US' -> 'en'; anything the interface does not offer -> None."""
    primary = (value or "").split(",")[0].split("-")[0].strip().lower()
    return primary if primary in SUPPORTED_UI_LANGUAGES else None


def get_ui_language(session: Session, user_id: str) -> str | None:
    """The saved language, or None. Sign-in and /me read it: a database problem here must not fail them."""
    try:
        preference = session.get(UserPreference, user_id)
    except SQLAlchemyError as exc:
        session.rollback()
        log_with_context(
            logger,
            logging.WARNING,
            "Could not read the account interface language",
            user_id=user_id,
            error_type=type(exc).__name__,
        )
        return None
    return preference.ui_language if preference else None


def set_ui_language(session: Session, user_id: str, language: str | None) -> str | None:
    """Stage a supported language and return it; an unsupported value changes nothing and returns None.

    Adds to the caller's session without committing or reading anything else, so sign-up can keep
    it in its own transaction.
    """
    normalized = normalize_ui_language(language)
    if normalized is None:
        return None
    preference = session.get(UserPreference, user_id)
    if preference is None:
        preference = UserPreference(user_id=user_id)
    preference.ui_language = normalized
    preference.updated_at = utcnow()
    session.add(preference)
    return normalized
