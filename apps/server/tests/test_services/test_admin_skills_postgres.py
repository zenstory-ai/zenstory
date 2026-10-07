"""PostgreSQL proof for mutually exclusive admin skill decisions."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, func
from sqlmodel import Session, select

from api.admin.schemas import SkillReviewRequest
from api.admin.skills import approve_skill, reject_skill
from core.error_handler import APIException
from models import PublicSkill, User, UserSkill
from models.points import PointsTransaction
from models.subscription import AdminAuditLog
from services.features.points_service import POINTS_SKILL_CONTRIBUTION

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
    reason="isolated PostgreSQL URL not configured",
)

TABLES = [User.__table__, PublicSkill.__table__, UserSkill.__table__, AdminAuditLog.__table__, PointsTransaction.__table__]


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(os.environ["ZENSTORY_TEST_POSTGRES_URL"], pool_pre_ping=True)
    for table in reversed(TABLES):
        table.drop(engine, checkfirst=True)
    for table in TABLES:
        table.create(engine, checkfirst=True)
    try:
        yield engine
    finally:
        for table in reversed(TABLES):
            table.drop(engine, checkfirst=True)
        engine.dispose()


def test_concurrent_approve_reject_has_one_winner_and_consistent_link(pg_engine):
    with Session(pg_engine) as setup:
        approver = User(
            username="skill-pg-approver",
            email="skill-pg-approver@example.test",
            hashed_password="hashed",
            is_superuser=True,
        )
        rejecter = User(
            username="skill-pg-rejecter",
            email="skill-pg-rejecter@example.test",
            hashed_password="hashed",
            is_superuser=True,
        )
        author = User(
            username="skill-pg-author",
            email="skill-pg-author@example.test",
            hashed_password="hashed",
        )
        setup.add_all([approver, rejecter, author])
        setup.commit()
        setup.refresh(approver)
        setup.refresh(rejecter)
        setup.refresh(author)
        skill = PublicSkill(
            name="Concurrent decision",
            instructions="instructions",
            category="writing",
            source="community",
            status="pending",
            author_id=author.id,
        )
        setup.add(skill)
        setup.commit()
        setup.refresh(skill)
        link = UserSkill(
            user_id=author.id,
            name="Linked skill",
            instructions="instructions",
            is_shared=True,
            shared_skill_id=skill.id,
        )
        setup.add(link)
        setup.commit()
        setup.refresh(link)
        approver_id, rejecter_id = approver.id, rejecter.id
        skill_id, link_id = skill.id, link.id

    barrier = threading.Barrier(2)

    def decide(decision: str):
        with Session(pg_engine) as session:
            admin_id = approver_id if decision == "approve" else rejecter_id
            admin = session.get(User, admin_id)
            assert admin is not None
            barrier.wait()
            try:
                if decision == "approve":
                    approve_skill(skill_id, http_request=None, current_user=admin, session=session)
                else:
                    reject_skill(
                        skill_id,
                        SkillReviewRequest(rejection_reason="not ready"),
                        http_request=None,
                        current_user=admin,
                        session=session,
                    )
                return 200
            except APIException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, ["approve", "reject"]))

    assert sorted(outcomes) == [200, 409]
    with Session(pg_engine) as verify:
        skill = verify.get(PublicSkill, skill_id)
        link = verify.get(UserSkill, link_id)
        assert skill is not None and link is not None
        if skill.status == "approved":
            assert link.is_shared is True
            assert link.shared_skill_id == skill_id
        else:
            assert skill.status == "rejected"
            assert link.is_shared is False
            assert link.shared_skill_id is None
        assert verify.exec(
            select(func.count()).select_from(AdminAuditLog).where(
                AdminAuditLog.resource_type == "skill",
                AdminAuditLog.resource_id == skill_id,
            )
        ).one() == 1
        assert verify.exec(
            select(func.count()).select_from(PointsTransaction).where(
                PointsTransaction.transaction_type == "skill_contribution",
                PointsTransaction.source_id == skill_id,
            )
        ).one() == (1 if skill.status == "approved" and POINTS_SKILL_CONTRIBUTION > 0 else 0)
