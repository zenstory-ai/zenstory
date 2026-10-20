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


RENAME_ASK = "把项目名改成《雾港来信》"


async def _update_project(session, user, project, args, *, author_message=None, author_steering=None):
    engine = session.get_bind()
    mcp_tools.ToolContext.set_context(
        None,
        user.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
        author_message=author_message,
        author_steering=author_steering,
    )
    try:
        raw = await mcp_tools.update_project(args)
    finally:
        mcp_tools.ToolContext.clear_context()
    return json.loads(raw["content"][0]["text"])


@pytest.mark.parametrize(
    ("default_name", "project_type"),
    [
        ("我的小说", "novel"),
        ("我的短剧", "screenplay"),
        ("My Short Story", "short"),
        ("未命名项目", "novel"),
        # The dashboard numbers a default name the author already uses (2026-10-20).
        ("我的小说 2", "novel"),
        ("My Novel 12", "novel"),
        ("我的小说 99", "novel"),
    ],
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


async def test_ai_named_project_follows_the_next_ai_title(db_session, owner):
    """AI 自动起的名字不算作者起的名：之后 AI 再定名，项目名照样跟着改（审计 N4）。"""
    project = _project(db_session, owner, "我的小说")

    first = await _update_project(db_session, owner, project, {"title": "雾港来信"})
    assert first["data"]["project_name_updated"] is True

    second = await _update_project(db_session, owner, project, {"title": "雾港旧事"})

    assert second["data"]["project_name_updated"] is True
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "雾港旧事"


async def test_author_rename_after_ai_naming_locks_the_name_again(db_session, owner):
    """作者在界面里把 AI 起的名字改掉以后，那就是作者起的名字，AI 不能再改。"""
    project = _project(db_session, owner, "我的小说")
    await _update_project(db_session, owner, project, {"title": "雾港来信"})

    db_session.expire_all()
    renamed_by_author = db_session.get(Project, project.id)
    renamed_by_author.name = "我自己起的书名"
    db_session.add(renamed_by_author)
    db_session.commit()

    payload = await _update_project(db_session, owner, project, {"title": "雾港旧事"})

    assert payload["data"]["project_name_updated"] is False
    assert payload["data"]["title_skipped"] == "author_named"
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "我自己起的书名"


async def test_refusal_names_the_real_manual_rename_entry(db_session, owner):
    """拒绝改名时把真实的手动入口写进结果，模型照着说，不用自己编一个「项目设置」。"""
    project = _project(db_session, owner, "我自己起的书名")

    payload = await _update_project(db_session, owner, project, {"title": "雾港来信"})

    note = payload["data"]["title_note"]
    assert "我自己起的书名" in note
    # 每次拒绝都带这段话：不能教模型「带上 author_requested 再试一次」。
    assert "author_requested" not in note
    assert "重试" in note
    assert "项目切换器" in note
    assert "铅笔" in note
    assert "「编辑项目名称」" in note
    assert "项目设置" not in note


@pytest.mark.parametrize(
    ("author_message", "author_steering", "flag"),
    [
        ("把项目名改成《雾港来信》", None, True),
        ("不要把项目名改成雾港来信", None, True),
        ("继续写第二章", ["对了，项目名换成雾港来信"], True),
        ("把项目名改成《雾港来信》", None, "true"),
    ],
)
async def test_model_flag_and_author_prose_never_override_author_name_lock(
    db_session, owner, author_message, author_steering, flag
):
    project = _project(db_session, owner, "我自己起的书名")

    payload = await _update_project(
        db_session,
        owner,
        project,
        {"title": "雾港来信", "author_requested": flag},
        author_message=author_message,
        author_steering=author_steering,
    )

    assert payload["status"] == "success"
    assert payload["data"]["project_name_updated"] is False
    assert payload["data"]["title_skipped"] == "author_named"
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "我自己起的书名"


async def test_direct_human_project_api_can_rename_author_owned_title(db_session, owner):
    from api.projects import UpdateProjectRequest, update_project

    project = _project(db_session, owner, "我自己起的书名")
    updated = update_project(
        project.id,
        UpdateProjectRequest(name="雾港来信"),
        current_user=owner,
        session=db_session,
    )

    assert updated.name == "雾港来信"
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "雾港来信"


async def test_model_flag_still_cannot_apply_invalid_title(db_session, owner):
    project = _project(db_session, owner, "我自己起的书名")

    payload = await _update_project(
        db_session,
        owner,
        project,
        {"title": "长" * 31, "author_requested": True},
        author_message=RENAME_ASK,
    )

    assert payload["data"]["project_name_updated"] is False
    assert payload["data"]["title_skipped"] == "invalid_title"
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "我自己起的书名"


def test_update_project_schema_does_not_expose_model_authority_flag():
    from agent.tools.tool_schemas import UPDATE_PROJECT_TOOL

    properties = UPDATE_PROJECT_TOOL["input_schema"]["properties"]
    assert "author_requested" not in properties
    assert "author_requested" not in UPDATE_PROJECT_TOOL["description"]
    assert "title_note" in properties["title"]["description"]


@pytest.mark.parametrize("name", ["我的小说 2024", "我的小说 1", "我的小说 100", "我的小说2"])
async def test_numbered_names_beyond_what_the_dashboard_makes_stay_the_authors(db_session, owner, name):
    """The dashboard numbers duplicates from 2 (「我的小说 2」); a year or other number is the author's."""
    project = _project(db_session, owner, name)

    payload = await _update_project(db_session, owner, project, {"title": "雾港来信"})

    assert payload["data"]["project_name_updated"] is False
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == name


def test_update_project_status_ends_its_transaction_when_nothing_changes(db_session, owner):
    """The FOR UPDATE row lock must not outlive the call: the tool writes its artifact ledger in a
    separate session right after (2026-10-20 production deadlock)."""
    from agent.tools.file_ops.project import ProjectOperations

    project = _project(db_session, owner, "作者自己起的名字")

    result = ProjectOperations(db_session, owner.id).update_project_status(
        project_id=project.id, title="雾港来信"
    )

    assert result["project_name_updated"] is False
    assert not db_session.in_transaction()
