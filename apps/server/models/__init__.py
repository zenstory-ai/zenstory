"""
Database models module.

Exports all database entities and models.
"""

from .activation_event import (
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
    ACTIVATION_EVENT_NAMES,
    ACTIVATION_EVENT_PROJECT_CREATED,
    ACTIVATION_EVENT_SIGNUP_SUCCESS,
    ActivationEvent,
)
from .agent_api_key import (
    DEFAULT_SCOPES,
    AgentApiKey,
)
from .ai_cost_budget import AICostDailyBudget, AICostReservation
from .entities import (
    AgentArtifactLedger,
    ChatMessage,
    ChatSession,
    Project,
    Snapshot,
    SystemPromptConfig,
    User,
)
from .feedback import UserFeedback
from .file_model import (
    FILE_TYPE_CHARACTER,
    FILE_TYPE_DRAFT,
    FILE_TYPE_FOLDER,
    FILE_TYPE_LORE,
    FILE_TYPE_METADATA_SCHEMA,
    FILE_TYPE_OUTLINE,
    FILE_TYPE_SCRIPT,
    FILE_TYPE_SNIPPET,
    File,
)
from .file_version import (
    CHANGE_SOURCE_AI,
    CHANGE_SOURCE_SYSTEM,
    CHANGE_SOURCE_USER,
    CHANGE_TYPE_AI_EDIT,
    CHANGE_TYPE_AUTO_SAVE,
    CHANGE_TYPE_CREATE,
    CHANGE_TYPE_EDIT,
    CHANGE_TYPE_RESTORE,
    VERSION_BASE_INTERVAL,
    FileVersion,
)
from .inspiration import Inspiration
from .llm_usage import LLMUsageEvent
from .material_models import (
    Chapter,
    Character,
    CharacterRelationship,
    EventTimeline,
    GoldenFinger,
    IngestionJob,
    Novel,
    Plot,
    ProcessCheckpoint,
    Story,
    StoryLine,
    StoryPlotLink,
    WorldView,
)
from .payment import PaymentOrder
from .persona_profile import UserPersonaProfile
from .points import (
    CheckInRecord,
    PointsTransaction,
)
from .public_skill import PublicSkill
from .referral import (
    REFERRAL_STATUS_COMPLETED,
    REFERRAL_STATUS_PENDING,
    REFERRAL_STATUS_REWARDED,
    REWARD_TYPE_CREDITS,
    REWARD_TYPE_POINTS,
    REWARD_TYPE_PRO_TRIAL,
    InviteCode,
    Referral,
    UserReward,
    UserStats,
)
from .refresh_token import RefreshTokenRecord
from .skill import UserSkill
from .skill_resource import SkillResource
from .skill_usage import SkillUsage
from .subscription import (
    AdminAuditLog,
    RedemptionCode,
    SubscriptionHistory,
    SubscriptionPlan,
    UsageQuota,
    UserSubscription,
)
from .upgrade_funnel_event import (
    UPGRADE_FUNNEL_ACTION_CLICK,
    UPGRADE_FUNNEL_ACTION_CONVERSION,
    UPGRADE_FUNNEL_ACTION_EXPOSE,
    UPGRADE_FUNNEL_ACTIONS,
    UPGRADE_FUNNEL_CTAS,
    UPGRADE_FUNNEL_EVENT_NAME_BY_ACTION,
    UPGRADE_FUNNEL_SURFACES,
    UpgradeFunnelEvent,
)
from .user_added_skill import UserAddedSkill
from .writing_stats import (
    WritingStats,
    WritingStreak,
)

__all__ = [
    "AICostDailyBudget",
    "AICostReservation",
    # Entities
    "User",
    "Project",
    "AgentArtifactLedger",
    "Snapshot",
    "ChatSession",
    "ChatMessage",
    "SystemPromptConfig",
    # File models
    "File",
    "FileVersion",
    "UserFeedback",
    # File type constants
    "FILE_TYPE_OUTLINE",
    "FILE_TYPE_DRAFT",
    "FILE_TYPE_CHARACTER",
    "FILE_TYPE_LORE",
    "FILE_TYPE_SNIPPET",
    "FILE_TYPE_SCRIPT",
    "FILE_TYPE_FOLDER",
    "FILE_TYPE_METADATA_SCHEMA",
    # FileVersion constants
    "VERSION_BASE_INTERVAL",
    "CHANGE_TYPE_CREATE",
    "CHANGE_TYPE_EDIT",
    "CHANGE_TYPE_AI_EDIT",
    "CHANGE_TYPE_RESTORE",
    "CHANGE_TYPE_AUTO_SAVE",
    "CHANGE_SOURCE_USER",
    "CHANGE_SOURCE_AI",
    "CHANGE_SOURCE_SYSTEM",
    # Skill model
    "UserSkill",
    "SkillResource",
    "SkillUsage",
    "PublicSkill",
    "UserAddedSkill",
    # Material library models
    "Novel",
    "Chapter",
    "Plot",
    "Story",
    "StoryLine",
    "StoryPlotLink",
    "Character",
    "CharacterRelationship",
    "GoldenFinger",
    "WorldView",
    "IngestionJob",
    "ProcessCheckpoint",
    "EventTimeline",
    # Inspiration model
    "Inspiration",
    # Referral system models
    "InviteCode",
    "Referral",
    "UserReward",
    "UserStats",
    # Referral system constants
    "REFERRAL_STATUS_PENDING",
    "REFERRAL_STATUS_COMPLETED",
    "REFERRAL_STATUS_REWARDED",
    "REWARD_TYPE_POINTS",
    "REWARD_TYPE_PRO_TRIAL",
    "REWARD_TYPE_CREDITS",
    # Subscription models
    "SubscriptionPlan",
    "UserSubscription",
    "RedemptionCode",
    "UsageQuota",
    "SubscriptionHistory",
    "AdminAuditLog",
    "PaymentOrder",
    # LLM usage ledger
    "LLMUsageEvent",
    # Points and check-in models
    "PointsTransaction",
    "CheckInRecord",
    "UserPersonaProfile",
    # Auth security models
    "RefreshTokenRecord",
    # Agent API Key models
    "AgentApiKey",
    "DEFAULT_SCOPES",
    # Activation events
    "ActivationEvent",
    "ACTIVATION_EVENT_SIGNUP_SUCCESS",
    "ACTIVATION_EVENT_PROJECT_CREATED",
    "ACTIVATION_EVENT_FIRST_FILE_SAVED",
    "ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED",
    "ACTIVATION_EVENT_NAMES",
    # Upgrade funnel events
    "UpgradeFunnelEvent",
    "UPGRADE_FUNNEL_ACTION_EXPOSE",
    "UPGRADE_FUNNEL_ACTION_CLICK",
    "UPGRADE_FUNNEL_ACTION_CONVERSION",
    "UPGRADE_FUNNEL_ACTIONS",
    "UPGRADE_FUNNEL_EVENT_NAME_BY_ACTION",
    "UPGRADE_FUNNEL_SURFACES",
    "UPGRADE_FUNNEL_CTAS",
    # Writing statistics models
    "WritingStats",
    "WritingStreak",
]
