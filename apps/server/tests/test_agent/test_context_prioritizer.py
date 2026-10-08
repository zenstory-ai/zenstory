from __future__ import annotations

from agent.context.prioritizer import ContextPrioritizer
from agent.schemas.context import ContextItem, ContextPriority


def _item(
    *,
    item_type: str,
    title: str,
    relevance_score: float | None = None,
    metadata: dict | None = None,
    priority: ContextPriority = ContextPriority.INSPIRATION,
) -> ContextItem:
    return ContextItem(
        id=f"{item_type}-{title}",
        type=item_type,
        title=title,
        content=f"content for {title}",
        relevance_score=relevance_score,
        metadata=metadata or {},
        priority=priority,
    )


def test_classify_priority_uses_focus_flag_and_outline_relations():
    prioritizer = ContextPrioritizer()

    focus_outline = _item(item_type="outline", title="focus", metadata={"is_focus": True, "relation": "child"})
    sibling_outline = _item(item_type="outline", title="sibling", metadata={"relation": "sibling"})
    other_outline = _item(item_type="outline", title="other", metadata={"relation": "other"})

    assert prioritizer.classify_priority(focus_outline) == ContextPriority.CRITICAL
    assert prioritizer.classify_priority(sibling_outline) == ContextPriority.RELEVANT
    assert prioritizer.classify_priority(other_outline) == ContextPriority.RELEVANT


def test_classify_priority_handles_lore_and_snippet_thresholds():
    prioritizer = ContextPrioritizer()

    high_lore = _item(item_type="lore", title="law", metadata={"importance": "high"})
    medium_lore = _item(item_type="lore", title="history", metadata={"importance": "medium"})
    weak_snippet = _item(item_type="snippet", title="weak", relevance_score=0.3)
    strong_snippet = _item(item_type="snippet", title="strong", relevance_score=0.8)

    assert prioritizer.classify_priority(high_lore) == ContextPriority.CONSTRAINT
    assert prioritizer.classify_priority(medium_lore) == ContextPriority.RELEVANT
    assert prioritizer.classify_priority(weak_snippet) == ContextPriority.INSPIRATION
    assert prioritizer.classify_priority(strong_snippet) == ContextPriority.RELEVANT


def test_classify_priority_upgrades_parent_outline_to_critical():
    """Parent outlines built via from_outline must reach CRITICAL despite the CONSTRAINT preset."""
    prioritizer = ContextPrioritizer()

    parent = ContextItem.from_outline(
        id="outline-parent",
        title="全书大纲",
        content="全书故事背景",
        is_focus=False,
        relation="parent",
    )

    assert parent.priority == ContextPriority.CONSTRAINT  # factory preset
    assert prioritizer.classify_priority(parent) == ContextPriority.CRITICAL


def test_classify_priority_never_downgrades_preset_priority():
    prioritizer = ContextPrioritizer()

    # User-attached outlines get an explicit CRITICAL override in the assembler
    attached = ContextItem.from_outline(
        id="outline-attached",
        title="attached",
        content="user attached file",
        is_focus=False,
        relation="attached",
    )
    attached.priority = ContextPriority.CRITICAL
    assert prioritizer.classify_priority(attached) == ContextPriority.CRITICAL

    # Sibling chapters are the one documented exception: demoted from the
    # CONSTRAINT preset to RELEVANT so they cannot crowd out characters/core lore
    sibling = ContextItem.from_outline(
        id="outline-sibling",
        title="sibling",
        content="sibling outline",
        is_focus=False,
        relation="sibling",
    )
    assert prioritizer.classify_priority(sibling) == ContextPriority.RELEVANT

    # Retrieval snippets are pinned to RELEVANT even below the 0.7 threshold
    snippet = ContextItem.from_snippet(
        id="snippet-retrieval",
        title="retrieved",
        content="snippet",
        relevance_score=0.55,
    )
    snippet.priority = ContextPriority.RELEVANT
    assert prioritizer.classify_priority(snippet) == ContextPriority.RELEVANT


def _cjk(count: int) -> str:
    return "字" * count


