"""Tests for structured prompt context blocks in MessageManager."""

import re

from agent.core.message_manager import MessageManager


def test_build_context_section_emits_structured_blocks():
    manager = MessageManager(project_id="proj-1")
    context_items = [
        {
            "type": "character",
            "priority": "constraint",
            "title": "张三",
            "content": "主角，性格坚韧，遇强则强。",
        },
        {
            "type": "lore",
            "priority": "constraint",
            "title": "魔法体系",
            "content": "魔法分为火、水、风、土四系，不可跨系瞬发。",
        },
        {
            "type": "outline",
            "priority": "relevant",
            "title": "第一章大纲",
            "content": "主角初入宗门，与师兄起冲突。",
        },
        {
            "type": "quote",
            "priority": "critical",
            "title": "引用自: 第一章",
            "content": "“你敢再说一遍？”",
        },
    ]

    section = manager._build_context_section(  # noqa: SLF001 - intentional private-method unit test
        assembled_context="项目状态\n相关内容详情",
        context_items=context_items,
        force_en=False,
    )
    text = "\n".join(section)

    assert "<world_model_truth>" in text
    assert "<world_model_surface>" in text
    assert "张三" in text
    assert "魔法体系" in text
    assert "第一章大纲" in text
    assert "<world_knowledge>" not in text
    assert "<working_set>" not in text
    assert "<project_context_raw>" in text
    assert "项目状态" in text


def test_narrative_constraints_are_injected_independently():
    """Narrative constraints should be independent from world model blocks."""
    manager = MessageManager(project_id="proj-1")
    context_items = [
        {
            "type": "character",
            "priority": "constraint",
            "title": "张三",
            "content": "主角，性格坚韧，遇强则强。",
        },
        {
            "type": "quote",
            "priority": "critical",
            "title": "引用自: 第一章",
            "content": "“你敢再说一遍？”",
        },
        {
            "type": "outline",
            "priority": "relevant",
            "title": "第一章大纲",
            "content": "主角初入宗门，与师兄起冲突。",
        },
    ]

    constraints, world_truth, world_surface = manager._extract_structured_context(  # noqa: SLF001
        context_items=context_items,
        force_en=False,
    )

    constraints_section = manager._build_narrative_constraints_section(  # noqa: SLF001
        constraints=constraints,
        force_en=False,
    )
    context_section = manager._build_context_section(  # noqa: SLF001
        assembled_context="项目状态\n相关内容详情",
        context_items=context_items,
        force_en=False,
        world_truth=world_truth,
        world_surface=world_surface,
    )

    constraints_text = "\n".join(constraints_section)
    context_text = "\n".join(context_section)

    assert "<narrative_constraints>" in constraints_text
    assert "角色一致性" in constraints_text
    assert "不得与用户引用文本冲突" in constraints_text

    assert "<world_model_truth>" in context_text
    assert "<world_model_surface>" in context_text
    assert "<narrative_constraints>" not in context_text
    assert "<world_knowledge>" not in context_text
    assert "<working_set>" not in context_text


def test_extract_structured_context_is_deduplicated_and_bounded():
    manager = MessageManager(project_id="proj-1")
    duplicated_items = [
        {
            "type": "character",
            "priority": "constraint",
            "title": "张三",
            "content": "主角，性格坚韧。",
        }
        for _ in range(50)
    ]

    constraints, world, working = manager._extract_structured_context(  # noqa: SLF001
        context_items=duplicated_items,
        force_en=False,
    )

    assert len(constraints) == 1
    assert len(world) == 1
    assert len(working) == 0
    assert len(constraints) <= 12
    assert len(world) <= 24


