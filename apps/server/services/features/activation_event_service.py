"""
Activation event service.

Provides milestone recording and funnel aggregation for first-day activation analytics.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from config.datetime_utils import beijing_day_bounds, utcnow
from models import (
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
    ACTIVATION_EVENT_NAMES,
    ACTIVATION_EVENT_PROJECT_CREATED,
    ACTIVATION_EVENT_SIGNUP_SUCCESS,
    ActivationEvent,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

ACTIVATION_FUNNEL_ORDER = [
    ACTIVATION_EVENT_SIGNUP_SUCCESS,
    ACTIVATION_EVENT_PROJECT_CREATED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
]

ACTIVATION_FUNNEL_LABELS = {
    ACTIVATION_EVENT_SIGNUP_SUCCESS: "Signup Success",
    ACTIVATION_EVENT_PROJECT_CREATED: "Project Created",
    ACTIVATION_EVENT_FIRST_FILE_SAVED: "First File Saved",
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED: "First AI Action Accepted",
}

ACTIVATION_GUIDE_ACTION_BY_EVENT = {
    ACTIVATION_EVENT_SIGNUP_SUCCESS: "/dashboard",
    ACTIVATION_EVENT_PROJECT_CREATED: "/dashboard",
    ACTIVATION_EVENT_FIRST_FILE_SAVED: "/dashboard",
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED: "/dashboard",
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


class ActivationGuideStep(TypedDict):
    """Single first-day activation step for user onboarding guide."""

    event_name: str
    label: str
    completed: bool
    completed_at: str | None
    action_path: str


class ActivationGuideStats(TypedDict):
    """User-level activation guide payload."""

    user_id: str
    window_hours: int
    within_first_day: bool
    total_steps: int
    completed_steps: int
    completion_rate: float
    is_activated: bool
    next_event_name: str | None
    next_action: str | None
    steps: list[ActivationGuideStep]


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
        """Get activation funnel metrics for the recent time window."""
        window_days = max(1, min(days, 90))
        period_end = utcnow()
        # Window starts at 00:00 Beijing time, window_days - 1 days ago.
        period_start = beijing_day_bounds(period_end - timedelta(days=window_days - 1))[0]

        steps: list[ActivationFunnelStep] = []
        previous_count: int | None = None

        for event_name in ACTIVATION_FUNNEL_ORDER:
            user_count = session.exec(
                select(func.count(func.distinct(ActivationEvent.user_id))).where(
                    ActivationEvent.event_name == event_name,
                    ActivationEvent.created_at >= period_start,
                    ActivationEvent.created_at <= period_end,
                )
            ).one() or 0
            count = int(user_count)

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

        signup_users = steps[0]["users"] if steps else 0
        final_users = steps[-1]["users"] if steps else 0
        activation_rate = round(final_users / signup_users, 4) if signup_users > 0 else 0.0

        return ActivationFunnelStats(
            window_days=window_days,
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            steps=steps,
            activation_rate=activation_rate,
        )

    def get_user_guide(
        self,
        session: Session,
        *,
        user_id: str,
        user_created_at: datetime | None = None,
        window_hours: int = 24,
    ) -> ActivationGuideStats:
        """
        Build user-level first-day activation guide.

        Notes:
        - signup step is treated as completed when user_created_at is known,
          even if historical signup event is missing.
        - remaining steps are derived from activation_event milestones.
        """
        bounded_window_hours = max(1, min(window_hours, 72))
        events = session.exec(
            select(ActivationEvent).where(
                ActivationEvent.user_id == user_id,
                ActivationEvent.event_name.in_(ACTIVATION_FUNNEL_ORDER),
            )
        ).all()

        event_by_name = {event.event_name: event for event in events}
        created_at = user_created_at or utcnow()
        now = utcnow()
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        within_first_day = (now - created_at) <= timedelta(hours=bounded_window_hours)

        steps: list[ActivationGuideStep] = []
        completed_steps = 0

        for event_name in ACTIVATION_FUNNEL_ORDER:
            event = event_by_name.get(event_name)
            if event_name == ACTIVATION_EVENT_SIGNUP_SUCCESS:
                completed = event is not None or user_created_at is not None
                completed_at = (
                    event.created_at.isoformat()
                    if event is not None
                    else created_at.isoformat()
                )
            else:
                completed = event is not None
                completed_at = event.created_at.isoformat() if event is not None else None

            if completed:
                completed_steps += 1

            steps.append(
                ActivationGuideStep(
                    event_name=event_name,
                    label=ACTIVATION_FUNNEL_LABELS[event_name],
                    completed=completed,
                    completed_at=completed_at,
                    action_path=ACTIVATION_GUIDE_ACTION_BY_EVENT[event_name],
                )
            )

        total_steps = len(steps)
        completion_rate = round(completed_steps / total_steps, 4) if total_steps > 0 else 0.0
        next_step = next((step for step in steps if not step["completed"]), None)
        is_activated = bool(steps and steps[-1]["completed"])

        return ActivationGuideStats(
            user_id=user_id,
            window_hours=bounded_window_hours,
            within_first_day=within_first_day,
            total_steps=total_steps,
            completed_steps=completed_steps,
            completion_rate=completion_rate,
            is_activated=is_activated,
            next_event_name=next_step["event_name"] if next_step else None,
            next_action=next_step["action_path"] if next_step else None,
            steps=steps,
        )


activation_event_service = ActivationEventService()
