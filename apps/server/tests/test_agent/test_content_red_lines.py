"""内容红线：所有角色共用的系统提示里都带着同一段不可协商规则，而且位于稳定的共享前缀里。

用例只核对提示词合同（红线在哪、覆盖哪些类别、冲突时谁优先），不判断模型输出。
"""

from os.path import commonprefix
from unittest.mock import patch

import pytest

from agent.core.message_manager import MessageManager
from agent.prompts.base import (
    CONTENT_RED_LINES,
    OUTPUT_PROTOCOL,
    PRODUCT_IDENTITY_PROTOCOL,
    get_base_prompt,
)
from agent.prompts.subagents import QUALITY_REVIEWER_PROMPT
from models import Project, User

CONFIG = {
    "role_definition": "你是一位资深网文编辑",
    "capabilities": "项目写作能力",
    "directory_structure": "正文: {draft}",
    "content_structure": "项目内容结构",
    "file_types": "项目文件类型",
    "writing_guidelines": "项目写作指导",
}


@pytest.mark.unit
def test_red_lines_name_the_categories_and_outrank_project_rules():
    for category in ("未成年人", "色情", "政治", "暴恐", "真实人物"):
        assert category in CONTENT_RED_LINES
    assert "不可协商" in CONTENT_RED_LINES


@pytest.mark.unit
@pytest.mark.parametrize("project_id", ["project-a", "project-b"])
def test_red_lines_sit_in_the_shared_prefix_right_after_identity(project_id):
    prompt = get_base_prompt(project_id, {"draft": f"{project_id}-draft"}, CONFIG)

    assert prompt.count(CONTENT_RED_LINES) == 1
    assert (
        prompt.index(PRODUCT_IDENTITY_PROTOCOL)
        < prompt.index(CONTENT_RED_LINES)
        < prompt.index(OUTPUT_PROTOCOL)
    )


@pytest.mark.unit
def test_red_lines_do_not_break_the_cross_project_prefix_cache():
    rendered = [
        get_base_prompt(project_id, {"draft": f"{project_id}-draft"}, CONFIG)
        for project_id in ("project-a", "project-b")
    ]
    assert CONTENT_RED_LINES in commonprefix(rendered)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.usefixtures("writing_prompt_configs")
@pytest.mark.parametrize("project_type", ["novel", "short", "screenplay"])
@pytest.mark.parametrize("language", ["zh", "en"])
async def test_every_role_runs_with_the_real_system_prompt_carrying_the_red_lines(
    db_session, project_type, language
):
    """真实装配的系统提示（三种作品类型、中英界面）都带红线；每个角色的运行都原样使用它，
    红线不会因为换角色而被替换或丢掉。角色提示只放在运行指令里。"""
    from agent.graph.nodes import run_streaming_agent

    owner = User(
        email=f"red-lines-{project_type}-{language}@example.com",
        username=f"red_lines_{project_type}_{language}",
        hashed_password="hashed_password",
        email_verified=True,
        is_active=True,
    )
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)
    project = Project(name="Red Lines", owner_id=owner.id, project_type=project_type)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    system_prompt = MessageManager(project_id=project.id, user_id=owner.id).build_system_prompt(
        session=db_session, language=language
    )
    assert system_prompt.count(CONTENT_RED_LINES) == 1

    seen: dict[str, str] = {}

    async def fake_runner(state, agent_type, system_prompt, get_steering_messages=None, agent_directive=""):
        seen[agent_type] = system_prompt
        if False:  # pragma: no cover - 让函数成为异步生成器
            yield None

    state = {"user_message": "写第一章", "messages": [], "system_prompt": system_prompt}
    with patch("agent.graph.nodes.run_openai_agents_streaming_agent", new=fake_runner):
        for agent_type in ("planner", "hook_designer", "writer", "quality_reviewer"):
            _ = [event async for event in run_streaming_agent(state=state, agent_type=agent_type)]

    assert set(seen) == {"planner", "hook_designer", "writer", "quality_reviewer"}
    assert all(prompt == system_prompt for prompt in seen.values())


@pytest.mark.unit
@pytest.mark.parametrize("force_en", [False, True])
def test_highest_priority_scope_constraint_does_not_outrank_the_red_lines(force_en):
    """运行指令里的 [最高优先级] 范围约束不能盖过内容红线，中英文固定说明一致。"""
    layout = "\n".join(MessageManager._build_input_layout_section(force_en))
    if force_en:
        assert "except the content red lines" in layout
    else:
        assert "内容红线除外" in layout


@pytest.mark.unit
def test_reviewer_lists_red_line_violations_as_must_fix():
    assert "内容红线" in QUALITY_REVIEWER_PROMPT
    assert "必须修改" in QUALITY_REVIEWER_PROMPT


@pytest.mark.unit
def test_router_red_lines_come_before_routing_rules_and_force_read_only():
    from agent.prompts.subagents import ROUTER_PROMPT

    section = ROUTER_PROMPT.index("## 内容红线")
    rules = ROUTER_PROMPT.index("## 选择规则")
    assert section < rules
    assert "read_only=true" in ROUTER_PROMPT[section:rules]
