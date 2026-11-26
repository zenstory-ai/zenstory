"""Server-side cold-replay rehydration tests for persisted client-safe skill cards."""

from __future__ import annotations

import pytest
from sqlmodel import Session

from agent.skills import (
    SKILL_REPLAY_TOKEN_BUDGET,
    index_active_skills,
    load_active_skills,
    rehydrate_skill_tool_result,
)
from agent.skills.content_budget import (
    MAX_LOADED_SKILLS_PER_REQUEST,
    SKILL_CONTENT_TOKEN_BUDGET,
    SkillContentBudget,
)
from agent.utils.token_utils import estimate_text_tokens
from models import PublicSkill, SkillResource, User, UserAddedSkill, UserSkill


def _user(db_session: Session, suffix: str) -> User:
    user = User(
        email=f"skill_replay_{suffix}@example.com",
        username=f"skill_replay_{suffix}",
        hashed_password="hashed_password",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _long_text(chars: int) -> str:
    return ("冲突层层递进，每个场景都要留下余味。" * (chars // 18 + 1))[:chars]


@pytest.mark.integration
def test_rehydrate_load_skill_for_active_user_skill(db_session: Session):
    owner = _user(db_session, "load_user")
    skill = UserSkill(user_id=owner.id, name="悬念大师", description="d", instructions="先抛钩子再留悬念。")
    db_session.add(skill)
    db_session.commit()
    db_session.add(SkillResource(user_skill_id=skill.id, path="references/hooks.md", content="钩子表", size=9))
    db_session.add(SkillResource(user_skill_id=skill.id, path="examples/ch1.md", content="第一章示例", size=15))
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))
    card = {
        "skill_id": skill.id,
        "skill_name": "悬念大师",
        "source": "user",
        "instructions_chars": 9,
        "resources": [],
    }
    card_snapshot = dict(card)
    budget = SkillContentBudget(total=SKILL_REPLAY_TOKEN_BUDGET)

    rehydrated = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "load_skill",
        card,
        remaining_budget=budget,
    )

    assert card == card_snapshot  # pure: does not mutate persisted card
    assert rehydrated == {
        "skill_id": skill.id,
        "skill_name": "悬念大师",
        "source": "user",
        "instructions": "先抛钩子再留悬念。",
        "truncated": False,
        "resources": [
            {"path": "examples/ch1.md", "size": 15},
            {"path": "references/hooks.md", "size": 9},
        ],
    }
    assert budget.remaining < SKILL_REPLAY_TOKEN_BUDGET


@pytest.mark.integration
def test_rehydrate_added_official_and_community_skills(db_session: Session):
    """服务端冷回放可为模型还原已添加且 approved 的官方/社区技能，包含自定义名称与资源。"""
    owner = _user(db_session, "added_public")
    official = PublicSkill(
        name="官方节奏",
        description="控制节奏",
        instructions="官方完整方法：先压后扬。",
        status="approved",
        source="official",
    )
    db_session.add(official)
    db_session.commit()
    db_session.add(UserAddedSkill(
        user_id=owner.id,
        public_skill_id=official.id,
        custom_name="我的节奏表",
        is_active=True,
    ))
    db_session.add(SkillResource(
        public_skill_id=official.id,
        path="templates/beats.md",
        content="三幕节拍模板",
        size=18,
    ))
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))
    budget = SkillContentBudget()

    loaded = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "load_skill",
        {"skill_id": official.id, "skill_name": "官方节奏"},
        remaining_budget=budget,
    )
    resource = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "read_skill_resource",
        {"skill_id": official.id, "skill_name": "官方节奏", "path": "templates/beats.md", "size": 18},
        remaining_budget=budget,
    )

    assert loaded is not None
    assert loaded["skill_id"] == official.id
    assert loaded["skill_name"] == "我的节奏表"
    assert loaded["source"] == "added"
    assert loaded["instructions"] == "官方完整方法：先压后扬。"
    assert loaded["resources"] == [{"path": "templates/beats.md", "size": 18}]
    assert resource == {
        "skill_id": official.id,
        "skill_name": "我的节奏表",
        "path": "templates/beats.md",
        "content": "三幕节拍模板",
        "truncated": False,
    }


