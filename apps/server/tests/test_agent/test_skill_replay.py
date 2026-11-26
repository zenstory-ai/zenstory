"""Server-side cold-replay rehydration tests (unit helper + end-to-end load_loop_history)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session

from agent.loop.history import (
    _SKILL_CONTENT_NOT_KEPT,
    LOOP_ENGINE_METADATA_VALUE,
    SKILL_REPLAY_RECENT_TURNS,
    client_tool_calls,
    load_loop_history,
)
from agent.skills import (
    index_active_skills,
    load_active_skills,
    rehydrate_skill_tool_result,
)
from agent.skills.content_budget import MAX_LOADED_SKILLS_PER_REQUEST
from agent.stream_adapter import StreamAdapter
from agent.utils.token_utils import estimate_text_tokens
from models import (
    ChatMessage,
    ChatSession,
    Project,
    PublicSkill,
    SkillResource,
    User,
    UserAddedSkill,
    UserSkill,
)


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


def _project_and_session(db_session: Session, owner: User, suffix: str) -> tuple[Project, ChatSession]:
    project = Project(name=f"Replay Project {suffix}", owner_id=owner.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(
        user_id=owner.id,
        project_id=project.id,
        title=f"Replay Chat {suffix}",
        is_active=True,
        message_count=0,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    return project, chat


def _long_text(chars: int) -> str:
    return ("冲突层层递进，每个场景都要留下余味。" * (chars // 18 + 1))[:chars]


def _add_loop_turn(
    db_session: Session,
    chat_session_id: str,
    *,
    base_time: datetime,
    user_text: str,
    tool_calls: list[dict],
    assistant_text: str = "已完成。",
    steering_texts: list[str] | None = None,
) -> ChatMessage:
    user_row = ChatMessage(
        session_id=chat_session_id,
        role="user",
        content=user_text,
        created_at=base_time,
    )
    db_session.add(user_row)
    offset_seconds = 1
    for steer in steering_texts or []:
        db_session.add(ChatMessage(
            session_id=chat_session_id,
            role="user",
            content=steer,
            created_at=base_time + timedelta(seconds=offset_seconds),
        ))
        offset_seconds += 1

    display_events: list[dict] = [
        {"type": "tool_call", "tool_call_index": idx}
        for idx in range(len(tool_calls))
    ]
    if assistant_text:
        display_events.append({"type": "content", "content": assistant_text})

    assistant_row = ChatMessage(
        session_id=chat_session_id,
        role="assistant",
        content=assistant_text,
        tool_calls=json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
        message_metadata=json.dumps(
            {"engine": LOOP_ENGINE_METADATA_VALUE, "display_events": display_events},
            ensure_ascii=False,
        ),
        created_at=base_time + timedelta(seconds=offset_seconds),
    )
    db_session.add(assistant_row)
    db_session.commit()
    db_session.refresh(assistant_row)
    return assistant_row


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

    rehydrated = rehydrate_skill_tool_result(active_by_id, db_session, "load_skill", card)

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

    loaded = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "load_skill",
        {"skill_id": official.id, "skill_name": "官方节奏"},
    )
    resource = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "read_skill_resource",
        {"skill_id": official.id, "skill_name": "官方节奏", "path": "templates/beats.md", "size": 18},
    )

    assert loaded is not None
    assert loaded["skill_id"] == official.id
    assert loaded["skill_name"] == "我的节奏表"
    assert loaded["source"] == "added"
    assert loaded["instructions"] == "官方完整方法：先压后扬。"
    assert loaded["truncated"] is False
    assert loaded["resources"] == [{"path": "templates/beats.md", "size": 18}]
    assert resource == {
        "skill_id": official.id,
        "skill_name": "我的节奏表",
        "path": "templates/beats.md",
        "content": "三幕节拍模板",
        "truncated": False,
    }


@pytest.mark.integration
def test_rehydrate_returns_full_long_skill_without_artificial_truncation(db_session: Session):
    """冷回放对超过 24k tokens（≤ 50k 字符上限）的长技能返回全文，不做人为 token 截断。"""
    owner = _user(db_session, "long_untruncated")
    long_body = _long_text(50_000)
    assert estimate_text_tokens(long_body) > 24_000
    skill = UserSkill(user_id=owner.id, name="超长技能", instructions=long_body)
    db_session.add(skill)
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))
    rehydrated = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "load_skill",
        {"skill_id": skill.id, "skill_name": "超长技能"},
    )

    assert rehydrated is not None
    assert rehydrated["truncated"] is False
    assert rehydrated["instructions"] == long_body
    assert "continue_hint" not in rehydrated


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

    body_slice = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "read_skill_resource",
        {"skill_id": skill.id, "skill_name": "分段技能", "path": "SKILL.md", "offset": 10},
    )
    nfc_resource = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "read_skill_resource",
        # NFD decomposed path should match stored NFC resource path
        {"skill_id": skill.id, "skill_name": "分段技能", "path": "examples/cafe\u0301.md", "offset": 5},
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
        assert rehydrate_skill_tool_result(active_by_id, db_session, "load_skill", bad_card) is None
        assert rehydrate_skill_tool_result(
            active_by_id, db_session, "read_skill_resource", {**bad_card, "path": "SKILL.md"}
        ) is None


@pytest.mark.integration
def test_rehydrate_returns_none_for_missing_resource_bad_offset_or_unknown_tool(db_session: Session):
    owner = _user(db_session, "edge_cases")
    skill = UserSkill(user_id=owner.id, name="边界技能", instructions="短正文")
    db_session.add(skill)
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))

    assert rehydrate_skill_tool_result(
        active_by_id, db_session, "read_skill_resource", {"skill_id": skill.id, "path": "references/deleted.md"}
    ) is None
    assert rehydrate_skill_tool_result(
        active_by_id, db_session, "read_skill_resource", {"skill_id": skill.id}
    ) is None
    for bad_offset in (-1, True, "10", len(skill.instructions) + 1):
        assert rehydrate_skill_tool_result(
            active_by_id,
            db_session,
            "read_skill_resource",
            {"skill_id": skill.id, "path": "SKILL.md", "offset": bad_offset},
        ) is None
    assert rehydrate_skill_tool_result(active_by_id, db_session, "query_files", {"skill_id": skill.id}) is None
    assert rehydrate_skill_tool_result(active_by_id, db_session, "load_skill", "not-a-dict") is None


@pytest.mark.integration
def test_rehydrate_across_many_skills_is_not_capped_by_per_turn_skill_limit(db_session: Session):
    """跨多轮冷回放可能合法包含超过单轮 3 个的不同技能。"""
    owner = _user(db_session, "multi_turn")
    skills = [
        UserSkill(user_id=owner.id, name=f"技能{i}", instructions=f"方法{i}")
        for i in range(MAX_LOADED_SKILLS_PER_REQUEST + 2)
    ]
    db_session.add_all(skills)
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))

    results = [
        rehydrate_skill_tool_result(
            active_by_id,
            db_session,
            "load_skill",
            {"skill_id": skill.id, "skill_name": skill.name},
        )
        for skill in skills
    ]

    assert all(item is not None for item in results)
    assert [item["instructions"] for item in results if item is not None] == [
        f"方法{i}" for i in range(MAX_LOADED_SKILLS_PER_REQUEST + 2)
    ]


@pytest.mark.integration
def test_rehydrate_read_skill_resource_resolves_offset_from_call_arguments(db_session: Session):
    """当 result_data 未显式携带 offset 时，从配对的 call_arguments 还原正确偏移片段。"""
    owner = _user(db_session, "call_args_offset")
    skill = UserSkill(user_id=owner.id, name="续读技能", instructions="0123456789FINAL_SLICE")
    db_session.add(skill)
    db_session.commit()
    db_session.add(SkillResource(
        user_skill_id=skill.id,
        path="references/guide.md",
        content="HEAD_PART|TAIL_PART",
        size=19,
    ))
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))

    # 1) dict 形式的 call_arguments（SKILL.md）
    from_dict = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "read_skill_resource",
        {"skill_id": skill.id, "skill_name": "续读技能", "path": "SKILL.md", "size": 11},
        call_arguments={"name": skill.id, "path": "SKILL.md", "offset": 10},
    )
    assert from_dict == {
        "skill_id": skill.id,
        "skill_name": "续读技能",
        "path": "SKILL.md",
        "content": "FINAL_SLICE",
        "truncated": False,
        "offset": 10,
    }

    # 2) JSON 字符串形式的 call_arguments（对应 ChatMessage.tool_calls[i]["arguments"]）
    from_json_str = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "read_skill_resource",
        {"skill_id": skill.id, "skill_name": "续读技能", "path": "references/guide.md", "size": 9},
        call_arguments=json.dumps({"name": skill.id, "path": "references/guide.md", "offset": 10}),
    )
    assert from_json_str == {
        "skill_id": skill.id,
        "skill_name": "续读技能",
        "path": "references/guide.md",
        "content": "TAIL_PART",
        "truncated": False,
        "offset": 10,
    }

    # 3) result_data 显式提供 offset 时优先于 call_arguments
    explicit_wins = rehydrate_skill_tool_result(
        active_by_id,
        db_session,
        "read_skill_resource",
        {"skill_id": skill.id, "path": "SKILL.md", "offset": 15},
        call_arguments={"name": skill.id, "path": "SKILL.md", "offset": 10},
    )
    assert explicit_wins is not None
    assert explicit_wins["content"] == "_SLICE"
    assert explicit_wins["offset"] == 15


@pytest.mark.integration
def test_rehydrate_rejects_malformed_call_arguments_and_invalid_call_offset(db_session: Session):
    owner = _user(db_session, "bad_call_args")
    skill = UserSkill(user_id=owner.id, name="参数校验技能", instructions="0123456789")
    db_session.add(skill)
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))

    for bad_call_args in (
        "{not-json",
        '["not-an-object"]',
        123,
        {"offset": -1},
        {"offset": True},
        {"offset": "10"},
        {"offset": 999},
    ):
        assert rehydrate_skill_tool_result(
            active_by_id,
            db_session,
            "read_skill_resource",
            {"skill_id": skill.id, "path": "SKILL.md"},
            call_arguments=bad_call_args,
        ) is None


@pytest.mark.integration
def test_rehydrate_read_skill_resource_ignores_malformed_call_arguments_when_result_has_offset(db_session: Session):
    """result_data 已携带合法 offset 时，不再解析 call_arguments，空串或损坏的 call_arguments 不影响水合。"""
    owner = _user(db_session, "explicit_offset_malformed_args")
    skill = UserSkill(user_id=owner.id, name="显式偏移技能", instructions="0123456789TAIL")
    db_session.add(skill)
    db_session.commit()

    active_by_id = index_active_skills(load_active_skills(db_session, owner.id))

    for malformed_args in ("", "{corrupted", '["not-a-dict"]', 123):
        rehydrated = rehydrate_skill_tool_result(
            active_by_id,
            db_session,
            "read_skill_resource",
            {"skill_id": skill.id, "path": "SKILL.md", "offset": 10},
            call_arguments=malformed_args,
        )
        assert rehydrated == {
            "skill_id": skill.id,
            "skill_name": "显式偏移技能",
            "path": "SKILL.md",
            "content": "TAIL",
            "truncated": False,
            "offset": 10,
        }

        rehydrated_zero = rehydrate_skill_tool_result(
            active_by_id,
            db_session,
            "read_skill_resource",
            {"skill_id": skill.id, "path": "SKILL.md", "offset": 0},
            call_arguments=malformed_args,
        )
        assert rehydrated_zero == {
            "skill_id": skill.id,
            "skill_name": "显式偏移技能",
            "path": "SKILL.md",
            "content": "0123456789TAIL",
            "truncated": False,
        }


# ==================== End-to-end cold replay via load_loop_history ====================


@pytest.mark.integration
def test_load_loop_history_cold_replay_rehydrates_recent_skills_and_keeps_official_text_out_of_client(
    db_session: Session,
    monkeypatch,
):
    """端到端冷回放：最近 3 轮内技能完整水合（含 offset），旧轮与停用/下架回退说明，且绝不泄露官方正文到前端。"""
    import agent.skills.active_skills as active_skills_mod

    owner = _user(db_session, "e2e_history")
    project, chat = _project_and_session(db_session, owner, "e2e")

    official_secret = "OFFICIAL_SECRET_METHOD_NEVER_IN_CLIENT_PAYLOAD"
    official = PublicSkill(
        name="官方悬疑",
        description="悬疑技法",
        instructions=official_secret,
        status="approved",
        source="official",
    )
    unapproved = PublicSkill(
        name="已下架技能",
        description="下架",
        instructions="UNAPPROVED_SECRET",
        status="rejected",
        source="official",
    )
    user_skill = UserSkill(
        user_id=owner.id,
        name="自建大纲法",
        description="大纲方法",
        instructions="0123456789USER_FULL_BODY",
        is_active=True,
    )
    disabled_skill = UserSkill(
        user_id=owner.id,
        name="停用技能",
        description="已停用",
        instructions="DISABLED_SECRET",
        is_active=False,
    )
    db_session.add_all([official, unapproved, user_skill, disabled_skill])
    db_session.commit()
    db_session.add(UserAddedSkill(user_id=owner.id, public_skill_id=official.id, is_active=True))
    db_session.add(UserAddedSkill(user_id=owner.id, public_skill_id=unapproved.id, is_active=True))
    db_session.add(SkillResource(
        public_skill_id=official.id,
        path="templates/beat.md",
        content="PREFIX_OFFICIAL_RESOURCE_TAIL",
        size=29,
    ))
    db_session.commit()

    t0 = datetime(2026, 11, 26, 10, 0, 0)
    # 模拟 StreamAdapter 落库的前端安全载荷（绝不含官方/自建正文）
    safe_official_load = StreamAdapter._client_safe_skill_payload(
        "load_skill",
        {
            "skill_id": official.id,
            "skill_name": official.name,
            "source": "added",
            "instructions": official_secret,
            "resources": [{"path": "templates/beat.md", "size": 29}],
        },
    )
    safe_official_res_with_offset = StreamAdapter._client_safe_skill_payload(
        "read_skill_resource",
        {
            "skill_id": official.id,
            "skill_name": official.name,
            "path": "templates/beat.md",
            "content": "OFFICIAL_RESOURCE_TAIL",
            "offset": 7,
        },
    )
    safe_user_res_args_offset = {
        "skill_id": user_skill.id,
        "skill_name": user_skill.name,
        "path": "SKILL.md",
        "size": 14,
        # 故意不写 offset，验证从 call_arguments 回退解析
    }
    assert official_secret not in json.dumps(safe_official_load, ensure_ascii=False)
    assert "OFFICIAL_RESOURCE_TAIL" not in json.dumps(safe_official_res_with_offset, ensure_ascii=False)
    assert safe_official_res_with_offset["skill_id"] == official.id
    assert safe_official_res_with_offset["offset"] == 7

    # 第 1 轮（距最新超过 3 轮）：应不水合，回退 _SKILL_CONTENT_NOT_KEPT
    old_assistant = _add_loop_turn(
        db_session,
        chat.id,
        base_time=t0,
        user_text="第1轮：很久以前加载了官方技能",
        tool_calls=[
            {
                "id": "old-1",
                "name": "load_skill",
                "arguments": json.dumps({"name": "官方悬疑"}, ensure_ascii=False),
                "status": "success",
                "result": dict(safe_official_load),
            },
        ],
    )
    # 第 2 轮（最近第 3 轮）：包含有效官方技能 load_skill + 带 result_data.offset 的 read_skill_resource
    recent_turn_1 = _add_loop_turn(
        db_session,
        chat.id,
        base_time=t0 + timedelta(minutes=1),
        user_text="第2轮：加载官方技能和模板",
        tool_calls=[
            {
                "id": "r1-load",
                "name": "load_skill",
                "arguments": json.dumps({"name": "官方悬疑"}, ensure_ascii=False),
                "status": "success",
                "result": dict(safe_official_load),
            },
            {
                "id": "r1-res",
                "name": "read_skill_resource",
                "arguments": json.dumps({"name": official.id, "path": "templates/beat.md", "offset": 7}),
                "status": "success",
                "result": dict(safe_official_res_with_offset),
            },
        ],
    )
    # 第 3 轮（最近第 2 轮，含 steering 连续 user 行不应被算成多个轮次）：call_arguments.offset 回退
    recent_turn_2 = _add_loop_turn(
        db_session,
        chat.id,
        base_time=t0 + timedelta(minutes=2),
        user_text="第3轮：续读自建技能",
        steering_texts=["补充一句：保持紧凑"],
        tool_calls=[
            {
                "id": "r2-res",
                "name": "read_skill_resource",
                "arguments": json.dumps({"name": user_skill.id, "path": "SKILL.md", "offset": 10}),
                "status": "success",
                "result": dict(safe_user_res_args_offset),
            },
        ],
    )
    # 第 4 轮（最新 1 轮）：停用技能 + 下架技能 + 已删资源 → 全部体面回退 _SKILL_CONTENT_NOT_KEPT
    recent_turn_3 = _add_loop_turn(
        db_session,
        chat.id,
        base_time=t0 + timedelta(minutes=3),
        user_text="第4轮：历史里有停用与下架技能",
        tool_calls=[
            {
                "id": "r3-disabled",
                "name": "load_skill",
                "arguments": json.dumps({"name": disabled_skill.id}),
                "status": "success",
                "result": {"skill_id": disabled_skill.id, "skill_name": "停用技能", "source": "user"},
            },
            {
                "id": "r3-unapproved",
                "name": "load_skill",
                "arguments": json.dumps({"name": unapproved.id}),
                "status": "success",
                "result": {"skill_id": unapproved.id, "skill_name": "已下架技能", "source": "added"},
            },
            {
                "id": "r3-missing-res",
                "name": "read_skill_resource",
                "arguments": json.dumps({"name": official.id, "path": "references/gone.md"}),
                "status": "success",
                "result": {"skill_id": official.id, "skill_name": "官方悬疑", "path": "references/gone.md", "size": 5},
            },
        ],
    )

    assert SKILL_REPLAY_RECENT_TURNS == 3
    load_calls = 0
    orig_load_active = active_skills_mod.load_active_skills

    def _counting_load_active(session: Session, uid: str):
        nonlocal load_calls
        load_calls += 1
        return orig_load_active(session, uid)

    monkeypatch.setattr(active_skills_mod, "load_active_skills", _counting_load_active)

    history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=owner.id,
        chat_session_id=chat.id,
    )

    # 整个 load_loop_history 只惰性查库 1 次启用技能
    assert load_calls == 1

    outputs_by_call_id = {
        item["call_id"]: json.loads(item["output"])
        for item in history.items
        if item.get("type") == "function_call_output"
    }

    # 第 1 轮（超出最近 3 个用户轮次）：不水合，回退提示
    old_out = outputs_by_call_id[f"h{old_assistant.id}_0"]
    assert old_out["note"] == _SKILL_CONTENT_NOT_KEPT["load_skill"]
    assert "instructions" not in old_out["data"]

    # 第 2 轮（最近第 3 轮）：load_skill & read_skill_resource(offset=7) 完整水合且无 _SKILL_CONTENT_NOT_KEPT
    r1_load_out = outputs_by_call_id[f"h{recent_turn_1.id}_0"]
    assert "note" not in r1_load_out
    assert r1_load_out["data"]["instructions"] == official_secret
    assert r1_load_out["data"]["truncated"] is False
    assert r1_load_out["data"]["resources"] == [{"path": "templates/beat.md", "size": 29}]

    r1_res_out = outputs_by_call_id[f"h{recent_turn_1.id}_1"]
    assert "note" not in r1_res_out
    assert r1_res_out["data"] == {
        "skill_id": official.id,
        "skill_name": "官方悬疑",
        "path": "templates/beat.md",
        "content": "OFFICIAL_RESOURCE_TAIL",
        "truncated": False,
        "offset": 7,
    }

    # 第 3 轮（带 steering，仍在最近 3 轮内）：通过 call_arguments 还原 offset=10
    r2_res_out = outputs_by_call_id[f"h{recent_turn_2.id}_0"]
    assert "note" not in r2_res_out
    assert r2_res_out["data"] == {
        "skill_id": user_skill.id,
        "skill_name": "自建大纲法",
        "path": "SKILL.md",
        "content": "USER_FULL_BODY",
        "truncated": False,
        "offset": 10,
    }

    # 第 4 轮：停用 / 下架 / 已删资源均体面回退 _SKILL_CONTENT_NOT_KEPT，绝不泄露正文
    for idx, tool_name in ((0, "load_skill"), (1, "load_skill"), (2, "read_skill_resource")):
        fallback = outputs_by_call_id[f"h{recent_turn_3.id}_{idx}"]
        assert fallback["note"] == _SKILL_CONTENT_NOT_KEPT[tool_name]
        assert "DISABLED_SECRET" not in json.dumps(fallback, ensure_ascii=False)
        assert "UNAPPROVED_SECRET" not in json.dumps(fallback, ensure_ascii=False)

    # DB 持久化记录和前端 client_tool_calls() 绝未被改写，也绝不泄露官方技能正文
    for row in (old_assistant, recent_turn_1, recent_turn_2, recent_turn_3):
        db_session.refresh(row)
        client_payload = client_tool_calls(row.tool_calls) or ""
        assert official_secret not in (row.tool_calls or "")
        assert official_secret not in (row.message_metadata or "")
        assert official_secret not in client_payload
        assert "OFFICIAL_RESOURCE_TAIL" not in client_payload


@pytest.mark.integration
def test_load_loop_history_skips_active_skills_query_when_no_recent_skill_calls(
    db_session: Session,
    monkeypatch,
):
    """没有近期技能调用时，load_loop_history 绝不触发额外的 load_active_skills 数据库查询。"""
    import agent.skills.active_skills as active_skills_mod

    owner = _user(db_session, "no_skill_calls")
    project, chat = _project_and_session(db_session, owner, "no_skill")
    _add_loop_turn(
        db_session,
        chat.id,
        base_time=datetime(2026, 11, 26, 11, 0, 0),
        user_text="普通一轮",
        tool_calls=[
            {
                "id": "q-1",
                "name": "query_files",
                "arguments": "{}",
                "status": "success",
                "result": {"files": []},
            },
        ],
    )

    def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError("load_active_skills should not be called without recent skill tool calls")

    monkeypatch.setattr(active_skills_mod, "load_active_skills", _must_not_be_called)

    history = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=owner.id,
        chat_session_id=chat.id,
    )
    assert len(history.items) >= 3
