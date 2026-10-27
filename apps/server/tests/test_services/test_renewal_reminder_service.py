"""Pro renewal reminders: who gets one, on which Beijing day, and only once."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, select

from models import User
from models.payment import PaymentOrder
from models.subscription import SubscriptionHistory, SubscriptionPlan, UserSubscription
from models.user_preference import UserPreference
from services.core.auth_service import hash_password
from services.infra import email_client
from services.subscription.renewal_reminder_service import (
    RENEWAL_REMINDER_ACTION,
    send_due_renewal_reminders,
)

# 11:00 on 2026-11-04 in Beijing: reminders go to Pro ending on 11-07 and Pro
# that ended on 11-03 (Beijing dates).
NOW = datetime(2026, 11, 4, 3, 0)
APP = "https://app.example.com"


@pytest.fixture
def sender(monkeypatch):
    monkeypatch.setattr(email_client, "RESEND_API_KEY", "mock-only")
    send = MagicMock(return_value={"id": "mock-delivery"})
    monkeypatch.setattr(email_client.resend.Emails, "send", send)
    return send


@pytest.fixture
def pro(db_session: Session) -> SubscriptionPlan:
    plan = SubscriptionPlan(name="pro", display_name="Pro", price_monthly_cents=4900, features={})
    db_session.add(plan)
    db_session.commit()
    return plan


def author(
    db_session: Session,
    plan: SubscriptionPlan,
    name: str,
    period_end: datetime,
    *,
    status: str = "active",
    paid: bool = True,
    superuser: bool = False,
    language: str | None = None,
) -> User:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_superuser=superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.add(
        UserSubscription(
            user_id=user.id,
            plan_id=plan.id,
            status=status,
            current_period_start=datetime(2026, 10, 1),
            current_period_end=period_end,
        )
    )
    if paid:
        db_session.add(
            PaymentOrder(
                out_trade_no=f"order-{name}"[:32],
                user_id=user.id,
                plan_name="pro",
                plan_display_name="Pro",
                product_name="ZenStory Pro",
                cycle="month",
                amount_cents=4900,
                duration_days=30,
                payment_method="alipay",
                status="paid",
            )
        )
    if language:
        db_session.add(UserPreference(user_id=user.id, ui_language=language))
    db_session.commit()
    return user


def recipients(send: MagicMock) -> dict[str, dict]:
    return {call.args[0]["to"][0]: call.args[0] for call in send.call_args_list}


def run(db_session: Session, **kwargs):
    return send_due_renewal_reminders(db_session, frontend_url=APP, now=NOW, **kwargs)


def test_reminds_paying_authors_three_days_before_and_one_day_after_expiry(db_session, pro, sender):
    # 00:30 on 11-07 in Beijing is still 11-06 in UTC.
    author(db_session, pro, "ends_soon", datetime(2026, 11, 6, 16, 30))
    author(db_session, pro, "ended", datetime(2026, 11, 3, 4, 0), status="expired")
    author(db_session, pro, "ended_not_yet_marked", datetime(2026, 11, 3, 10, 0))
    author(db_session, pro, "ends_later", datetime(2026, 11, 8, 4, 0))
    author(db_session, pro, "code_only", datetime(2026, 11, 7, 4, 0), paid=False)
    author(db_session, pro, "staff", datetime(2026, 11, 7, 4, 0), superuser=True)
    author(db_session, pro, "revoked", datetime(2026, 11, 3, 4, 0), status="cancelled")

    result = run(db_session)

    sent = recipients(sender)
    assert set(sent) == {
        "ends_soon@example.com",
        "ended@example.com",
        "ended_not_yet_marked@example.com",
    }
    assert result.stages["before_expiry"].sent == 1
    assert result.stages["after_expiry"].sent == 2
    before = sent["ends_soon@example.com"]
    assert before["subject"] == "你的 ZenStory Pro 将在11月7日到期"
    assert f"{APP}/dashboard/billing?plan=pro&amp;source=renewal_email" in before["html"]
    assert before["reply_to"] == email_client.RENEWAL_REPLY_TO


def test_each_reminder_is_sent_once_and_a_renewal_cancels_it(db_session, pro, sender):
    author(db_session, pro, "ends_soon", datetime(2026, 11, 7, 4, 0))
    renewed = author(db_session, pro, "renewed", datetime(2026, 11, 7, 4, 0))
    subscription = db_session.exec(
        select(UserSubscription).where(UserSubscription.user_id == renewed.id)
    ).one()
    subscription.current_period_end = datetime(2026, 12, 7, 4, 0)
    db_session.add(subscription)
    db_session.commit()

    run(db_session)
    second = run(db_session)

    assert list(recipients(sender)) == ["ends_soon@example.com"]
    assert sender.call_count == 1
    assert second.stages["before_expiry"].already_sent == 1


def test_failed_delivery_is_recorded_and_not_retried(db_session, pro, sender):
    sender.side_effect = RuntimeError("provider down")
    user = author(db_session, pro, "ends_soon", datetime(2026, 11, 7, 4, 0))

    first = run(db_session)
    second = run(db_session)

    assert first.stages["before_expiry"].failed == 1
    assert second.stages["before_expiry"].already_sent == 1
    assert sender.call_count == 1
    claim = db_session.exec(
        select(SubscriptionHistory).where(
            SubscriptionHistory.user_id == user.id,
            SubscriptionHistory.action == RENEWAL_REMINDER_ACTION,
        )
    ).one()
    assert claim.event_metadata == {"stage": "before_expiry", "delivered": False}


def test_dry_run_counts_without_sending_or_claiming(db_session, pro, sender):
    author(db_session, pro, "ends_soon", datetime(2026, 11, 7, 4, 0))

    result = run(db_session, dry_run=True)

    assert result.stages["before_expiry"].due == 1
    sender.assert_not_called()
    assert db_session.exec(
        select(SubscriptionHistory).where(SubscriptionHistory.action == RENEWAL_REMINDER_ACTION)
    ).all() == []


def test_english_readers_get_the_english_email(db_session, pro, sender):
    author(db_session, pro, "reader", datetime(2026, 11, 7, 4, 0), language="en")

    run(db_session)

    assert sender.call_args.args[0]["subject"] == "Your ZenStory Pro ends on November 7"
