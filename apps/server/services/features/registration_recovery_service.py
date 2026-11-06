"""Password-proven address correction for unfinished password registrations."""

import asyncio

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import User
from services.core.auth_service import verify_password
from services.core.registration_email_policy import ensure_registration_email_allowed
from services.features import verification_service
from services.infra.email_client import InvalidRecipientError
from utils.email_identity import email_identity_matches, normalize_email_identity, validate_registration_email
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


async def correct_registration_email(
    session: Session, *, email: str, password: str, new_email: str, language: str,
) -> str:
    old_email = normalize_email_identity(email)
    user = await asyncio.to_thread(lambda: session.exec(
        select(User).where(email_identity_matches(User.email, old_email)).with_for_update()
        .execution_options(populate_existing=True)
    ).first())
    valid_password = False
    if user and user.hashed_password:
        valid_password = await asyncio.to_thread(verify_password, password, user.hashed_password)
    if not user or not valid_password or user.email_verified or not user.is_active:
        raise APIException(error_code=ErrorCode.AUTH_INVALID_CREDENTIALS, status_code=401)

    corrected = normalize_email_identity(new_email)
    ensure_registration_email_allowed(corrected, entry="registration_email")
    try:
        validate_registration_email(corrected)
    except ValueError:
        raise APIException(error_code=ErrorCode.AUTH_EMAIL_UNDELIVERABLE, status_code=400) from None
    duplicate = session.exec(select(User).where(
        email_identity_matches(User.email, corrected), User.id != user.id,
    )).first()
    if duplicate:
        raise APIException(error_code=ErrorCode.AUTH_EMAIL_EXISTS, status_code=400)

    try:
        # Reserve the new address before creating its code. Unique-index waits
        # and verification's User lock must never block the event loop.
        user.email = corrected
        user.updated_at = utcnow()
        session.add(user)
        await asyncio.to_thread(session.flush)
        sent, _error = await verification_service.send_verification_code(corrected, language=language)
        if not sent:
            session.rollback()
            raise APIException(error_code=ErrorCode.AUTH_RESEND_FAILED, status_code=503)
        if old_email != corrected:
            try:
                # Retain the old unique address reservation until its code is
                # gone; a subsequent registration must write only after this.
                await verification_service.clear_registration_verification(old_email)
            except Exception:
                session.rollback()
                log_with_context(logger, 30, "Registration address invalidation unavailable")
                raise APIException(error_code=ErrorCode.AUTH_RESEND_FAILED, status_code=503) from None
        session.commit()
    except InvalidRecipientError:
        session.rollback()
        raise APIException(error_code=ErrorCode.AUTH_EMAIL_UNDELIVERABLE, status_code=400) from None
    except IntegrityError:
        session.rollback()
        raise APIException(error_code=ErrorCode.AUTH_EMAIL_EXISTS, status_code=400) from None

    return corrected
