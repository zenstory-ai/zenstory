#!/usr/bin/env python3
"""Seed E2E test users in database."""
import os
import sys
from datetime import datetime, timedelta

# Load .env.test file
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.test"))

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from passlib.context import CryptContext  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from database import sync_engine  # noqa: E402
from models import SystemPromptConfig, User  # noqa: E402
from models.referral import InviteCode  # noqa: E402
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription  # noqa: E402

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
LEGACY_CREATED_AT = datetime(2025, 1, 1)


def upsert_user(
    session: Session,
    *,
    email: str,
    password: str,
    username: str,
    is_superuser: bool,
) -> User:
    hashed_password = pwd_context.hash(password)
    existing = session.exec(select(User).where(User.email == email)).first()

    if existing:
        existing.hashed_password = hashed_password
        existing.username = username
        existing.email_verified = True
        existing.is_active = True
        existing.is_superuser = is_superuser
        # Keep seeded E2E users outside "new user" onboarding gate.
        existing.created_at = LEGACY_CREATED_AT
        session.add(existing)
        session.commit()
        session.refresh(existing)
        print(f"User updated: {existing.email}")
        user = existing
    else:
        user = User(
            email=email,
            username=username,
            hashed_password=hashed_password,
            email_verified=True,
            is_active=True,
            is_superuser=is_superuser,
            created_at=LEGACY_CREATED_AT,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        print(f"User created: {email}")

    print(f"  Username: {username}")
    print("  Password: [REDACTED]")
    print("  Email verified: True")
    print("  Active: True")
    print(f"  Superuser: {is_superuser}")
    print(f"  Created at: {user.created_at.isoformat()}")
    return user


def upsert_invite_code(
    session: Session,
    *,
    owner_id: str,
    code: str,
) -> InviteCode:
    normalized_code = code.strip().upper()
    existing = session.exec(select(InviteCode).where(InviteCode.code == normalized_code)).first()

    if existing:
        existing.owner_id = owner_id
        existing.max_uses = 9999
        existing.current_uses = 0
        existing.is_active = True
        existing.expires_at = None
        session.add(existing)
        session.commit()
        session.refresh(existing)
        print(f"Invite code updated: {existing.code}")
        return existing

    invite_code = InviteCode(
        code=normalized_code,
        owner_id=owner_id,
        max_uses=9999,
        current_uses=0,
        is_active=True,
        expires_at=None,
    )
    session.add(invite_code)
    session.commit()
    session.refresh(invite_code)
    print(f"Invite code created: {invite_code.code}")
    return invite_code


def upsert_plan(
    session: Session,
    *,
    name: str,
    display_name: str,
    features: dict,
) -> SubscriptionPlan:
    existing = session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == name)).first()
    if existing:
        existing.display_name = display_name
        existing.display_name_en = display_name
        existing.features = features
        existing.is_active = True
        existing.updated_at = datetime.utcnow()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    plan = SubscriptionPlan(
        name=name,
        display_name=display_name,
        display_name_en=display_name,
        price_monthly_cents=2900 if name != "free" else 0,
        price_yearly_cents=29000 if name != "free" else 0,
        features=features,
        is_active=True,
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def upsert_subscription(
    session: Session,
    *,
    user_id: str,
    plan_id: str,
) -> UserSubscription:
    now = datetime.utcnow()
    existing = session.exec(select(UserSubscription).where(UserSubscription.user_id == user_id)).first()
    if existing:
        existing.plan_id = plan_id
        existing.status = "active"
        existing.current_period_start = now - timedelta(days=1)
        existing.current_period_end = now + timedelta(days=30)
        existing.cancel_at_period_end = False
        existing.updated_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    subscription = UserSubscription(
        user_id=user_id,
        plan_id=plan_id,
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    )
    session.add(subscription)
    session.commit()
    session.refresh(subscription)
    return subscription


def upsert_quota(
    session: Session,
    *,
    user_id: str,
    skill_creates_used: int,
) -> UsageQuota:
    now = datetime.utcnow()
    existing = session.exec(select(UsageQuota).where(UsageQuota.user_id == user_id)).first()
    if existing:
        existing.period_start = now - timedelta(days=1)
        existing.period_end = now + timedelta(days=29)
        existing.monthly_period_start = now - timedelta(days=1)
        existing.monthly_period_end = now + timedelta(days=29)
        existing.ai_conversations_used = 0
        existing.material_uploads_used = 0
        existing.material_decompositions_used = 0
        existing.skill_creates_used = skill_creates_used
        existing.inspiration_copies_used = 0
        existing.last_reset_at = now
        existing.updated_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    quota = UsageQuota(
        user_id=user_id,
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=29),
        monthly_period_start=now - timedelta(days=1),
        monthly_period_end=now + timedelta(days=29),
        ai_conversations_used=0,
        material_uploads_used=0,
        material_decompositions_used=0,
        skill_creates_used=skill_creates_used,
        inspiration_copies_used=0,
        last_reset_at=now,
    )
    session.add(quota)
    session.commit()
    session.refresh(quota)
    return quota


