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


@pytest.mark.parametrize("flag", [True, "true"])
async def test_author_requested_rename_overrides_the_author_name_lock(db_session, owner, flag):
    project = _project(db_session, owner, "我自己起的书名")

    payload = await _update_project(
        db_session,
        owner,
        project,
        {"title": "《雾港来信》", "author_requested": flag},
        author_message=RENAME_ASK,
    )

    assert payload["status"] == "success"
    assert payload["data"]["project_name_updated"] is True
    assert "title_note" not in payload["data"]
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "雾港来信"


async def test_author_requested_name_is_not_overwritten_by_later_ai_titles(db_session, owner):
    """作者让 AI 改成的名字仍然是作者的选择：之后 AI 自动定名不能覆盖它。"""
    project = _project(db_session, owner, "我的小说")
    await _update_project(db_session, owner, project, {"title": "雾港来信"})
    await _update_project(
        db_session,
        owner,
        project,
        {"title": "海雾", "author_requested": True},
        author_message="项目名改成海雾吧",
    )

    payload = await _update_project(db_session, owner, project, {"title": "雾港旧事"})

    assert payload["data"]["title_skipped"] == "author_named"
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "海雾"


async def test_author_requested_still_rejects_invalid_titles(db_session, owner):
    project = _project(db_session, owner, "我自己起的书名")

    payload = await _update_project(
        db_session,
        owner,
        project,
        {"title": "长" * 31, "author_requested": True},
        author_message=RENAME_ASK,
    )

    assert payload["data"]["title_skipped"] == "invalid_title"
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "我自己起的书名"


@pytest.mark.parametrize(
    "author_message",
    [
        None,
        "",
        "帮我写一份大纲，主角是灯塔看守人",
        "大纲第一行写上书名《雾港来信》",
        "第三章的标题改成「潮汐」",
    ],
)
async def test_author_requested_without_a_rename_ask_keeps_the_author_name(
    db_session, owner, author_message
):
    """模型自己带上 author_requested，但作者这一轮没说要改名：服务端按没传处理。"""
    project = _project(db_session, owner, "我自己起的书名")

    payload = await _update_project(
        db_session,
        owner,
        project,
        {"title": "雾港来信", "author_requested": True},
        author_message=author_message,
    )

    data = payload["data"]
    assert data["project_name_updated"] is False
    assert data["title_skipped"] == "author_named"
    assert "项目切换器" in data["title_note"]
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "我自己起的书名"


async def test_rename_ask_sent_while_the_run_is_going_counts(db_session, owner):
    """作者在 AI 运行中追加「改名」（steering），也算这一轮的明确要求。"""
    project = _project(db_session, owner, "我自己起的书名")
    steering: list[str] = []

    steering.append("对了，项目名换成雾港来信")
    payload = await _update_project(
        db_session,
        owner,
        project,
        {"title": "雾港来信", "author_requested": True},
        author_message="继续写第二章",
        author_steering=steering,
    )

    assert payload["data"]["project_name_updated"] is True
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "雾港来信"


@pytest.mark.parametrize(
    "message",
    [
        "把项目名改成《雾港来信》",
        "帮我改个名吧，叫雾港来信",
        "书名换成雾港旧事",
        "这本小说的名字改一下",
        "作品名就叫海雾",
        "重命名为海雾",
        "Rename the project to Fog Harbor",
        "change the book title to Fog Harbor",
    ],
)
def test_rename_asks_are_recognised(message):
    from agent.tools.file_ops.project import author_message_asks_rename

    assert author_message_asks_rename(message) is True


@pytest.mark.parametrize(
    "message",
    [
        None,
        "   ",
        "帮我写一份大纲",
        "主角的名字改成李雷",
        "第三章的标题改成「潮汐」",
        "书名是什么来着？",
        "write an outline for chapter one",
    ],
)
def test_non_rename_messages_are_not_rename_asks(message):
    from agent.tools.file_ops.project import author_message_asks_rename

    assert author_message_asks_rename(message) is False


def test_update_project_schema_documents_author_requested():
    from agent.tools.tool_schemas import UPDATE_PROJECT_TOOL

    properties = UPDATE_PROJECT_TOOL["input_schema"]["properties"]
    assert properties["author_requested"]["type"] == "boolean"
    assert "明确要求改项目名" in properties["author_requested"]["description"]
    assert "author_requested=true" in UPDATE_PROJECT_TOOL["description"]
    assert "title_note" in properties["title"]["description"]
