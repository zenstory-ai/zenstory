"""
Activation event service.

Provides milestone recording and funnel aggregation for first-day activation analytics.
"""

import logging
from datetime import timedelta
from typing import TypedDict

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from config.datetime_utils import beijing_day_bounds, utcnow
from models import (
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT,
    ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED,
    ACTIVATION_EVENT_NAMES,
    ACTIVATION_EVENT_PROJECT_CREATED,
    ACTIVATION_EVENT_SIGNUP_SUCCESS,
    ActivationEvent,
    User,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# 后台漏斗：同一批新注册用户 24 小时内走到各步的人数。最后一步（AI 写进了作品文件）
# 就是激活定义，见 feature/2026-10-19-first-message-activation-events.md。走到后面一步的人
# 也计入前面每一步（发过 AI 消息必然有作品），所以各步只减不增。手动保存（first_file_saved）
# 不在这条 AI 激活链上，用 AI 的作者会跳过它，故不列入。
ACTIVATION_FUNNEL_ORDER = [
    ACTIVATION_EVENT_SIGNUP_SUCCESS,
    ACTIVATION_EVENT_PROJECT_CREATED,
    ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT,
    ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED,
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
]
ACTIVATION_FUNNEL_COHORT_HOURS = 24

ACTIVATION_FUNNEL_LABELS = {
    ACTIVATION_EVENT_SIGNUP_SUCCESS: "Signup Success",
    ACTIVATION_EVENT_PROJECT_CREATED: "Project Created",
    ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT: "First AI Message Sent",
    ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED: "First AI Reply Succeeded",
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED: "First AI Action Accepted",
}

class ActivationFunnelStep(TypedDict):
    """Single funnel step statistics."""

    event_name: str
    label: str
    users: int
    conversion_from_previous: float | None
    drop_off_from_previous: int | None


class ActivationFunnelStats(TypedDict):
    """Activation funnel statistics payload."""

    window_days: int
    period_start: str
    period_end: str
    steps: list[ActivationFunnelStep]
    activation_rate: float


def furthest_funnel_steps(rows, *, observation: timedelta) -> dict[str, int]:
    """Each user's furthest ``ACTIVATION_FUNNEL_ORDER`` index reached within ``observation`` of signup.

    ``rows`` are ``(user_id, event_name, event_at, signed_up_at)``; events outside the
    observation window or missing a timestamp do not count.
    """
    step_index = {event_name: index for index, event_name in enumerate(ACTIVATION_FUNNEL_ORDER)}
    furthest: dict[str, int] = {}
    for user_id, event_name, event_at, signed_up_at in rows:
        index = step_index.get(event_name)
        if index is None or event_at is None or signed_up_at is None or event_at - signed_up_at > observation:
            continue
        furthest[user_id] = max(furthest.get(user_id, 0), index)
    return furthest


class ActivationEventService:
    """Service for recording and querying activation milestone events."""

    def record_once(
        self,
        session: Session,
        *,
        user_id: str,
        event_name: str,
        project_id: str | None = None,
        event_metadata: dict | None = None,
    ) -> ActivationEvent:
        """
        Record an activation event once per user/event pair.

        Duplicate records are ignored and the existing event is returned.
        """
        if event_name not in ACTIVATION_EVENT_NAMES:
            raise ValueError(f"Unsupported activation event: {event_name}")

        existing = session.exec(
            select(ActivationEvent).where(
                ActivationEvent.user_id == user_id,
                ActivationEvent.event_name == event_name,
            )
        ).first()
        if existing:
            return existing

        event = ActivationEvent(
            user_id=user_id,
            project_id=project_id,
            event_name=event_name,
            event_metadata=event_metadata or {},
        )
        session.add(event)

        try:
            session.commit()
            session.refresh(event)
            return event
        except IntegrityError:
            # Another concurrent request already created the same milestone.
            session.rollback()
            existing = session.exec(
                select(ActivationEvent).where(
                    ActivationEvent.user_id == user_id,
                    ActivationEvent.event_name == event_name,
                )
            ).first()
            if existing:
                return existing
            raise

    def record_once_best_effort(
        self,
        session: Session,
        *,
        user_id: str,
        event_name: str,
        project_id: str | None = None,
        event_metadata: dict | None = None,
    ) -> None:
        """``record_once`` for observation points that must never fail the user's request."""
        try:
            self.record_once(
                session,
                user_id=user_id,
                event_name=event_name,
                project_id=project_id,
                event_metadata=event_metadata,
            )
        except Exception as exc:
            session.rollback()
            log_with_context(
                logger,
                logging.WARNING,
                "Failed to record activation event",
                user_id=user_id,
                project_id=project_id,
                event_name=event_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def record_ai_write_accepted(
        self,
        session: Session,
        *,
        user_id: str | None,
        project_id: str | None,
        file_id: str,
        file_type: str | None,
        tool: str,
    ) -> None:
        """Record ``first_ai_action_accepted`` after an agent tool wrote content.

        Agent tools persist content before any review, and the review flow only
        reaches the REST update when the user rejected something, so the agent
        write itself is the reliable signal. Best effort: the content is
        already committed, so a failure here is logged and swallowed.
        """
        if not user_id:
            return
        try:
            self.record_once(
                session,
                user_id=user_id,
                event_name=ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
                project_id=project_id,
                event_metadata={
                    "file_id": file_id,
                    "file_type": file_type,
                    "via": "agent_tool",
                    "tool": tool,
                },
            )
        except Exception as exc:
            session.rollback()
            log_with_context(
                logger,
                logging.WARNING,
                "Failed to record AI activation event from agent tool",
                user_id=user_id,
                project_id=project_id,
                file_id=file_id,
                tool=tool,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def get_funnel_stats(self, session: Session, *, days: int = 7) -> ActivationFunnelStats:
        """New-user funnel: of the customers who signed up in the window, how many got at least
        as far as each step within ``ACTIVATION_FUNNEL_COHORT_HOURS`` of signing up.

        The cohort is the user table (not ``signup_success`` rows), so accounts created by
        any path count. A user who reached a later step counts for every earlier one (a first
        work created automatically records no ``project_created``), so the steps never grow.
        Users who signed up less than a day ago are still being observed.
        """
        window_days = max(1, min(days, 90))
        period_end = utcnow()
        # Window starts at 00:00 Beijing time, window_days - 1 days ago.
        period_start = beijing_day_bounds(period_end - timedelta(days=window_days - 1))[0]
        start, end = period_start.replace(tzinfo=None), period_end.replace(tzinfo=None)
        customer_cohort = (
            User.is_superuser.is_(False),
            User.created_at >= start,
            User.created_at <= end,
        )

        cohort_size = int(session.exec(select(func.count(User.id)).where(*customer_cohort)).one() or 0)
        rows = session.exec(
            select(ActivationEvent.user_id, ActivationEvent.event_name, ActivationEvent.created_at, User.created_at)
            .join(User, User.id == ActivationEvent.user_id)
            .where(*customer_cohort, ActivationEvent.event_name.in_(ACTIVATION_FUNNEL_ORDER[1:]))
        ).all()
        furthest = furthest_funnel_steps(rows, observation=timedelta(hours=ACTIVATION_FUNNEL_COHORT_HOURS))

        steps: list[ActivationFunnelStep] = []
        previous_count: int | None = None

        for index, event_name in enumerate(ACTIVATION_FUNNEL_ORDER):
            count = cohort_size if index == 0 else sum(1 for reached in furthest.values() if reached >= index)

            conversion: float | None = None
            drop_off: int | None = None
            if previous_count is not None:
                conversion = round(count / previous_count, 4) if previous_count > 0 else 0.0
                drop_off = max(previous_count - count, 0)

            steps.append(
                ActivationFunnelStep(
                    event_name=event_name,
                    label=ACTIVATION_FUNNEL_LABELS[event_name],
                    users=count,
                    conversion_from_previous=conversion,
                    drop_off_from_previous=drop_off,
                )
            )
            previous_count = count

        final_users = steps[-1]["users"] if steps else 0
        activation_rate = round(final_users / cohort_size, 4) if cohort_size > 0 else 0.0

        return ActivationFunnelStats(
            window_days=window_days,
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            steps=steps,
            activation_rate=activation_rate,
        )


activation_event_service = ActivationEventService()
