from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import User
from models.referral import InviteCode, Referral
from models.subscription import SubscriptionPlan
from services.features.referral_service import (
    complete_referral_and_reward,
    create_invite_code,
    get_user_invite_codes,
    get_user_referral_stats,
)
from services.subscription.subscription_service import subscription_service


def _user(db_session: Session, suffix: str) -> User:
    user = User(
        email=f"commercial-{suffix}@example.com",
        username=f"commercial-{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _plan(db_session: Session) -> SubscriptionPlan:
    plan = SubscriptionPlan(
        name="commercial-pro",
        display_name="Commercial Pro",
        display_name_en="Commercial Pro",
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


def _capture_selects(db_session: Session) -> tuple[list[object], object]:
    statements: list[object] = []
    original_exec = db_session.exec

    def recording_exec(statement, *args, **kwargs):
        statements.append(statement)
        return original_exec(statement, *args, **kwargs)

    return statements, recording_exec


def _has_fresh_user_lock(statements: list[object], user_id: str) -> bool:
    for statement in statements:
        sql = str(statement).upper()
        options = statement.get_execution_options()
        rendered = str(statement.compile(compile_kwargs={"literal_binds": True}))
        if (
            "FROM \"USER\"" in sql
            and "FOR UPDATE" in sql
            and user_id in rendered
            and options.get("populate_existing") is True
        ):
            return True
    return False


def test_subscription_mutation_locks_user_and_refreshes_locked_read(db_session: Session):
    user = _user(db_session, "subscription-lock")
    plan = _plan(db_session)
    statements, recording_exec = _capture_selects(db_session)

    with patch.object(db_session, "exec", side_effect=recording_exec):
        subscription_service.create_user_subscription(
            db_session,
            user.id,
            plan.name,
            duration_days=7,
        )

    assert _has_fresh_user_lock(statements, user.id)
    subscription_locks = [
        statement
        for statement in statements
        if "FROM USER_SUBSCRIPTION" in str(statement).upper()
        and "FOR UPDATE" in str(statement).upper()
    ]
    assert subscription_locks
    assert subscription_locks[0].get_execution_options().get("populate_existing") is True


@pytest.mark.asyncio
async def test_invite_code_cap_check_locks_owner_user(db_session: Session):
    user = _user(db_session, "invite-lock")
    statements, recording_exec = _capture_selects(db_session)

    with patch.object(db_session, "exec", side_effect=recording_exec):
        create_invite_code(user.id, db_session)

    assert _has_fresh_user_lock(statements, user.id)


@pytest.mark.asyncio
async def test_referral_completion_locks_and_refreshes_referral(db_session: Session):
    inviter = _user(db_session, "referral-inviter")
    invitee = _user(db_session, "referral-invitee")
    code = InviteCode(code="ABCD-EFGH", owner_id=inviter.id)
    db_session.add(code)
    db_session.commit()
    db_session.refresh(code)
    referral = Referral(
        inviter_id=inviter.id,
        invitee_id=invitee.id,
        invite_code_id=code.id,
    )
    db_session.add(referral)
    db_session.commit()
    db_session.refresh(referral)
    statements, recording_exec = _capture_selects(db_session)

    with patch.object(db_session, "exec", side_effect=recording_exec):
        await complete_referral_and_reward(referral.id, db_session)

    referral_locks = [
        statement
        for statement in statements
        if "FROM REFERRAL" in str(statement).upper()
        and "FOR UPDATE" in str(statement).upper()
    ]
    assert referral_locks
    assert referral_locks[0].get_execution_options().get("populate_existing") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", [get_user_referral_stats, get_user_invite_codes])
async def test_referral_read_failures_are_standardized_500(reader, db_session: Session):
    user = _user(db_session, reader.__name__)

    with patch.object(db_session, "exec", side_effect=SQLAlchemyError("database detail")):
        with pytest.raises(APIException) as exc_info:
            reader(user.id, db_session)

    assert exc_info.value.status_code == 500
    assert exc_info.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR
    assert "database detail" not in str(exc_info.value.detail)