def main():
    # Get regular E2E user credentials
    email = os.getenv("E2E_TEST_EMAIL", "e2e-test@example.com")
    password = os.getenv("E2E_TEST_PASSWORD", "E2eTestPassword123!")
    username = os.getenv("E2E_TEST_USERNAME", "e2e_test_user")

    # Get admin E2E user credentials
    admin_email = os.getenv("E2E_TEST_ADMIN_EMAIL") or os.getenv("E2E_ADMIN_EMAIL", "test-admin@example.com")
    admin_password = os.getenv("E2E_TEST_ADMIN_PASSWORD") or os.getenv("E2E_ADMIN_PASSWORD", "TestAdmin123!")
    admin_username = os.getenv("E2E_TEST_ADMIN_USERNAME") or os.getenv("E2E_ADMIN_USERNAME", "test_admin")
    skills_email = os.getenv("E2E_TEST_SKILLS_EMAIL", "e2e-skills@example.com")
    skills_password = os.getenv("E2E_TEST_SKILLS_PASSWORD", "E2eSkillsPassword123!")
    skills_username = os.getenv("E2E_TEST_SKILLS_USERNAME", "e2e_skills_user")
    invite_code = os.getenv("E2E_TEST_INVITE_CODE", "E2E1-TST1")

    with Session(sync_engine) as session:
        # E2E-only synthetic fixtures, never an official catalog or runtime fallback.
        for project_type in ("novel", "short", "screenplay"):
            if not session.exec(select(SystemPromptConfig).where(SystemPromptConfig.project_type == project_type)).first():
                session.add(SystemPromptConfig(
                    project_type=project_type, role_definition=f"E2E {project_type} editor",
                    capabilities="E2E writing capabilities", writing_guidelines="E2E test scope only",
                ))
        session.commit()
        free_plan = upsert_plan(
            session,
            name="free",
            display_name="Free",
            features={
                "ai_conversations_per_day": 10,
                "max_projects": 3,
                # Access is an explicit entitlement; material quotas never imply it.
                # The regular E2E user exercises the materials library.
                "materials_library_access": True,
                "material_uploads": 5,
                "material_decompositions": 5,
                "custom_skills": 3,
                "inspiration_copies_monthly": 10,
                "export_formats": ["txt"],
            },
        )
        pro_plan = upsert_plan(
            session,
            name="pro",
            display_name="Pro",
            features={
                "ai_conversations_per_day": -1,
                "max_projects": 8,
                "materials_library_access": True,
                "material_uploads": -1,
                "material_decompositions": -1,
                "custom_skills": -1,
                "inspiration_copies_monthly": 100,
                "export_formats": ["txt", "md"],
            },
        )

        print("Seeding regular E2E user:")
        regular_user = upsert_user(
            session,
            email=email,
            password=password,
            username=username,
            is_superuser=False,
        )
        invite = upsert_invite_code(
            session,
            owner_id=regular_user.id,
            code=invite_code,
        )
        print(f"  Usable invite code: {invite.code}")
        upsert_subscription(session, user_id=regular_user.id, plan_id=free_plan.id)
        upsert_quota(session, user_id=regular_user.id, skill_creates_used=3)

        print("Seeding admin E2E user:")
        admin_user = upsert_user(
            session,
            email=admin_email,
            password=admin_password,
            username=admin_username,
            is_superuser=True,
        )
        upsert_subscription(session, user_id=admin_user.id, plan_id=pro_plan.id)
        upsert_quota(session, user_id=admin_user.id, skill_creates_used=0)

        print("Seeding skills E2E user:")
        skills_user = upsert_user(
            session,
            email=skills_email,
            password=skills_password,
            username=skills_username,
            is_superuser=False,
        )
        upsert_subscription(session, user_id=skills_user.id, plan_id=pro_plan.id)
        upsert_quota(session, user_id=skills_user.id, skill_creates_used=0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
