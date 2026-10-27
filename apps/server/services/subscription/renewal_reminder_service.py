"""Pro renewal reminder emails: three days before expiry and the day after.

Pro is a one-time payment without auto-renewal, and the in-app banner only
reaches authors who still open the app. A scheduled job calls this service
once or twice a day (Beijing time); each user gets at most one email per
stage per billing period.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session, select

from config.datetime_utils import BEIJING_TIMEZONE, beijing_date, normalize_datetime_to_utc, utcnow
from models.entities import User
from models.payment import PaymentOrder
from models.subscription import SubscriptionHistory, SubscriptionPlan, UserSubscription
from models.user_preference import UserPreference
from services.infra.email_client import (
    RENEWAL_STAGE_AFTER_EXPIRY as STAGE_AFTER_EXPIRY,
)
from services.infra.email_client import (
    RENEWAL_STAGE_BEFORE_EXPIRY as STAGE_BEFORE_EXPIRY,
)
from services.infra.email_client import send_renewal_reminder_email
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# SubscriptionHistory rows with this action claim a sent reminder. They are not
# plan changes, so the author-facing history endpoint leaves them out.
RENEWAL_REMINDER_ACTION = "renewal_reminder"

# Beijing calendar days between today and the expiry date for each stage.
_STAGE_DAY_OFFSETS = {STAGE_BEFORE_EXPIRY: 3, STAGE_AFTER_EXPIRY: -1}
# After expiry the row may still read "active" until the author's next request
# marks it expired. Cancelled rows were revoked and get no reminder.
_STAGE_STATUSES = {STAGE_BEFORE_EXPIRY: ("active",), STAGE_AFTER_EXPIRY: ("active", "expired")}


@dataclass
class StageResult:
    due: int = 0
    sent: int = 0
    failed: int = 0
    already_sent: int = 0


@dataclass
class ReminderRunResult:
    dry_run: bool
    stages: dict[str, StageResult] = field(default_factory=dict)


@dataclass(frozen=True)
class _Candidate:
    user_id: str
    email: str
    period_end: datetime
    language: str


def _beijing_day_window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=BEIJING_TIMEZONE).astimezone(UTC)
    return start, start + timedelta(days=1)


def _naive_utc(value: datetime) -> datetime:
    # Timestamps are stored as naive UTC; compare against the same shape.
    return normalize_datetime_to_utc(value).replace(tzinfo=None)


def _find_candidates(session: Session, stage: str, today: date) -> list[_Candidate]:
    start, end = _beijing_day_window(today + timedelta(days=_STAGE_DAY_OFFSETS[stage]))
    paid_order = select(PaymentOrder.id).where(
        PaymentOrder.user_id == UserSubscription.user_id,
        PaymentOrder.status == "paid",
    )
    rows = session.exec(
        select(UserSubscription, User, UserPreference.ui_language)
        .join(SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id)
        .join(User, User.id == UserSubscription.user_id)
        .outerjoin(UserPreference, UserPreference.user_id == UserSubscription.user_id)
        .where(
            SubscriptionPlan.name == "pro",
            UserSubscription.status.in_(_STAGE_STATUSES[stage]),
            UserSubscription.current_period_end >= _naive_utc(start),
            UserSubscription.current_period_end < _naive_utc(end),
            User.is_active.is_(True),
            User.is_superuser.is_(False),
            User.email_verified.is_(True),
            # Pro granted by a code or an admin was never bought, so there is
            # nothing to renew.
            paid_order.exists(),
        )
        .order_by(UserSubscription.current_period_end)
    ).all()
    return [
        _Candidate(
            user_id=user.id,
            email=user.email,
            period_end=subscription.current_period_end,
            language="en" if (ui_language or "").startswith("en") else "zh",
        )
        for subscription, user, ui_language in rows
    ]


def _claim(session: Session, candidate: _Candidate, stage: str) -> SubscriptionHistory | None:
    """Record the reminder before sending; return None when it was already claimed.

    The subscription row lock serializes overlapping runs for the same author.
    A renewal moves current_period_end, so the reminder no longer applies.
    """
    subscription = session.exec(
        select(UserSubscription)
        .where(UserSubscription.user_id == candidate.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if subscription is None or subscription.current_period_end != candidate.period_end:
        session.rollback()
        return None

    claimed = session.exec(
        select(SubscriptionHistory).where(
            SubscriptionHistory.user_id == candidate.user_id,
            SubscriptionHistory.action == RENEWAL_REMINDER_ACTION,
            SubscriptionHistory.end_date == candidate.period_end,
        )
    ).all()
    if any((row.event_metadata or {}).get("stage") == stage for row in claimed):
        session.rollback()
        return None

    record = SubscriptionHistory(
        user_id=candidate.user_id,
        action=RENEWAL_REMINDER_ACTION,
        plan_name="pro",
        start_date=subscription.current_period_start,
        end_date=candidate.period_end,
        event_metadata={"stage": stage, "delivered": None},
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def send_due_renewal_reminders(
    session: Session,
    *,
    frontend_url: str,
    dry_run: bool = False,
    now: datetime | None = None,
) -> ReminderRunResult:
    """Send every reminder due on the current Beijing date.

    A failed send keeps its claim and is not retried: a missed reminder is
    better than a duplicate one.
    """
    today = beijing_date(now or utcnow())
    result = ReminderRunResult(dry_run=dry_run)
    renew_url = f"{frontend_url.rstrip('/')}/dashboard/billing?plan=pro&source=renewal_email"

    for stage in _STAGE_DAY_OFFSETS:
        stage_result = result.stages.setdefault(stage, StageResult())
        candidates = _find_candidates(session, stage, today)
        stage_result.due = len(candidates)
        if dry_run:
            continue

        for candidate in candidates:
            record = _claim(session, candidate, stage)
            if record is None:
                stage_result.already_sent += 1
                continue

            delivered = send_renewal_reminder_email(
                email=candidate.email,
                stage=stage,
                expires_on=beijing_date(candidate.period_end),
                renew_url=renew_url,
                language=candidate.language,
                user_id=candidate.user_id,
            )
            record.event_metadata = {**record.event_metadata, "delivered": delivered}
            session.add(record)
            session.commit()
            if delivered:
                stage_result.sent += 1
            else:
                stage_result.failed += 1

    log_with_context(
        logger,
        20,  # INFO
        "Renewal reminder run finished",
        dry_run=dry_run,
        beijing_date=today.isoformat(),
        **{
            f"{stage}_{key}": value
            for stage, stage_result in result.stages.items()
            for key, value in vars(stage_result).items()
        },
    )
    return result
