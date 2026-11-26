"""load_skill / read_skill_resource 工具（技能渐进式加载 L2/L3）。"""

import json
from unittest.mock import patch

import pytest
from sqlmodel import Session

from agent.skills.context_injector import SkillContextInjector
from agent.tools.mcp_tools import load_skill, read_skill_resource
from agent.tools.registry import get_exposed_tools
from agent.tools.tool_context import AgentRequestContext
from agent.tools.tool_schemas import TOOL_SCHEMAS
from agent.utils.token_utils import estimate_text_tokens
from models import Project, PublicSkill, SkillResource, User, UserAddedSkill, UserSkill


def _user(db_session: Session, suffix: str) -> User:
    user = User(
        email=f"skill_tools_{suffix}@example.com",
        username=f"skill_tools_{suffix}",
        hashed_password="hashed_password",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def owner(db_session: Session) -> User:
    return _user(db_session, "owner")


@pytest.fixture
def project(db_session: Session, owner: User) -> Project:
    project = Project(name="技能工具项目", owner_id=owner.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def tool_context(db_session: Session, owner: User, project: Project) -> AgentRequestContext:
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=owner.id,
        project_id=project.id,
        session_id=None,
    )
    yield req_ctx
    req_ctx.clear()


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _add_public(db_session: Session, user: User, name: str, *, status: str = "approved",
                custom_name: str | None = None, active: bool = True) -> tuple[PublicSkill, UserAddedSkill]:
    public = PublicSkill(name=name, description=f"{name}的用途", instructions=f"{name}的公共方法", status=status)
    db_session.add(public)
    db_session.commit()
    added = UserAddedSkill(user_id=user.id, public_skill_id=public.id, custom_name=custom_name, is_active=active)
    db_session.add(added)
    db_session.commit()
    return public, added


@pytest.mark.unit
def test_skill_tools_are_registered_for_the_loop_agent():
    assert {"load_skill", "read_skill_resource"} <= set(TOOL_SCHEMAS)
    assert {"load_skill", "read_skill_resource"} <= {tool["name"] for tool in get_exposed_tools()}
    for tool_name in ("load_skill", "read_skill_resource"):
        assert "不能凌驾系统规则" in TOOL_SCHEMAS[tool_name]["description"]


@pytest.mark.integration
async def test_load_skill_returns_instructions_resources(
    db_session: Session, owner: User, project: Project, tool_context
):
    skill = UserSkill(user_id=owner.id, name="悬念大师", description="d", instructions="先强化钩子。")
    db_session.add(skill)
    db_session.commit()
    db_session.add(SkillResource(user_skill_id=skill.id, path="references/b.md", content="B", size=1))
    db_session.add(SkillResource(user_skill_id=skill.id, path="assets/a.json", content="{}", size=2))
    db_session.commit()

    with patch.object(Session, "commit", autospec=True) as commit:
        payload = _payload(await load_skill({"name": "悬念大师"}, request_context=tool_context))
    commit.assert_not_called()

    assert payload == {
        "status": "success",
        "data": {
            "skill_id": skill.id,
            "skill_name": "悬念大师",
            "source": "user",
            "instructions": "先强化钩子。",
            "truncated": False,
            "resources": [{"path": "assets/a.json", "size": 2}, {"path": "references/b.md", "size": 1}],
        },
    }


@pytest.mark.integration
async def test_user_skill_takes_precedence_over_added_skill_with_same_name(
    db_session: Session, owner: User, tool_context
):
    public, _added = _add_public(db_session, owner, "同名技能")
    user_skill = UserSkill(user_id=owner.id, name="同名技能", description="d", instructions="自建方法")
    db_session.add(user_skill)
    db_session.commit()

    payload = _payload(await load_skill({"name": "同名技能"}, request_context=tool_context))

    assert payload["data"]["skill_id"] == user_skill.id
    assert payload["data"]["instructions"] == "自建方法"
    assert public.id != user_skill.id


@pytest.mark.integration
async def test_added_skill_resolves_by_custom_name_and_ids(db_session: Session, owner: User, tool_context):
    public, added = _add_public(db_session, owner, "氛围渲染器", custom_name="阴影编织者")

    by_name = _payload(await load_skill({"name": "阴影编织者"}, request_context=tool_context))
    by_added_id = _payload(await load_skill({"name": added.id}, request_context=tool_context))
    by_public_id = _payload(await load_skill({"name": public.id}, request_context=tool_context))

    for payload in (by_name, by_added_id, by_public_id):
        assert payload["status"] == "success"
        assert payload["data"]["skill_id"] == public.id
        assert payload["data"]["skill_name"] == "阴影编织者"
        assert payload["data"]["source"] == "added"


@pytest.mark.integration
async def test_inactive_unapproved_and_foreign_skills_are_hidden(
    db_session: Session, owner: User, tool_context
):
    other = _user(db_session, "other")
    db_session.add(UserSkill(user_id=owner.id, name="停用技能", instructions="x", is_active=False))
    db_session.add(UserSkill(user_id=other.id, name="别人的技能", instructions="x"))
    db_session.add(UserSkill(user_id=owner.id, name="正常技能", instructions="x"))
    db_session.commit()
    _add_public(db_session, owner, "待审技能", status="pending")
    _add_public(db_session, owner, "移除技能", active=False)

    for hidden in ("停用技能", "别人的技能", "待审技能", "移除技能"):
        payload = _payload(await load_skill({"name": hidden}, request_context=tool_context))
        assert payload["status"] == "error"
        assert "正常技能" in payload["error"]
        assert hidden not in payload["error"].split("可用技能：", 1)[1]


@pytest.mark.integration
async def test_unknown_skill_error_lists_at_most_ten_names(db_session: Session, owner: User, tool_context):
    for index in range(15):
        db_session.add(UserSkill(user_id=owner.id, name=f"技能{index:02d}", instructions="x"))
    db_session.commit()

    payload = _payload(await load_skill({"name": "不存在的技能"}, request_context=tool_context))

    assert payload["status"] == "error"
    listed = payload["error"].split("可用技能：", 1)[1].split("、")
    assert 1 <= len(listed) <= 10


@pytest.mark.integration
async def test_load_skill_requires_user_context(db_session: Session):
    req_ctx = AgentRequestContext(session=db_session, user_id=None, project_id="p", session_id=None)
    payload = _payload(await load_skill({"name": "x"}, request_context=req_ctx))
    assert payload["status"] == "error"


@pytest.mark.integration
async def test_read_skill_resource(db_session: Session, owner: User, tool_context):
    skill = UserSkill(user_id=owner.id, name="悬念大师", instructions="方法")
    db_session.add(skill)
    db_session.commit()
    db_session.add(SkillResource(user_skill_id=skill.id, path="references/hooks.md", content="钩子清单", size=12))
    db_session.commit()

    ok = _payload(await read_skill_resource({"name": "悬念大师", "path": "references/hooks.md"}, request_context=tool_context))
    missing = _payload(await read_skill_resource({"name": "悬念大师", "path": "references/none.md"}, request_context=tool_context))
    no_path = _payload(await read_skill_resource({"name": "悬念大师"}, request_context=tool_context))

    assert ok == {
        "status": "success",
        "data": {
            "skill_id": skill.id,
            "skill_name": "悬念大师",
            "path": "references/hooks.md",
            "content": "钩子清单",
            "truncated": False,
        },
    }
    assert missing["status"] == "error"
    assert "references/hooks.md" in missing["error"]
    assert no_path["status"] == "error"


@pytest.mark.integration
async def test_read_skill_resource_of_added_public_skill(db_session: Session, owner: User, tool_context):
    public, _added = _add_public(db_session, owner, "公共技能")
    db_session.add(SkillResource(public_skill_id=public.id, path="assets/t.json", content='{"a":1}', size=7))
    db_session.commit()

    payload = _payload(await read_skill_resource({"name": "公共技能", "path": "assets/t.json"}, request_context=tool_context))

    assert payload["data"]["content"] == '{"a":1}'


@pytest.mark.integration
async def test_read_skill_resource_cannot_reach_foreign_skill(db_session: Session, owner: User, tool_context):
    other = _user(db_session, "foreign")
    foreign = UserSkill(user_id=other.id, name="别人的技能", instructions="x")
    db_session.add(foreign)
    db_session.commit()
    db_session.add(SkillResource(user_skill_id=foreign.id, path="references/secret.md", content="机密", size=6))
    db_session.commit()

    by_name = _payload(await read_skill_resource({"name": "别人的技能", "path": "references/secret.md"}, request_context=tool_context))
    by_id = _payload(await read_skill_resource({"name": foreign.id, "path": "references/secret.md"}, request_context=tool_context))

    assert by_name["status"] == "error"
    assert by_id["status"] == "error"
    assert "机密" not in json.dumps(by_name, ensure_ascii=False) + json.dumps(by_id, ensure_ascii=False)


@pytest.mark.integration
async def test_repeated_load_skill_returns_same_instructions(
    db_session: Session, owner: User, project: Project, tool_context
):
    skill = UserSkill(user_id=owner.id, name="悬念大师", instructions="方法")
    db_session.add(skill)
    db_session.commit()

    first = _payload(await load_skill({"name": "悬念大师"}, request_context=tool_context))
    second = _payload(await load_skill({"name": skill.id}, request_context=tool_context))

    assert first["status"] == second["status"] == "success"
    assert first["data"]["instructions"] == second["data"]["instructions"] == "方法"


@pytest.mark.integration
async def test_load_skill_keeps_pending_state_of_tool_session(db_session: Session, owner: User):
    """加载技能不提交或回滚工具 session 上未提交的对象。"""
    other = _user(db_session, "pending_owner")
    foreign_project = Project(name="别人的项目", owner_id=other.id, project_type="novel")
    db_session.add(foreign_project)
    db_session.add(UserSkill(user_id=owner.id, name="悬念大师", instructions="方法"))
    db_session.commit()
    pending = Project(name="未提交的项目", owner_id=owner.id, project_type="novel")
    db_session.add(pending)
    req_ctx = AgentRequestContext(session=db_session, user_id=owner.id, project_id=foreign_project.id, session_id=None)

    payload = _payload(await load_skill({"name": "悬念大师"}, request_context=req_ctx))

    assert payload["status"] == "success"
    assert pending in db_session.new


@pytest.mark.integration
async def test_read_skill_resource_normalizes_path_to_nfc(db_session: Session, owner: User, tool_context):
    skill = UserSkill(user_id=owner.id, name="悬念大师", instructions="方法")
    db_session.add(skill)
    db_session.commit()
    db_session.add(SkillResource(user_skill_id=skill.id, path="references/café.md", content="C", size=1))
    db_session.commit()

    payload = _payload(await read_skill_resource({"name": "悬念大师", "path": "references/café.md"}, request_context=tool_context))

    assert payload["status"] == "success"
    assert payload["data"]["content"] == "C"


# ==================== 同名技能用 id 区分 ====================


@pytest.mark.integration
async def test_same_name_skills_are_listed_with_ids_and_loadable_by_id(
    db_session: Session, owner: User, tool_context
):
    """同名技能在目录里带 id，模型用 id 能载入每一个；按名称载入时会告知还有同名技能。"""
    user_skill = UserSkill(user_id=owner.id, name="续写", description="自建续写", instructions="自建方法")
    db_session.add(user_skill)
    db_session.commit()
    public, _added = _add_public(db_session, owner, "续写")
    db_session.add(UserSkill(user_id=owner.id, name="独有技能", description="只有一个", instructions="x"))
    db_session.commit()

    catalog = SkillContextInjector().build_skill_catalog(db_session, owner.id)
    by_name = _payload(await load_skill({"name": "续写"}, request_context=tool_context))
    by_public_id = _payload(await load_skill({"name": public.id}, request_context=tool_context))
    resource_by_id = _payload(await read_skill_resource({"name": public.id, "path": "SKILL.md"}, request_context=tool_context))

    assert f"**续写** (id: {user_skill.id})" in catalog
    assert f"**续写** (id: {public.id})" in catalog
    assert "- **独有技能**: 只有一个" in catalog
    assert by_name["data"]["skill_id"] == user_skill.id
    assert [item["id"] for item in by_name["data"]["same_name_skills"]] == [public.id]
    assert by_public_id["data"]["skill_id"] == public.id
    assert by_public_id["data"]["instructions"] == "续写的公共方法"
    assert "same_name_skills" not in by_public_id["data"]
    assert resource_by_id["data"]["content"] == "续写的公共方法"


# ==================== 技能全文加载与每轮 3 技能门禁 ====================


def _long_text(chars: int) -> str:
    return ("情节推进要有因果，人物动机要清楚。" * (chars // 16 + 1))[:chars]


@pytest.mark.integration
async def test_load_skill_and_read_skill_resource_return_full_long_content_without_truncation(
    db_session: Session, owner: User, tool_context
):
    """超过 24k tokens（≤ 50k 字符上限）的长技能与大资源文件一次完整返回，不再人为截断；offset 向后兼容。"""
    instructions = _long_text(50_000)
    assert estimate_text_tokens(instructions) > 24_000
    resource_body = "r1 p2\n" * 10_000  # 60,000 bytes (<= 64 KiB cap) and > 24k tokens
    assert len(resource_body.encode("utf-8")) <= 64 * 1024
    assert estimate_text_tokens(resource_body) > 24_000

    skill = UserSkill(user_id=owner.id, name="超长完整技能", instructions=instructions)
    db_session.add(skill)
    db_session.commit()
    db_session.add(SkillResource(
        user_skill_id=skill.id,
        path="references/full.md",
        content=resource_body,
        size=len(resource_body.encode("utf-8")),
    ))
    db_session.commit()

    loaded = _payload(await load_skill({"name": "超长完整技能"}, request_context=tool_context))["data"]
    assert loaded["truncated"] is False
    assert loaded["instructions"] == instructions
    assert "continue_hint" not in loaded
    assert "next_offset" not in loaded

    full_resource = _payload(await read_skill_resource(
        {"name": skill.id, "path": "references/full.md"}, request_context=tool_context
    ))["data"]
    assert full_resource["truncated"] is False
    assert full_resource["content"] == resource_body
    assert "offset" not in full_resource

    # 向后兼容：显式传 offset > 0 时按字符偏移切片返回全文余段，并回传 offset 供卡片持久化与冷回放使用
    sliced_body = _payload(await read_skill_resource(
        {"name": skill.id, "path": "SKILL.md", "offset": 49_000}, request_context=tool_context
    ))["data"]
    assert sliced_body == {
        "skill_id": skill.id,
        "skill_name": "超长完整技能",
        "path": "SKILL.md",
        "content": instructions[49_000:],
        "truncated": False,
        "offset": 49_000,
    }

    sliced_resource = _payload(await read_skill_resource(
        {"name": skill.id, "path": "references/full.md", "offset": 59_400}, request_context=tool_context
    ))["data"]
    assert sliced_resource == {
        "skill_id": skill.id,
        "skill_name": "超长完整技能",
        "path": "references/full.md",
        "content": resource_body[59_400:],
        "truncated": False,
        "offset": 59_400,
    }


@pytest.mark.integration
async def test_load_skill_does_not_truncate_when_legacy_skill_tokens_used_is_passed(
    db_session: Session, owner: User, project: Project
):
    """兼容旧构造参数 skill_tokens_used，但不再据此截断技能正文。"""
    instructions = _long_text(30_000)
    assert estimate_text_tokens(instructions) > 24_000
    skill = UserSkill(user_id=owner.id, name="长技能", instructions=instructions)
    db_session.add(skill)
    db_session.commit()
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=owner.id,
        project_id=project.id,
        session_id=None,
        skill_tokens_used=999_999,
    )

    loaded = _payload(await load_skill({"name": "长技能"}, request_context=req_ctx))["data"]

    assert loaded["truncated"] is False
    assert loaded["instructions"] == instructions


@pytest.mark.integration
async def test_read_skill_resource_rejects_bad_offset(db_session: Session, owner: User, tool_context):
    db_session.add(UserSkill(user_id=owner.id, name="悬念大师", instructions="方法"))
    db_session.commit()

    negative_text = _payload(await read_skill_resource({"name": "悬念大师", "path": "SKILL.md", "offset": "abc"}, request_context=tool_context))
    past_end = _payload(await read_skill_resource({"name": "悬念大师", "path": "SKILL.md", "offset": 99}, request_context=tool_context))

    assert negative_text["status"] == "error"
    assert past_end["status"] == "error"


@pytest.mark.integration
async def test_load_skill_gets_full_text_of_added_official_skill(db_session: Session, owner: User, tool_context):
    """官方技能的正文对用户隐藏，但 agent 自己的 load_skill / read_skill_resource 仍拿到全文。"""
    public = PublicSkill(
        name="官方节奏", description="控制节奏", instructions="官方完整方法：先压后扬。",
        status="approved", source="official",
    )
    db_session.add(public)
    db_session.commit()
    db_session.add(UserAddedSkill(user_id=owner.id, public_skill_id=public.id))
    db_session.add(SkillResource(public_skill_id=public.id, path="references/beats.md", content="节拍表", size=9))
    db_session.commit()

    loaded = _payload(await load_skill({"name": "官方节奏"}, request_context=tool_context))
    resource = _payload(await read_skill_resource({"name": "官方节奏", "path": "references/beats.md"}, request_context=tool_context))

    assert loaded["data"]["instructions"] == "官方完整方法：先压后扬。"
    assert loaded["data"]["resources"] == [{"path": "references/beats.md", "size": 9}]
    assert resource["data"]["content"] == "节拍表"


@pytest.mark.integration
async def test_load_skill_caps_distinct_skills_per_request(db_session: Session, owner: User, tool_context):
    from agent.skills.content_budget import MAX_LOADED_SKILLS_PER_REQUEST

    long_body = _long_text(30_000)
    names = [f"技能{i}" for i in range(MAX_LOADED_SKILLS_PER_REQUEST + 1)]
    for name in names:
        db_session.add(UserSkill(user_id=owner.id, name=name, description="d", instructions=f"{name}:{long_body}"))
    db_session.commit()

    for name in names[:MAX_LOADED_SKILLS_PER_REQUEST]:
        loaded = _payload(await load_skill({"name": name}, request_context=tool_context))
        assert loaded["status"] == "success"
        assert loaded["data"]["truncated"] is False
        assert loaded["data"]["instructions"] == f"{name}:{long_body}"
    # 已加载过的技能重复加载、续读不另计
    assert _payload(await load_skill({"name": names[0]}, request_context=tool_context))["status"] == "success"
    assert _payload(await read_skill_resource({"name": names[1], "path": "SKILL.md"}, request_context=tool_context))["status"] == "success"

    over = _payload(await load_skill({"name": names[-1]}, request_context=tool_context))
    over_read = _payload(await read_skill_resource({"name": names[-1], "path": "SKILL.md"}, request_context=tool_context))
    for result in (over, over_read):
        assert result["status"] == "error"
        assert str(MAX_LOADED_SKILLS_PER_REQUEST) in result["error"] and names[-1] in result["error"]

    # 新的一轮请求重新计数
    fresh_ctx = AgentRequestContext(session=db_session, user_id=owner.id, project_id=None, session_id=None)
    assert _payload(await load_skill({"name": names[-1]}, request_context=fresh_ctx))["status"] == "success"


@pytest.mark.unit
def test_skill_distinct_cap_and_tool_schema_descriptions():
    from agent.skills.active_skills import MAX_SELECTED_SKILLS
    from agent.skills.content_budget import MAX_LOADED_SKILLS_PER_REQUEST

    # 关键安全不变量：每轮最多加载/显式选择 3 个不同技能不得放宽
    assert MAX_LOADED_SKILLS_PER_REQUEST == 3
    assert MAX_SELECTED_SKILLS == 3
    assert "truncated=true" not in TOOL_SCHEMAS["load_skill"]["description"]
    assert "truncated=true" not in TOOL_SCHEMAS["read_skill_resource"]["description"]


@pytest.mark.integration
async def test_read_skill_resource_supports_examples_and_templates_directories(
    db_session: Session, owner: User, tool_context
):
    skill = UserSkill(user_id=owner.id, name="模板技能", instructions="按示例和模板写作")
    db_session.add(skill)
    db_session.commit()
    db_session.add(SkillResource(user_skill_id=skill.id, path="examples/scene.md", content="场景范例", size=12))
    db_session.add(SkillResource(user_skill_id=skill.id, path="templates/chapter.txt", content="章节模板", size=12))
    db_session.commit()

    example = _payload(await read_skill_resource(
        {"name": "模板技能", "path": "examples/scene.md"}, request_context=tool_context
    ))
    template = _payload(await read_skill_resource(
        {"name": "模板技能", "path": "templates/chapter.txt"}, request_context=tool_context
    ))

    assert example["status"] == "success"
    assert example["data"]["content"] == "场景范例"
    assert template["status"] == "success"
    assert template["data"]["content"] == "章节模板"
