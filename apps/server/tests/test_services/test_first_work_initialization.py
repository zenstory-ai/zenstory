"""Default first work: persisted qualification, atomic creation and honest progress."""
from __future__ import annotations

from importlib import import_module
from uuid import uuid4

import pytest
from sqlmodel import func, select

from models import ActivationEvent, File, Project, UsageQuota, User, UserPersonaProfile
from services.project_progress import get_projects_progress
from services.project_service import create_project_with_default_folders


@pytest.fixture(autouse=True)
def enabled_first_work_rollout(monkeypatch):
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "100")


def service():
    return import_module("services.first_work_service")


def seed(session, *, eligible=True, verified=True, profile=True, password="unused", personas=None):
    suffix = uuid4().hex
    user = User(username=f"first-{suffix}", email=f"{suffix}@example.test",
                hashed_password=password, email_verified=verified)
    session.add(user)
    if eligible:
        service().enroll_new_user(session, user)
    if profile:
        session.add(UserPersonaProfile(user_id=user.id, selected_personas=personas or "[]", skipped=True))
    session.commit()
    return user


def count(session, model):
    return session.exec(select(func.count()).select_from(model)).one()


@pytest.mark.parametrize("preferred,personas,expected,kind,folder,name", [
    (None, "[]", "novel", "draft", "draft-folder", "My Novel"),
    ("short", "[]", "short", "draft", "draft-folder", "My Short Story"),
    ("screenplay", "[]", "screenplay", "script", "script-folder", "My Drama"),
    ("invalid", '["screenwriter"]', "screenplay", "script", "script-folder", "My Drama"),
])
def test_new_verified_user_gets_one_empty_writing_file(db_session, preferred, personas, expected, kind, folder, name):
    user = seed(db_session, personas=personas)
    before = count(db_session, UsageQuota)
    result = service().initialize_first_work(db_session, user.id, preferred_type=preferred, lang="en")
    assert result.status == "created" and result.static_guidance is True
    project = db_session.get(Project, result.project_id)
    file = db_session.get(File, result.file_id)
    # Named like every other new work of its type, so the project list shows what it is.
    assert project.name == name and project.project_type == expected
    assert result.project_type == expected
    assert file.content == "" and file.file_type == kind and file.parent_id == f"{project.id}-{folder}"
    assert file.get_metadata()["creation_source"] == "first_work"
    assert count(db_session, Project) == 1
    assert len(db_session.exec(select(File).where(File.file_type != "folder")).all()) == 1
    assert count(db_session, UsageQuota) == before
    assert count(db_session, ActivationEvent) == 0
    progress = get_projects_progress(db_session, user.id)[0]
    assert (progress.written_units, progress.word_count, progress.framework_ready) == (0, 0, False)
    assert service().initialize_first_work(db_session, user.id, lang="zh") == result


@pytest.mark.parametrize("personas,name", [
    ("[]", "我的小说"),
    ('["short_story"]', "我的短篇"),
    ('["screenwriter"]', "我的短剧"),
])
def test_first_work_name_follows_the_chosen_type_in_chinese(db_session, personas, name):
    user = seed(db_session, personas=personas)
    result = service().initialize_first_work(db_session, user.id, lang="zh")
    assert db_session.get(Project, result.project_id).name == name


@pytest.mark.parametrize("eligible,verified,profile,status", [
    (False, True, True, "ineligible"),
    (True, False, True, "waiting_verification"),
    (True, True, False, "waiting_onboarding"),
])
def test_registration_and_server_onboarding_gate_creation(db_session, eligible, verified, profile, status):
    user = seed(db_session, eligible=eligible, verified=verified, profile=profile)
    result = service().initialize_first_work(db_session, user.id)
    assert result.status == status and result.project_id is None
    assert count(db_session, Project) == count(db_session, File) == 0


@pytest.mark.parametrize("deleted", [False, True])
def test_any_preexisting_project_consumes_eligibility(db_session, deleted):
    user = seed(db_session)
    db_session.add(Project(owner_id=user.id, name="Author work", is_deleted=deleted))
    db_session.commit()
    assert service().initialize_first_work(db_session, user.id).status == "skipped_existing"
    for project in db_session.exec(select(Project)).all():
        db_session.delete(project)
    db_session.commit()
    assert service().initialize_first_work(db_session, user.id).status == "skipped_existing"
    assert count(db_session, Project) == 0