def _attached_character(*, id: str = "attached-char", chars: int = 300) -> ContextItem:
    """用户从聊天框附加的角色文件（assembler 会覆写为 CRITICAL 并打 attached 标记）。"""
    item = ContextItem.from_character(id=id, name="附加角色", profile=_cjk(chars))
    item.priority = ContextPriority.CRITICAL
    item.metadata["attached"] = True
    return item


def _batch(
    *,
    focus_chars: int,
    parent_chars: int,
    attached: ContextItem | None,
    characters: int = 10,
    medium_lore: int = 6,
    low_lore: int = 4,
) -> list[ContextItem]:
    """焦点草稿 + 父大纲 + 可调规模的 CONSTRAINT/RELEVANT/INSPIRATION 需求。"""
    focus = ContextItem.from_outline(
        id="focus",
        title="第10章",
        content=_cjk(focus_chars),
        is_focus=True,
    )
    focus.metadata["file_type"] = "draft"

    parent = ContextItem.from_outline(
        id="parent",
        title="全书大纲",
        content=_cjk(parent_chars),
        is_focus=False,
        relation="parent",
    )
    parent.metadata["file_type"] = "outline"

    items = [focus, parent]
    if attached is not None:
        items.append(attached)
    items.extend(
        ContextItem.from_character(id=f"char-{i}", name=f"角色{i}", profile=_cjk(300))
        for i in range(characters)
    )
    items.extend(
        ContextItem.from_lore(
            id=f"lore-med-{i}", title=f"设定{i}", content=_cjk(300), importance="medium"
        )
        for i in range(medium_lore)
    )
    items.extend(
        ContextItem.from_lore(
            id=f"lore-low-{i}", title=f"杂项{i}", content=_cjk(300), importance="low"
        )
        for i in range(low_lore)
    )
    return items


def _select(items: list[ContextItem], max_tokens: int) -> dict[str, ContextItem]:
    from agent.context.budget import TokenBudget

    prioritizer = ContextPrioritizer()
    budget = TokenBudget(max_tokens=max_tokens)
    prioritized = prioritizer.prioritize(items)
    groups = prioritizer.group_by_priority(prioritized)
    selected, _ = budget.select_items(prioritized, groups)
    return {item.id: item for item in selected}


def test_parent_outline_upgrade_yields_to_user_attached_content():
    """有用户附加内容时父大纲留在 CONSTRAINT，避免抢走 CRITICAL 的池化预算。"""
    prioritizer = ContextPrioritizer()

    with_attached = _batch(
        focus_chars=1000, parent_chars=600, attached=_attached_character()
    )
    groups = prioritizer.group_by_priority(with_attached)
    critical_ids = [item.id for item in groups[ContextPriority.CRITICAL]]
    constraint_ids = [item.id for item in groups[ContextPriority.CONSTRAINT]]

    assert critical_ids[:2] == ["focus", "attached-char"]
    assert "parent" not in critical_ids
    assert "parent" in constraint_ids

    # 没有附加内容时升级照旧生效（父大纲才能借用低档闲置额度）
    without_attached = _batch(
        focus_chars=1000, parent_chars=600, attached=None
    )
    groups = prioritizer.group_by_priority(without_attached)
    assert "parent" in [item.id for item in groups[ContextPriority.CRITICAL]]


def test_declined_parent_upgrade_still_never_downgrades():
    """拒绝升级只退到 CONSTRAINT，不能把条目压到比预设更低的档位。"""
    prioritizer = ContextPrioritizer()
    parent = _item(
        item_type="outline",
        title="parent",
        relevance_score=0.8,
        metadata={"relation": "parent"},
    )
    assert parent.priority == ContextPriority.INSPIRATION  # 预设最低档

    groups = prioritizer.group_by_priority([parent, _attached_character()])

    assert [item.title for item in groups[ContextPriority.CONSTRAINT]] == ["parent"]
    assert groups[ContextPriority.INSPIRATION] == []