@pytest.mark.integration
def test_rehydrate_read_skill_resource_supports_skill_md_offset_and_nfc(db_session: Session):
    owner = _user(db_session, "resource_offset")
    skill = UserSkill(user_id=owner.id, name="分段技能", instructions="0123456789ABCDEF")
    db_session.add(skill)
    db_session.commit()
    db_session.add(SkillResource(
        user_skill_id=skill.id,
        path="examples/café.md",
        content="头段内容｜尾段内容",
        size=27,
    ))
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))
    budget = SkillContentBudget()

    body_slice = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "read_skill_resource",
        {"skill_id": skill.id, "skill_name": "分段技能", "path": "SKILL.md", "offset": 10},
        remaining_budget=budget,
    )
    nfc_resource = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "read_skill_resource",
        # NFD decomposed path should match stored NFC resource path
        {"skill_id": skill.id, "skill_name": "分段技能", "path": "examples/cafe\u0301.md", "offset": 5},
        remaining_budget=budget,
    )

    assert body_slice == {
        "skill_id": skill.id,
        "skill_name": "分段技能",
        "path": "SKILL.md",
        "content": "ABCDEF",
        "truncated": False,
        "offset": 10,
    }
    assert nfc_resource == {
        "skill_id": skill.id,
        "skill_name": "分段技能",
        "path": "examples/café.md",
        "content": "尾段内容",
        "truncated": False,
        "offset": 5,
    }


@pytest.mark.integration
def test_rehydrate_returns_none_for_deleted_disabled_unapproved_or_foreign_skills(db_session: Session):
    owner = _user(db_session, "auth_owner")
    other = _user(db_session, "auth_other")

    disabled = UserSkill(user_id=owner.id, name="停用", instructions="secret", is_active=False)
    foreign = UserSkill(user_id=other.id, name="别人的", instructions="foreign-secret", is_active=True)
    # 同名干扰：owner 拥有另一个同名技能，但 persisted card 的 skill_id 是已删除/别人的 id，绝不能按名回退
    same_name_owned = UserSkill(user_id=owner.id, name="别人的", instructions="owned-body", is_active=True)
    unapproved = PublicSkill(name="下架", description="d", instructions="unpublished", status="rejected")
    removed_pub = PublicSkill(name="已移除", description="d", instructions="removed", status="approved")
    db_session.add_all([disabled, foreign, same_name_owned, unapproved, removed_pub])
    db_session.commit()
    db_session.add(UserAddedSkill(user_id=owner.id, public_skill_id=unapproved.id, is_active=True))
    db_session.add(UserAddedSkill(user_id=owner.id, public_skill_id=removed_pub.id, is_active=False))
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))

    for bad_card in (
        {"skill_id": disabled.id, "skill_name": "停用"},
        {"skill_id": foreign.id, "skill_name": "别人的"},
        {"skill_id": unapproved.id, "skill_name": "下架"},
        {"skill_id": removed_pub.id, "skill_name": "已移除"},
        {"skill_id": "deleted-skill-id", "skill_name": "别人的"},
        # 历史遗留卡片缺少 skill_id：不能仅凭 skill_name 猜测
        {"skill_name": "别人的", "path": "SKILL.md"},
    ):
        assert rehydrate_skill_tool_result(
            active_by_id,
            db_session,
            "load_skill",
            bad_card,
            remaining_budget=SkillContentBudget(),
        ) is None
        assert rehydrate_skill_tool_result(
            active_by_id,
            db_session,
            "read_skill_resource",
            {**bad_card, "path": "SKILL.md"},
            remaining_budget=SkillContentBudget(),
        ) is None


