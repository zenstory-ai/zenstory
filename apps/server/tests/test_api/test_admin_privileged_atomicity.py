"""Regression coverage for admin business mutation + audit atomicity."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlmodel import Session, func, select

from api.admin.codes import update_code
from api.admin.schemas import (
    CodeUpdateRequest,
    SkillReviewRequest,
    SubscriptionUpdateRequest,
)
from api.admin.skills import approve_skill, reject_skill
from api.admin.subscriptions import update_user_subscription
from config.datetime_utils import utcnow
from models import PublicSkill, User, UserSkill
from models.subscription import RedemptionCode, SubscriptionHistory, SubscriptionPlan, UserSubscription


def _user(session: Session, suffix: str, *, admin: bool = False) -> User:
    user = User(
        username=f"atomic-{suffix}",
        email=f"atomic-{suffix}@example.test",
        hashed_password="hashed",
        email_verified=True,
        is_superuser=admin,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _audit_failure(*args, **kwargs):
    raise RuntimeError("audit unavailable")


def test_code_update_audit_failure_rolls_back_code(db_session: Session):
    admin = _user(db_session, "code-admin", admin=True)
    plan = SubscriptionPlan(name="atomic-code-plan", display_name="Atomic")
    db_session.add(plan)
    db_session.commit()
    code = RedemptionCode(
        code="ATOMIC-CODE-SECRET",
        tier=plan.name,
        duration_days=30,
        max_uses=1,
        created_by=admin.id,
        notes="before",
    )
    db_session.add(code)
    db_session.commit()

    with patch("api.admin.codes.admin_audit_service.log_action", side_effect=_audit_failure):
        with pytest.raises(RuntimeError, match="audit unavailable"):
            update_code(
                code.id,
                CodeUpdateRequest(is_active=False, notes="after"),
                http_request=None,
                current_user=admin,
                session=db_session,
            )

    persisted = db_session.get(RedemptionCode, code.id)
    assert persisted is not None
    assert persisted.is_active is True
    assert persisted.notes == "before"


def test_subscription_update_audit_failure_rolls_back_subscription_and_history(db_session: Session):
    admin = _user(db_session, "subscription-admin", admin=True)
    target = _user(db_session, "subscription-target")
    free = SubscriptionPlan(name="atomic-free", display_name="Free")
    pro = SubscriptionPlan(name="atomic-pro", display_name="Pro")
    db_session.add_all([free, pro])
    db_session.commit()
    now = utcnow()
    subscription = UserSubscription(
        user_id=target.id,
        plan_id=free.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(subscription)
    db_session.commit()

    with patch("api.admin.subscriptions.admin_audit_service.log_action", side_effect=_audit_failure):
        with pytest.raises(RuntimeError, match="audit unavailable"):
            update_user_subscription(
                target.id,
                SubscriptionUpdateRequest(plan_name=pro.name, duration_days=30),
                http_request=None,
                current_user=admin,
                session=db_session,
            )

    persisted = db_session.exec(
        select(UserSubscription).where(UserSubscription.user_id == target.id)
    ).one()
    assert persisted.plan_id == free.id
    assert db_session.exec(
        select(func.count()).select_from(SubscriptionHistory).where(
            SubscriptionHistory.user_id == target.id
        )
    ).one() == 0


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_skill_decision_audit_failure_rolls_back_status_and_link(db_session: Session, decision: str):
    admin = _user(db_session, f"skill-{decision}-admin", admin=True)
    author = _user(db_session, f"skill-{decision}-author")
    skill = PublicSkill(
        name=f"Atomic {decision}",
        instructions="instructions",
        category="writing",
        source="community",
        status="pending",
        author_id=author.id,
    )
    db_session.add(skill)
    db_session.commit()
    link = UserSkill(
        user_id=author.id,
        name="Linked",
        instructions="instructions",
        is_shared=True,
        shared_skill_id=skill.id,
    )
    db_session.add(link)
    db_session.commit()

    with patch("api.admin.skills.admin_audit_service.log_action", side_effect=_audit_failure):
        with pytest.raises(RuntimeError, match="audit unavailable"):
            if decision == "approve":
                approve_skill(skill.id, http_request=None, current_user=admin, session=db_session)
            else:
                reject_skill(
                    skill.id,
                    SkillReviewRequest(rejection_reason="reason"),
                    http_request=None,
                    current_user=admin,
                    session=db_session,
                )

    persisted_skill = db_session.get(PublicSkill, skill.id)
    persisted_link = db_session.get(UserSkill, link.id)
    assert persisted_skill is not None and persisted_skill.status == "pending"
    assert persisted_link is not None
    assert persisted_link.is_shared is True
    assert persisted_link.shared_skill_id == skill.id


def test_privileged_mutations_emit_complete_audits_without_issued_code_secret(db_session: Session):
    from api.admin.codes import create_code
    from api.admin.schemas import CodeCreateRequest
    from models.subscription import AdminAuditLog

    admin = _user(db_session, "audit-admin", admin=True)
    target = _user(db_session, "audit-target")
    plan = SubscriptionPlan(name="atomic-audit-plan", display_name="Audit")
    db_session.add(plan)
    db_session.commit()
    now = utcnow()
    db_session.add(UserSubscription(
        user_id=target.id,
        plan_id=plan.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    ))
    skill = PublicSkill(
        name="Audited approval",
        instructions="instructions",
        category="writing",
        source="community",
        status="pending",
        author_id=target.id,
    )
    db_session.add(skill)
    db_session.commit()

    issued_secret = "ISSUED-CODE-MUST-NOT-BE-AUDITED"
    with (
        patch("api.admin.codes.check_rate_limit", return_value=(True, None)),
        patch("api.admin.codes.generate_code", return_value=issued_secret),
    ):
        create_code(
            CodeCreateRequest(tier=plan.name, duration_days=30),
            http_request=None,
            current_user=admin,
            session=db_session,
        )
    update_user_subscription(
        target.id,
        SubscriptionUpdateRequest(status="cancelled"),
        http_request=None,
        current_user=admin,
        session=db_session,
    )
    approve_skill(skill.id, http_request=None, current_user=admin, session=db_session)

    audits = db_session.exec(
        select(AdminAuditLog).where(AdminAuditLog.admin_user_id == admin.id)
    ).all()
    by_action = {audit.action: audit for audit in audits}
    assert {"create_code", "update_subscription", "approve_skill"} <= set(by_action)
    for action in ("create_code", "update_subscription", "approve_skill"):
        audit = by_action[action]
        assert audit.resource_type
        assert audit.resource_id
        assert audit.new_value is not None
    assert issued_secret not in str(by_action["create_code"].new_value)
    assert by_action["update_subscription"].old_value is not None
    assert by_action["approve_skill"].old_value == {"status": "pending"}