def test_raw_context_keeps_paragraphs_and_skips_duplicate_previews():
    """生产事故回归：原始上下文被压成一行 + 重复 220 字预览，模型以为被压缩而重读。"""
    manager = MessageManager(project_id="proj-1")
    chapter_text = "第一段。" * 80 + "\n\n" + "第二段。" * 80
    context_items = [
        {
            "id": "file-ch1",
            "type": "outline",
            "title": "第一章",
            "content": chapter_text,
            "metadata": {"file_type": "draft"},
        },
        {
            "id": "file-cut",
            "type": "lore",
            "title": "魔法体系",
            "content": "魔法分四系。" * 80,
            "metadata": {"file_type": "lore", "truncated": True},
        },
    ]
    assembled = f"第一章 (id=file-ch1) [全文]\n{chapter_text}"

    _, truth, surface = manager._extract_structured_context(  # noqa: SLF001
        context_items=context_items,
        force_en=False,
        raw_context_complete=MessageManager._raw_context_is_complete(assembled),  # noqa: SLF001
    )
    section = "\n".join(
        manager._build_context_section(  # noqa: SLF001
            assembled_context=assembled,
            context_items=context_items,
            force_en=False,
            world_truth=truth,
            world_surface=surface,
        )
    )

    # 段落与换行原样保留
    assert chapter_text in section
    # 已在原始上下文中完整出现的条目只留标题 + id，不再附节选
    assert surface == ["第一章 (id=file-ch1)（全文见项目上下文）"]
    # 被预算截断的条目仍给节选，并显式标注
    assert truth[0].startswith("魔法体系：") and truth[0].endswith("…（节选）")


def test_truncate_text_marks_preview_explicitly_when_exceeding_limit():
    manager = MessageManager(project_id="proj-1")
    long_text = "设定" * 5000

    truncated = manager._truncate_text(long_text, 120)  # noqa: SLF001

    assert truncated.endswith("…（节选）")
    assert len(truncated) <= 120 + len("…（节选）")


def test_build_context_section_separates_world_model_surface_and_truth():
    """World model injection should separate surface facts and truth facts."""
    manager = MessageManager(project_id="proj-1")
    context_items = [
        {
            "type": "lore",
            "priority": "constraint",
            "title": "王城公开规则",
            "content": "子时关城门，黎明才开启。",
        },
        {
            "type": "outline",
            "priority": "relevant",
            "title": "城门冲突场景",
            "content": "主角在子时前赶到城门，与守卫争执。",
        },
        {
            "type": "character",
            "priority": "constraint",
            "title": "沈砚",
            "content": "寡言谨慎，不轻易泄密。",
        },
    ]

    section = manager._build_context_section(  # noqa: SLF001 - intentional private-method unit test
        assembled_context="原始上下文",
        context_items=context_items,
        force_en=False,
    )
    text = "\n".join(section)

    surface_block = re.search(
        r"<[^>]*surface[^>]*>(.*?)</[^>]*surface[^>]*>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    truth_block = re.search(
        r"<[^>]*truth[^>]*>(.*?)</[^>]*truth[^>]*>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    assert surface_block is not None
    assert truth_block is not None

    surface_text = surface_block.group(1)
    truth_text = truth_block.group(1)

    assert "城门冲突场景" in surface_text
    assert "王城公开规则" not in surface_text
    assert "王城公开规则" in truth_text


def test_extract_structured_context_respects_visibility_metadata_routes():
    """Visibility metadata should control truth/surface routing."""
    manager = MessageManager(project_id="proj-1")
    context_items = [
        {
            "type": "character",
            "priority": "constraint",
            "title": "主角卡",
            "content": "谨慎冷静，绝不冒进。",
            "metadata": {"visibility": "active"},
        },
        {
            "type": "lore",
            "priority": "constraint",
            "title": "旁支传闻",
            "content": "只作场景参考，不作为硬设定。",
            "metadata": {"visibility": "reference"},
        },
        {
            "type": "snippet",
            "priority": "relevant",
            "title": "隐藏线索",
            "content": "不应注入到世界模型。",
            "metadata": {"visibility": "hidden"},
        },
        {
            "type": "outline",
            "priority": "relevant",
            "title": "反向路由",
            "content": "该条通过显式 route 注入 truth。",
            "metadata": {"route": "truth"},
        },
    ]

    constraints, world_truth, world_surface = manager._extract_structured_context(  # noqa: SLF001
        context_items=context_items,
        force_en=False,
    )

    assert any("主角卡" in entry for entry in world_truth)
    assert any("反向路由" in entry for entry in world_truth)
    assert not any("旁支传闻" in entry for entry in world_truth)

    assert any("旁支传闻" in entry for entry in world_surface)
    assert not any("隐藏线索" in entry for entry in world_truth)
    assert not any("隐藏线索" in entry for entry in world_surface)

    assert any("主角卡" in rule for rule in constraints)
    assert not any("旁支传闻" in rule for rule in constraints)
