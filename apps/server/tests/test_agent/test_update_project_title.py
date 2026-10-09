"""update_project(title=...) follows the AI's work title only while the project has a default name."""

import json

import pytest
from sqlmodel import Session

from agent.tools import mcp_tools
from models import Project, User


@pytest.fixture
def owner(db_session):
    user = User(username="title-owner", email="title-owner@example.test", hashed_password="unused", email_verified=True)
    db_session.add(user)
    db_session.commit()
    return user


def _project(session, owner, name, project_type="novel"):
    project = Project(name=name, owner_id=owner.id, project_type=project_type)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


async def _update_project(session, user, project, args):
    engine = session.get_bind()
    mcp_tools.ToolContext.set_context(None, user.id, project.id, None, create_session_func=lambda: Session(engine))
    try:
        raw = await mcp_tools.update_project(args)
    finally:
        mcp_tools.ToolContext.clear_context()
    return json.loads(raw["content"][0]["text"])


@pytest.mark.parametrize(
    ("default_name", "project_type"),
    [("我的小说", "novel"), ("我的短剧", "screenplay"), ("My Short Story", "short"), ("未命名项目", "novel")],
)
async def test_default_project_name_is_replaced_by_the_work_title(db_session, owner, default_name, project_type):
    project = _project(db_session, owner, default_name, project_type)
    before = project.updated_at

    payload = await _update_project(db_session, owner, project, {"title": "雾港来信"})

    assert payload["status"] == "success"
    assert payload["data"]["project_name_updated"] is True
    db_session.expire_all()
    renamed = db_session.get(Project, project.id)
    assert renamed.name == "雾港来信"
    assert renamed.updated_at >= before


async def test_author_chosen_name_is_kept(db_session, owner):
    project = _project(db_session, owner, "我自己起的书名")

    payload = await _update_project(
        db_session, owner, project, {"title": "雾港来信", "notes": "主角怕水"}
    )

    assert payload["status"] == "success"
    data = payload["data"]
    assert data["project_name_updated"] is False
    assert data["title_skipped"] == "author_named"
    # 其他字段照常更新，title 不混进 updated_fields（前端按字段名展示）。
    assert data["updated_fields"] == ["notes"]
    db_session.expire_all()
    kept = db_session.get(Project, project.id)
    assert kept.name == "我自己起的书名"
    assert kept.notes == "主角怕水"


async def test_book_title_marks_are_stripped_and_overlong_titles_rejected(db_session, owner):
    project = _project(db_session, owner, "我的小说")

    payload = await _update_project(db_session, owner, project, {"title": "  《雾港 来信》 "})
    assert payload["data"]["project_name_updated"] is True
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "雾港 来信"

    other = _project(db_session, owner, "我的短篇", "short")
    overlong = "长" * 31
    payload = await _update_project(db_session, owner, other, {"title": f"《{overlong}》"})
    assert payload["status"] == "success"
    assert payload["data"]["project_name_updated"] is False
    assert payload["data"]["title_skipped"] == "invalid_title"
    db_session.expire_all()
    assert db_session.get(Project, other.id).name == "我的短篇"

    payload = await _update_project(db_session, owner, other, {"title": "《》"})
    assert payload["data"]["title_skipped"] == "invalid_title"