def test_select_items_keeps_user_attached_file_when_lower_tiers_saturated():
    """CONSTRAINT/RELEVANT/INSPIRATION 需求饱和时，用户附加的文件仍必须入选。"""
    attached = _attached_character()
    items = _batch(focus_chars=1000, parent_chars=600, attached=attached)

    selected = _select(items, max_tokens=5500)

    assert "focus" in selected
    assert "attached-char" in selected, "用户显式附加的文件被挤出上下文"
    assert not selected["attached-char"].is_truncated
    # 父大纲仍在 CONSTRAINT 档保底入选
    assert "parent" in selected


def test_select_items_lets_parent_outline_use_pooled_critical_budget():
    """无附加内容时，超出 CONSTRAINT 份额的父大纲应完整进入 CRITICAL 池化预算。"""
    # 低档需求很小 → CRITICAL 可借用闲置额度
    items = _batch(
        focus_chars=400,
        parent_chars=3000,
        attached=None,
        characters=2,
        medium_lore=1,
        low_lore=0,
    )

    selected = _select(items, max_tokens=8000)

    assert "parent" in selected
    assert not selected["parent"].is_truncated


def test_group_by_priority_keeps_focus_first_despite_recall_boost():
    """query recall 加成不得把其他条目排到焦点文件之前。"""
    prioritizer = ContextPrioritizer()
    items = _batch(focus_chars=1000, parent_chars=1500, attached=None)
    for item in items:
        if item.id == "parent":
            item.relevance_score = 1.04  # _apply_query_recall_ranking 的加成

    groups = prioritizer.group_by_priority(items)

    assert [item.id for item in groups[ContextPriority.CRITICAL]] == ["focus", "parent"]
    assert [item.id for item in prioritizer.prioritize(items)][:2] == ["focus", "parent"]


def test_group_by_priority_ranks_attached_before_higher_relevance_items():
    prioritizer = ContextPrioritizer()
    attached = _attached_character()
    quote = ContextItem.from_quote(id="quote", text="选中的正文", file_title="第10章")
    promoted_snippet = ContextItem.from_snippet(
        id="snippet", title="检索片段", content="内容", relevance_score=0.95
    )
    promoted_snippet.priority = ContextPriority.CRITICAL
    focus = ContextItem.from_outline(
        id="focus", title="第10章", content="正文", is_focus=True
    )

    groups = prioritizer.group_by_priority([promoted_snippet, attached, quote, focus])

    assert [item.id for item in groups[ContextPriority.CRITICAL]] == [
        "focus",
        "quote",
        "attached-char",
        "snippet",
    ]


def test_prioritize_sorts_by_priority_then_relevance_then_type():
    prioritizer = ContextPrioritizer()
    items = [
        _item(item_type="lore", title="lore", relevance_score=0.9, metadata={"importance": "low"}),
        _item(item_type="snippet", title="snippet", relevance_score=0.95),
        _item(item_type="character", title="character", relevance_score=0.1),
        _item(item_type="outline", title="outline", relevance_score=0.4, metadata={"relation": "parent"}),
    ]

    result = prioritizer.prioritize(items)

    assert [item.title for item in result] == ["outline", "character", "snippet", "lore"]


def test_group_by_priority_and_budget_allocation():
    prioritizer = ContextPrioritizer()
    items = [
        _item(item_type="snippet", title="high-snippet", relevance_score=0.9),
        _item(item_type="snippet", title="low-snippet", relevance_score=0.45),
        _item(item_type="character", title="hero"),
    ]

    groups = prioritizer.group_by_priority(items)
    allocation = prioritizer.get_budget_allocation(
        1000,
        {ContextPriority.CRITICAL: 0.1, ContextPriority.CONSTRAINT: 0.2},
    )

    assert [item.title for item in groups[ContextPriority.RELEVANT]] == ["high-snippet"]
    assert [item.title for item in groups[ContextPriority.INSPIRATION]] == ["low-snippet"]
    assert [item.title for item in groups[ContextPriority.CONSTRAINT]] == ["hero"]
    assert allocation == {
        ContextPriority.CRITICAL: 100,
        ContextPriority.CONSTRAINT: 200,
    }
