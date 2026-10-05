"""
Redemption Service - Handles redemption code validation and redemption.
"""
import hashlib
import hmac
import os
import re
from contextlib import suppress
from datetime import UTC

from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ERROR_MESSAGES, ErrorCode
from core.error_handler import APIException
from models.subscription import RedemptionCode
from services.subscription.subscription_service import subscription_service
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


class RedemptionService:
    """Service for redemption code operations."""

    CODE_PATTERN = re.compile(r"^ERG-([A-Z0-9]{2,8})-([A-Z0-9]{4})-([A-Z0-9]{8})$")

    def _get_error_message(self, error_code: str, lang: str = "en") -> str:
        """Get localized error message safely from error-code map."""
        lang_messages = ERROR_MESSAGES.get(lang) or ERROR_MESSAGES.get("en", {})
        return lang_messages.get(error_code, error_code)

    def get_hmac_secret(self) -> str:
        """Get HMAC secret from environment."""
        secret = os.getenv("REDEMPTION_CODE_HMAC_SECRET", "")
        if len(secret) < 32:
            raise ValueError("REDEMPTION_CODE_HMAC_SECRET must be at least 32 characters")
        return secret

    def validate_code_format(self, code: str) -> bool:
        """Validate code format: ERG-{TIER}{DURATION}-{CHECKSUM4}-{RANDOM8}"""
        return bool(self.CODE_PATTERN.match(code))

    def verify_checksum(self, code: str) -> bool:
        """Verify the checksum portion of the code using HMAC-SHA256."""
        match = self.CODE_PATTERN.match(code)
        if not match:
            return False

        tier_duration = match.group(1)
        random_part = match.group(3)
        provided_checksum = match.group(2)

        # Generate expected checksum
        secret = self.get_hmac_secret()
        message = f"{tier_duration}-{random_part}"
        signature = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).digest()

        # Take first 3 bytes, encode as hex, take first 4 chars uppercase
        expected_checksum = signature[:4].hex().upper()[:4]

        return hmac.compare_digest(provided_checksum, expected_checksum)

    def get_code_by_code(self, session: Session, code: str) -> RedemptionCode | None:
        """Look up a redemption code."""
        return session.exec(
            select(RedemptionCode).where(RedemptionCode.code == code)
        ).first()

    def _is_downgrade(self, session: Session, user_id: str, code_tier: str) -> bool:
        current_plan = subscription_service.get_effective_paid_plan(session, user_id)
        if not current_plan or current_plan.name == code_tier:
            return False
        code_plan = subscription_service.get_plan_by_name(session, code_tier)
        if not code_plan:
            return True
        return code_plan.price_monthly_cents < current_plan.price_monthly_cents

    def redeem_code(
        self,
        session: Session,
        code: str,
        user_id: str,
        *,
        attribution_source: str | None = None,
    ) -> tuple[bool, str, dict | None]:
        """
        Redeem a code for a user.

        Returns: (success, message, subscription_info)
        """
        # Step 1: Validate format
        if not self.validate_code_format(code):
            return (False, self._get_error_message(ErrorCode.REDEMPTION_CODE_INVALID), None)

        try:
            # Step 2: Verify checksum
            if not self.verify_checksum(code):
                return (False, self._get_error_message(ErrorCode.REDEMPTION_CODE_CHECKSUM_FAILED), None)

            # Step 3: Lock code row for safe concurrent redemption checks.
            redemption_code = session.exec(
                select(RedemptionCode)
                .where(RedemptionCode.code == code)
                .with_for_update()
            ).first()
            if not redemption_code:
                return (False, self._get_error_message(ErrorCode.REDEMPTION_CODE_INVALID), None)

            # Step 4: Check if active
            if not redemption_code.is_active:
                return (False, self._get_error_message(ErrorCode.REDEMPTION_CODE_DISABLED), None)

            # Step 5: Check expiration
            if redemption_code.expires_at:
                expires_at = redemption_code.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at < utcnow():
                    return (False, self._get_error_message(ErrorCode.REDEMPTION_CODE_EXPIRED), None)

            redeemed_by = list(redemption_code.redeemed_by or [])

            # Step 6: Prevent duplicate redemption by same user.
            if user_id in redeemed_by:
                return (False, "You have already redeemed this code", None)

            # Step 7: Enforce usage limits under lock.
            if redemption_code.max_uses is not None and redemption_code.current_uses >= redemption_code.max_uses:
                return (False, self._get_error_message(ErrorCode.REDEMPTION_CODE_USED), None)

            # Step 8: A lower tier would replace (and discard) the paid plan's
            # remaining days, so such codes are refused while that plan is running.
            if self._is_downgrade(session, user_id, redemption_code.tier):
                return (False, self._get_error_message(ErrorCode.REDEMPTION_CODE_DOWNGRADE), None)

            redemption_code.current_uses += 1
            redemption_code.redeemed_by = redeemed_by + [user_id]
            session.add(redemption_code)

            metadata = {"source": "redemption_code", "code_id": redemption_code.id}
            if attribution_source:
                metadata["upgrade_source"] = attribution_source

            subscription = subscription_service.create_user_subscription(
                session, user_id, redemption_code.tier, redemption_code.duration_days,
                metadata=metadata,
                commit=False,
            )
            session.commit()
            with suppress(Exception):
                session.refresh(subscription)

            return (
                True,
                f"Successfully redeemed {redemption_code.tier} plan for {redemption_code.duration_days} days",
                {
                    "tier": redemption_code.tier,
                    "duration_days": redemption_code.duration_days,
                    "subscription_id": subscription.id
                }
            )
        except Exception as e:
            session.rollback()
            log_with_context(
                logger,
                40,
                "redeem_code failed",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise APIException(
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                status_code=500,
                detail="Redemption failed",
            ) from e


redemption_service = RedemptionService()
