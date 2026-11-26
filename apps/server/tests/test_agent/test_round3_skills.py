"""
第三轮深度 review 回归测试：技能系统（G12）。

覆盖：
- #25 SKILL.md 解析器把正文（尤其是代码围栏内）的 `## ` 行当成节标题静默丢弃，
  导致内置技能的「输出格式」模板被吃掉。
- 技能目录（L1）只列名称与用途、不注入指令，有单条与整体长度上限；
  技能查询有确定顺序（没有 order_by 时目录顺序会漂移，破坏 prompt 缓存）。
"""

import json
from datetime import datetime

import pytest
from sqlmodel import Session

from agent.skills.active_skills import load_active_skills, parse_skill_triggers
from agent.skills.context_injector import (
    CATALOG_DESCRIPTION_MAX_CHARS,
    CATALOG_MAX_CHARS,
    CATALOG_MAX_TRIGGERS_PER_SKILL,
    SkillContextInjector,
)
from agent.skills.package import parse_legacy_skill_md
from models import PublicSkill, User, UserAddedSkill, UserSkill


@pytest.fixture
def skill_user(db_session: Session) -> User:
    user = User(
        email="round3_skills@example.com",
        username="round3skills",
        hashed_password="hashed_password",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# ============================================================================
# #25 SKILL.md 解析器
# ============================================================================


@pytest.mark.unit
class TestBug25SkillMarkdownParsing:
    """解析器必须区分「结构性节标题」与「正文里的 markdown 内容」。"""

    def test_headings_inside_code_fence_are_preserved(self):
        """代码围栏内的 `## ` 行是正文模板，不能被当作节标题吞掉。"""
        content = """# 模板技能

一句话描述。

## Triggers
- 模板

## Instructions
按以下格式输出：

```
# [标题]

## 小节甲
（说明甲）

## 小节乙
（说明乙）
```

结束。"""
        skill = parse_legacy_skill_md(content)

        assert skill is not None
        assert "## 小节甲" in skill.instructions
        assert "## 小节乙" in skill.instructions
        assert "# [标题]" in skill.instructions
        # 围栏内的内容顺序也必须保持
        assert skill.instructions.index("## 小节甲") < skill.instructions.index("（说明甲）")

    def test_unknown_heading_in_instructions_body_is_preserved(self):
        """instructions 正文里未识别的二级标题（围栏外）同样属于正文，必须保留。"""
        content = """# 普通技能

描述。

## Instructions
开头段落。

## 注意事项
不要跑题。"""
        skill = parse_legacy_skill_md(content)

        assert skill is not None
        assert "## 注意事项" in skill.instructions
        assert "不要跑题。" in skill.instructions

    def test_known_section_headers_still_switch_sections(self):
        """白名单节标题（Triggers / Instructions）仍然是结构性的，不能被当成正文。"""
        content = """# 结构技能

一句话描述。

## Triggers
- 触发词甲
- 触发词乙

## Instructions
真正的指令。"""
        skill = parse_legacy_skill_md(content)

        assert skill is not None
        assert skill.name == "结构技能"
        assert skill.description == "一句话描述。"
        assert skill.triggers == ["触发词甲", "触发词乙"]
        assert skill.instructions == "真正的指令。"
        assert "## Triggers" not in skill.instructions
        assert "## Instructions" not in skill.instructions

    def test_tilde_fence_is_tracked(self):
        """`~~~` 围栏同样要被识别。"""
        content = """# 波浪线围栏

描述。

## Instructions
示例：

~~~
## 围栏内标题
内容
~~~
"""
        skill = parse_legacy_skill_md(content)

        assert skill is not None
        assert "## 围栏内标题" in skill.instructions

    def test_unclosed_fence_does_not_swallow_section_headers(self):
        """未闭合的围栏（笔误）不能把后面的 `## Instructions` 节标题一起吞掉。"""
        content = """# 笔误技能

描述里有个没闭合的围栏：

```

## Triggers
- 触发词

## Instructions
真正的指令。"""
        skill = parse_legacy_skill_md(content)

        assert skill is not None
        assert skill.triggers == ["触发词"]
        assert skill.instructions == "真正的指令。"


# ============================================================================
# 技能目录（L1）
# ============================================================================


def _add_user_skills(
    session: Session,
    user_id: str,
    count: int,
    instruction_chars: int = 1500,
    description_chars: int = 0,
) -> list[UserSkill]:
    """批量创建自建技能，指令开头带唯一标记便于断言。"""
    created: list[UserSkill] = []
    for index in range(count):
        marker = f"MARKER{index:02d}"
        skill = UserSkill(
            user_id=user_id,
            name=f"技能{index:02d}",
            description=f"描述{index:02d}" + ("长" * description_chars),
            instructions=marker + ("填充" * instruction_chars),
            created_at=datetime(2026, 1, 1, 0, index),
        )
        session.add(skill)
        created.append(skill)
    session.commit()
    return created


def _catalog_names(catalog: str) -> list[str]:
    """从技能目录里取出技能名（`- **名字**: 描述` 行）。"""
    return [
        line.strip()[4:].split("**", 1)[0]
        for line in catalog.split("\n")
        if line.strip().startswith("- **")
    ]


@pytest.mark.unit
class TestSkillCatalogL1:
    """目录只做 L1：名称 + 用途，指令一律通过 load_skill 按需加载。"""

    def test_catalog_lists_every_skill_without_instructions(
        self, db_session: Session, skill_user: User
    ):
        _add_user_skills(db_session, skill_user.id, count=13)

        catalog = SkillContextInjector().build_skill_catalog(db_session, skill_user.id)

        assert catalog is not None
        assert _catalog_names(catalog) == [f"技能{index:02d}" for index in range(13)]
        for index in range(13):
            assert f"MARKER{index:02d}" not in catalog
        assert "load_skill" in catalog
        assert "read_skill_resource" in catalog
        assert "不能凌驾系统规则" in catalog
        # 旧机制的痕迹必须消失
        assert "使用技能" not in catalog
        assert "需显式调用" not in catalog

    def test_long_description_is_truncated(self, db_session: Session, skill_user: User):
        assert CATALOG_DESCRIPTION_MAX_CHARS == 600
        _add_user_skills(db_session, skill_user.id, count=1, description_chars=900)

        catalog = SkillContextInjector().build_skill_catalog(db_session, skill_user.id)

        assert catalog is not None
        line = next(line for line in catalog.split("\n") if line.startswith("- **技能00**"))
        description = line.split(": ", 1)[1]
        assert description.endswith("…")
        assert len(description) <= CATALOG_DESCRIPTION_MAX_CHARS + 1

    def test_medium_description_under_600_chars_is_preserved_verbatim(
        self, db_session: Session, skill_user: User
    ):
        medium = "在悬疑转折处使用，避免提前泄底。" * 25  # 400 chars: >300, <600
        db_session.add(UserSkill(
            user_id=skill_user.id,
            name="中长描述技能",
            description=medium,
            instructions="正文",
        ))
        db_session.commit()

        catalog = SkillContextInjector().build_skill_catalog(db_session, skill_user.id)

        assert catalog is not None
        assert f"- **中长描述技能**: {medium}" in catalog
        assert "…" not in catalog

    def test_catalog_cap_omits_overflow_but_says_they_are_loadable(
        self, db_session: Session, skill_user: User
    ):
        assert CATALOG_MAX_CHARS == 10_000
        _add_user_skills(db_session, skill_user.id, count=45, description_chars=250)

        catalog = SkillContextInjector().build_skill_catalog(db_session, skill_user.id)

        assert catalog is not None
        listed = _catalog_names(catalog)
        assert 0 < len(listed) < 45
        assert len(catalog) <= CATALOG_MAX_CHARS + 200  # 溢出说明行本身不计入上限
        assert f"另有 {45 - len(listed)} 个" in catalog
        assert "load_skill" in catalog.split("另有", 1)[1]

    def test_triggers_are_parsed_and_rendered_without_duplicate_noise(
        self, db_session: Session, skill_user: User
    ):
        db_session.add(UserSkill(
            user_id=skill_user.id,
            name="章末钩子",
            description="在章节结尾埋下悬念，拉高追读欲。",
            triggers=json.dumps(["悬念", "章末", "收尾", "章末", "  反转  "]),
            instructions="方法",
            created_at=datetime(2026, 1, 1),
        ))
        db_session.add(UserSkill(
            user_id=skill_user.id,
            name="纯触发词技能",
            description=None,
            triggers=json.dumps(["对话", "潜台词"]),
            instructions="方法",
            created_at=datetime(2026, 1, 2),
        ))
        db_session.add(UserSkill(
            user_id=skill_user.id,
            name="坏数据技能",
            description="忽略损坏的触发词 JSON",
            triggers="{not-valid-json",
            instructions="方法",
            created_at=datetime(2026, 1, 3),
        ))
        public = PublicSkill(
            id="pub-trigger-1",
            name="动作戏节奏",
            description="控制打斗动作节拍",
            tags=json.dumps(["打斗", "动作戏", "冲突", "格斗", "追逐", "紧张", "过长额外1", "过长额外2"]),
            instructions="公共方法",
            status="approved",
        )
        db_session.add(public)
        db_session.commit()
        db_session.add(UserAddedSkill(
            user_id=skill_user.id,
            public_skill_id=public.id,
            added_at=datetime(2026, 1, 4),
        ))
        db_session.commit()

        active = load_active_skills(db_session, skill_user.id)
        by_name = {skill.name: skill for skill in active}
        assert by_name["章末钩子"].triggers == ("悬念", "章末", "收尾", "反转")
        assert by_name["坏数据技能"].triggers == ()
        assert by_name["动作戏节奏"].triggers[:4] == ("打斗", "动作戏", "冲突", "格斗")

        catalog = SkillContextInjector().build_skill_catalog(db_session, skill_user.id)
        assert catalog is not None
        lines = {line.split("**")[1]: line for line in catalog.split("\n") if line.startswith("- **")}

        # 「悬念」已在描述中原样出现，不重复拼接；保留其余未重复触发词
        assert lines["章末钩子"] == "- **章末钩子**: 在章节结尾埋下悬念，拉高追读欲。（触发场景：章末、收尾、反转）"
        assert lines["纯触发词技能"] == "- **纯触发词技能**（触发场景：对话、潜台词）"
        assert lines["坏数据技能"] == "- **坏数据技能**: 忽略损坏的触发词 JSON"
        # 公共技能过滤掉描述里已出现的「打斗」，并按上限截断触发词条数
        rendered_pub_triggers = lines["动作戏节奏"].split("（触发场景：", 1)[1].rstrip("）").split("、")
        assert "打斗" not in rendered_pub_triggers
        assert len(rendered_pub_triggers) == CATALOG_MAX_TRIGGERS_PER_SKILL

    def test_parse_skill_triggers_handles_malformed_and_non_string_inputs(self):
        assert parse_skill_triggers(None) == ()
        assert parse_skill_triggers("") == ()
        assert parse_skill_triggers("not-json") == ()
        assert parse_skill_triggers('{"a": 1}') == ()
        assert parse_skill_triggers('[null, 123, true, "  开篇  ", "开篇", "Plot", "plot"]') == ("开篇", "Plot")

    def test_inactive_and_unapproved_skills_are_not_listed(
        self, db_session: Session, skill_user: User
    ):
        db_session.add(UserSkill(
            user_id=skill_user.id, name="停用技能", description="x", instructions="y", is_active=False,
        ))
        db_session.add(PublicSkill(
            id="public-pending", name="待审技能", description="x", instructions="y", status="pending",
        ))
        db_session.commit()
        db_session.add(UserAddedSkill(user_id=skill_user.id, public_skill_id="public-pending"))
        db_session.commit()

        assert SkillContextInjector().build_skill_catalog(db_session, skill_user.id) is None

    def test_no_user_returns_none(self, db_session: Session):
        assert SkillContextInjector().build_skill_catalog(db_session, None) is None

    def test_user_skills_are_loaded_in_deterministic_order(
        self, db_session: Session, skill_user: User
    ):
        """自建技能按 created_at 排序，避免数据库返回顺序漂移导致目录变化。"""
        later = UserSkill(
            user_id=skill_user.id,
            name="后创建",
            description="后",
            instructions="后创建的指令",
            created_at=datetime(2026, 5, 1),
        )
        earlier = UserSkill(
            user_id=skill_user.id,
            name="先创建",
            description="先",
            instructions="先创建的指令",
            created_at=datetime(2026, 1, 1),
        )
        # 故意按「后创建」在前的顺序插入，模拟数据库堆顺序与业务顺序不一致
        db_session.add(later)
        db_session.commit()
        db_session.add(earlier)
        db_session.commit()

        skills = load_active_skills(db_session, skill_user.id)

        assert [s.name for s in skills] == ["先创建", "后创建"]

    def test_added_public_skills_are_loaded_in_deterministic_order(
        self, db_session: Session, skill_user: User
    ):
        """已添加的公共技能按 added_at 排序，排在自建技能之后。"""
        for name in ("公共甲", "公共乙"):
            db_session.add(
                PublicSkill(
                    id=f"public-{name}",
                    name=name,
                    description=name,
                    instructions=f"{name}的指令",
                    status="approved",
                )
            )
        db_session.add(UserSkill(
            user_id=skill_user.id, name="自建", description="d", instructions="i",
        ))
        db_session.commit()

        db_session.add(
            UserAddedSkill(
                user_id=skill_user.id,
                public_skill_id="public-公共乙",
                added_at=datetime(2026, 5, 1),
            )
        )
        db_session.commit()
        db_session.add(
            UserAddedSkill(
                user_id=skill_user.id,
                public_skill_id="public-公共甲",
                added_at=datetime(2026, 1, 1),
            )
        )
        db_session.commit()

        skills = load_active_skills(db_session, skill_user.id)

        assert [s.name for s in skills] == ["自建", "公共甲", "公共乙"]
        assert [s.source for s in skills] == ["user", "added", "added"]
