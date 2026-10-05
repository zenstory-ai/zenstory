"""Agent file tools record the first_ai_action_accepted activation milestone."""

import pytest
from sqlmodel import Session, select

from agent.tools.file_ops import FileCRUD, FileEditor
from models import (
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ActivationEvent,
    File,
    Project,
    User,
)
from services.features.activation_event_service import activation_event_service


@pytest.fixture
def owner(db_session):
    from services.core.auth_service import hash_password

    user = User(
        email="agent_activation@example.com",
        username="agent_activation",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def project(db_session, owner):
    project = Project(name="Activation Project", owner_id=owner.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def version_engine(db_session, monkeypatch):
    import database

    engine = db_session.get_bind()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    return engine


def _draft(db_session, project_id: str, content: str) -> File:
    file = File(project_id=project_id, title="第一章", file_type="draft", content=content)
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)
    return file


def _ai_events(db_session, user_id: str) -> list[ActivationEvent]:
    return list(
        db_session.exec(
            select(ActivationEvent).where(
                ActivationEvent.user_id == user_id,
                ActivationEvent.event_name == ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
            )
        ).all()
    )


def test_edit_file_records_first_ai_action(db_session, owner, project, version_engine):
    file = _draft(db_session, project.id, "开头。")

    FileEditor(db_session, user_id=owner.id).edit_file(
        file.id, [{"op": "append", "text": "AI 续写。"}]
    )

    events = _ai_events(db_session, owner.id)
    assert len(events) == 1
    assert events[0].project_id == project.id
    assert events[0].event_metadata["via"] == "agent_tool"
    assert events[0].event_metadata["tool"] == "edit_file"
    assert events[0].event_metadata["file_id"] == file.id


def test_create_file_with_content_records_once(db_session, owner, project, version_engine):
    crud = FileCRUD(db_session, user_id=owner.id)
    crud.create_file(project.id, "大纲", file_type="outline", content="第一卷：起")
    crud.create_file(project.id, "人物", file_type="character", content="主角")

    events = _ai_events(db_session, owner.id)
    assert len(events) == 1
    assert events[0].event_metadata["tool"] == "create_file"


def test_empty_create_and_noop_edit_record_nothing(db_session, owner, project, version_engine):
    crud = FileCRUD(db_session, user_id=owner.id)
    crud.create_file(project.id, "空白草稿", file_type="draft", content="")
    file = _draft(db_session, project.id, "不变")
    FileEditor(db_session, user_id=owner.id).edit_file(
        file.id, [{"op": "replace", "old": "不变", "new": "不变"}], continue_on_error=True
    )

    assert _ai_events(db_session, owner.id) == []


def test_update_file_content_records_first_ai_action(db_session, owner, project, version_engine):
    file = _draft(db_session, project.id, "v1")

    FileCRUD(db_session, user_id=owner.id).update_file(file.id, content="v2")

    events = _ai_events(db_session, owner.id)
    assert len(events) == 1
    assert events[0].event_metadata["tool"] == "update_file"


def test_activation_failure_does_not_break_the_edit(
    db_session, owner, project, version_engine, monkeypatch
):
    file = _draft(db_session, project.id, "开头。")

    def _boom(*args, **kwargs):
        raise RuntimeError("activation store down")

    monkeypatch.setattr(activation_event_service, "record_once", _boom)

    result = FileEditor(db_session, user_id=owner.id).edit_file(
        file.id, [{"op": "append", "text": "仍然写入。"}]
    )

    assert result["edits_applied"] == 1
    with Session(version_engine) as check:
        assert check.get(File, file.id).content == "开头。仍然写入。"