@pytest.mark.integration
def test_rehydrate_returns_none_for_missing_resource_bad_offset_or_unknown_tool(db_session: Session):
    owner = _user(db_session, "edge_cases")
    skill = UserSkill(user_id=owner.id, name="边界技能", instructions="短正文")
    db_session.add(skill)
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))

    # 资源已被删除
    assert rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "read_skill_resource",
        {"skill_id": skill.id, "path": "references/deleted.md"},
        remaining_budget=SkillContentBudget(),
    ) is None
    # 缺少 path
    assert rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "read_skill_resource",
        {"skill_id": skill.id},
        remaining_budget=SkillContentBudget(),
    ) is None
    # offset 越界或类型非法
    for bad_offset in (-1, True, "10", len(skill.instructions) + 1):
        assert rehydrate_skill_tool_result(
            active_by_id,
            db_session,
            "read_skill_resource",
            {"skill_id": skill.id, "path": "SKILL.md", "offset": bad_offset},
            remaining_budget=SkillContentBudget(),
        ) is None
    # 非技能工具或非法 result_data
    assert rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "query_files",
        {"skill_id": skill.id},
        remaining_budget=SkillContentBudget(),
    ) is None
    assert rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "load_skill",
        "not-a-dict",
        remaining_budget=SkillContentBudget(),
    ) is None


@pytest.mark.integration
def test_rehydrate_honors_shared_budget_truncation_and_exhaustion(db_session: Session):
    owner = _user(db_session, "budget")
    long_body = _long_text(20_000)
    skill_a = UserSkill(user_id=owner.id, name="长技能A", instructions=long_body)
    skill_b = UserSkill(user_id=owner.id, name="短技能B", instructions="短方法")
    db_session.add_all([skill_a, skill_b])
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))
    # 预算只有 500 tokens：长技能会被截断；余额耗尽后再回放应返回 None 回退到 _SKILL_CONTENT_NOT_KEPT
    budget = SkillContentBudget(total=500)

    first = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "load_skill",
        {"skill_id": skill_a.id, "skill_name": "长技能A"},
        remaining_budget=budget,
    )

    assert first is not None
    assert first["truncated"] is True
    assert estimate_text_tokens(first["instructions"]) <= 500
    assert first["next_offset"] == len(first["instructions"])
    assert first["total_chars"] == len(long_body)
    assert "read_skill_resource" in first["continue_hint"]
    assert budget.remaining < 500

    # 继续消耗剩余零头直到余额归零，下一次回放必须返回 None
    offset = first["next_offset"]
    for _ in range(20):
        nxt = rehydrate_skill_tool_result(
            active_by_id,
            db_session,
            "read_skill_resource",
            {"skill_id": skill_a.id, "path": "SKILL.md", "offset": offset},
            remaining_budget=budget,
        )
        if nxt is None:
            break
        offset = nxt["next_offset"]
    assert budget.remaining == 0

    after_exhausted = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "load_skill",
        {"skill_id": skill_b.id, "skill_name": "短技能B"},
        remaining_budget=budget,
    )
    assert after_exhausted is None

    # 传入 0 或负数整数预算也直接返回 None
    assert rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "load_skill",
        {"skill_id": skill_b.id},
        remaining_budget=0,
    ) is None
    assert rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "load_skill",
        {"skill_id": skill_b.id},
        remaining_budget=-10,
    ) is None


@pytest.mark.integration
def test_rehydrate_across_many_turns_is_bounded_by_token_budget_not_per_turn_skill_cap(
    db_session: Session,
):
    """跨多轮冷回放可能合法包含超过单轮 3 个的不同技能；回放只受 token 预算约束。"""
    owner = _user(db_session, "multi_turn")
    skills = [
        UserSkill(user_id=owner.id, name=f"技能{i}", instructions=f"方法{i}")
        for i in range(MAX_LOADED_SKILLS_PER_REQUEST + 2)
    ]
    db_session.add_all(skills)
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))
    budget = SkillContentBudget(total=SKILL_CONTENT_TOKEN_BUDGET)

    results = [
        rehydrate_skill_tool_result(
            active_by_id,
            db_session,
            "load_skill",
            {"skill_id": skill.id, "skill_name": skill.name},
            remaining_budget=budget,
        )
        for skill in skills
    ]

    assert all(item is not None for item in results)
    assert [item["instructions"] for item in results if item is not None] == [
        f"方法{i}" for i in range(MAX_LOADED_SKILLS_PER_REQUEST + 2)
    ]
