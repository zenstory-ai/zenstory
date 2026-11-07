"""
Feature services package.

Contains business logic services for various features:
- referral_service: Invite code and referral management
- verification_service: Email verification
- file_version_service: File version management
- export_service: Project export
- points_service: Points and check-in management
"""

from .activation_event_service import (
    ActivationEventService,
    activation_event_service,
)
from .points_service import (
    PointsService,
    points_service,
)
from .referral_service import (
    complete_pending_referral_for_invitee,
    complete_referral_and_reward,
    create_invite_code,
    create_referral,
    generate_invite_code,
    get_user_invite_codes,
    get_user_referral_stats,
    validate_invite_code,
)
from .upgrade_funnel_event_service import (
    UpgradeFunnelEventService,
    upgrade_funnel_event_service,
)
from .verification_service import (
    generate_verification_code,
    get_code_ttl,
    get_remaining_cooldown,
    send_verification_code,
    verify_code,
)

__all__ = [
    # Referral service
    "generate_invite_code",
    "create_invite_code",
    "validate_invite_code",
    "create_referral",
    "complete_referral_and_reward",
    "complete_pending_referral_for_invitee",
    "get_user_referral_stats",
    "get_user_invite_codes",
    # Verification service
    "generate_verification_code",
    "send_verification_code",
    "verify_code",
    "get_remaining_cooldown",
    "get_code_ttl",
    # Points service
    "points_service",
    "PointsService",
    # Activation event service
    "activation_event_service",
    "ActivationEventService",
    # Upgrade funnel event service
    "upgrade_funnel_event_service",
    "UpgradeFunnelEventService",
]
