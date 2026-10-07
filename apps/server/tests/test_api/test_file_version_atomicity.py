"""Lazy subscription expiry cannot commit an in-flight file save."""
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks
from sqlmodel import select

from api import files
from models import File, FileVersion, Project, User
from models.subscription import SubscriptionPlan, UserSubscription
from services.features.file_version_service import get_file_version_service


def test_expired_subscription_precheck_keeps_content_and_snapshot_atomic(db_session, monkeypatch):
    user = User(username="expired-version", email="expired-version@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="Atomic expired save", owner_id=user.id)
    file = File(project_id=project.id, title="Draft", file_type="draft", content="Original")
    plan = SubscriptionPlan(name="pro", display_name="Pro", features={"file_versions_per_file": 50})
    expired_end = datetime(2026, 1, 1, tzinfo=UTC)
    subscription = UserSubscription(user_id=user.id, plan_id=plan.id, status="active", current_period_start=expired_end - timedelta(days=30), current_period_end=expired_end)
    db_session.add_all([user, project, file, plan, subscription])
    db_session.commit()
    db_session.refresh(subscription)

    service = get_file_version_service()
    count_versions = Mock(side_effect=RuntimeError("version quota query unavailable"))
    monkeypatch.setattr(service, "get_version_count", count_versions)
    with pytest.raises(RuntimeError, match="version quota query unavailable"):
        files.update_file(file.id, files.FileUpdate(content="Changed before snapshot"), BackgroundTasks(), current_user=user, session=db_session)

    # Match request-session cleanup after an unexpected precheck failure.
    db_session.rollback()
    count_versions.assert_called_once()
    db_session.refresh(file)
    db_session.refresh(subscription)
    assert file.content == "Original"
    assert subscription.status == "active"
    assert db_session.exec(select(FileVersion).where(FileVersion.file_id == file.id)).all() == []