@pytest.mark.parametrize("hard_delete", [False, True])
def test_deleted_first_work_is_never_recreated(db_session, hard_delete):
    user = seed(db_session)
    first = service().initialize_first_work(db_session, user.id)
    project = db_session.get(Project, first.project_id)
    if hard_delete:
        for file in db_session.exec(select(File)).all():
            db_session.delete(file)
        db_session.delete(project)
    else:
        project.is_deleted = True
        db_session.add(project)
    db_session.commit()
    repeated = service().initialize_first_work(db_session, user.id)
    assert repeated.project_id == first.project_id and repeated.file_id == first.file_id
    assert repeated.static_guidance is False
    assert repeated.project_type is None
    assert count(db_session, Project) == (0 if hard_delete else 1)


def test_creation_failure_rolls_back_folders_file_and_result(db_session, monkeypatch):
    user = seed(db_session)
    original_flush = db_session.flush
    def fail_on_empty_file(*args, **kwargs):
        if any(isinstance(item, File) and item.file_type == "draft" for item in db_session.new):
            raise RuntimeError("synthetic write failure")
        return original_flush(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(db_session, "flush", fail_on_empty_file)
        with pytest.raises(RuntimeError, match="synthetic write failure"):
            service().initialize_first_work(db_session, user.id)
    assert count(db_session, Project) == count(db_session, File) == 0
    assert service().get_first_work_state(db_session, user.id).status == "pending"
    assert service().initialize_first_work(db_session, user.id).status == "created"


def test_manual_creation_is_persistently_consumed_before_hard_delete(db_session):
    user = seed(db_session)
    manual = Project(name="My authored work", owner_id=user.id)
    create_project_with_default_folders(db_session, manual)
    for file in db_session.exec(select(File)).all():
        db_session.delete(file)
    # Activation FK is independent of the durable initialization record.
    for event in db_session.exec(select(ActivationEvent)).all():
        db_session.delete(event)
    db_session.delete(manual)
    db_session.commit()
    assert service().initialize_first_work(db_session, user.id).status == "skipped_existing"
    assert count(db_session, Project) == 0


def test_password_and_oauth_accounts_share_qualification_contract(db_session):
    for password in ("hashed-password", ""):
        user = seed(db_session, password=password)
        result = service().initialize_first_work(db_session, user.id)
        assert result.status == "created"
        assert service().get_first_work_state(db_session, user.id).project_id == result.project_id



def test_default_work_obeys_project_capacity_without_spending_ai(db_session):
    from core.error_handler import APIException
    from models import SubscriptionPlan

    user = seed(db_session)
    plan = SubscriptionPlan(name="free", display_name="Free", features={"max_projects": 0})
    db_session.add(plan)
    db_session.commit()
    with pytest.raises(APIException) as caught:
        service().initialize_first_work(db_session, user.id)
    assert caught.value.status_code == 402
    assert count(db_session, Project) == count(db_session, File) == 0
    assert service().get_first_work_state(db_session, user.id).status == "pending"


@pytest.mark.parametrize("rollout", [None, "0", "-1", "101", "invalid"])
def test_default_off_and_invalid_rollout_never_enrolls_or_backfills(db_session, monkeypatch, rollout):
    if rollout is None:
        monkeypatch.delenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", raising=False)
    else:
        monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", rollout)
    user = seed(db_session)
    from models import WorkspaceOnboarding
    assert db_session.get(WorkspaceOnboarding, user.id) is None
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "100")
    assert service().initialize_first_work(db_session, user.id).status == "ineligible"
    assert count(db_session, Project) == 0


def test_rollout_sampling_is_stable_and_persisted_qualification_is_not_recomputed(db_session, monkeypatch):
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "10")
    choose = service().should_enroll_new_user
    first = [choose(f"synthetic-sample-{index}") for index in range(1000)]
    assert 50 < sum(first) < 150 and first == [choose(f"synthetic-sample-{i}") for i in range(1000)]
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "100")
    user = seed(db_session)
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "1")
    assert service().initialize_first_work(db_session, user.id).status == "created"
