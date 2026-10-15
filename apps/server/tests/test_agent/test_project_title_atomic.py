"""Project-title provenance is checked on the fresh, locked mutation row."""
import json
from unittest.mock import patch

from sqlmodel import Session, select

from agent.tools.file_ops.project import ProjectOperations
from api.projects import UpdateProjectRequest, update_project
from models import AgentArtifactLedger, Project, User


def test_agent_stale_session_cannot_replace_a_new_author_title(db_session):
    owner = User(username="rename-race", email="rename-race@example.com", hashed_password="unused")
    db_session.add(owner)
    db_session.commit()
    project = Project(name="我的小说", owner_id=owner.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    project_id, user_id = project.id, owner.id
    assert project.name == "我的小说"  # Keep this stale instance in the Agent session.

    with Session(db_session.get_bind()) as author_session:
        author = author_session.get(User, user_id)
        update_project(project_id, UpdateProjectRequest(name="作者刚确定的书名"), author, author_session)
    assert project.name == "我的小说"

    result = ProjectOperations(db_session, user_id).update_project_status(project_id, title="AI 旧草案名")

    assert result["project_name_updated"] is False
    assert result["title_skipped"] == "author_named"
    with Session(db_session.get_bind()) as reader:
        assert reader.get(Project, project_id).name == "作者刚确定的书名"
        events = reader.exec(select(AgentArtifactLedger)).all()
        assert len(events) == 1
        assert json.loads(events[0].payload)["source"] == "user"


def test_author_rename_and_agent_status_update_both_lock_the_project(db_session):
    owner = User(username="rename-lock", email="rename-lock@example.com", hashed_password="unused")
    db_session.add(owner)
    db_session.commit()
    project = Project(name="我的小说", owner_id=owner.id)
    db_session.add(project)
    db_session.commit()
    project_id = project.id

    statements = []
    original = db_session.exec

    def capture(statement, *args, **kwargs):
        statements.append(statement)
        return original(statement, *args, **kwargs)

    with patch.object(db_session, "exec", side_effect=capture):
        update_project(project_id, UpdateProjectRequest(name="人类书名"), owner, db_session)
    assert any(getattr(statement, "_for_update_arg", None) is not None for statement in statements)
    statements.clear()
    with patch.object(db_session, "exec", side_effect=capture):
        ProjectOperations(db_session, owner.id).update_project_status(project_id, title="AI草案")
    assert any(getattr(statement, "_for_update_arg", None) is not None for statement in statements)


def test_default_title_is_not_renamed_when_provenance_lookup_fails(db_session):
    owner = User(username="title-unavailable", email="title-unavailable@example.com", hashed_password="unused")
    db_session.add(owner)
    db_session.commit()
    project = Project(name="我的小说", owner_id=owner.id)
    db_session.add(project)
    db_session.commit()
    original_exec = db_session.exec

    def unavailable_ledger(statement, *args, **kwargs):
        if any(item.get("entity") is AgentArtifactLedger for item in getattr(statement, "column_descriptions", [])):
            raise RuntimeError("provenance unavailable")
        return original_exec(statement, *args, **kwargs)

    with patch.object(db_session, "exec", side_effect=unavailable_ledger):
        result = ProjectOperations(db_session, owner.id).update_project_status(project.id, title="不能自作主张")
    assert result["project_name_updated"] is False
    assert project.name == "我的小说"


def test_explicit_author_title_wins_even_when_ledger_timestamps_tie(db_session):
    from config.datetime_utils import utcnow

    owner = User(username="title-tied", email="title-tied@example.com", hashed_password="unused")
    db_session.add(owner)
    db_session.commit()
    project = Project(name="同名书", owner_id=owner.id)
    db_session.add(project)
    db_session.commit()
    timestamp = utcnow()
    for tool, source in (("update_project", "ai_auto"), ("author_project_rename", "user")):
        db_session.add(AgentArtifactLedger(
            project_id=project.id, user_id=owner.id, action="project_title", tool_name=tool,
            created_at=timestamp, artifact_ref=f"project:{project.id}", payload=json.dumps({"project_name": project.name, "source": source}),
        ))
    db_session.commit()
    result = ProjectOperations(db_session, owner.id).update_project_status(project.id, title="AI覆盖")
    assert result["project_name_updated"] is False
    assert result["title_skipped"] == "author_named"
    assert project.name == "同名书"
