"""The Playwright E2E seed must grant what the E2E specs exercise.

Since materials-library access stopped being inferred from nonzero material
quotas (#140), a seeded plan has to grant ``materials_library_access``
explicitly; otherwise the regular E2E user only sees the paid teaser and every
material-library spec finds zero material cards.
"""

import pytest
from sqlmodel import Session, select

import scripts.seed_test_user as seed
from api.subscription import _normalize_plan_features_for_response
from models import User
from services.quota_service import quota_service
from tests.conftest import test_engine

E2E_ENV_KEYS = (
    "E2E_TEST_EMAIL",
    "E2E_TEST_PASSWORD",
    "E2E_TEST_USERNAME",
    "E2E_TEST_ADMIN_EMAIL",
    "E2E_ADMIN_EMAIL",
    "E2E_TEST_ADMIN_PASSWORD",
    "E2E_ADMIN_PASSWORD",
    "E2E_TEST_ADMIN_USERNAME",
    "E2E_ADMIN_USERNAME",
    "E2E_TEST_SKILLS_EMAIL",
    "E2E_TEST_SKILLS_PASSWORD",
    "E2E_TEST_SKILLS_USERNAME",
    "E2E_TEST_INVITE_CODE",
)


@pytest.fixture
def seeded_session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    for key in E2E_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(seed, "sync_engine", test_engine)
    assert seed.main() == 0
    db_session.expire_all()
    return db_session


@pytest.mark.parametrize(
    "email",
    ["e2e-test@example.com", "test-admin@example.com", "e2e-skills@example.com"],
)
def test_seeded_e2e_users_have_materials_library_access(seeded_session: Session, email: str) -> None:
    user = seeded_session.exec(select(User).where(User.email == email)).one()
    plan = quota_service.get_user_plan(seeded_session, user.id)

    assert quota_service.has_feature_access(seeded_session, user.id, "materials_library_access") is True
    assert _normalize_plan_features_for_response(plan.features, plan.name)["materials_library_access"] is True
